"""Render every paired native-admission outcome; no fitted or pooled effect."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def render(out):
    from scripts.check_shared_admission import protocol_check, validate_complete_report
    from v2.admission_study import paired_summary
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter

    report = json.loads((ROOT/'analysis/v2-ollama-shared-admission/results.json').read_bytes())
    validate_complete_report(report, protocol_check(ROOT))
    if paired_summary(report['trials']) != report['pairs']:
        raise ValueError('Paired differences disagree with the retained trials')
    plt.rcParams.update({'font.size': 11, 'svg.hashsalt': 'alien6-native-admission'})
    fig, axes = plt.subplots(2, 2, figsize=(11, 8), sharex=True, sharey=True)
    colors = ('#176b87', '#bd5d27', '#6554a4')
    for axis, (workers, load) in zip(axes.flat, ((1, 'near'), (8, 'near'), (1, 'high'), (8, 'high')), strict=True):
        for repetition in range(3):
            selected = {row['mode']: row for row in report['trials']
                        if (row['replicas'], row['profile'], row['repetition']) == (workers, load, repetition)}
            b, g = selected['bypass'], selected['global']
            n = b['measurement_arrivals']
            if n != g['measurement_arrivals']:
                raise ValueError('Paired arrival denominators differ')
            values = [row['timely_correct_arrivals']/n for row in (b, g)]
            axis.plot((0, 1), values, marker='o', linewidth=2, color=colors[repetition],
                      label=f'Trace {repetition} ({n} arrivals)')
        axis.set(title=f'{workers} API worker' + ('s' if workers != 1 else '') + f' / {load} load',
                 xticks=(0, 1), xticklabels=('Bypass', 'Shared cap K=1'), ylim=(0, 1), xlim=(-.15, 1.15))
        axis.yaxis.set_major_formatter(PercentFormatter(1))
        axis.grid(axis='y', alpha=.2)
        axis.spines[['top', 'right']].set_visible(False)
        axis.legend(loc='upper right', fontsize=8, framealpha=.9)
    fig.suptitle('Does a shared native-generation cap preserve timely answers?', fontsize=16)
    fig.supylabel('Correct within 10 s / all measurement arrivals')
    fig.text(.5, .015, 'Each line is one paired trace. Refusals and expirations remain in the denominator.\n'
             'Three trace repetitions per condition; exploratory results on the retained CPU/Ollama workload.',
             ha='center', fontsize=9)
    fig.tight_layout(rect=(.02, .07, 1, .94))
    out.mkdir(parents=True, exist_ok=True)
    for suffix in ('svg', 'png'):
        options = {'metadata': {'Date': None}} if suffix == 'svg' else {'dpi': 160}
        fig.savefig(out/('shared-admission-pairs.'+suffix), **options)
    plt.close(fig)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT/'analysis/v2-ollama-shared-admission')
    render(parser.parse_args().out)
