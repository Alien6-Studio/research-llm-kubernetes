"""Generate trial-level contrasts and source-linked figures from archived outcomes."""
import argparse
from collections import defaultdict
import csv
import gzip
import json
from pathlib import Path
import statistics
import tempfile

from v2.analyze import interval, planning
from v2.plan import ROOT


def paired(rows, left, right, metric):
    a = {r['repetition']: r for r in rows if all(r[k] == value for k, value in left.items())}
    b = {r['repetition']: r for r in rows if all(r[k] == value for k, value in right.items())}
    if set(a) != {0, 1, 2} or set(b) != {0, 1, 2}: return None
    difference = interval(b[i][metric] - a[i][metric] for i in range(3))
    effect = .05 if metric == 'useful_fraction' else 5
    return {'left': left, 'right': right, 'metric': metric, 'right_minus_left': difference,
            'confirmation_planning': planning(difference['sd'], effect)}


def prepare():
    datasets = {}
    for path in sorted((ROOT / 'analysis').glob('v2-*/results.json')):
        data = json.loads(path.read_text())
        if 'included_for_inference' in data: datasets[path.parent.name] = data
    contrasts = []
    primary = datasets.get('v2-linux-primary', {})
    if primary.get('included_for_inference'):
        rows = primary['trials']
        for a, b in [(1, 2), (2, 4), (4, 8)]:
            contrasts.append({'question': 'H1', **paired(rows, {'profile': 'unlimited', 'policy': 'none', 'replicas': a},
                {'profile': 'unlimited', 'policy': 'none', 'replicas': b}, 'useful_cohort_per_second')})
        for profile in ['unlimited', 'quota', 'bursty']:
            for workers in [1, 2, 4, 8]:
                for metric in ['useful_cohort_per_second', 'useful_fraction']:
                    contrasts.append({'question': 'H3', **paired(rows, {'profile': profile, 'policy': 'none', 'replicas': workers},
                        {'profile': profile, 'policy': 'global', 'replicas': workers}, metric)})
        for profile in ['unlimited', 'quota']:
            contrasts.append({'question': 'H2', **paired(rows, {'profile': profile, 'policy': 'none', 'replicas': 1},
                {'profile': profile, 'policy': 'none', 'replicas': 8}, 'useful_cohort_per_second')})
    for name, dataset in datasets.items():
        if not dataset['included_for_inference'] or not dataset['trials']: continue
        rows = dataset['trials']
        if rows[0]['block'] == 'cpu-sensitivity':
            for profile in ['unlimited', 'quota']:
                for policy in ['none', 'global']:
                    for workers in [1, 8]:
                        contrasts.append({'question': 'CPU sensitivity', 'dataset': name, **paired(rows,
                            {'profile': profile, 'policy': policy, 'replicas': workers, 'node_cpus': 4},
                            {'profile': profile, 'policy': policy, 'replicas': workers, 'node_cpus': 8}, 'useful_cohort_per_second')})
        if rows[0]['block'] == 'real-model':
            for profile in ['low', 'near', 'high']:
                for workers in [1, 2, 4, 8]:
                    left = {'path': 'direct-model', 'profile': profile}
                    right = {'path': 'workers', 'profile': profile, 'replicas': workers}
                    for metric in ['useful_cohort_per_second', 'useful_fraction']:
                        result = paired(rows, left, right, metric)
                        if result:
                            if metric != 'useful_fraction':
                                result['confirmation_planning'] = planning(result['right_minus_left']['sd'], .2 * 4.689436567862612)
                            contrasts.append({'question': 'H4', 'dataset': name, **result})
    return {'datasets': datasets, 'contrasts': contrasts,
            'uncertainty': 'Three paired workload repetitions; descriptive df2 Student intervals with unverified normality/independence, no multiplicity-adjusted confirmation.'}


def illustrate_deadline_loss():
    root = ROOT / 'archives/v2-linux-primary'
    for path in sorted(root.glob('*.json.gz')):
        d = json.loads(gzip.decompress(path.read_bytes()))
        if 'configuration' not in d: continue
        c, result = d['configuration'], d['result']
        if (c['profile'], c['policy'], c['replicas'], c['repetition']) != ('quota', 'global', 8, 0): continue
        clients = {r['id']: r for r in result['requests']}
        providers = {r['id']: r for r in result['role_records']['provider']['records'] if r['allowed']}
        workers = {r['id']: r for key, archive in result['role_records'].items() if key != 'provider' for r in archive['records']}
        trace = {r['id']: r for r in c['trace']}
        candidates = [key for key, row in clients.items() if 3 <= trace[key]['offset'] < 13
                      and key in providers and row['status'] != 200 and trace[key]['service_seconds'] <= .24]
        candidates.sort(key=lambda key: (workers[key]['worker_finished'] - workers[key]['worker_received'], trace[key]['offset']))
        key = candidates[len(candidates) // 2]
        worker = workers[key]
        return {'selection_rule': 'Post hoc mechanism illustration: median worker duration among consumed-but-unsuccessful measurement arrivals with non-tail service duration in quota/global/8/repetition0; scheduled arrival breaks ties. Not a prevalence estimate.',
                'candidate_count': len(candidates),
                'source': str(path.relative_to(ROOT)), 'request_id': key, 'deadline_seconds': c['deadline_seconds'],
                'client_status': clients[key]['status'], 'client_reason': clients[key].get('reason'),
                'client_observed_latency_seconds': clients[key]['latency_seconds'],
                'queue_seconds': worker['queue_left'] - worker['queue_entered'],
                'admission_seconds': worker['admission_finished'] - worker['admission_started'],
                'provider_http_seconds': worker['provider_call_finished'] - worker['provider_call_started'],
                'declared_units_consumed': providers[key]['units'], 'provider_correct': providers[key]['correct'],
                'provider_status': providers[key]['status'],
                'clock_rule': 'Each stage span uses its own worker clock; client latency uses the generator clock.'}


def figures(report, target):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.size': 11, 'axes.titlesize': 13, 'axes.labelsize': 11,
                         'svg.hashsalt': 'alien6-capacity-study',
                         'axes.spines.top': False, 'axes.spines.right': False, 'svg.fonttype': 'none'})
    colors = {'none': '#173b59', 'global': '#087f5b'}
    source = report['datasets'].get('v2-linux-primary', {})
    if source.get('included_for_inference'):
        fig, axes = plt.subplots(1, 3, figsize=(13, 4.8), sharey=False)
        for axis, profile, title in zip(axes, ['unlimited', 'quota', 'bursty'], ['Provider headroom', 'Shared quota', 'Bursty arrivals + quota']):
            for policy, label in [('none', 'No prior admission'), ('global', 'Global leases')]:
                groups = [[r for r in source['trials'] if r['profile'] == profile and r['policy'] == policy and r['replicas'] == n] for n in [1, 2, 4, 8]]
                means = [statistics.mean(r['useful_cohort_per_second'] for r in group) for group in groups]
                axis.plot([1, 2, 4, 8], means, '-o', color=colors[policy], label=label, lw=2)
                for n, group in zip([1, 2, 4, 8], groups):
                    axis.scatter([n] * 3, [r['useful_cohort_per_second'] for r in group], color=colors[policy], s=22, alpha=.6)
            axis.set(title=title, xlabel='Application replicas', xticks=[1, 2, 4, 8], ylim=(0, None))
            axis.grid(axis='y', alpha=.18)
        axes[0].set_ylabel('Timely cohort completions / arrival second')
        axes[2].legend(frameon=False, fontsize=9)
        fig.suptitle('Measured Kubernetes API outcomes', fontsize=16)
        fig.text(.02, .015, 'Dots: 3 workload repetitions; lines: means. 10 s arrival cohort + bounded drain, not a production steady-state capacity estimate.', fontsize=9)
        fig.tight_layout(rect=(0, .07, 1, .94))
        save(fig, target / 'replicas-and-admission')
        fig, axes = plt.subplots(1, 2, figsize=(10, 4.8))
        for policy, label in [('none', 'No prior\nadmission'), ('global', 'Global\nleases')]:
            rows = [r for r in source['trials'] if r['profile'] == 'quota' and r['policy'] == policy and r['replicas'] == 8]
            x = 0 if policy == 'none' else 1
            used = statistics.mean(r['provider_consumed'] / 10 for r in rows)
            useful = statistics.mean(r['useful_cohort_per_second'] for r in rows)
            axes[0].bar(x, useful, color=colors[policy], width=.55)
            axes[0].bar(x, max(0, used - useful), bottom=useful, color='#c9d5dc', width=.55)
            axes[0].text(x, useful + .35, f'{useful:.1f}', ha='center')
            axes[1].bar(x, statistics.mean(r['all_terminal_latency_seconds']['p95'] for r in rows), color=colors[policy], width=.55)
        axes[0].set(title='Started work and useful results', ylabel='Requests per arrival second')
        axes[0].set_ylim(0, 30)
        axes[1].set(title='Client waiting, including failures', ylabel='Mean trial p95 terminal latency (s)')
        for axis in axes:
            axis.set_xticks([0, 1], ['No prior\nadmission', 'Global\nleases'])
            axis.grid(axis='y', alpha=.18)
        fig.suptitle('A quota grant does not guarantee a timely result', fontsize=15)
        fig.text(.02, .015, '8 replicas, 40,000 declared units/s, 2 s deadline. Color: useful outcomes; grey: started work without a useful client outcome.', fontsize=9)
        fig.tight_layout(rect=(0, .07, 1, .94))
        save(fig, target / 'deadline-and-quota-consumption')
    provider = report['datasets'].get('v2-linux-provider-control', {})
    sensitivity = report['datasets'].get('v2-linux-quota-sensitivity', {})
    if provider.get('included_for_inference') and sensitivity.get('included_for_inference') and source.get('included_for_inference'):
        fig, axis = plt.subplots(figsize=(8.5, 5.2))
        quotas = [20000, 40000, 80000]
        for workers, label, color in [(0, 'Provider-only control', '#9a4d17'), (1, '1 API replica, global leases', '#173b59'), (8, '8 API replicas, global leases', '#087f5b')]:
            groups = []
            for quota in quotas:
                available = provider['trials'] if workers == 0 else source['trials'] + sensitivity['trials']
                groups.append([r for r in available if r['quota'] == quota and
                               (workers == 0 or r['replicas'] == workers and r['policy'] == 'global' and r['profile'] == 'quota')])
            axis.plot([20, 40, 80], [statistics.mean(r['useful_cohort_per_second'] for r in g) for g in groups], '-o', label=label, color=color, lw=2)
            for x, group in zip([20, 40, 80], groups): axis.scatter([x] * len(group), [r['useful_cohort_per_second'] for r in group], color=color, s=25, alpha=.6)
        axis.set(title='Changing the imposed quota changes the outcome', xlabel='Provider quota (thousand declared units / second)',
                 ylabel='Timely cohort completions / arrival second', xticks=[20, 40, 80], ylim=(0, None))
        axis.legend(frameon=False, fontsize=10)
        axis.grid(axis='y', alpha=.18)
        fig.text(.02, .01, 'Measured points only; 3 repetitions per condition. Declared emulator units are not real model tokens.', fontsize=9)
        fig.tight_layout(rect=(0, .04, 1, 1))
        save(fig, target / 'quota-counterfactual')
    cpu = [c for c in report['contrasts'] if c['question'] == 'CPU sensitivity']
    if cpu:
        fig, axis = plt.subplots(figsize=(9, 6))
        for index, contrast in enumerate(cpu):
            effect = contrast['right_minus_left']
            lo, hi = effect['descriptive_95pct_t_interval']
            axis.errorbar(effect['mean'], index, xerr=[[effect['mean'] - lo], [hi - effect['mean']]], fmt='o', color='#087f5b', capsize=4)
        axis.set_yticks(range(len(cpu)), [f"{c['left']['profile']} / {c['left']['policy']} / {c['left']['replicas']} replica(s)" for c in cpu])
        axis.axvline(0, color='#667085', linestyle='--', lw=1)
        axis.set(title='Changing only the node CPU allowance', xlabel='8 CPU minus 4 CPU: timely cohort requests / second')
        axis.grid(axis='x', alpha=.18)
        fig.text(.02, .01, 'Paired means and descriptive 95% df2 Student intervals; n=3 workloads. Normality is not established.', fontsize=9)
        fig.tight_layout(rect=(0, .04, 1, 1))
        save(fig, target / 'cpu-counterfactual')
    for name, data in report['datasets'].items():
        if not data['included_for_inference'] or not data['trials'] or data['trials'][0]['block'] != 'real-model': continue
        fig, axes = plt.subplots(1, 3, figsize=(13, 4.8))
        for axis, profile in zip(axes, ['low', 'near', 'high']):
            groups = [[r for r in data['trials'] if r['profile'] == profile and
                      (r['path'] == 'direct-model' if n == 0 else r['path'] == 'workers' and r['replicas'] == n)] for n in [0, 1, 2, 4, 8]]
            values = [statistics.mean(r['useful_cohort_per_second'] for r in group) for group in groups]
            axis.plot(range(5), values, '-o', color='#087f5b', lw=2)
            for x, group in enumerate(groups): axis.scatter([x] * 3, [r['useful_cohort_per_second'] for r in group], color='#173b59', s=22, alpha=.6)
            axis.set(title=profile.title() + ' offered load', xlabel='Direct reference / API replicas', xticks=range(5), xticklabels=['Direct', '1', '2', '4', '8'], ylim=(0, None))
            axis.grid(axis='y', alpha=.18)
        axes[0].set_ylabel('Timely cohort completions / arrival second')
        fig.suptitle('Actual Qwen inference through the same private transport', fontsize=15)
        fig.text(.02, .015, '20 s arrival cohorts, 10 s deadline, 3 workload repetitions. Fixed warm-cache classification task; no artificial quota.', fontsize=9)
        fig.tight_layout(rect=(0, .07, 1, .94))
        save(fig, target / 'real-model-replicas')


def save(fig, destination):
    fig.savefig(destination.with_suffix('.svg'), bbox_inches='tight', metadata={'Creator': 'Alien6 Research; v2/report.py', 'Date': None})
    svg = destination.with_suffix('.svg')
    svg.write_text('\n'.join(line.rstrip() for line in svg.read_text().splitlines()) + '\n')
    fig.savefig(destination.with_suffix('.png'), dpi=180, bbox_inches='tight')
    import matplotlib.pyplot as plt
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--figures', action='store_true')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.check:
        import subprocess, sys
        with tempfile.TemporaryDirectory() as temporary:
            subprocess.run([sys.executable, '-m', 'v2.report', '--output', temporary], check=True)
            for name in ['results.json', 'trials.csv', 'tables.md']:
                if (Path(temporary)/name).read_bytes() != (ROOT/'analysis/v2-study'/name).read_bytes():
                    raise ValueError('Published report differs from archived outcomes: '+name)
        print('Verified complete numerical report, trial CSV and condition tables')
        return
    target = args.output or ROOT / 'analysis/v2-study'
    target.mkdir(parents=True, exist_ok=True)
    result = prepare()
    result['deadline_illustration'] = illustrate_deadline_loss()
    (target / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    columns = ['dataset', 'trial_id', 'profile', 'policy', 'path', 'replicas', 'node_cpus', 'quota', 'valid',
               'offered', 'emitted', 'provider_consumed', 'useful', 'useful_fraction', 'useful_cohort_per_second', 'useful_window_per_second']
    with (target / 'trials.csv').open('w') as output:
        writer = csv.DictWriter(output, fieldnames=columns, lineterminator='\n')
        writer.writeheader()
        for name, data in result['datasets'].items():
            for row in data['trials']: writer.writerow({key: name if key == 'dataset' else row[key] for key in columns})
    if args.figures:
        figures(result, target)
        import hashlib
        import matplotlib
        import numpy
        import platform
        (target / 'rendering.json').write_text(json.dumps({
            'matplotlib': matplotlib.__version__, 'numpy': numpy.__version__,
            'python': platform.python_version(), 'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'scope': 'Standalone figure rendering only; these libraries are absent from measured service images.'}, indent=2) + '\n')
    lines = ['# Generated condition tables', '',
             'All rates count arrivals during the measurement window followed through their own deadline. Invalid blocks are retained and excluded as a whole.', '',
             'Three repetitions per condition; mean and descriptive 95% df2 Student interval. Normality and independence are not established. Full latency populations, status counts and resources are in each analysis JSON.', '']
    for name, dataset in result['datasets'].items():
        lines.extend(['## ' + name, '', 'Included for capacity inference: **' + str(dataset['included_for_inference']) + '**.', '',
                      '| Profile | Path | Policy | Replicas | CPU | Quota | Useful/s: mean [interval] | Useful fraction: mean |',
                      '| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |'])
        for group in dataset['conditions']:
            c, rate = group['condition'], group['useful_cohort_per_second']
            ci = rate['descriptive_95pct_t_interval']
            uncertainty = f'[{ci[0]:.2f}, {ci[1]:.2f}]' if ci else 'unavailable'
            lines.append(f"| {c['profile']} | {c['path']} | {c['policy']} | {c['replicas']} | {c['node_cpus']} | {c['quota']} | {rate['mean']:.2f} {uncertainty} | {group['useful_fraction']['mean']:.4f} |")
        lines.append('')
    (target / 'tables.md').write_text('\n'.join(lines).rstrip() + '\n')
    print(json.dumps({'datasets': len(result['datasets']), 'contrasts': len(result['contrasts']), 'illustration': result['deadline_illustration']}))


if __name__ == '__main__': main()
