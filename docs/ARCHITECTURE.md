# Prescient — architecture (v4)

**"Your product, before your full stop."** · AgentX Challenge 1, Anticipatory Commerce (Techfest IIT Bombay 2026–27)

> **Prediction is not permission to act.** Prescient predicts the shopper's filters on every
> word, but it shows a product only when the request has *stabilized*. A wrong commit costs
> 1.0 point; being early earns at most 0.6. So every rule leans towards waiting.

```
 word ──► ANTICIPATE ───────────────────► STABILIZE ─────────────────► COMMIT
          read the word, update 5 filters, is the shopper still talking     one product left AND
          search once, narrow locally     about this value, or correcting?  confidence ≥ 0.80
```

**1. Anticipate.** Hand-built vocabularies turn each word into a value for one of five
filters: category, colour, size, brand and max price, in English or Hindi ("kaala" → black,
"dhai hazaar" → ₹2,500). A filter is never tighter than what was said: a price applies only
once its number is finished, and an unclear word keeps every option open ("shoes" = running
shoes *or* sneakers). So if exactly one product still fits, it must be the target, unless the
shopper changes their mind. Once the category or brand is known, Prescient makes one counted
`tools.search` and narrows those results locally on every word (1.03 searches per request).

**2. Stabilize.** The only danger is a correction ("black, no wait, white"). Prescient waits
while a number or size phrase is still open, after a correction cue ("sorry", "actually",
"matlab"), and until a **neutral word** follows the last value. Lead-ins ("in", "by"), value
endings ("rupees") and polite closers ("thanks", "ji") do not count as neutral, because
corrections often hide just behind them.

**3. Commit.** Confidence = (1 ÷ products left) × (1 − chance a correction is still coming),
with the chances measured on dev by `tune.py`. Prescient commits at **≥ 0.80**. If nothing
fits at the end, it loosens the most doubtful filter and tries reading unknown words as typos.

## Results (v4)

| Set | Requests | What it is | Score | Accuracy | Wrong commits | Earliness (best possible) |
|---|---|---|---|---|---|---|
| **Dev (official)** | 300 | Organisers' set, scored by `evaluate.py` | **45.9** (baseline 20.0) | 1.000 | 0 | 10.0 % (16.3 %) |
| Cross-validation | 300 | 5-fold on dev: settings chosen on 4 folds, scored on the 5th | 45.9 ± 1.0 | — | 0 on every fold | — |
| Stress *(used for fixes)* | 150 | Our own wording: synonyms, typos, price formats | 44.0 | 1.000 | 0 | 6.6 % (11.3 %) |
| Blind *(used for fixes)* | 100 | Our own set; v3 fixes were made from its failures | 39.0 | 0.940 | 0 | 2.5 % (8.4 %) |
| **Holdout (frozen, untouched)** | 250 | Made from the catalogue only, never used for fixes | **37.6** (baseline 21.2) | 0.980 | 13 | 6.2 % (11.3 %) |

The holdout is the honest estimate for new wording. All 13 of its wrong commits are
corrections that came after the request sounded finished; the final answer was still right
every time.

**Stabilizing pays off on new wording.** Removing the closing-word rule raises dev slightly
(46.1, still 0 wrong commits), but on the holdout it causes 22 wrong commits instead of 13
(score 34.4 instead of 37.6). A rule that looks useless on dev prevents 9 wrong commits on
wording we never saw.

**Scaling.** One search fetches up to 200 products and today's largest pool is 174, so
"one product left" is proof; with a bigger catalogue, a fetch-limit guard (v4) treats a full
200-product list as possibly cut short, never commits early on it, and finishes with a normal
filtered search, so a larger catalogue costs earliness, not safety.

Standard library only, no API calls, products only through the counted `tools.search`.
Full design detail, the ablation table and the file list: `docs/DETAILS.md`.
