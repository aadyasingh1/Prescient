# Prescient — finals Q&A (draft answers)

Every number here comes from our own runs (`evaluate.py`, `tune.py`, `compare.py`, holdout
report). Code references are to `agent.py` v3. Short answers first; details after the dash.

---

**1. Why not just wait for the full sentence?**
Because being early is the whole challenge, and we can be early safely. Waiting for the end
gives no earliness points. Prescient commits 1.38 words before the end on average (10.0 % of
the request), which adds 6.0 points on dev (0.6 × 10.0 %), with 0 wrong commits. For a real
shopper, the product appears while they are still talking. (The organisers' baseline scores
20.0, but most of that gap comes from understanding requests better, not from earliness:
our accuracy is 1.000 against its 0.527.)

**2. What makes it WAIT?**
Any of these, read from the agent's state on every word (`_confidence()`):
- a correction word is open: "no", "sorry", "actually", "wait", "I mean", "matlab", "galti"…;
- a number is still being spoken ("forty five…"), or a bare number could still be a size;
- a size or price phrase is open ("extra…", "under…");
- no neutral word since the last value. Lead-ins ("in", "by"), parts of a value ("rupees",
  "waist") and closers ("thanks", "please", "ji") do not count;
- more than one product (or none) still fits.

**3. What makes it COMMIT?**
All three: (a) exactly one product fits the filters; (b) no safety check is open, and either
at least one neutral word has passed (`CONFIG["quiet_words"] = 1`) or the last value was a
category or price word with no punctuation after it (the "instant" rule); (c) confidence ≥ 0.80.

**4. What does "0.80 confidence" mean, and how is it computed?**
Confidence = (1 ÷ products left) × (1 − chance a correction is still coming). The correction
chance depends on how many neutral words have passed since the last value: 6.5 % after 0,
1.2 % after 1, 0.3 % after 2 (`CORRECTION_HAZARD`, measured on dev by `tune.py`). For an
instant category or price word it is 2 % (`INSTANT_HAZARD`); a correction followed such a
word 0 times out of 84 on dev, so 2 % is a cautious figure. With 2 or more products left,
confidence is at most 50 %, so it never commits. Honest note: once the safety checks pass
with one product left, confidence is 95–99.7 %, so in practice the checks decide. In `tune.py`'s
grid, every threshold from 0.5 to 0.95 gives the same dev score (45.9) with `quiet_words` = 1.

**5. What happens if the shopper corrects something after it has committed?**
It keeps listening. The correction cue reopens the wait. When the corrected filters again
leave exactly one product and the checks pass, it commits to the new product. Example:
holdout hold-0142 committed to the brown wallet on word 9, then to the black one on word 16.
If it never re-commits, `finish()` gives the answer from the corrected filters. So the final
answer is still right (13 of 13 times on the holdout), but the scorer still charges −1.0 for
the first, wrong commit.

**6. Why is a neutral-word rule better than a fixed delay?**
Because what matters is *which* word comes next, not *how many*. Corrections hide behind
words like "thanks," or "in", which a fixed delay would count as moving on. Ablation
(`results/ablation.md`): a naive 1-word delay makes 5 wrong commits on dev and 25 on the
holdout; the neutral-word rule makes 0 and 13. A longer fixed wait is safer but slower:
waiting for 2 neutral words scores 41.7 on dev, against 45.9.

**7. Why only about one search per request?**
As soon as the category (or brand) is known, it makes one counted `tools.search` for it
(up to 200 products) and filters those locally on every later word. It searches again only
if the category or brand changes. `CONFIG["max_searches"] = 3` is a hard cap. Result: 1.03
searches per request on dev and 1.08 on the holdout. The baseline uses 1.53.

**8. What happens with 1 million products?**
Honest answer: two things matter. The first is already guarded; the second needs a real database.
- *Pool size.* Today one search fetches up to 200 products (`fetch_k`) and the largest
  pool is 174, so the pool is always complete. With 1M products a pool of 200 would be cut
  off, and "one product left" would no longer prove the target. **The code already guards
  against this (v4):** if a search comes back with exactly 200 results, the agent treats the
  list as possibly cut short. For that request it never commits early (confidence 0), and at
  the end it asks `search` for the best match using all the filters it heard, instead of
  trusting the pool. So a big catalogue costs earliness, not safety. To win the earliness
  back, the next step is to pass all known filters (colour, size, brand, max_price) into the
  pool search so pools stay small.
- *Search speed.* The organisers' `search.py` scans every product: about 0.4 ms for 1,074
  products on our laptop. A straight-line guess for 1M products is hundreds of milliseconds
  per search, so a real deployment would use an indexed product database. The agent's own
  per-word work does not grow with the catalogue: it only looks at the pool.

**9. What is the biggest failure mode?**
A correction that comes **after the request already sounded finished**: "…under ₹999 thanks,
wait, I meant size XL". All 13 holdout wrong commits are this kind. In 5 of them the
commit came straight after a price (the instant rule). In the other 8 it came on a word the
agent counts as neutral: "if" (in "if you have it"), the "you," of "thank you,", or "made".
The correction arrived a few words later. Safe mode (no instant commits, 2 neutral words) cuts them to 5 but
scores 4.2 points less on dev. We did not tune on the holdout, so we kept v3.

**10. Why is the blind set only 94 %?**
All 6 wrong answers are misspellings it could not read: "gery" (grey), "strdo oliv sandles",
"bleu … lomsy shrit", "stryod bleu", "marun hodie", "stryo hodie". It still made **0** wrong
commits: when it does not understand, it waits instead of guessing. Typo matching runs only
at the end and is trusted only if it leaves exactly one product. Note: v3's fixes came from
blind failures, so 94 % is not an independent number.

**11. Are your tests independent?**
Only the two holdouts.
- **Dev** is official but we tuned on it. **5-fold cross-validation** (45.9 ± 1.0, 0 wrong commits
  on every fold) shows the settings are not fitted to particular requests.
- **Stress** and **blind** were written from the catalogue without looking at the agent, but we
  then fixed the agent using their failures, so they are no longer independent.
- **Holdout** (250 requests) was generated from the catalogue only, is read-only with a
  recorded SHA-256, was run once, and no fix was made from it: 37.6, accuracy 0.980,
  13 wrong commits. Caveat: we wrote its generator, so its sentence styles are ours, and it has
  twice dev's share of corrections (36 % against 18 %).
- **Holdout 2** (200 requests, v5) is a second frozen set with new Indian-shopper wording (Hinglish,
  synonyms like "lowers" and "frock", typos, ₹/k/word prices). Run once on v5: **45.3**, accuracy
  **0.995**, **3** wrong commits (all from corrections at the very end). Same caveat: we wrote it.

**12. Isn't this just hard-coded rules?**
The vocabulary is hand-built, yes, but the decisions rest on one guarantee and on measured
numbers. The guarantee: filters are never tighter than what was said, so "one product left"
means "this is the target" unless the shopper corrects something. The measured numbers: the
correction chances come from dev data (`tune.py`), and the settings were chosen by 5-fold
cross-validation. Every part is shown to earn its place by removing it (ablation table).
Rules make it explainable, deterministic, free to run and checkable by the jury line by line.
The weakness is vocabulary coverage: new synonyms and typos need adding.

**13. What are the real latency and cost?**
- **Latency:** 0.05–0.06 ms per word on average, p95 0.43 ms (the words that trigger a search).
  This is timed around each `observe()` call, searches included, on a MacBook Air M4,
  Python 3.14. All 300 dev requests run in 0.22 s.
- **Cost:** no API calls, no model, no GPU, standard library only. The cost is CPU time,
  under 1 ms per request (about 13 words), plus about 1 catalogue search.

**14. Why not commit the moment a brand is said, like a category?**
We tried it (v5 experiment). Cross-validated score went up by 0.5 (45.9 → 46.4), but it made
1 wrong commit on dev (dev-0292). Our rule is zero wrong commits on dev, whatever the score,
because a wrong commit costs −1.0, more than any earliness gain. So we rejected it.
