"""Measured HTTP services for the dedicated Kubernetes experiment, not production software."""
from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
import resource
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

ROLE = os.environ.get("ROLE", "provider")
POD = os.environ.get("HOSTNAME", "local")
PROVIDER = os.environ.get("PROVIDER_URL", "http://provider:8080")
SLOTS = threading.BoundedSemaphore(4)
PENDING = threading.BoundedSemaphore(36)
LOCK = threading.Lock()
COUNTERS = {"queued": 0, "active": 0, "completed": 0, "quota_refused": 0, "calls": 0,
            "tokens_input": 0, "tokens_output": 0, "token_usage_unknown": 0, "declared_tokens_accepted": 0}
CONFIG = {"tokens_per_second": 1_000_000_000, "requests_per_second": 1000,
          "mode": "emulator", "ollama_url": "http://host.docker.internal:11435"}
WINDOWS = {"admission": [-1, 0, 0], "provider": [-1, 0, 0]}
CURRENT_ADDRESSES = []
LAST_SAMPLE = {}
HEARTBEAT = None
RUN_LOCK = threading.Lock()


class Heartbeat:
    """Independent 20ms heartbeat; overlapping snapshots are diagnostics, not trials."""
    def __init__(self, interval=0.02):
        self.interval = interval
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.samples = deque(maxlen=3000)
        self.events = deque(maxlen=128)
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        previous_mono, previous_wall = time.monotonic(), time.time()
        while not self.stopped.wait(self.interval):
            mono, wall = time.monotonic(), time.time()
            sample = {'monotonic': mono,
                      'lag_seconds': max(0, mono - previous_mono - self.interval),
                      'wall_drift_seconds': (wall - previous_wall) - (mono - previous_mono)}
            with self.lock:
                self.samples.append(sample)
                if sample['lag_seconds'] > .005 or abs(sample['wall_drift_seconds']) > .005:
                    self.events.append(sample)
            previous_mono, previous_wall = mono, wall

    def start(self):
        self.thread.start()

    def snapshot(self):
        now = time.monotonic()
        with self.lock:
            recent = [s for s in self.samples if s['monotonic'] >= now - 1]
            return {'interval_seconds': self.interval, 'window_seconds': 1,
                    'recent_sample_count': len(recent),
                    'max_recent_lag_seconds': max((s['lag_seconds'] for s in recent), default=None),
                    'max_recent_abs_wall_drift_seconds': max((abs(s['wall_drift_seconds']) for s in recent), default=None),
                    'retained_events_over_5ms': list(self.events)}

    def stop(self):
        self.stopped.set()
        self.thread.join(timeout=2)
        return self.snapshot()


def http(url, payload=None, timeout=5):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "Connection": "close"})
    try:
        with urllib.request.urlopen(req, timeout=max(0.05, timeout)) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def debit(bucket, tokens, now=None):
    now = time.monotonic() if now is None else now
    window = math.floor(now)
    with LOCK:
        record = WINDOWS[bucket]
        if record[0] != window:
            record[:] = [window, 0, 0]
        allowed = record[1] + 1 <= CONFIG["requests_per_second"] and record[2] + tokens <= CONFIG["tokens_per_second"]
        if allowed:
            record[1] += 1
            record[2] += tokens
        return allowed, max(0.001, window + 1 - now)


def stats():
    usage = resource.getrusage(resource.RUSAGE_SELF)
    result = {"pod": POD, "time": time.time(), "monotonic": time.monotonic(),
              "clock": "CLOCK_MONOTONIC; single-node experiment",
              "heartbeat": HEARTBEAT.snapshot() if HEARTBEAT else None, "cpu_seconds": usage.ru_utime + usage.ru_stime,
              "peak_rss_bytes": usage.ru_maxrss * (1 if sys.platform == "darwin" else 1024),
              "code_sha256": hashlib.sha256(open(__file__, "rb").read()).hexdigest(), "python": sys.version, **COUNTERS}
    try:
        result['time_namespace_inode'] = os.stat('/proc/self/ns/time').st_ino
    except OSError:
        result['time_namespace_inode'] = None
    for path, key in [("/sys/fs/cgroup/memory.current", "memory_current_bytes")]:
        try:
            with open(path) as file:
                result[key] = int(file.read())
        except OSError:
            pass
    try:
        with open("/sys/fs/cgroup/cpu.stat") as file:
            values = dict(line.split() for line in file)
        result["container_cpu_seconds"] = int(values["usage_usec"]) / 1_000_000
        result["cpu_stat"] = {key: int(value) for key, value in values.items()}
    except OSError:
        pass
    for name in ['cpu.max', 'cpu.pressure', 'memory.max', 'memory.pressure', 'memory.events']:
        try:
            with open('/sys/fs/cgroup/' + name) as file:
                result[name.replace('.', '_')] = file.read().strip()
        except OSError as error:
            result[name.replace('.', '_')] = {'unavailable': type(error).__name__}
    return result


def infer(payload):
    permitted, delay = debit("provider", payload["tokens"])
    with LOCK:
        COUNTERS["calls"] += 1
        if not permitted:
            COUNTERS["quota_refused"] += 1
        else:
            COUNTERS["declared_tokens_accepted"] += payload["tokens"]
    if not permitted:
        return 429, {"reason": "upstream_quota", "retry_after": delay}
    with LOCK:
        COUNTERS["active"] += 1
    try:
        return complete_inference(payload)
    except Exception:
        if CONFIG["mode"] == "ollama":
            with LOCK:
                COUNTERS["token_usage_unknown"] += 1
        raise
    finally:
        with LOCK:
            COUNTERS["active"] -= 1


def complete_inference(payload):
    if CONFIG["mode"] == "ollama":
        # A checkable, controlled task, not a production-quality summarisation claim.
        expected = "ok" if payload["task_status"] == 200 else "error"
        prompt = ("Classify the HTTP status in the last line. Status 200 is ok; all other "
                  "statuses are error. Return JSON with the label only.\n" + payload["prompt_text"] +
                  f"\nHTTP status: {payload['task_status']}")
        code, raw = http(CONFIG["ollama_url"] + "/api/generate", {
            "model": "qwen3.6:latest", "prompt": prompt, "stream": False, "think": False,
            "format": {"type": "object", "properties": {"label": {"type": "string", "enum": ["ok", "error"]}},
                       "required": ["label"]},
            "options": {"temperature": 0, "seed": 20261112, "num_ctx": 8192, "num_predict": 32, "num_thread": 8},
            "keep_alive": "30m",
        }, timeout=max(0.1, payload["deadline_monotonic"] - time.monotonic()))
        try:
            correct = json.loads(raw["response"])["label"] == expected
        except (KeyError, ValueError):
            correct = False
        with LOCK:
            COUNTERS["tokens_input"] += raw.get("prompt_eval_count", 0)
            COUNTERS["tokens_output"] += raw.get("eval_count", 0)
            COUNTERS["token_usage_unknown"] += int("eval_count" not in raw)
        return code, {"id": payload["id"], "correct": correct, "inference": raw}
    time.sleep(payload["service_seconds"])
    return 200, {"id": payload["id"], "correct": True}


def work(payload):
    received = time.monotonic()
    deadline = payload["deadline_monotonic"]
    if not PENDING.acquire(blocking=False):
        return 429, {"id": payload["id"], "pod": POD, "reason": "queue_full", "attempts": 0}
    acquired = False
    with LOCK:
        COUNTERS["queued"] += 1
    try:
        acquired = SLOTS.acquire(timeout=max(0, deadline - time.monotonic()))
        queued = time.monotonic() - received
        with LOCK:
            COUNTERS["queued"] -= 1
            if acquired:
                COUNTERS["active"] += 1
        if not acquired:
            return 504, {"id": payload["id"], "pod": POD, "reason": "queue_deadline", "queue_seconds": queued, "attempts": 0}
        admission_wait = 0.0
        attempts = 0
        max_attempts = payload.get("max_attempts", 1)
        while attempts < max_attempts and time.monotonic() < deadline:
            if payload["policy"] == "global":
                start = time.monotonic()
                while time.monotonic() < deadline:
                    status, permission = http(PROVIDER + "/admit", {"tokens": payload["tokens"]}, timeout=deadline - time.monotonic())
                    if status == 200:
                        break
                    pause = min(permission["retry_after"] + 0.002, max(0, deadline - time.monotonic()))
                    time.sleep(pause)
                admission_wait += time.monotonic() - start
                if time.monotonic() >= deadline:
                    break
            attempts += 1
            status, result = http(PROVIDER + "/infer", payload, timeout=deadline - time.monotonic())
            if status != 429 or attempts >= max_attempts:
                late = time.monotonic() > deadline
                with LOCK:
                    COUNTERS["completed"] += int(status == 200 and not late and result.get("correct", False))
                return 504 if late else status, {**result, "pod": POD, "queue_seconds": queued,
                                                "admission_seconds": admission_wait, "attempts": attempts,
                                                "reason": "response_deadline" if late else result.get("reason")}
            jitter = int(hashlib.sha256(payload["id"].encode()).hexdigest()[:4], 16) / 65535 * 0.02
            time.sleep(min(result["retry_after"] + jitter, max(0, deadline - time.monotonic())))
        return 504, {"id": payload["id"], "pod": POD, "reason": "admission_deadline",
                     "queue_seconds": queued, "admission_seconds": admission_wait, "attempts": attempts}
    finally:
        with LOCK:
            if acquired:
                COUNTERS["active"] -= 1
        if acquired:
            SLOTS.release()
        PENDING.release()


def invoke_one(item, start, wall_start, config):
    scheduled = start + item["offset"]
    launched = time.monotonic()
    payload = {**item, "deadline_monotonic": scheduled + config["deadline_seconds"], "policy": config["policy"],
               "max_attempts": config.get("max_attempts", 1)}
    record = {"id": item["id"], "scheduled_at": wall_start + item["offset"], "launched_at": time.time(),
              "scheduled_monotonic": scheduled, "launched_monotonic": launched,
              "scheduling_lag_seconds": max(0, launched - scheduled), "tokens_declared": item["tokens"]}
    if launched >= payload["deadline_monotonic"]:
        return {**record, "status": 0, "reason": "generator_deadline", "ended_at": time.time(),
                "ended_monotonic": launched, "latency_seconds": launched - scheduled, "correct": False}
    try:
        status, result = http("http://workers:8080/work", payload, timeout=config["deadline_seconds"] + 1)
        record.update({"status": status, **result})
    except Exception as error:
        record.update({"status": 0, "reason": type(error).__name__, "correct": False})
    record["ended_monotonic"] = time.monotonic()
    record["ended_at"] = time.time()
    record["latency_seconds"] = record["ended_monotonic"] - scheduled
    return record


def run_load(config):
    global CURRENT_ADDRESSES, LAST_SAMPLE
    CURRENT_ADDRESSES = list(config["worker_addresses"])
    idle_deadline = time.monotonic() + 60
    while True:
        _, upstream = http(PROVIDER + "/stats")
        if upstream.get("active", 0) == 0:
            break
        if time.monotonic() > idle_deadline:
            raise RuntimeError("Previous upstream requests have not drained")
        time.sleep(0.25)
    http(PROVIDER + "/configure", config["provider"])
    start = time.monotonic() + 0.2
    wall_start = time.time() + 0.2
    samples = []
    stopped = threading.Event()

    def sample():
        global LAST_SAMPLE
        while not stopped.is_set():
            snapshot = {"time": time.time(), "monotonic": time.monotonic(), "controller": stats(), "workers": []}
            for address in list(CURRENT_ADDRESSES):
                try:
                    _, data = http(address + "/stats", timeout=0.5)
                    snapshot["workers"].append(data)
                except Exception:
                    pass
            try:
                _, snapshot["upstream"] = http(PROVIDER + "/stats", timeout=0.5)
            except Exception:
                snapshot["upstream"] = {}
            snapshot["sampling_seconds"] = time.monotonic() - snapshot["monotonic"]
            samples.append(snapshot)
            LAST_SAMPLE = snapshot
            stopped.wait(0.5)

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    futures = []
    with ThreadPoolExecutor(max_workers=512) as executor:
        for item in config["trace"]:
            time.sleep(max(0, start + item["offset"] - time.monotonic()))
            futures.append(executor.submit(invoke_one, item, start, wall_start, config))
        results = [future.result() for future in as_completed(futures)]
    stopped.set()
    sampler.join(timeout=3)
    _, provider = http(PROVIDER + "/stats")
    return {"started_at": wall_start, "started_monotonic": start, "finished_at": time.time(),
            "finished_monotonic": time.monotonic(), "requests": sorted(results, key=lambda r: r["scheduled_monotonic"]),
            "resource_samples": samples, "provider": provider, "controller": stats()}


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 1024


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send(self, status, value):
        body = json.dumps(value).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        self.send(200, LAST_SAMPLE if self.path == "/live" else stats() if self.path == "/stats" else {"ready": True, "role": ROLE})

    def do_POST(self):
        global CURRENT_ADDRESSES
        try:
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if ROLE == "provider" and self.path == "/configure":
                with LOCK:
                    CONFIG.update(payload)
                    WINDOWS["provider"] = [-1, 0, 0]
                    WINDOWS["admission"] = [-1, 0, 0]
                    for key in COUNTERS:
                        COUNTERS[key] = 0
                self.send(200, {"configured": True})
            elif ROLE == "provider" and self.path == "/admit":
                allowed, delay = debit("admission", payload["tokens"])
                self.send(200 if allowed else 429, {"allowed": allowed, "retry_after": delay})
            elif ROLE == "provider" and self.path == "/infer":
                status, result = infer(payload)
                self.send(status, result)
            elif ROLE == "worker" and self.path == "/work":
                status, result = work(payload)
                self.send(status, result)
            elif ROLE == "controller" and self.path == "/run":
                if not RUN_LOCK.acquire(blocking=False):
                    self.send(409, {"reason": "trial_already_running"})
                else:
                    try:
                        self.send(200, run_load(payload))
                    finally:
                        RUN_LOCK.release()
            elif ROLE == "controller" and self.path == "/workers":
                CURRENT_ADDRESSES = list(payload["addresses"])
                self.send(200, {"updated": True})
            else:
                self.send(404, {"reason": "unknown_endpoint"})
        except Exception as error:
            self.send(500, {"reason": type(error).__name__})


if __name__ == "__main__":
    HEARTBEAT = Heartbeat()
    HEARTBEAT.start()
    Server(("0.0.0.0", 8080), Handler).serve_forever()
