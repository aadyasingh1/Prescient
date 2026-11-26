# Prescient — design details (v4)

The one-page summary is `docs/ARCHITECTURE.md`. This file holds the detail that was moved out of it.

## 1. Anticipate: predict from every word
- **Understand the word.** Hand-built vocabularies turn words into values: colours ("kaala" →
  black, "navy blue" → navy), categories ("floaters" → sandals, "joggers" → track pants),
  brands (one typo allowed), sizes ("XL", "size nine", "32 waist") and prices in English or
  Hindi ("forty five hundred", "0.8k", "dhai hazaar"). Brand names and sizes come from
  `catalogue.csv`, which is read for vocabulary only.
- **Filter state, never too tight.** There are five slots: category, colour, size, brand and
  max price. A price applies only once its number is finished ("under two…" is not ₹200). An
  unclear word keeps every option open ("shoes" = running shoes *or* sneakers). So if exactly
  one product still fits, **it must be the target**, unless the shopper changes their mind.
- **Search once.** When the category (or brand) is known, Prescient makes one counted
  `tools.search`, then filters those results locally on every word (1.03 searches/request).

## 2. Stabilize: wait until the request has settled
The only danger is a correction ("black, no wait, white", "sorry, I meant XS"). Prescient
does not commit while:
- a number is still being spoken ("forty five…"), or a size or price phrase is still open;
- a correction cue has been heard ("no", "sorry", "actually", "I mean", "matlab", "galti se"…);
- no **neutral word** has come after the last value. Words that lead into the next value
  ("in", "by"), finish the current one ("rupees", "waist") or are polite closers ("thanks",
  "please", "ji") are **not** neutral, because corrections often hide just behind them.

## 3. Commit: act only when it is safe
Confidence = (1 ÷ products left) × (1 − chance a correction is still coming). Prescient
commits at **≥ 0.80**. The correction chances were measured on dev by `tune.py`. One
exception: a category or price word with no punctuation after it may commit at once (never
followed by a correction on dev, 0 of 84). At the end, if nothing fits, it loosens the most
doubtful filter (price, then size…) and tries reading unknown words as small typos of a colour or category before it answers.

**Fetch-limit guard (v4).** If a search returns exactly `CONFIG["fetch_k"]` (200) products,
the list may be cut short, so "one product left" is not trusted: that request never commits
early (confidence 0), and at the end Prescient asks `search` for the best match with every
filter it heard. It never fires on today's catalogue (largest pool 174), so v4's results are
identical to v3's on dev, stress, blind and holdout.

**Rules kept:** standard library only, no API calls, products only through the counted
`tools.search`, and `dev.jsonl` is never read at run time.

## Where the holdout fails
Stress and blind are **not independent**: we looked at their failures to make v2 and v3. The
holdout is the honest estimate for new wording. Its weak spots are (1) corrections that
come *after* the request sounded finished: all 13 wrong commits, and the right product was
still the final answer every time; and (2) misspelled colours ("blie", "gey"): 4 of its 5
wrong answers. Full report: `results/holdout_report.md`.

## What each part is worth
`compare.py` → `results/ablation.md`, one part removed per row (measured on v3; v4 gives
identical results).

| Variant | Dev score / wrong | Holdout score / wrong |
|---|---|---|
| Eager (no safety gate) | 43.0 / 21 | 33.1 / 32 |
| − neutral-word rule (any word counts) | 45.1 / 5 | 33.6 / 25 |
| − closing-word rule | 46.1 / 0 | 34.4 / 22 |
| − instant category/price commits | 44.4 / 0 | 36.5 / 10 |
| Safe mode (no instant commits, 2 neutral words) | 41.7 / 0 | 37.6 / 5 |
| **Prescient v4 (full)** | **45.9 / 0** | **37.6 / 13** |

Speed is about 0.05 ms per word, searches included (MacBook Air M4, Python 3.14).

## Files
`agent.py` (the agent; settings in `CONFIG`) · `tune.py`
(correction chances, cross-validation) · `compare.py` (ablation) · `demo_app.py` (live
word-by-word demo) · `tests/` (stress, blind, frozen holdout).
