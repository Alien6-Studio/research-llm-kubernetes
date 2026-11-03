"""Generate the final result report and source ledger from deterministic analysis."""
import argparse
import ast
import gzip
import json
from pathlib import Path
import statistics
import textwrap

from v2.plan import ROOT
from v2.report import (prepare, illustrate_deadline_loss, qualified_real_model_blocks,
                       real_model_figure_name)


def snippet(path, name):
    text=path.read_text();tree=ast.parse(text)
    nodes=[n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name]
    node=nodes[0]
    return textwrap.dedent('\n'.join(text.splitlines()[node.lineno-1:node.end_lineno]))


def actual_model_lines(blocks):
    """Describe only observations from separately qualified registered blocks."""
    lines = ['## Qualified actual-model comparisons', '',
        'These datasets compare direct Ollama calls with 1/2/4/8 Kubernetes application workers at the registered low/near/high loads. Each block has all 45 trials qualified; historical excluded blocks remain excluded. These are exploratory, descriptive observations from n=3 independently seeded trace repetitions per condition; normality and statistical independence are not established by three repetitions. They concern only the fixed classification task, model, transport and hardware recorded for that dataset. Different datasets are not pooled.', '',
        'The actual-model design uses policy=none and a nonbinding quota. It does not test a quota change or global admission. Its qualification therefore does not establish transfer of the emulator quota/admission effects, nor a general production capacity, semantic-quality benefit or library improvement. We do not infer general causal hardware explanations or superiority of a new pattern from these local comparisons.', '']
    for name, data in blocks:
        lines += ['### ' + name, '',
            f"Source: `analysis/{name}/results.json`, `trials[]`; the underlying complete archive is replayed by `v2.verify`. Three paired repetitions per condition, 20-second arrival cohorts and a 10-second client deadline. These finite-cohort rates are distinct from completion-window throughput.", '',
            '| Offered load | Path | API workers | Useful/s: each trial | Mean useful/s | Mean trial useful fraction |',
            '| --- | --- | ---: | --- | ---: | ---: |']
        for profile in ('low', 'near', 'high'):
            for path, replicas in [('direct-model', 1), *[('workers', n) for n in (1, 2, 4, 8)]]:
                rows = sorted((row for row in data['trials'] if
                    (row['profile'], row['path'], row['replicas']) == (profile, path, replicas)),
                    key=lambda row: row['repetition'])
                values = ' / '.join(f"{row['useful_cohort_per_second']:.2f}" for row in rows)
                label = 'Direct Ollama' if path == 'direct-model' else 'Kubernetes workers'
                workers = '—' if path == 'direct-model' else str(replicas)
                rate = statistics.mean(row['useful_cohort_per_second'] for row in rows)
                fraction = statistics.mean(row['useful_fraction'] for row in rows)
                lines.append(f'| {profile} | {label} | {workers} | {values} | {rate:.2f} | {fraction:.2%} |')
        figure = real_model_figure_name(name, len(blocks))
        lines += ['', f'![Actual-model comparison: {name}](analysis/v2-study/{figure}.png)', '',
            f'Paired direct-versus-worker differences for `{name}` appear in the H4 rows of `analysis/v2-study/results.json`, `contrasts[]`. Intervals are descriptive at three trial-level repetitions; qualification alone does not imply a positive scaling effect.', '']
    return lines


def excluded_model_lines(datasets):
    """Keep additional failed/partial model blocks visible without pooling them."""
    blocks = [(name, data) for name, data in sorted(datasets.items())
              if name != 'v2-local-real-model' and not data['included_for_inference']
              and any(row.get('block') == 'real-model' for row in data.get('trials', []))]
    if not blocks:
        return []
    lines = ['## Additional excluded actual-model blocks', '',
             'Every block below remains excluded as a whole. Passing individual trials from an incomplete or invalid block are not combined with another attempt, used for paired inference or substituted for its missing trials. These records document instrument or collection limits, not model-capacity estimates.', '',
             '| Dataset | Archived trials | Individually passing | Complete block | Qualified block |',
             '| --- | ---: | ---: | --- | --- |']
    for name, data in blocks:
        rows, block = data['trials'], data.get('block', {})
        lines.append(f"| [{name}](analysis/{name}/results.json) | {len(rows)} | {sum(bool(row['valid']) for row in rows)} | {block.get('complete', False)} | {block.get('qualified', False)} |")
    lines += ['', 'Lossless observations and block status remain under the matching `archives/DATASET/` directory. An incomplete block does not establish a unique hardware, model, transport or scheduler cause.', '']
    return lines


def native_baseline_lines():
    """Keep certified observations separate from a qualified causal comparison."""
    name = 'v2-ollama-fra6s004-cpu-continuation'
    path = ROOT / 'analysis' / name / 'results.json'
    if not path.exists():
        return []
    report = json.loads(path.read_bytes())
    if (report.get('schema') != 'alien6-cpu-continuation-report-v1'
            or report.get('observed_trials') != 45 or report.get('continuation_observed') != 28
            or report.get('status_counts') != {'certified_observation': 44, 'native_completion_uncertain': 1}
            or report.get('qualified') is not False or report.get('included_for_inference') is not False):
        raise ValueError('Native baseline observation status differs from its evidence boundary')
    rows = report['trials']
    complete = sum(row['native_completed'] for row in rows if row['native_completion'] == 'certified')
    if complete != 1490 or any(row['observation_status'] != 'certified_observation' for row in rows[17:]):
        raise ValueError('Native completion inventory is incomplete')
    lines = ['## Native inference: observed client outcomes and completion evidence', '',
        'We measured direct Ollama calls and 1/2/4/8 Kubernetes application workers to distinguish an answer that eventually finishes from an answer that reaches its caller in time. The task classifies a supplied HTTP status into a short JSON label; it does not evaluate general reasoning or retrieval quality.', '',
        f'All {report["observed_trials"]} frozen conditions are retained: {report["status_counts"]["certified_observation"]} have complete native-completion certificates and one has uncertain native completion. The certified trials archive {complete:,} complete native response bodies. This is an evidence inventory, not a throughput estimate. All {report["continuation_observed"]} previously unattempted conditions passed their individual integrity and completion checks.', '',
        'The first 17 observations share one inference epoch; that original block remains unqualified. Each of the 28 additional observations starts after a proved process termination/restart and four separate technical warmup responses. The continuation retains 29 reset proofs, including its initial reset, and 112 technical warmup responses. It also passes three refreshed transport controls and all 12 qualification trials. These checks establish the observation apparatus; they do not turn the mixed-regime 45-row table into a qualified causal comparison.', '',
        'Every primary fraction uses all planned measurement arrivals during 335 seconds after a five-second arrival warmup. A useful result is correct HTTP 200 within ten seconds of its scheduled arrival. A native response observed later, even HTTP 200 with `done=true`, never replaces the client outcome. Native completion, resource qualification and client usefulness remain separate fields.', '',
        'The following examples use only the reset-per-trial regime. They illustrate the denominator and the observed variability; the full table retains every condition.', '',
        '| Load / trace repetition | Path | Timely correct / measurement arrivals | Native completion |',
        '| --- | --- | ---: | --- |']
    for profile, repetition, selected in [('low', 0, [('direct-model', 1), *[('workers', n) for n in (1, 2, 4, 8)]]),
                                           ('high', 0, [('workers', n) for n in (1, 2, 4)])]:
        for route, replicas in selected:
            row = next(row for row in rows if (row['profile'], row['repetition'], row['path'], row['replicas']) ==
                       (profile, repetition, route, replicas))
            if row['segment'] != 'continuation_reset_epoch': raise ValueError('Do not mix inference lifecycle regimes')
            label = 'Direct Ollama' if route == 'direct-model' else f'{replicas} application worker' + ('s' if replicas != 1 else '')
            lines.append(f"| {profile} / {repetition} | {label} | {row['timely_correct_arrivals']} / {row['measurement_arrivals']} | {row['native_completion']} |")
    failed = next(row for row in rows if row['observation_status'] == 'native_completion_uncertain')
    lines += ['', f"The retained `{failed['trial_id']}` observation has {failed['native_expected']} expected native calls, {failed['native_completed']} terminal records, {failed['native_uncertain']} uncertain terminal outcomes and {failed['native_active']} active calls at capture. Its recorded client cohort remains visible. It was not rerun, replaced or relabelled as certified.", '',
        'Different arrival seeds and inference lifecycle regimes are not pooled for means, confidence intervals, significance tests or replica-effect claims. In particular, a successful high-load observation in the original shared epoch is not a fair substitute for a missing reset-epoch comparison.', '',
        f'Sources: [all 45 observations and exact raw SHA values](analysis/{name}/results.json), [trial CSV](analysis/{name}/trials.csv), [immutable protocol and selected plans](protocol/ollama-fra6s004-cpu-continuation/manifest.json), and [lossless evidence index](archives/cpu-native-baseline/evidence-index.json). `python scripts/check_cpu_continuation.py` restores and replays the complete evidence offline, including every reset, warmup and native certificate.', '',
        'These observations support examining how client deadlines, queued native work and admission decisions interact. The baseline uses policy=none and does not compare a new admission algorithm. No benefit of an Alien6 library, production SLO, monetary saving or general model-capability improvement is established.', '']
    return lines


def native_admission_lines():
    """Describe every native pair without pooling requests or older baselines."""
    from scripts.check_shared_admission import protocol_check, validate_complete_report
    from v2.admission_study import paired_summary
    name = 'v2-ollama-shared-admission'
    path = ROOT / 'analysis' / name / 'results.json'
    if not path.exists():
        return []
    report = json.loads(path.read_bytes())
    validate_complete_report(report, protocol_check(ROOT))
    if paired_summary(report['trials']) != report['pairs']:
        raise ValueError('Native paired contrasts differ from their trial outcomes')
    rows = {row['case_id']: row for row in report['trials']}
    for row in rows.values():
        n, useful = row['measurement_arrivals'], row['timely_correct_arrivals']
        if (not 0 <= useful <= n or row['timely_correct_fraction'] != useful / n
                or row['timely_correct_per_second'] != useful / 335):
            raise ValueError('Native client metric differs from its complete arrival denominator')
    positive = sum(pair['global_minus_bypass_fraction'] > 0 for pair in report['pairs'])
    negative = sum(pair['global_minus_bypass_fraction'] < 0 for pair in report['pairs'])
    resource_complete = sum(row['resource_qualified'] for row in rows.values())
    lines = ['## What a shared native-generation limit changed', '',
        'We asked whether a common limit could preserve timely answers when application workers call the same CPU-hosted Ollama process. We compared bypass with one shared native-generation permit on identical arrival traces. The complete intervention combines K=1, immediate refusal when occupied, release only after successful native HTTP completion, and quarantine of uncertain completion. Both arms use the same instrument; every arm starts after certified process reset and four common technical warmup responses.', '',
        f'The collection on 15 November 2026 retains all {len(rows)} arms and {len(report["pairs"])} complete pairs. All client cohorts are observable; {resource_complete} arms have complete native-resource certificates. Fresh controls pass in all three network repetitions and all twelve generator trials. These checks are replayed from retained evidence, without Ollama.', '',
        'The primary outcome is a correct response within ten seconds of its scheduled arrival, divided by every measurement arrival. Refusals, unissued requests and expired callers remain in that denominator. The table shows each of the three trace repetitions per worker/load condition; it does not pool requests or combine these data with the older 45-condition baseline.', '',
        '| API workers / load / trace | Bypass: useful / arrivals | Shared K=1: useful / arrivals | Difference (percentage points) |',
        '| --- | ---: | ---: | ---: |']
    for pair in report['pairs']:
        bypass, capped = rows[pair['bypass_case']], rows[pair['global_case']]
        lines.append(f"| {bypass['replicas']} / {bypass['profile']} / {bypass['repetition']} | {bypass['timely_correct_arrivals']} / {bypass['measurement_arrivals']} | {capped['timely_correct_arrivals']} / {capped['measurement_arrivals']} | {100 * pair['global_minus_bypass_fraction']:+.2f} |")
    lines += ['', '![All twelve paired native-admission outcomes](analysis/v2-ollama-shared-admission/shared-admission-pairs.png)', '',
        'Each line joins the two arms of one trace, using the same arrival denominator. Regenerate the SVG/PNG with `python scripts/plot_shared_admission.py`; rendering requires the optional analysis dependencies.', '',
        f'The shared cap increases timely correct responses in {positive} pair and decreases them in {negative}. This sign tally describes the twelve observed contrasts; it is not a significance test or twelve independent replications of one common effect. At eight workers and high load, the direction changes between traces. At one worker, the shared cap also changes the concurrency budget: it is not a pure coordination-overhead control.', '',
        'K=1 follows the frozen fallback rule applied to the earlier calibration: no tested concurrency met the ten-second empirical p95 criterion. It was never SLO-qualified. These observations do not support treating this fixed cap as a generally beneficial setting for the retained workload, and do not validate a dynamic or deadline-aware scheduler.', '',
        'A client HTTP 429 does not alone identify a shared-cap refusal; worker-local refusals and the separate admission ledger must be distinguished. Native API counts and summed HTTP/permit lifetimes cover the whole trace, including arrival warmup. They are not measurement-arrival counts, CPU time, energy, or per-client wasted work. Any future uncertain native lifetime must remain censored rather than removed to select a favorable resource subset.', '',
        'For a possible Alien6 component, the study supports keeping client usefulness, refusal, native completion and occupied capacity separately observable. It does not establish a production-library benefit. Choosing admission limits from workload and deadlines, or adding cancellation or queueing, remains an untested engineering hypothesis.', '',
        f'Sources: [all 24 arms and 12 paired differences](analysis/{name}/results.json), [trial CSV](analysis/{name}/trials.csv), [frozen protocol](studies/shared-admission/PROTOCOL.md), [selected plans](protocol/shared-admission/manifest.json), and [lossless evidence index](archives/shared-admission/evidence-index.json). Run `python scripts/check_shared_admission.py --mode final` to restore and replay this comparison offline.', '']
    return lines


def build():
    r=prepare();r['deadline_illustration']=illustrate_deadline_loss()
    d=r['datasets'];primary=d['v2-linux-primary']['trials']
    real_blocks=qualified_real_model_blocks(d)
    excluded_lines=excluded_model_lines(d)
    mean=lambda rows,key:statistics.mean(x[key] for x in rows)
    select=lambda profile,policy,n:[x for x in primary if (x['profile'],x['policy'],x['replicas'])==(profile,policy,n)]
    lines=['# Measured capacity, admission and validity results', '',
    'Generated by `python -m v2.publication` from the stored observations and their replayed analyses.', '',
    '## Scope and complete blocks', '',
    'Replicas increase useful outcomes while the emulator has headroom. A shared quota reduces that benefit. The tested atomic global policy accounts correctly for capacity yet can worsen client outcomes. Separately, 45 actual-model conditions are observed with explicit client and native-completion evidence; their lifecycle regimes are reported below without causal pooling. A separate paired native experiment tests one shared concurrency cap; it does not establish transfer of the emulator quota policy.', '',
    '| Dataset | Complete trials | Passing trials | Included for inference |', '| --- | ---: | ---: | --- |']
    for name,x in d.items():
        lines.append(f"| {name} | {len(x['trials'])} | {sum(t['valid'] for t in x['trials'])} | {x['included_for_inference']} |")
    lines += ['', 'A failed technical trial excludes the complete block, not only that trial. The native pilot and remote transport controls are separate from these trial datasets; see the evidence ledger and validity discussion. All raw failures are retained.', '',
    '## Primary emulator outcomes', '',
    'Useful means a correct HTTP200 result before the planned-arrival deadline. Rate is useful arrivals in a 10-second measurement cohort, followed through their deadline, divided by 10. Completion-window throughput counts finishes during the window separately. Four active slots and 32 queue places per worker; deadline 2 s. Declared quota units are not model tokens. Three paired seeded trace repetitions per condition.', '',
    '| Profile | Policy | Replicas | Useful/s: each trial | Mean useful/s | Mean useful fraction |', '| --- | --- | ---: | --- | ---: | ---: |']
    for profile in ['unlimited','quota','bursty']:
        for policy in ['none','global']:
            for n in [1,2,4,8]:
                rows=sorted(select(profile,policy,n),key=lambda x:x['repetition'])
                values=' / '.join(f"{x['useful_cohort_per_second']:.2f}" for x in rows)
                lines.append(f"| {profile} | {policy} | {n} | {values} | {mean(rows,'useful_cohort_per_second'):.2f} | {mean(rows,'useful_fraction'):.2%} |")
    rows=select('unlimited','none',8)
    lines += ['', f"At eight unlimited workers the cohort mean is {mean(rows,'useful_cohort_per_second'):.2f}/s; completion-window mean is {mean(rows,'useful_window_per_second'):.2f}/s. Neither is a measured steady-state production capacity.", '',
    '## Paired comparisons', '',
    'Source: `analysis/v2-study/results.json`, `contrasts[]`. Each row has three paired trial differences. Intervals are descriptive 95% df 2 Student intervals; normality and independence cannot be verified at n = 3. No multiplicity-adjusted confirmatory hypothesis test. A confidence interval spanning zero is not proof of equivalence.', '',
    '| Question / dataset | Left → right | Metric | Mean right−left [descriptive 95% interval] |', '| --- | --- | --- | --- |']
    for c in r['contrasts']:
        x=c['right_minus_left'];lo,hi=x['descriptive_95pct_t_interval']
        cond=lambda value:', '.join(str(k)+'='+str(v) for k,v in value.items())
        lines.append(f"| {c['question']} / {c.get('dataset','v2-linux-primary')} | {cond(c['left'])} → {cond(c['right'])} | {c['metric']} | {x['mean']:+.3f} [{lo:+.3f}, {hi:+.3f}] |")
    lines += ['', 'H1 observes scaling at the finite offered rate, with no established diminishing-return knee beyond eight replicas. H2 is supported here by quota manipulation and provider bypass. Paired CPU controls weaken a CPU-only explanation within that VM without proving equivalence. Different hosts/source identities remain separate, not pooled hardware replications.', '',
    '## Accounting versus useful client outcomes', '',
    '| Eight workers, 40k quota | Useful arrivals / total arrivals | Mean useful/s | Provider consumed | Consumed without timely useful client outcome | Mean per-trial p95 terminal latency |', '| --- | --- | ---: | ---: | ---: | ---: |']
    for policy in ['none','global']:
        rows=select('quota',policy,8)
        lines.append(f"| {policy} | {sum(x['useful'] for x in rows)} / {sum(x['offered'] for x in rows)} | {mean(rows,'useful_cohort_per_second'):.2f} | {sum(x['provider_consumed'] for x in rows)} | {sum(x['consumed_without_timely_client_outcome'] for x in rows)} | {statistics.mean(x['all_terminal_latency_seconds']['p95'] for x in rows):.3f}s |")
    ill=r['deadline_illustration']
    lines += ['', 'Quota conservation does not establish scheduling quality. This global policy waits while holding worker slots and has no deadline-feasibility scheduling. Outcomes do not condemn all coordinated admission policies.', '',
    f"The post hoc median non-tail consumed-but-unsuccessful illustration is `{ill['source']}`, request `{ill['request_id']}` (selected from {ill['candidate_count']} candidates in one trial). It waits {ill['queue_seconds']:.3f}s in the queue, {ill['admission_seconds']:.3f}s for admission, and {ill['provider_http_seconds']:.3f}s on the provider call. The client expires at {ill['client_observed_latency_seconds']:.3f}s despite eventual provider HTTP {ill['provider_status']} with a correct task result. This single trace is not a prevalence estimate; separate stage quantiles must never be added to invent a typical request.", '',
    '## Controls, real-model exclusions and unmeasured work', '',
    'All condition-level outcomes, resources, failures and planning sensitivities are in [the generated tables](analysis/v2-study/tables.md), [trial CSV](analysis/v2-study/trials.csv), and [full analysis](analysis/v2-study/results.json). Provider-only quota changes and independent CPU contrasts retain their own datasets and three-repetition uncertainty. Per-trial resource integrals are role observations, not billing or energy estimates.', '',
    'The historical local Qwen pilot has 52 actual classifications (4 warmup,48 measured); its local load plan uses the observed 4.689/s closed-loop rate. That local 45-trial actual-model block remains excluded in full after 15 invalid trials, although 12 zero-service model-path controls passed. Three separate remote transport blocks each fail the unchanged 100 ms p95 gate. Timely emission and HTTP success do not repair latency qualification. These historical blocks provide no qualified real-model scaling comparison and remain separate from the CPU native baseline. The unique local/remote failure cause is not identified.', '',
    'HPA/KEDA protocols/configurations are prepared only. No autoscaling result, monetary saving, production SLO, general GPU efficiency or semantic-quality comparison is measured. The native shared-cap experiment evaluates one research policy; no Alien6 production-library benefit is established.', '',
    '## Reproducible figures', '',
    '![Replica outcomes](analysis/v2-study/replicas-and-admission.png)', '',
    '![Quota counterfactual](analysis/v2-study/quota-counterfactual.png)', '',
    '![CPU counterfactual](analysis/v2-study/cpu-counterfactual.png)', '',
    '![Quota consumption versus timely outcomes](analysis/v2-study/deadline-and-quota-consumption.png)', '',
    '`python scripts/check_cpu_continuation.py --restore-only`, `python -m v2.verify`, `python scripts/check_cpu_continuation.py`, `python -m v2.report --check`, and `python -m v2.publication --check` restore/replay the data and check these numerical reports offline. `python -m v2.report --figures --output /tmp/study-figures` rebuilds SVG/PNG from the same records with the pinned optional rendering environment. [VALIDITY.md](VALIDITY.md) defines the interpretation boundary.']
    position=lines.index('## Primary emulator outcomes')
    lines[position:position]=native_baseline_lines() + native_admission_lines()
    if real_blocks:
        old_scope='Transfer to actual LLM inference is **not demonstrated**.'
        new_scope=('Qualified actual-model direct-versus-worker comparisons are reported separately below. '
                   'They do not test transfer of the emulator quota/admission effects.')
        lines=[line.replace(old_scope,new_scope)
               .replace('No qualified real-model scaling comparison follows.',
                        'These historical blocks provide no qualified real-model scaling comparison; separately qualified blocks are listed below.')
               .replace('The unique local/remote failure cause is not identified.',
                        'The unique cause of those historical local/remote failures is not identified.')
               for line in lines]
        position=lines.index('## Reproducible figures')
        lines[position:position]=actual_model_lines(real_blocks)
    if excluded_lines:
        lines=[line.replace('| Dataset | Complete trials | Passing trials |',
                            '| Dataset | Archived trials | Passing trials |') for line in lines]
        position=lines.index('## Reproducible figures')
        lines[position:position]=excluded_lines
    pilot=json.loads(gzip.decompress((ROOT/'archives/v2-model-pilot/pilot.json.gz').read_bytes()))
    pilot_observations=[row for setting in pilot['results'] for row in setting['observations']]
    real=d['v2-local-real-model']['trials']
    qualification=d['v2-local-model-qualification']['trials']
    real_count=len(real); invalid_count=sum(not row['valid'] for row in real)
    passing_count=real_count-invalid_count
    pilot_rate=max(setting['completion_rate'] for setting in pilot['results'])
    results='\n'.join(lines)+'\n'
    results=results.replace('52 actual classifications (4 warmup,48 measured)',
        f"{len(pilot['warmup'])+len(pilot_observations)} actual classifications ({len(pilot['warmup'])} warmup, {len(pilot_observations)} measured)")
    results=results.replace('45-trial actual-model block',f'{real_count}-trial actual-model block').replace('after 15 invalid trials',f'after {invalid_count} invalid trials')
    results=results.replace('12 zero-service model-path controls passed',f"{sum(row['valid'] for row in qualification)} zero-service model-path controls passed")
    results=results.replace('4.689/s',f'{pilot_rate:.3f}/s')
    ledger=['# Article evidence ledger', '',
    'Source material for later writing; no article is written here. The scientific question is when scaling clients yields timely useful tasks, and whether shared capacity accounting is enough to deliver them. The architectural boundary is between the client deadline, worker queue and provider-owned ledger.', '',
    '## Apparatus and hypotheses', '',
    'Read [PROTOCOL.md](PROTOCOL.md) for H1 replicas/headroom, H2 quota counterfactuals, H3 admission, H4 real-model transfer. The collected primary emulator matrix has 72 trials; quota 12, provider-only 12, CPU 48 are separate controls, with their complete qualification blocks. Three trace repetitions are the experimental units. Emulator workload: 180/s regular Poisson or alternating 180/18/s bursts, 3 s warmup, 10 s cohort, 2 s deadline, 4 active slots +32 queue places/worker. The actual-model baseline has its own 335-second arrival cohorts, ten-second deadline, actual generation records and separate lifecycle regimes.', '',
    '## Citable numerical findings', '',
    'Every number in [RESULTS.md](RESULTS.md) is generated from the files below. A row in `analysis/v2-study/trials.csv` is one trial, never a request-level replication. The individual archive for a trial is located by `trial_id` in that dataset’s archive manifest; `source_relative_path` is the original collection path, not a download URL.', '',
    '| Claim / source | Metric and denominator | Comparison and limits |', '| --- | --- | --- |']
    for profile in ['unlimited','quota']:
        values=[mean(select(profile,'none',n),'useful_cohort_per_second') for n in [1,2,4,8]]
        ledger.append(f"| Replica curve: {' / '.join(f'{v:.2f}' for v in values)}/s. `analysis/v2-linux-primary/results.json`, trials filtered profile={profile},policy=none | `useful_cohort_per_second`; useful/10s per trial; n = 3 paired repetitions, same arrival denominator retained | 1/2/4/8 workers; finite offered load, emulator only. All per-trial values and uncertainty are in `RESULTS.md` / `contrasts[]` |")
    rows=select('quota','global',8);used=sum(x['provider_consumed'] for x in rows);late=sum(x['consumed_without_timely_client_outcome'] for x in rows)
    ledger.append(f"| Correct accounting yet lost utility: {late}/{used} consumed tasks have no timely useful client outcome ({late/used:.2%}). `analysis/v2-linux-primary/results.json` | Sum `consumed_without_timely_client_outcome` / sum `provider_consumed` across 3 quota/global/8 trials; this is a consumed-work denominator, not all arrivals | Compare quota/none/8 using the same fields and paired trace seeds; 4 held slots, 32-place queue, no deadline-feasibility scheduling |")
    ledger += [
    '| Quota movement: `analysis/v2-linux-provider-control/results.json` and `analysis/v2-linux-quota-sensitivity/results.json` | `useful_cohort_per_second`, useful/10s per trial; n = 3 per condition | Direct provider 20k/40k/80k versus unlimited; global-worker 20k/40k/80k. Emulator units, not LLM tokens; do not pool paths |',
    '| CPU explanation: `analysis/v2-independent-cpu-sensitivity/results.json`; combined `contrasts[]` with question=`CPU sensitivity` | Paired 4→8 CPU differences at fixed profile/policy/replicas; n = 3 per contrast | Real 8-vCPU VM; fresh randomized CPU arms. Do not equate a nonsignificant difference with equivalence or pool with GitHub hardware |',
    f"| Deadline mechanism: `{ill['source']}` / `{ill['request_id']}` | Queue {ill['queue_seconds']:.3f}s, admission {ill['admission_seconds']:.3f}s, provider HTTP {ill['provider_http_seconds']:.3f}s; one selected request out of {ill['candidate_count']} non-tail consumed-unsuccessful candidates | Post hoc median worker-duration illustration in one trial, not a prevalence estimate; client outcome on generator clock, stages on worker clock |", '',
    'Every other citable comparison is in `analysis/v2-study/results.json`, `contrasts[]`: exact left/right filters, metric, three paired differences, mean, df 2 interval and prospective planning sensitivity. `RESULTS.md` prints all of them. Claim only the condition in that row. Request outcomes, stage times, status counts and resources are in dataset `trials[]`; raw records are under the matching `archives/v2-*/` directory. `v2.verify` replays those from the archives, not from handwritten summaries.', '',
    '## Historical excluded actual-model instruments', '',
    '| Data | Metric / denominator | Allowed interpretation |', '| --- | --- | --- |',
    '| `analysis/v2-local-real-model/results.json`, `archives/v2-local-real-model/` |30/45 trials pass individual integrity gates; whole 45 excluded | Cannot select passing trials to claim real-model scaling. All generation bodies/token counts remain inspectable |',
    '| `archives/v2-model-pilot/`, `protocol/v2-model/plan.json` |4 warmup +48 measured classifications; closed-loop 16 per concurrency 1/2/4; best observed 4.689/s | Load selection only; no qualified Kubernetes replica comparison |',
    '| `archives/v2-remote-network/`, `archives/v2-remote-compressed-network/`, `archives/v2-remote-fixture-network/` |Three headroom traces per block; p95 latency ≤100 ms required in every repetition; all 3 blocks fail as a whole | Transport instrument unqualified despite timely emission/HTTP success. Metadata/fixture controls are not inference throughput; unique cause remains unidentified |',
    '| `studies/autoscaling/PROTOCOL.md` and manifests |No completed HPA/KEDA measurement denominator | Prepared only |', '',
    '## Code actually used', '',
    'The exact measured source is preserved as inert bytes in `instruments/manifest.json`. Select the recorded source ID from a raw manifest. The following ledger conversion comes from the primary measured instrument; it is not hypothetical library code.', '', '```python']
    manifest=json.loads((ROOT/'instruments/manifest.json').read_text())
    files=manifest['sources']['b6d035ae6b53fdd67de08a4b3e6f1501ccda7016']['files']
    ledger += [snippet(ROOT/files['v2/quota.py']['path'],'consume'),'```', '',
    '`v2/service.py::invoke` in that same captured source records planned-arrival deadlines; `v2/analyze.py::summarize` recomputes timely useful results from archived client/provider/worker observations. Current code adds server-owned destination validation; no throughput observation is retroactively assigned to that security change.', '',
    '## Figures, exact sources and commands', '',
    '| Standalone SVG/PNG under `analysis/v2-study/` | Source in `analysis/v2-study/results.json` |', '| --- | --- |',
    '| `replicas-and-admission` | `datasets.v2-linux-primary.trials`, profile/policy/replicas/useful_cohort_per_second; show all 3 repetitions |',
    '| `quota-counterfactual` | provider-only/quota-sensitivity datasets, condition quotas; n = 3 per arm |',
    '| `cpu-counterfactual` | paired CPU sensitivity contrasts, df 2 intervals; no equivalence claim |',
    '| `deadline-and-quota-consumption` | primary quota/8 none/global; provider_consumed,useful,late consumption plus rule-selected illustration |', '',
    '```sh', 'python scripts/check_cpu_continuation.py --restore-only', 'python -m unittest discover -p "test_*.py"', 'python scripts/verify_evidence.py', 'python -m v2.verify', 'python scripts/check_cpu_continuation.py', 'python -m v2.report --check', 'python -m v2.publication --check', 'python -m pip install -r requirements-analysis.txt', 'python -m v2.report --figures --output /tmp/study-figures', '```', '',
    'No Kubernetes or model server is needed for these calculations. Collection commands and environment gates are in `README.md` / `PROTOCOL.md`; new collection must use a fresh directory and complete qualification.', '',
    '## Editorial permissions and engineering hypothesis', '',
    '**Established within this emulator apparatus:** headroom permits useful replica scaling; changing a shared quota moves the limitation; an atomic provider ledger alone does not guarantee useful scheduling. **Observed in the native baseline:** 45 retained client cohorts, 44 complete native certificates and one preserved uncertain observation; the 28 reset-epoch observations are individually certified. **Exploratory:** numerical effect sizes and 3-repetition intervals in the qualified emulator apparatus; CPU sensitivity and finite resource-integral comparisons. **Not demonstrated:** transfer of quota/admission effects to real LLM inference; pooled replica effects across lifecycle regimes; steady-state production sizing; HPA/KEDA benefit; ROI, energy saving or HA.', '',
    'The observations motivate a deadline-aware admission boundary that coordinates provider budget, queue capacity and client deadlines. A reusable Alien6 component is a future engineering hypothesis. The current negative policy comparison does not demonstrate that an unimplemented library improves outcomes. A subsequent comparison would need identical traces/quotas/deadlines, complete qualification, useful-arrival outcomes and wasted-work accounting; no such new campaign is required to finish this publication.', '',
    '**Disallowed formulations:** extra pods never help; global admission always improves reliability; CPU/network can never limit; emulator quota units equal actual model tokens; eventual HTTP200 equals a useful client result; three trial repetitions prove a universal law; prepared HPA/KEDA manifests are measured results.', '',
    '## Primary method references and licensing', '',
    '- Kubernetes resource management: https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/ — resource requests/limits and CPU throttling.',
    '- Kubernetes HPA: https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/ — controller semantics only; no HPA outcome in this study.',
    '- Ollama API and model controls: https://docs.ollama.com/api and https://docs.ollama.com/faq — raw version/digest/options in the archive override mutable model tags.',
    '- Little, 1961, queueing relation: https://doi.org/10.1287/opre.9.3.383 — a theoretical context, not proof of stationarity or a fit to these short experiments.',
    '- Apache-2.0 original code/docs; upstream images/tools/model weights retain their licenses; no model weights or customer deliverables bundled. See `THIRD_PARTY_NOTICES.md`.', '',
    'References explain methods, not independent corroboration of this experiment’s numbers.']
    ledger_text='\n'.join(ledger)+'\n'
    ledger_text=ledger_text.replace('## Historical excluded actual-model instruments',
        '\n'.join(native_baseline_lines() + native_admission_lines())+'\n## Historical excluded actual-model instruments')
    ledger_text=ledger_text.replace(
        'A subsequent comparison would need identical traces/quotas/deadlines, complete qualification, useful-arrival outcomes and wasted-work accounting; no such new campaign is required to finish this publication.',
        'The separate native comparison retains identical traces, deadlines and resources, full-arrival outcomes and native-completion accounting. Its negative shared-cap results do not validate an untested alternative scheduling policy.')
    ledger_text=ledger_text.replace('30/45 trials',f'{passing_count}/{real_count} trials').replace('whole 45 excluded',f'whole {real_count} excluded')
    ledger_text=ledger_text.replace('4.689/s',f'{pilot_rate:.3f}/s').replace('4 warmup +48 measured',f"{len(pilot['warmup'])} warmup +{len(pilot_observations)} measured")
    if real_blocks:
        ledger_text=ledger_text.replace(
            'Real Qwen comparisons use actual token records and have a separate failed qualification status.',
            'Real Qwen comparisons use actual token records. Historical failed blocks remain excluded; separately qualified blocks are listed below.')
        ledger_text=ledger_text.replace('## Historical excluded actual-model instruments',
            '\n'.join(actual_model_lines(real_blocks))+'\n## Historical failed actual-model instruments')
        ledger_text=ledger_text.replace(
            '**Not demonstrated:** transfer to real LLM capacity;',
            '**Qualified actual-model evidence:** exploratory, descriptive direct-model versus application-worker outcomes for the listed fixed-task blocks (n=3); only the observed local curves. '
            '**Not demonstrated:** transfer of quota/admission effects to real LLM inference;')
        ledger_text=ledger_text.replace(
            'no such new campaign is required to finish this publication.',
            'the qualified actual-model comparison uses policy=none; quota/admission intervention effects require a separate registered comparison.')
    if excluded_lines:
        ledger_text=ledger_text.replace('## Code actually used',
            '\n'.join(excluded_lines)+'\n## Code actually used')
    ledger_text+='\n## Exact remote qualification observations\n\n'
    ledger_text+='Source per block: `archives/BLOCK/control.json.gz`. Each row is one headroom repetition; transport timing, not model inference. The p95 latency gate is100ms and emission-lag gate50ms; every repetition must pass.\n\n'
    ledger_text+='| Block | Repetition | Request denominator | p95 latency(ms) | p95 emission lag(ms) | Passed |\n| --- | ---: | ---: | ---: | ---: | --- |\n'
    for name in ['v2-remote-network','v2-remote-compressed-network','v2-remote-fixture-network']:
        control=json.loads(gzip.decompress((ROOT/'archives'/name/'control.json.gz').read_bytes()))
        for trial in control['trials']:
            ledger_text+=f"| {name} | {trial['repetition']} | {len(trial['requests'])} | {1000*trial['latency_p95_seconds']:.2f} | {1000*trial['emission_lag_p95_seconds']:.2f} | {trial['passed']} |\n"
    return {'RESULTS.md':results,'ARTICLE_EVIDENCE.md':ledger_text}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true');args=parser.parse_args()
    for name,text in build().items():
        if args.check:
            if (ROOT/name).read_text()!=text:raise ValueError('Published evidence differs from calculation: '+name)
        else:(ROOT/name).write_text(text)
    print('Verified publication reports' if args.check else 'Built publication reports')


if __name__=='__main__': main()
