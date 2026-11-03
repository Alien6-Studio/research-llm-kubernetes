# Generated condition tables

All rates count arrivals during the measurement window followed through their own deadline. Invalid blocks are retained and excluded as a whole.

Three repetitions per condition; mean and descriptive 95% df2 Student interval. Normality and independence are not established. Full latency populations, status counts and resources are in each analysis JSON.

## v2-independent-calibration-4

Included for capacity inference: **True**.

| Profile | Path | Policy | Replicas | CPU | Quota | Useful/s: mean [interval] | Useful fraction: mean |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| fastpath | workers | none | 1 | 4 | 1000000000 | 267.92 unavailable | 1.0000 |
| fastpath | workers | none | 8 | 4 | 1000000000 | 267.92 unavailable | 1.0000 |

## v2-independent-calibration-8

Included for capacity inference: **True**.

| Profile | Path | Policy | Replicas | CPU | Quota | Useful/s: mean [interval] | Useful fraction: mean |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| fastpath | workers | none | 1 | 8 | 1000000000 | 267.90 unavailable | 1.0000 |
| fastpath | workers | none | 8 | 8 | 1000000000 | 267.92 unavailable | 1.0000 |

## v2-independent-cpu-sensitivity

Included for capacity inference: **True**.

| Profile | Path | Policy | Replicas | CPU | Quota | Useful/s: mean [interval] | Useful fraction: mean |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| quota | workers | global | 1 | 4 | 40000 | 16.90 [12.59, 21.21] | 0.0962 |
| quota | workers | global | 1 | 8 | 40000 | 15.43 [8.45, 22.41] | 0.0880 |
| quota | workers | global | 8 | 4 | 40000 | 10.20 [8.03, 12.37] | 0.0580 |
| quota | workers | global | 8 | 8 | 40000 | 10.03 [7.16, 12.90] | 0.0569 |
| quota | workers | none | 1 | 4 | 40000 | 17.70 [10.59, 24.81] | 0.1003 |
| quota | workers | none | 1 | 8 | 40000 | 19.17 [14.79, 23.54] | 0.1089 |
| quota | workers | none | 8 | 4 | 40000 | 25.13 [22.40, 27.87] | 0.1429 |
| quota | workers | none | 8 | 8 | 40000 | 25.73 [24.61, 26.85] | 0.1463 |
| unlimited | workers | global | 1 | 4 | 1000000000 | 14.30 [4.28, 24.32] | 0.0813 |
| unlimited | workers | global | 1 | 8 | 1000000000 | 15.90 [9.18, 22.62] | 0.0904 |
| unlimited | workers | global | 8 | 4 | 1000000000 | 165.57 [164.77, 166.37] | 0.9413 |
| unlimited | workers | global | 8 | 8 | 1000000000 | 165.00 [160.90, 169.10] | 0.9380 |
| unlimited | workers | none | 1 | 4 | 1000000000 | 19.13 [17.48, 20.79] | 0.1088 |
| unlimited | workers | none | 1 | 8 | 1000000000 | 17.83 [10.75, 24.91] | 0.1016 |
| unlimited | workers | none | 8 | 4 | 1000000000 | 167.50 [159.10, 175.90] | 0.9525 |
| unlimited | workers | none | 8 | 8 | 1000000000 | 168.47 [163.47, 173.46] | 0.9577 |

## v2-linux-calibration-4

Included for capacity inference: **True**.

| Profile | Path | Policy | Replicas | CPU | Quota | Useful/s: mean [interval] | Useful fraction: mean |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| fastpath | workers | none | 1 | 4 | 1000000000 | 267.92 unavailable | 1.0000 |
| fastpath | workers | none | 8 | 4 | 1000000000 | 267.92 unavailable | 1.0000 |

## v2-linux-primary

Included for capacity inference: **True**.

| Profile | Path | Policy | Replicas | CPU | Quota | Useful/s: mean [interval] | Useful fraction: mean |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| bursty | workers | global | 1 | 4 | 40000 | 19.37 [8.44, 30.29] | 0.1917 |
| bursty | workers | global | 2 | 4 | 40000 | 18.37 [15.40, 21.33] | 0.1827 |
| bursty | workers | global | 4 | 4 | 40000 | 18.07 [14.68, 21.46] | 0.1794 |
| bursty | workers | global | 8 | 4 | 40000 | 17.77 [16.27, 19.26] | 0.1763 |
| bursty | workers | none | 1 | 4 | 40000 | 19.13 [10.41, 27.85] | 0.1895 |
| bursty | workers | none | 2 | 4 | 40000 | 23.60 [20.80, 26.40] | 0.2341 |
| bursty | workers | none | 4 | 4 | 40000 | 24.27 [21.47, 27.06] | 0.2407 |
| bursty | workers | none | 8 | 4 | 40000 | 24.57 [22.88, 26.26] | 0.2439 |
| quota | workers | global | 1 | 4 | 40000 | 17.67 [9.26, 26.08] | 0.1001 |
| quota | workers | global | 2 | 4 | 40000 | 14.70 [12.22, 17.18] | 0.0837 |
| quota | workers | global | 4 | 4 | 40000 | 12.97 [8.97, 16.96] | 0.0736 |
| quota | workers | global | 8 | 4 | 40000 | 9.17 [3.99, 14.34] | 0.0522 |
| quota | workers | none | 1 | 4 | 40000 | 19.60 [16.58, 22.62] | 0.1115 |
| quota | workers | none | 2 | 4 | 40000 | 25.20 [22.83, 27.57] | 0.1433 |
| quota | workers | none | 4 | 4 | 40000 | 25.10 [24.20, 26.00] | 0.1427 |
| quota | workers | none | 8 | 4 | 40000 | 25.47 [23.97, 26.96] | 0.1448 |
| unlimited | workers | global | 1 | 4 | 1000000000 | 18.60 [14.25, 22.95] | 0.1056 |
| unlimited | workers | global | 2 | 4 | 1000000000 | 39.60 [38.70, 40.50] | 0.2252 |
| unlimited | workers | global | 4 | 4 | 1000000000 | 74.07 [65.06, 83.07] | 0.4212 |
| unlimited | workers | global | 8 | 4 | 1000000000 | 169.13 [160.07, 178.20] | 0.9613 |
| unlimited | workers | none | 1 | 4 | 1000000000 | 18.87 [15.78, 21.95] | 0.1072 |
| unlimited | workers | none | 2 | 4 | 1000000000 | 39.63 [32.67, 46.60] | 0.2255 |
| unlimited | workers | none | 4 | 4 | 1000000000 | 76.27 [66.40, 86.13] | 0.4339 |
| unlimited | workers | none | 8 | 4 | 1000000000 | 170.73 [159.84, 181.62] | 0.9704 |

## v2-linux-provider-control

Included for capacity inference: **True**.

| Profile | Path | Policy | Replicas | CPU | Quota | Useful/s: mean [interval] | Useful fraction: mean |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| unlimited | direct-provider | none | 1 | 4 | 20000 | 13.17 [12.54, 13.79] | 0.0749 |
| unlimited | direct-provider | none | 1 | 4 | 40000 | 25.73 [24.37, 27.10] | 0.1464 |
| unlimited | direct-provider | none | 1 | 4 | 80000 | 49.80 [48.56, 51.04] | 0.2831 |
| unlimited | direct-provider | none | 1 | 4 | 1000000000 | 175.97 [164.63, 187.31] | 1.0000 |

## v2-linux-quota-sensitivity

Included for capacity inference: **True**.

| Profile | Path | Policy | Replicas | CPU | Quota | Useful/s: mean [interval] | Useful fraction: mean |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| quota | workers | global | 1 | 4 | 20000 | 7.43 [6.81, 8.06] | 0.0422 |
| quota | workers | global | 1 | 4 | 80000 | 17.50 [8.96, 26.04] | 0.0997 |
| quota | workers | global | 8 | 4 | 20000 | 4.27 [3.64, 4.89] | 0.0243 |
| quota | workers | global | 8 | 4 | 80000 | 21.50 [20.64, 22.36] | 0.1222 |

## v2-local-calibration-4

Included for capacity inference: **False**.

| Profile | Path | Policy | Replicas | CPU | Quota | Useful/s: mean [interval] | Useful fraction: mean |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| fastpath | workers | none | 1 | 4 | 1000000000 | 261.97 unavailable | 0.9826 |
| fastpath | workers | none | 8 | 4 | 1000000000 | 267.92 unavailable | 1.0000 |

## v2-local-calibration-8

Included for capacity inference: **False**.

| Profile | Path | Policy | Replicas | CPU | Quota | Useful/s: mean [interval] | Useful fraction: mean |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| fastpath | workers | none | 1 | 8 | 1000000000 | 263.52 unavailable | 0.9866 |
| fastpath | workers | none | 8 | 8 | 1000000000 | 267.92 unavailable | 1.0000 |

## v2-local-model-qualification

Included for capacity inference: **True**.

| Profile | Path | Policy | Replicas | CPU | Quota | Useful/s: mean [interval] | Useful fraction: mean |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| headroom | workers | none | 1 | 4 | 1000000000 | 14.33 [10.41, 18.25] | 1.0000 |
| headroom | workers | none | 8 | 4 | 1000000000 | 14.33 [10.41, 18.25] | 1.0000 |
| high | workers | none | 1 | 4 | 1000000000 | 7.27 [4.70, 9.83] | 1.0000 |
| high | workers | none | 8 | 4 | 1000000000 | 7.27 [4.70, 9.83] | 1.0000 |

## v2-local-real-model

Included for capacity inference: **False**.

| Profile | Path | Policy | Replicas | CPU | Quota | Useful/s: mean [interval] | Useful fraction: mean |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| high | direct-model | none | 1 | 4 | 1000000000 | 1.27 [-3.32, 5.86] | 0.1907 |
| low | direct-model | none | 1 | 4 | 1000000000 | 1.10 [0.44, 1.76] | 1.0000 |
| near | direct-model | none | 1 | 4 | 1000000000 | 3.83 [2.32, 5.35] | 1.0000 |
| high | workers | none | 1 | 4 | 1000000000 | 1.80 [-1.31, 4.91] | 0.2655 |
| high | workers | none | 2 | 4 | 1000000000 | 1.63 [-1.89, 5.16] | 0.2446 |
| high | workers | none | 4 | 4 | 1000000000 | 1.45 [-3.18, 6.08] | 0.1793 |
| high | workers | none | 8 | 4 | 1000000000 | 1.43 [-3.40, 6.27] | 0.2176 |
| low | workers | none | 1 | 4 | 1000000000 | 1.10 [0.44, 1.76] | 1.0000 |
| low | workers | none | 2 | 4 | 1000000000 | 1.10 [0.44, 1.76] | 1.0000 |
| low | workers | none | 4 | 4 | 1000000000 | 1.10 [0.44, 1.76] | 1.0000 |
| low | workers | none | 8 | 4 | 1000000000 | 1.10 [0.44, 1.76] | 1.0000 |
| near | workers | none | 1 | 4 | 1000000000 | 2.88 [0.18, 5.58] | 0.7889 |
| near | workers | none | 2 | 4 | 1000000000 | 3.20 [1.82, 4.58] | 0.8593 |
| near | workers | none | 4 | 4 | 1000000000 | 2.27 [-0.04, 4.57] | 0.6246 |
| near | workers | none | 8 | 4 | 1000000000 | 3.27 [2.15, 4.39] | 0.8741 |

## v2-model-control-interruption

Included for capacity inference: **False**.

| Profile | Path | Policy | Replicas | CPU | Quota | Useful/s: mean [interval] | Useful fraction: mean |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
