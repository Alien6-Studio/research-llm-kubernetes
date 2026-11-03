"""Apply the prospectively retained headroom gate to a complete calibration block."""
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
rows = []
for path in sorted(root.glob('emulator-fastpath-*.json')):
    trial = json.loads(path.read_text())
    config, result = trial['configuration'], trial['result']
    offsets = {r['id']: r['offset'] for r in config['trace']}
    measured = [r for r in result['requests'] if config['warmup_seconds'] <= offsets[r['id']] < config['warmup_seconds'] + config['measurement_seconds']]
    useful = sum(r['status'] == 200 and r.get('correct', False) and r.get('latency_seconds', float('inf')) <= config['deadline_seconds'] for r in measured)
    rows.append({'file': path.name, 'valid': trial['valid'], 'issues': trial['validation_issues'],
                 'measured': len(measured), 'useful': useful,
                 'useful_fraction': useful / len(measured) if measured else 0})
expected = {(replicas, rate, repetition) for replicas in [1, 8] for rate in [180, 360] for repetition in range(3)}
observed = set()
for row in rows:
    c = json.loads((root / row['file']).read_text())['configuration']
    observed.add((c['replicas'], c['offered_rate'], c['repetition']))
passed = observed == expected and len(rows) == 12 and all(r['valid'] and r['useful_fraction'] >= .99 for r in rows)
report = {'complete': observed == expected and len(rows) == 12, 'passed': passed, 'trials': rows,
          'limits': 'Passing is local harness headroom, not production capacity or proof of hardware independence.'}
(root / 'qualification.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps({'complete': report['complete'], 'passed': passed, 'valid_trials': sum(r['valid'] for r in rows), 'trials': len(rows)}))
sys.exit(0 if passed else 1)
