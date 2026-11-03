"""Reconcile admission and native lifetimes offline; no model is needed for replay."""
from collections import Counter
import copy
import math
import os
import re
import socket
import time

from v2.model import expected_native_calls, validate_proxy_state, model_request_sha256, pilot_item
from v2.run import json_http

SCHEMA = 'alien6-admission-completion-v1'


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def integer(value):
    return type(value) is int and value >= 0


def indexed(rows, total, label):
    require(isinstance(rows, list), label + ' records are unavailable')
    require(all(isinstance(row, dict) and type(row.get('sequence')) is int
                and 0 < row['sequence'] <= total for row in rows), label + ' sequence is invalid')
    result = {row['sequence']: row for row in rows}
    require(len(result) == len(rows), label + ' sequence is duplicated')
    return result


def known(row):
    return (row.get('status') == 200 and row.get('done') is True
            and row.get('error') is None and row.get('uncertain') is False
            and isinstance(row.get('response'), dict) and row['response'].get('done') is True)


def settings(config, study):
    shared = config.get('shared_admission', {})
    require(shared.get('study_source_id') == study['source_id'] and shared.get('git_base') == study['git_base'],
            'Configured study identity differs')
    mode, capacity = shared.get('mode'), shared.get('capacity')
    require(mode in ('bypass', 'global') and type(capacity) is int and 1 <= capacity <= 4,
            'Configured admission arm is invalid')
    return mode, capacity, config['trial_id']


def state_pair(admission, native, config, study, base, before=None):
    """Validate one atomic proxy snapshot, including partial and uncertain work."""
    mode, capacity, trial_id = settings(config, study)
    require(isinstance(admission, dict), 'Admission state is unavailable')
    require(admission.get('native_state') == native, 'Atomic native snapshot differs')
    validate_proxy_state(native, base, before[1] if before else None)
    for name in ('source_id', 'git_base', 'admission_proxy_sha256'):
        require(admission.get(name) == study[name], 'Admission source identity differs: ' + name)
    require(admission.get('schema') == 'alien6-shared-admission-state-v1'
            and admission.get('base_source_id') == base['source_id']
            and admission.get('clock') == 'time.monotonic'
            and admission.get('clock_domain') == native['clock_domain'], 'Admission epoch or schema differs')
    require(admission.get('configured') is True and admission.get('mode') == mode
            and admission.get('capacity') == capacity and admission.get('trial_id') == trial_id,
            'Admission arm or case changed')
    for name in ('started_total', 'finished_total', 'occupied', 'quarantined'):
        require(integer(admission.get(name)), 'Admission counter is invalid: ' + name)
    active = indexed(admission.get('active'), admission['started_total'], 'Active admission')
    records = indexed(admission.get('records'), admission['started_total'], 'Terminal admission')
    require(not active.keys() & records.keys() and set(active) | set(records) == set(range(1, admission['started_total'] + 1)),
            'Admission sequence coverage is incomplete')
    require(len(records) == admission['finished_total']
            and admission['started_total'] - admission['finished_total'] == len(active), 'Admission counters disagree')
    require(admission['occupied'] == len(active) + admission['quarantined'], 'Permit occupancy disagrees')
    require(mode != 'global' or admission['occupied'] <= capacity, 'Global permit cap exceeded')
    nactive = indexed(native.get('active'), native['started_total'], 'Active native')
    nrecords = indexed(native.get('records'), native['started_total'], 'Terminal native')
    require(not nactive.keys() & nrecords.keys() and set(nactive) | set(nrecords) == set(range(1, native['started_total'] + 1)),
            'Native sequence coverage is incomplete')
    require(len(nrecords) == native['finished_total'], 'Native terminal counter differs')
    require(sum(row.get('uncertain') is True for row in nrecords.values()) == native['uncertain_total'],
            'Native uncertainty counter differs')
    for row in [*nactive.values(), *nrecords.values()]:
        require(isinstance(row.get('request_sha256'), str) and re.fullmatch('[0-9a-f]{64}', row['request_sha256'])
                and finite(row.get('started')), 'Native payload identity or time is invalid')
    for row in nrecords.values():
        require(finite(row.get('finished')) and row['started'] <= row['finished']
                and type(row.get('done')) is bool and type(row.get('uncertain')) is bool,
                'Native terminal fields are invalid')
        require((row.get('status') is None or type(row.get('status')) is int and 100 <= row['status'] <= 599)
                and (row.get('error') is None or isinstance(row.get('error'), str) and bool(row['error'])), 'Native terminal status or error is invalid')
        computed = row.get('error') is not None or row.get('status') != 200 or row.get('done') is not True
        require(row['uncertain'] == computed and row['done'] == (isinstance(row.get('response'), dict)
                and row['response'].get('done') is True), 'Native terminal semantics disagree')
    configuration = admission.get('configuration_record')
    require(isinstance(configuration, dict) and finite(configuration.get('at'))
            and configuration.get('mode') == mode and configuration.get('capacity') == capacity
            and configuration.get('trial_id') == trial_id and configuration.get('study_source_id') == study['source_id']
            and integer(configuration.get('native_started_before')) and integer(configuration.get('api_started_before')),
            'Immutable configuration record is invalid')
    if before:
        old_admission, old_native = before
        require(admission['clock_domain'] == old_admission['clock_domain']
                and configuration == old_admission['configuration_record'], 'Configured proxy epoch changed')
        require(all(admission[key] >= old_admission[key] for key in ('started_total', 'finished_total', 'quarantined')),
                'Admission counters moved backwards')
        for current, old, label in ((records, indexed(old_admission['records'], old_admission['started_total'], 'Prior admission'), 'Admission'),
                                    (nrecords, indexed(old_native['records'], old_native['started_total'], 'Prior native'), 'Native')):
            require(all(current.get(seq) == row for seq, row in old.items()), label + ' terminal history changed')
        for previous_rows, current_rows, label in ((old_admission['active'], {**active, **records}, 'Admission'), (old_native['active'], {**nactive, **nrecords}, 'Native')):
            for old in previous_rows:
                current = current_rows.get(old['sequence'])
                require(isinstance(current, dict) and all(current.get(key) == old.get(key) for key in
                    (('sequence', 'request_sha256', 'received', 'occupied_before', 'native_sequence', 'decision', 'trial_id', 'mode', 'capacity', 'clock_domain') if label == 'Admission' else ('sequence', 'request_sha256', 'started'))), label + ' active history changed')
    return active, records, nactive, nrecords


def reconcile(admission, native, before_admission, before_native, config, expected):
    """Replay same-clock permit events and exact API-to-native sequence mapping."""
    mode, capacity = admission['mode'], admission['capacity']
    abase, nbase = before_admission['started_total'], before_native['started_total']
    active = {row['sequence']: row for row in admission['active'] if row['sequence'] > abase}
    records = {row['sequence']: row for row in admission['records'] if row['sequence'] > abase}
    rows = {**active, **records}
    require(len(rows) <= expected['count'], 'Unexpected inference API attempts')
    counts = Counter(row.get('request_sha256') for row in rows.values())
    require(not counts - Counter(expected['request_sha256_counts']), 'API request-payload multiset differs')
    native_rows = {row['sequence']: row for row in [*native['records'], *native['active']] if row['sequence'] > nbase}
    admitted = {}; rejected = 0; events = []; uncertain = 0
    for seq, row in rows.items():
        require(row.get('trial_id') == config['trial_id'] and row.get('mode') == mode
                and type(row.get('capacity')) is int and row.get('capacity') == capacity and row.get('clock_domain') == admission['clock_domain'],
                'Admission attempt belongs to a different arm or epoch')
        require(finite(row.get('received')) and row['received'] >= admission['configuration_record']['at']
                and integer(row.get('occupied_before')) and type(row.get('permit_released')) is bool
                and type(row.get('quarantined')) is bool, 'Admission attempt fields are invalid')
        decision = row.get('decision')
        require(decision in ('admitted', 'rejected'), 'Admission decision is unavailable')
        events.append((row['received'], 0, seq, row))
        if seq in records:
            require(finite(row.get('finished')) and row['received'] <= row['finished']
                    and integer(row.get('occupied_after')), 'Admission terminal time or occupancy is invalid')
        if decision == 'rejected':
            rejected += 1
            require(seq in records and mode == 'global' and row.get('status') == 429
                    and row.get('error') is None and row.get('native_sequence') is None
                    and row['permit_released'] is False and row['quarantined'] is False
                    and row['occupied_before'] >= capacity and row['occupied_after'] == row['occupied_before'],
                    'Rejected API attempt could have generated native work')
            continue
        nseq = row.get('native_sequence')
        require(type(nseq) is int and nseq > nbase and nseq not in admitted, 'Admitted native sequence is missing or duplicated')
        admitted[nseq] = row
        nrow = native_rows.get(nseq)
        require(isinstance(nrow, dict) and nrow.get('request_sha256') == row.get('request_sha256')
                and row['received'] <= nrow['started'], 'Admitted/native payload or ordering differs')
        if seq in active:
            require(nseq in {item['sequence'] for item in native['active']} and row.get('status') is None
                    and row.get('error') is None and not row['permit_released'] and not row['quarantined'],
                    'Active admission/native lifetime differs')
        else:
            require('finished' in nrow and nrow['finished'] <= row['finished']
                    and row.get('status') == nrow.get('status') and row.get('error') == nrow.get('error'),
                    'API terminal does not follow its native terminal')
            success = known(nrow)
            require(row['permit_released'] is success and row['quarantined'] is (not success),
                    'Permit released without successful native completion')
            uncertain += int(not success)
            events.append((row['finished'], 1, seq, row))
    require(set(admitted) == set(native_rows) == set(range(nbase + 1, native['started_total'] + 1)),
            'Native calls are not in bijection with admitted API attempts')
    require(uncertain == admission['quarantined'] - before_admission['quarantined'], 'Quarantined permit count differs')
    occupied = peak = 0
    for _, kind, _, row in sorted(events, key=lambda event: event[:3]):
        if kind == 0:
            require(row['occupied_before'] == occupied, 'Permit acquisition history disagrees')
            if row['decision'] == 'admitted':
                require(mode != 'global' or occupied < capacity, 'Global admission exceeded capacity')
                occupied += 1
        else:
            occupied -= int(row['permit_released'])
            require(row['occupied_after'] == occupied, 'Permit release history disagrees')
        peak = max(peak, occupied)
        require(occupied >= 0 and (mode != 'global' or occupied <= capacity), 'Permit lifetime violated global capacity')
    require(occupied == admission['occupied'], 'Final occupied permits differ from event replay')
    return {'api_attempts': len(rows), 'admitted': len(admitted), 'rejected': rejected,
            'native_terminal': native['finished_total'] - before_native['finished_total'],
            'native_active': len(native['active']), 'native_uncertain': native['uncertain_total'] - before_native['uncertain_total'],
            'api_active': len(active), 'quarantined': admission['quarantined'],
            'expected_api': expected['count'], 'api_complete': counts == Counter(expected['request_sha256_counts']),
            'peak_occupied': peak,
            'native_http_seconds_full_trace': sum(row['finished'] - row['started'] for row in native_rows.values()) if all(known(row) for row in native_rows.values()) else None,
            'admission_held_seconds_full_trace': sum(row['finished'] - row['received'] for row in admitted.values()) if all(row.get('permit_released') is True for row in admitted.values()) else None,
            'duration_scope': 'Sum of same-proxy-clock HTTP/permit lifetimes over all trial calls, including arrival warmup; not CPU, client/native timestamp subtraction or per-client wasted work.'}


def replay_certificate(config, result, certificate, study_identity, base_identity):
    """Return recalculated integrity and completion flags; stored flags are not evidence."""
    verdict = {'coverage_valid': False, 'native_complete': False,
               'execution_issues': [], 'native_issues': [], 'counts': {}}
    try:
        require(isinstance(certificate, dict) and certificate.get('schema') == SCHEMA, 'Completion schema differs')
        expected = expected_native_calls(config, result)
        require(certificate.get('expected_api') == expected, 'Expected API calls are not reproducible')
        began, deadline, finished = (certificate.get(name) for name in ('driver_began', 'driver_deadline', 'driver_finished'))
        require(all(finite(value) for value in (began, deadline, finished)) and began <= finished
                and certificate.get('budget_seconds') == 900 and abs(deadline - began - 900) <= 1e-6,
                'Driver budget or timestamps differ')
        clock = certificate.get('driver_clock', {})
        require(clock.get('clock') == 'time.monotonic' and clock.get('scope') == 'requesting_VM_process'
                and type(clock.get('pid')) is int and clock['pid'] > 0 and isinstance(clock.get('clock_domain'), str)
                and bool(clock['clock_domain']), 'Driver clock domain is unavailable')
        admission, native = certificate.get('pre_admission_state'), certificate.get('pre_native_state')
        state_pair(admission, native, config, study_identity, base_identity)
        require(not admission['active'] and not admission['occupied'] and not admission['quarantined']
                and not native['active'] and native['uncertain_total'] == 0,
                'The trial did not start from a certified idle epoch')
        configuration = admission['configuration_record']
        require(configuration['native_started_before'] == native['started_total']
                and configuration['api_started_before'] == admission['started_total'], 'Configuration baseline differs')
        require(native['started_total'] == admission['started_total'] == 4
                and all(known(row) for row in native['records']), 'Four completed technical warmups are required')
        require(Counter(row['request_sha256'] for row in native['records']) == Counter(model_request_sha256(pilot_item(i)) for i in range(4)), 'Technical warmup payloads differ')
        for row in admission['records']:
            linked = next((item for item in native['records'] if item['sequence'] == row.get('native_sequence')), None)
            require(row.get('trial_id') == 'technical-warmup' and row.get('mode') == 'bypass'
                    and row.get('decision') == 'admitted' and row.get('permit_released') is True
                    and row.get('quarantined') is False and isinstance(linked, dict)
                    and row.get('request_sha256') == linked['request_sha256'], 'Common warmup admission evidence differs')
        before_admission, before_native = admission, native
        polls = certificate.get('polls')
        require(isinstance(polls, list) and bool(polls), 'Completion observations are unavailable')
        previous_at = began
        for poll in polls:
            at = poll.get('driver_at')
            require(finite(at) and previous_at <= at <= min(deadline, finished), 'Driver observations are not within the fixed budget')
            current_a, current_n = poll.get('admission'), poll.get('native')
            state_pair(current_a, current_n, config, study_identity, base_identity, (admission, native))
            reconcile(current_a, current_n, before_admission, before_native, config, expected)
            admission, native, previous_at = current_a, current_n, at
        require(certificate.get('terminal_admission_state') == admission
                and certificate.get('terminal_native_state') == native, 'Terminal archive differs from the final atomic observation')
        counts = reconcile(admission, native, before_admission, before_native, config, expected)
        require(counts['api_complete'], 'API write coverage is incomplete')
        require('collection_error' not in certificate, 'Atomic ledger collection failed')
        verdict['counts'] = counts
        verdict['coverage_valid'] = True
        verdict['native_complete'] = not any(counts[key] for key in ('api_active', 'native_active', 'native_uncertain', 'quarantined'))
        if not verdict['native_complete']:
            verdict['native_issues'].append('native_lifetime_not_certified')
    except (ValueError, KeyError, TypeError, IndexError) as error:
        verdict['execution_issues'].append(str(error))
    return verdict


def collect_certificate(config, result, before_native, before_admission, study_identity, base_identity, deadline, began):
    """Read atomic proxy ledgers until all callbacks finish or the shared budget ends."""
    certificate = {'schema': SCHEMA, 'pre_native_state': copy.deepcopy(before_native),
        'pre_admission_state': copy.deepcopy(before_admission), 'expected_api': None, 'polls': [],
        'terminal_native_state': None, 'terminal_admission_state': None, 'driver_began': began,
        'driver_deadline': deadline, 'budget_seconds': 900,
        'driver_clock': {'clock': 'time.monotonic', 'scope': 'requesting_VM_process', 'pid': os.getpid(),
                         'clock_domain': socket.gethostname() + ':pid:' + str(os.getpid())}}
    try:
        certificate['expected_api'] = expected_native_calls(config, result)
        url = config['provider']['ollama_url'].rstrip('/') + '/study-admission/state?after=0&records=1'
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            admission = json_http(url, timeout=min(5, remaining))
            at = time.monotonic()
            if at > deadline:
                raise TimeoutError('Atomic proxy observation exceeded the shared driver budget')
            native = admission.get('native_state')
            certificate['polls'].append({'driver_at': at, 'admission': admission, 'native': native})
            certificate['terminal_admission_state'], certificate['terminal_native_state'] = admission, native
            state_pair(admission, native, config, study_identity, base_identity)
            if not admission['active'] and not native['active']:
                break
            time.sleep(min(1, max(0, deadline - time.monotonic())))
    except Exception as error:
        certificate['collection_error'] = {'type': type(error).__name__, 'detail': str(error)}
    certificate['driver_finished'] = time.monotonic()
    verdict = replay_certificate(config, result, certificate, study_identity, base_identity)
    if 'collection_error' in certificate:
        verdict['execution_issues'].append('Atomic ledger collection failed: ' + certificate['collection_error']['type'])
        verdict['coverage_valid'] = verdict['native_complete'] = False
    certificate.update(verdict)
    return certificate
