"""Export an English scientific figure from the audited descriptive trial summaries."""
import importlib.metadata
import json
from pathlib import Path
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'analysis/2026-11-12-calibration'
result = json.loads((OUT / 'results.json').read_text())
cohorts = result['cohorts']
fig, axes = plt.subplots(1, len(cohorts), figsize=(4.0 * len(cohorts), 3.8), squeeze=False, layout='constrained')
maximum = max(160, max(r['dispatch_lag_p95_ms'] for c in cohorts for r in c['trials']) * 1.12)
for ax, cohort in zip(axes[0], cohorts):
    groups = [(workers, rate) for workers in [1, 8] for rate in [180, 360]]
    for index, group in enumerate(groups):
        trials = [r for r in cohort['trials'] if (r['workers'], r['offered_rate']) == group]
        for repetition, trial in enumerate(trials):
            value = trial['dispatch_lag_p95_ms']
            ax.scatter(value, index + (repetition - 1) * .15, s=35, color='#087d58' if trial['valid'] else '#ae4426', zorder=3)
    ax.axvline(50, color='#941f25', linestyle='--', linewidth=1.5)
    ax.set_xlim(0, maximum)
    ax.set_ylim(3.5, -.5)
    ax.set_yticks(range(4), [f'{w} worker(s), {r}/s' for w, r in groups])
    ax.set_xlabel('P95 dispatch delay per trial (ms)')
    ax.set_title(f"{cohort['id']}: {cohort['valid_trials']}/12 valid", fontsize=12)
    ax.grid(axis='x', color='#dce5df')
fig.suptitle('Successful responses do not establish on-time load delivery', fontsize=14)
fig.text(.5, -.025, 'Each point is one complete trial; dashed line = preregistered 50 ms gate. Cohorts are not pooled.', ha='center', fontsize=10)
for extension in ['svg', 'png']:
    path = OUT / ('qualification-dispatch-lag-en.' + extension)
    fig.savefig(path, dpi=180, bbox_inches='tight', facecolor='white')
    if extension == 'svg':
        path.write_text('\n'.join(line.rstrip() for line in path.read_text().splitlines()) + '\n')
(OUT / 'figure-environment.json').write_text(json.dumps({'python': sys.version, 'matplotlib': importlib.metadata.version('matplotlib'), 'scope': 'Figure rendering only; these packages were not added to the measured HTTP-service image.'}, indent=2) + '\n')
