"""Pure host-observer checks; no host process or LXD instance is stopped."""

import json
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock
from unittest.mock import patch
from contextlib import redirect_stderr

from scripts import observe_study_host as host_guard

from scripts.observe_study_host import (
    GIB, INSTANCE, MemoryGuard, atomic_checkpoint, parse_cpu_stat, parse_loadavg,
    parse_meminfo, parse_pressure, parse_vmstat, parse_arcstats, read_arcstats, stop_study_instance,
)


class HostCounterParsingTests(unittest.TestCase):
    def test_arc_payload_is_bounded_and_preserves_signed_available_counter(self):
        value = parse_arcstats("name type data\nsize 4 123\nc 4 456\nc_min 4 100\nc_max 4 1000\nmemory_available_bytes 3 -32\nunknown_future_stat 4 999\n")
        self.assertEqual(value["size"], 123)
        self.assertEqual(value["memory_available_bytes"], -32)
        self.assertNotIn("unknown_future_stat", value)
        with self.assertRaises(ValueError):
            parse_arcstats("size 4 123\nsize 4 456\n")

    def test_absent_or_malformed_optional_arc_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "arcstats"
            self.assertEqual(read_arcstats(path), {"available": False, "reason": "not_present"})
            path.write_text("size 4 malformed\n")
            result = read_arcstats(path)
            self.assertFalse(result["available"])
            self.assertEqual(result["reason"], "ValueError")

    def test_memory_kibibytes_and_nearly_full_swap_remain_distinct(self):
        parsed = parse_meminfo("MemTotal: 201326592 kB\nMemAvailable: 8388608 kB\nSwapTotal: 8388608 kB\nSwapFree: 256 kB\n")
        self.assertEqual(parsed["MemAvailable"], 8 * GIB)
        self.assertEqual(parsed["SwapTotal"], 8 * GIB)
        self.assertEqual(parsed["SwapFree"], 256 * 1024)
        with self.assertRaises(ValueError):
            parse_meminfo("MemAvailable: 8 GB\n")

    def test_missing_swap_counters_are_not_reported_as_zero(self):
        self.assertEqual(parse_vmstat("pswpin 12\npswpout 34\npgmajfault 5\n"), {"pswpin": 12, "pswpout": 34, "pgmajfault": 5})
        with self.assertRaises(ValueError):
            parse_vmstat("pswpin 12\n")

    def test_pressure_units_cpu_ticks_and_runnable_tasks_are_preserved(self):
        pressure = parse_pressure("some avg10=1.25 avg60=0.5 avg300=0.1 total=120000\nfull avg10=0.1 avg60=0.0 avg300=0.0 total=3000\n")
        self.assertEqual(pressure["some"]["total_microseconds"], 120000)
        self.assertEqual(pressure["some"]["avg10"], 1.25)
        cpu = parse_cpu_stat("cpu 10 2 30 400 5 6 7 8 0 0\ncpu0 1 0 2 3 0 0 0 0\ncpu1 1 0 2 3 0 0 0 0\nprocs_running 3\nprocs_blocked 1\n")
        self.assertEqual(cpu["aggregate_ticks"]["steal"], 8)
        self.assertEqual(cpu["logical_cpus"], 2)
        self.assertEqual(parse_loadavg("6.1 7.2 8.3 3/456 789\n")["total_tasks"], 456)
        with self.assertRaises(ValueError):
            parse_pressure("some avg10=nan avg60=0 avg300=0 total=1\n")


class MemoryGuardTests(unittest.TestCase):
    def test_twenty_four_hour_cli_bound_does_not_change_the_memory_floor_or_sample_rule(self):
        self.assertEqual(host_guard.MAX_DURATION_SECONDS,86400)
        with patch.object(sys,'argv',['observer','--out','unused.json']), \
                patch.object(host_guard,'observe',return_value=0) as observe:
            self.assertEqual(host_guard.main(),0)
        args = observe.call_args.args[0]
        self.assertEqual(args.max_duration_seconds,86400)
        self.assertEqual(args.min_available_gib,16)
        self.assertEqual(args.instance,INSTANCE)
        with patch.object(sys,'argv',['observer','--out','unused.json','--max-duration-seconds','86401']), \
                patch.object(host_guard,'observe') as observe, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                host_guard.main()
        self.assertEqual(caught.exception.code,2)
        observe.assert_not_called()

    def test_fra_floor_is_stricter_and_retains_the_same_two_sample_rule(self):
        guard = MemoryGuard(16 * GIB)
        self.assertFalse(guard.observe(16 * GIB))
        self.assertFalse(guard.observe(16 * GIB - 1))
        self.assertTrue(guard.observe(15 * GIB))

    def test_two_consecutive_low_samples_trigger_exactly_once(self):
        guard = MemoryGuard(8 * GIB)
        self.assertFalse(guard.observe(8 * GIB - 1))
        self.assertTrue(guard.observe(7 * GIB))
        self.assertFalse(guard.observe(6 * GIB))

    def test_a_recovery_at_the_floor_resets_the_sequence(self):
        guard = MemoryGuard(8 * GIB)
        self.assertFalse(guard.observe(7 * GIB))
        self.assertFalse(guard.observe(8 * GIB))
        self.assertEqual(guard.consecutive_low_samples, 0)
        self.assertFalse(guard.observe(7 * GIB))
        self.assertTrue(guard.observe(7 * GIB))

    def test_unknown_memory_is_not_fabricated_as_a_low_sample(self):
        guard = MemoryGuard(8 * GIB)
        for value in (None, -1, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                guard.observe(value)
        self.assertEqual(guard.consecutive_low_samples, 0)

    def test_only_the_exact_whitelisted_instance_can_be_stopped(self):
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
        with self.assertRaises(ValueError):
            stop_study_instance("a6-capacity-v2", runner)
        runner.assert_not_called()
        result = stop_study_instance(INSTANCE, runner)
        self.assertTrue(result["succeeded"])
        self.assertEqual(runner.call_args.args[0], ["lxc", "stop", "local:a6-ollama-study", "--force"])
        self.assertEqual(runner.call_args.kwargs["timeout"], 60)

    def test_failed_stop_is_recorded_without_an_unbounded_retry(self):
        runner = Mock(side_effect=subprocess.TimeoutExpired(["lxc"], 60))
        result = stop_study_instance(INSTANCE, runner)
        self.assertFalse(result["succeeded"])
        self.assertEqual(result["error"], "TimeoutExpired")
        self.assertEqual(runner.call_count, 1)

    def test_checkpoint_replacement_preserves_valid_json_and_leaves_no_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "guard.json"
            atomic_checkpoint(path, {"samples": [], "complete": False})
            atomic_checkpoint(path, {"samples": [{"available": 8 * GIB}], "complete": True})
            self.assertTrue(json.loads(path.read_text())["complete"])
            self.assertEqual(list(Path(directory).iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
