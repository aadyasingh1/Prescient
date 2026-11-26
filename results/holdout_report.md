# Prescient v3 — frozen holdout report

**Run once on 2026-11-26. For honest reporting only. No changes were made to `agent.py` because of these results.**

## Setup checks
- `tests/holdout.jsonl` SHA-256 = `0c8c7bfd59e42f1e4c881bb463278f4b988c447fcc7937dc49fc9bd615632e7c`, which matches the value recorded in CLAUDE.md. `python3 tests/make_holdout.py --check` printed "holdout matches generator".
- `agent.py` is byte-for-byte the same as `agent_frozen_v3.py`.
- Command (run once): `python3 evaluate.py --agent agent.py --data tests/holdout.jsonl --log results/holdout_v3.jsonl`
- Full console output: `results/holdout_v3_stdout.txt`. Per-request log: `results/holdout_v3.jsonl`.

## Headline numbers (250 requests)

| Metric | Holdout (v3) | Dev (v3) for comparison |
|---|---|---|
| **SCORE** | **37.6** (baseline 21.2) | 45.9 |
| Accuracy (final answer) | **0.980** (245 / 250) | 1.000 |
| Wrong commits | **13** (in 13 requests) | 0 |
| Earliness | 6.2% of the request, 0.88 words (best possible 11.3%) | 10.0% |
| Searches / request | 1.08 (wasted 0.08) | 1.03 |
| Latency | 0.05 ms/word, p95 0.43 ms | — |

Filter accuracy at the last word: category 0.96, colour 0.93, size 0.99, brand 1.00, max_price 0.96.

**What costs the points:** the 13 wrong commits cost 13 × 1.0 = 5.2 score points. Without them the score would be 42.8. The 5 wrong final answers cost about 2 more points.

## By-tag table (main tags)

| Tag | n | Accuracy | Wrong commits | Earliness | Score |
|---|---|---|---|---|---|
| broad | 10 | 0.900 | 0 | 7.6% | 40.6 |
| code_mixed | 25 | 1.000 | 0 | 11.8% | 47.1 |
| correction | 90 | 1.000 | **13** | 3.7% | 27.3 |
| correction_1 | 65 | 1.000 | **13** | 3.9% | 22.1 |
| correction_2 | 15 | 1.000 | 0 | 2.5% | 40.7 |
| correction_3 | 10 | 1.000 | 0 | 4.0% | 41.2 |
| correction_after_polite | 20 | 1.000 | **7** | 1.2% | **5.6** |
| correction_at_end | 23 | 1.000 | **6** | 4.8% | **16.2** |
| has_price | 181 | 0.989 | 12 | 7.0% | 37.0 |
| informal | 20 | 1.000 | 0 | 11.2% | 46.7 |
| late_constraint | 25 | 1.000 | 0 | 0.4% | 40.2 |
| order_variation | 30 | 1.000 | 0 | 6.5% | 43.9 |
| price_format | 30 | 1.000 | 0 | 12.3% | 47.4 |
| typo | 20 | **0.800** | 0 | 2.5% | 33.5 |

The 94 `order:*` tags (word order of the request) are in `results/holdout_v3_stdout.txt`. Their only failures are the 18 requests listed below.

## Every failure, grouped

18 requests failed: 13 had a wrong commit (but a correct final answer), and 5 had a wrong final answer.

| Group | Requests | What it cost |
|---|---|---|
| Entity not recognised | 5 | 5 wrong final answers |
| Price misparsed | 0 | — |
| Correction missed | 0 (see note 2 under premature commit) | — |
| Premature commit | 13 | 13 wrong commits (all 13 recovered to the right final answer) |
| No product found | 0 | — |
| Ambiguity | 0 | — |

### Premature commit (13): committed, then the shopper corrected themselves

In all 13, the request **up to the commit point** described exactly one product, and that product was the right one for what had been said. Then the shopper changed one value **after what sounded like the end of the request** (a price, "thank you", "if you have it", "if possible"). The agent committed too soon, then read the correction and moved to the right product.

| ID | Committed after… | …then the shopper said | Value changed |
|---|---|---|---|
| hold-0007 | "less than 2999" | "rupees if you have it actually, make that by Horavo" | brand |
| hold-0018 | "under 700/-" | "oh no, I meant Urbanyx brand" | brand |
| hold-0019 | "under ₹999" | "thanks, wait, I meant size XL please" | size |
| hold-0031 | "by Zentrail thank you," | "wait, I meant from Strydo please" | brand |
| hold-0036 | "30 waist if" | "you have it wait, I meant size 32" | size |
| hold-0063 | "by Loomsy if" | "you have it. wait, I meant size L" | size |
| hold-0094 | "32 waist if" | "possible, oh no, I meant size thirty six" | size |
| hold-0107 | "grey colour thank you," | "oh, actually size thirty thanks" | size |
| hold-0134 | "extra small thank you," | "oh, actually size S please" | size |
| hold-0142 | "at most ₹700" | "thank you, oh, actually in black not the other one" | colour |
| hold-0174 | "₹3700, made" | "by Zentrail if possible actually, make that in medium" | size |
| hold-0195 | "under 1000" | "thanks, oh, actually size L" | size |
| hold-0223 | "thirty two thank you," | "sorry, make it size thirty six" | size |

What these show:
1. **Two triggers.** In 5 of them (0007, 0018, 0019, 0142, 0195) the commit came straight after a price with no punctuation after it, which is the `instant_slots` exception. In the other 8 it came on a word the agent counts as neutral: "if" (in "if you have it"), the "you," of "thank you,", or "made".
2. **Reported filter wrong in 2, final answer still right.** In hold-0094 and hold-0223 the final product is correct, but the reported `size` filter stays at 32 when the shopper corrected it to "thirty six". The size correction in spoken numbers did not reach the reported filters. Neither cost points, because the final product was right. This is why I count no failure as "correction missed".
3. **The holdout has more late corrections than dev.** 90 of 250 holdout requests (36%) are corrections, and 43 of them come after a polite closer or at the very end. Dev has 55 of 300 (18%). This mix makes this group look worse than it may be on the hidden set, but it shows the risk is real.

### Entity not recognised (5): wrong final answer

| ID | Request | Not recognised | Got | Target |
|---|---|---|---|---|
| hold-0057 | "…running shoes size seven in a **blie** colour under rs 3000…" | colour "blie" → blue | P0042 | P0046 |
| hold-0087 | "Size L, I'd like **nvay** Pacevo **huodies**" | colour "nvay" → navy, category "huodies" → hoodies | P0278 | P0669 |
| hold-0136 | "…anything **white-ish**, maybe from Wavlo…" | colour "white-ish" → white | P0996 | P0994 |
| hold-0167 | "Size XL, I need some **gey** Loomsy t-shirts" | colour "gey" → grey | P0305 | P0299 |
| hold-0250 | "Size 28, I need a pair of Urbannyx **ble** jeans…" | colour "ble" → blue (the brand typo "Urbannyx" *was* read correctly) | P0392 | P0400 |

All 5 are misspellings or "-ish" forms the agent has never seen, and 4 of the 5 are colours. Typo accuracy is 0.80 here, against 1.000 on stress and blind. Those sets were used to tune the typo handling, so this is the more honest number.

## Bottom line
- On data it was never tuned on, v3 scores **37.6** (dev 45.9) with **0.980** accuracy and **13 wrong commits**.
- The main weakness is **committing just before a late correction**, right after a price or a phrase that sounds finished ("thank you,", "if you have it"). All 13 wrong commits are in this group.
- The second weakness is **misspelled colour words**: 4 of the 5 wrong answers.
- Price parsing, ambiguity and "no product found" caused no failures.
- Per the project rules, **these results must not be used to tune `agent.py`**. If a later version targets these weaknesses, it should be built and checked on *other* data. This holdout then no longer gives an independent estimate for that version.
