# Ablation study: Prescient's commit rule

Made by `python3 compare.py`. `agent.py` was not edited: each row changes one setting in memory for one run, then puts it back. Scored by the organisers' `evaluate.run()` / `evaluate.summarise()`.

## Setup
- Machine: MacBook Air (Mac16,13), Apple M4, 16 GB RAM, macOS 15.6.1
- Python 3.14.6, standard library only
- Latency: timed by `evaluate.run()` with `time.perf_counter()` around **each `observe()` call, including any search made inside it**. Mean and p95 are over all words. One run, so treat them as rough sizes.
- Requests: dev = 300 (`dev.jsonl`), holdout = 250 (`tests/holdout.jsonl`, SHA-256 checked unchanged)
- Best possible earliness (oracle): dev 16.3%, holdout 11.3%
- The holdout is used for reporting only. No setting was chosen by looking at it.

## dev (300 requests)

| Strategy | Score | Accuracy | Wrong commits | Earliness % | Searches/req | Mean ms/word | p95 ms/word |
|---|---:|---:|---:|---:|---:|---:|---:|
| Baseline (wait for full request) | 20.0 | 0.527 | 0 | 0.0 | 1.53 | 0.000 | 0.000 |
| Eager (no safety gate) | 43.0 | 1.000 | 21 | 16.7 | 1.03 | 0.057 | 0.431 |
| Prescient − neutral-word rule (naive 1-word delay) | 45.1 | 1.000 | 5 | 11.4 | 1.03 | 0.052 | 0.427 |
| Prescient − instant category/price commits | 44.4 | 1.000 | 0 | 7.3 | 1.03 | 0.052 | 0.425 |
| Prescient − closing-word rule | 46.1 | 1.000 | 0 | 10.3 | 1.03 | 0.052 | 0.427 |
| Prescient safe mode | 41.7 | 1.000 | 0 | 3.0 | 1.03 | 0.052 | 0.426 |
| Prescient v3 (full) | 45.9 | 1.000 | 0 | 10.0 | 1.03 | 0.052 | 0.424 |

## holdout (250 requests)

| Strategy | Score | Accuracy | Wrong commits | Earliness % | Searches/req | Mean ms/word | p95 ms/word |
|---|---:|---:|---:|---:|---:|---:|---:|
| Baseline (wait for full request) | 21.2 | 0.548 | 0 | 0.0 | 1.36 | 0.000 | 0.000 |
| Eager (no safety gate) | 33.1 | 0.980 | 32 | 11.4 | 1.08 | 0.047 | 0.422 |
| Prescient − neutral-word rule (naive 1-word delay) | 33.6 | 0.980 | 25 | 7.6 | 1.08 | 0.049 | 0.422 |
| Prescient − instant category/price commits | 36.5 | 0.980 | 10 | 2.5 | 1.08 | 0.047 | 0.421 |
| Prescient − closing-word rule | 34.4 | 0.980 | 22 | 6.9 | 1.08 | 0.047 | 0.420 |
| Prescient safe mode | 37.6 | 0.980 | 5 | 1.0 | 1.08 | 0.047 | 0.420 |
| Prescient v3 (full) | 37.6 | 0.980 | 13 | 6.2 | 1.08 | 0.047 | 0.423 |

## What each row removes
- **Baseline**: the organisers' agent. It waits for the last word.
- **Eager**: commits the moment one product is left. No neutral words needed, any value may be committed on its own word, all hazards 0. Mid-sentence guards (an unfinished number, an open "no wait") still apply.
- **− neutral-word rule**: any next word counts as moving on (a naive 1-word delay), so lead-ins, value tails and closers no longer hold the commit back.
- **− instant category/price commits**: a category or price word must also be followed by a neutral word.
- **− closing-word rule**: "thanks", "please", "ji" and the like count as neutral.
- **Safe mode**: no instant commits, and 2 neutral words needed instead of 1.
- **v3 (full)**: `agent.py` exactly as it is.

Chart: `results/speed_safety.svg` (earliness on x, wrong commits on y).

The tables above were measured on v3. v4 and v5 give identical numbers on dev and holdout.

## v5 experiments (2026-11-27)

Measured in memory (one `CONFIG` change per run, `agent.py` not edited) with `evaluate.run()` and `tune.py`.

| Variant | Dev score | Dev accuracy | Dev wrong | Dev earliness % | 5-fold CV | Decision |
|---|---:|---:|---:|---:|---|---|
| v4 | 45.9 | 1.000 | 0 | 10.0 | 45.9 ± 1.0 | — |
| v5 = v4 + broader vocabulary | 45.9 | 1.000 | 0 | 10.0 | 45.9 ± 1.0 | **kept** |
| v5 + instant commit on brand word (`instant_slots` += brand) | 46.4 | 1.000 | **1** | 11.3 | 46.4 ± 0.4 (fold 0: 1 wrong) | **rejected** |

- The brand experiment gains +0.5 cross-validated, but dev-0292 commits P0506 on word 7 (target P0505).
  The project rule is 0 wrong commits on dev regardless of score, so it was rejected. It was not run on
  either holdout.
- v5 on the other sets: stress 44.0 / 1.000 / 0, blind 39.0 / 0.940 / 0, holdout 37.6 / 0.980 / 13 (all
  identical to v4). Holdout 2 (200, new, run once): v5 **45.3 / 0.995 / 3**; frozen v4 45.1 / 0.990 / 3.
