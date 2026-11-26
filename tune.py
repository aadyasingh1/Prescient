"""Tune the commit policy on dev.jsonl with honest cross-validation.

    python tune.py

1. Measures the "correction hazard": once exactly one product is left and the request has
   been quiet for d words, how often does a correction still arrive later? (This is the
   CORRECTION_HAZARD table in agent.py.)
2. Grid-searches commit_threshold x quiet_words. To avoid fooling ourselves, it uses
   5-fold cross-validation: settings are picked on 4/5 of dev and scored on the 1/5 they
   never saw. The held-out score is the number to trust for the hidden test set.
"""
import json
import random
import statistics
from collections import defaultdict
from pathlib import Path

import agent as A
import evaluate

HERE = Path(__file__).resolve().parent
REQS = [json.loads(line) for line in open(HERE / "dev.jsonl", encoding="utf-8") if line.strip()]


def hazard_table(reqs, max_d=6):
    """P(a correction arrives later | unique, quiet for d words)."""
    tools = evaluate.Tools()
    ag = A.Agent(tools)
    seen, bad = defaultdict(int), defaultdict(int)
    for r in reqs:
        ag.start(r["id"])
        for w in r["words"]:
            ag.observe(w)
            c = ag._candidates()
            st = ag.state
            if not c or len(c) != 1 or st.cue_open or st.number is not None:
                continue
            d = min(st.neutral_quiet, max_d)
            seen[d] += 1
            bad[d] += c[0]["product_id"] != r["target"]
    return {d: (bad[d] + 0.5) / (seen[d] + 1) for d in sorted(seen)}, dict(seen)


def score(reqs, threshold, quiet):
    A.CONFIG["commit_threshold"], A.CONFIG["quiet_words"] = threshold, quiet
    results, _ = evaluate.run(A.Agent, reqs)
    return evaluate.summarise(results)


def main():
    table, counts = hazard_table(REQS)
    print("correction hazard (words quiet -> P(wrong), n):")
    for d in table:
        print(f"  {d}: {table[d]:.3f}  (n={counts[d]})")

    grid = [(t, q) for t in (0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95) for q in (0, 1, 2, 3)]
    print("\nfull-dev grid (score / wrong commits / earliness):")
    full = {}
    for t, q in grid:
        s = score(REQS, t, q)
        full[(t, q)] = s
        print(f"  threshold {t:.2f} quiet {q}:  {s['score']:5.1f}  wrong {s['wrong_commits']:2d}"
              f"  early {s['early_share']:.1%}")

    random.seed(7)
    order = REQS[:]
    random.shuffle(order)
    folds = [order[i::5] for i in range(5)]
    held = []
    for i in range(5):
        train = [r for j, f in enumerate(folds) if j != i for r in f]
        best = max(grid, key=lambda g: score(train, *g)["score"])
        s = score(folds[i], *best)
        held.append(s["score"])
        print(f"fold {i}: picked threshold {best[0]} quiet {best[1]} -> held-out {s['score']:.1f}"
              f"  wrong {s['wrong_commits']}")
    print(f"\ncross-validated score: {statistics.mean(held):.1f} +- {statistics.stdev(held):.1f}")


if __name__ == "__main__":
    main()
