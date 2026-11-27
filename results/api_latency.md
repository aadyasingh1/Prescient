# api.py latency (measured)

Made by `python3 tests/bench_api.py` on 2026-11-27: all 300 dev requests (3987 words) through `api.py` over HTTP on 127.0.0.1, one keep-alive connection, one call at a time. Round trip per call: HTTP + JSON + the agent's own work (searches included).

Machine: arm64, Darwin 24.6.0, Python 3.14.6.

| Call | Calls | Mean ms | p50 ms | p95 ms | Max ms |
|---|---|---|---|---|---|
| `/session` | 300 | 3.372 | 2.743 | 7.390 | 15.285 |
| `/observe` | 3987 | 0.300 | 0.215 | 0.682 | 3.188 |
| `/finish` | 300 | 0.259 | 0.205 | 0.529 | 3.241 |
| whole request (session + words + finish) | 300 | 7.70 | 6.48 | 16.45 | 25.63 |

All calls together: 4587 calls in 2.31 s (1984 calls/s on one connection).

## The API changes nothing

| | Score | Accuracy | Wrong commits | Earliness | Searches/request |
|---|---|---|---|---|---|
| through api.py | 45.9 | 1.000 | 0 | 10.0% | 1.03 |
| evaluate.run(agent.py) | 45.9 | 1.000 | 0 | 10.0% | 1.03 |

Same final answer, commits and search count on every request: **yes**.
