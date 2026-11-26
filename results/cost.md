# Prescient — compute cost

**Bottom line:** one shopping request costs about **0.8 milliseconds of CPU**, searches included.
On a small cloud VM that is roughly **₹0.5 per million requests** (pessimistic case: about ₹3).
There are no API calls, GPUs or model fees: the agent is plain Python on one CPU core.

## 1. What was measured (real numbers)

Reproduce with `python3 tests/measure_cost.py` (it only reads `agent.py`, `dev.jsonl` and the
catalogue; it changes nothing).

- **Requests:** all 300 dev requests, run **5 times** (1,500 timed requests).
- **What one request includes:** `start()`, every `observe(word)` and `finish()`. The searches
  happen inside these calls, through the organisers' counted `tools.search`, so they are included.
- **Timer:** CPU time (`time.process_time`), not wall-clock time. Waiting time and other apps
  do not count.

### Machine

| | |
|---|---|
| Computer | MacBook Air (Mac16,13), Apple M4, 10 cores (4 performance + 6 efficiency), 16 GB |
| OS | macOS 15.6.1 |
| Python | CPython 3.14.6, standard library only, one process, one core |

### Results

| Measure | Value |
|---|---|
| **Mean CPU time per request (incl. searches)** | **0.78 ms** |
| Median per request | 0.73 ms |
| 95th percentile per request | 1.22 ms |
| Slowest single request | 9.1 ms (a one-off spike, probably the OS) |
| Of which: search time per request (mean) | 0.45 ms (≈ 58% of the total) |
| Searches per request | 1.03 |
| Words per request | 13.3 |
| CPU time per word | 0.06 ms |
| Mean per request in each of the 5 runs | 0.80, 0.79, 0.84, 0.73, 0.71 ms |
| One-off start-up (load agent + vocabularies) | 4.8 ms per process, not per request |

The CPU time per word (0.06 ms) is thousands of times shorter than the gap between spoken
words (about 300–500 ms), so the agent is never what slows a shopper down.

## 2. Cost per million requests on a cloud VM (estimate)

> **ASSUMPTIONS — not measured, change them if you know better:**
> - **VM:** AWS EC2 `t4g.small` in Mumbai (ap-south-1): 2 vCPUs (ARM Graviton2), 2 GB RAM, Linux.
> - **Price:** about **US$0.0168 per hour** on-demand ≈ **₹1.43 per hour** at ₹85 = US$1.
>   This is an approximate list price; check the current AWS price before quoting it.
> - **Speed:** one cloud vCPU is **3× slower** than one M4 performance core (central case).
>   Pessimistic case: **10× slower**, and the VM sits idle half the time (50% use).
> - Both vCPUs are used (two Python processes; each request is independent).

| | Central | Pessimistic |
|---|---|---|
| CPU per request on the VM | 0.78 ms × 3 = 2.3 ms | 0.78 ms × 10 = 7.8 ms |
| vCPU-hours per million requests | 0.65 | 2.16 |
| VM-hours per million requests (2 vCPUs) | 0.32 | 2.16 (with 50% use) |
| **Cost per million requests** | **≈ ₹0.46** | **≈ ₹3.1** |
| Requests one VM can handle per second | ≈ 850 | ≈ 130 |

How it is worked out (central case): 1,000,000 requests × 2.3 ms = 2,333 CPU-seconds
= 0.65 vCPU-hours. With 2 vCPUs that is 0.32 VM-hours. 0.32 × ₹1.43 ≈ **₹0.46**.

So even in the pessimistic case, a million shopping requests cost **less than ₹5** in compute.

## 3. What this does not include

- **Speech-to-text.** The voice demo uses the browser's own recogniser (free in Chrome). A
  production voice product would pay a speech-to-text provider, and that would cost far more
  than the agent itself.
- **Web server, network and logging around the agent.** Tiny per request, but not measured here.
- **The real search service.** Here, search runs over the 1,074-product local catalogue. A real
  shop's search backend would have its own cost. Prescient keeps that low with
  **1.03 searches per request**.
- **Whether the timing holds on other machines.** It was measured on one Mac only. The cloud
  numbers are scaled with the assumed slow-down factor, not measured on a VM.
