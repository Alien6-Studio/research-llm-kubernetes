# Independently hosted emulator contrasts

Descriptive essential contrasts on one independently hosted Linux VM after a complete passing fast-path block. Registered CPU-envelope, quota-level, admission, burst, autoscaling and real-model follow-ups remain incomplete; this is not the full confirmatory matrix.

The complete Linux fast-path calibration passed 12/12 trials. Every essential contrast below passed the registered integrity and dispatch checks. HTTP rejections, deadline failures and 500 TimeoutErrors remain outcomes, not exclusions.

| Profile | Workers | Useful event completions/s, repetitions 0/1/2 | Useful arrival-cohort requests/s, repetitions 0/1/2 |
| --- | ---: | --- | --- |
| unlimited | 1 | 19.7 / 20.6 / 20.3 | 20.2 / 20.8 / 20.5 |
| unlimited | 8 | 165.5 / 161.5 / 158.9 | 173.8 / 169.6 / 170.1 |
| quota | 1 | 21.0 / 20.2 / 20.4 | 21.0 / 20.5 / 20.3 |
| quota | 8 | 24.8 / 24.3 / 24.1 | 24.8 / 24.3 / 24.2 |

Window completions count useful responses ending at offsets [3,13), including warm-up arrivals. Arrival-cohort results count requests scheduled at [3,13) and completed before their own 2-second deadline, including bounded drain. These measures are reported separately.

The unlimited 8-worker arm exceeds the roughly 24/s quota-limited arm on the same VM. This is evidence for the imposed emulator-quota mechanism in this controlled setup, not a real-LLM capacity claim or completion of the full validity program.

## Limits

- Three seeded repetitions are the experimental units; requests within a trial are correlated.
- HTTP500 TimeoutError outcomes are retained as failures, not dropped. Attempt counts missing from failure records remain unknown.
- Sampled CPU and OOM counters show only observed windows; continuous pod restart counts were not archived, so their absence cannot prove no restart.
- The local Mac v3 block did not qualify. Linux contrasts are not a successful cross-host replication of a capacity effect.
- No real-model inference throughput, semantic quality, GPU, cloud cost or production sizing conclusion follows.
