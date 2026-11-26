"""Ablation study: what each part of Prescient's commit rule is worth. agent.py is not edited.

    python3 compare.py               # ablation on dev.jsonl and tests/holdout.jsonl
    python3 compare.py --strategies  # the older dev-only strategy table (results/strategies.csv)

Every row uses the same agent.py code. For one run, a setting in agent.CONFIG, a hazard
value or one rule function is changed in memory. It is always put back afterwards, so
agent.py's defaults never change. The numbers come straight from evaluate.run() and
evaluate.summarise(), the organisers' scorer.

Rows (each "Prescient −" row removes ONE part of the full agent and keeps the rest)
  Baseline (wait for full request)   evaluate.py's BaselineAgent: never commits early.
  Eager (no safety gate)             commit as soon as one product is left: no neutral words
                                     needed, any value may be committed on its own word,
                                     all hazards 0.
  Prescient − neutral-word rule      any word counts as "moving on" (naive 1-word delay):
   (naive 1-word delay)              FilterState._is_neutral always returns True.
  Prescient − instant category/price commits   CONFIG["instant_slots"] = ().
  Prescient − closing-word rule      polite closers ("thanks", "please", "ji") count as
                                     neutral words. CLOSING_WORDS is emptied ONLY inside
                                     _is_neutral, so typo matching is unchanged.
  Prescient safe mode                CONFIG["instant_slots"] = () and CONFIG["quiet_words"] = 2.
  Prescient v3 (full)                agent.py exactly as it is.

Note: in every agent.py row the built-in "mid-sentence" guards still apply (a number still
being spoken, an open correction cue like "no wait"). They are in the code, not in CONFIG,
so "Eager" means "as eager as the code allows".

Latency is timed by evaluate.run(): time.perf_counter() around each observe() call. It
includes any search done inside that call. It is one run on one machine, so read it as a
rough size, not a precise number.

Holdout: tests/holdout.jsonl is used here for REPORTING only. No setting was chosen by
looking at it. The SHA-256 is checked before the run.
"""
import copy
import csv
import hashlib
import json
import platform
import statistics
import subprocess
import sys
from pathlib import Path

import agent as A
import evaluate

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
ALL_SLOTS = ("category", "colour", "size", "brand", "max_price")
HAZARD_NAMES = ("CORRECTION_HAZARD", "CORRECTION_HAZARD_TAIL", "INSTANT_HAZARD")
ZERO_HAZARDS = {"CORRECTION_HAZARD": {}, "CORRECTION_HAZARD_TAIL": 0.0, "INSTANT_HAZARD": 0.0}
HOLDOUT_SHA256 = "0c8c7bfd59e42f1e4c881bb463278f4b988c447fcc7937dc49fc9bd615632e7c"

DATASETS = [("dev", HERE / "dev.jsonl"), ("holdout", HERE / "tests" / "holdout.jsonl")]


# -- replacements for one rule, used for a single run then put back ------------------------
ORIGINAL_IS_NEUTRAL = A.FilterState._is_neutral


def always_neutral(self, tok):
    """Naive delay: every word counts as the shopper moving on."""
    return True


def closers_are_neutral(self, tok):
    """The normal neutral-word rule, but with polite closing words NOT held back."""
    saved = A.CLOSING_WORDS
    A.CLOSING_WORDS = frozenset()
    try:
        return ORIGINAL_IS_NEUTRAL(self, tok)
    finally:
        A.CLOSING_WORDS = saved


# (label, short label for the chart, agent class, CONFIG changes, hazard changes, _is_neutral)
ABLATION = [
    ("Baseline (wait for full request)", "Baseline", evaluate.BaselineAgent, None, None, None),
    ("Eager (no safety gate)", "Eager", A.Agent,
     {"quiet_words": 0, "instant_slots": ALL_SLOTS},
     {"CORRECTION_HAZARD": {0: 0.0}, "CORRECTION_HAZARD_TAIL": 0.0, "INSTANT_HAZARD": 0.0}, None),
    ("Prescient − neutral-word rule (naive 1-word delay)", "− neutral-word rule", A.Agent,
     None, None, always_neutral),
    ("Prescient − instant category/price commits", "− instant commits", A.Agent,
     {"instant_slots": ()}, None, None),
    ("Prescient − closing-word rule", "− closing-word rule", A.Agent,
     None, None, closers_are_neutral),
    ("Prescient safe mode", "Safe mode", A.Agent,
     {"instant_slots": (), "quiet_words": 2}, None, None),
    ("Prescient v3 (full)", "v3 (full)", A.Agent, None, None, None),
]

# The older dev-only table (python3 compare.py --strategies).
STRATEGIES = [
    ("1 baseline", evaluate.BaselineAgent, None, None, None),
    ("2 eager", A.Agent, {"quiet_words": 0, "instant_slots": ALL_SLOTS},
     {"CORRECTION_HAZARD": {0: 0.0}, "CORRECTION_HAZARD_TAIL": 0.0, "INSTANT_HAZARD": 0.0}, None),
    ("3 neutral-word wait (1 word)", A.Agent, {"quiet_words": 1, "instant_slots": ()},
     ZERO_HAZARDS, None),
    ("4 neutral-word wait (2 words)", A.Agent, {"quiet_words": 2, "instant_slots": ()},
     ZERO_HAZARDS, None),
    ("5 naive delay (any word counts)", A.Agent, {"quiet_words": 1, "instant_slots": ()},
     ZERO_HAZARDS, always_neutral),
    ("6 prescient (full agent)", A.Agent, {}, {}, None),
]


def run_strategy(agent_cls, config_changes, hazard_changes, is_neutral, requests):
    """Run evaluate.run() with some settings changed; always restore them afterwards."""
    saved_config = copy.deepcopy(A.CONFIG)
    saved_hazards = {n: copy.deepcopy(getattr(A, n)) for n in HAZARD_NAMES}
    try:
        if is_neutral is not None:
            A.FilterState._is_neutral = is_neutral
        A.CONFIG.update(config_changes or {})
        for name, value in (hazard_changes or {}).items():
            setattr(A, name, value)
        results, per_word = evaluate.run(agent_cls, requests)
        return evaluate.summarise(results), per_word
    finally:
        A.FilterState._is_neutral = ORIGINAL_IS_NEUTRAL
        A.CONFIG.clear()
        A.CONFIG.update(saved_config)
        for name, value in saved_hazards.items():
            setattr(A, name, value)


def load(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def machine_info():
    """Mac model, chip, macOS and Python version, for the latency columns."""
    info = {"python": platform.python_version(), "os": f"{platform.system()} {platform.release()}"}
    if sys.platform == "darwin":
        try:
            hw = subprocess.run(["system_profiler", "SPHardwareDataType"], capture_output=True,
                                text=True, timeout=30).stdout
            fields = dict(line.strip().split(": ", 1) for line in hw.splitlines() if ": " in line)
            info["machine"] = (f"{fields.get('Model Name', '?')} ({fields.get('Model Identifier', '?')}), "
                               f"{fields.get('Chip', '?')}, {fields.get('Memory', '?')} RAM")
            info["os"] = "macOS " + subprocess.run(["sw_vers", "-productVersion"], capture_output=True,
                                                   text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    info.setdefault("machine", platform.machine())
    return info


def row_from(dataset, label, s, per_word):
    ms = sorted(1000 * t for t in per_word)
    return {
        "dataset": dataset,
        "strategy": label,
        "requests": s["requests"],
        "score": round(s["score"], 1),
        "accuracy": round(s["accuracy"], 3),
        "wrong_commits": s["wrong_commits"],
        "earliness_pct": round(100 * s["early_share"], 1),
        "searches_per_request": round(s["searches"], 2),
        "latency_mean_ms_per_word": round(statistics.mean(ms), 3),
        "latency_p95_ms_per_word": round(ms[int(.95 * (len(ms) - 1))], 3),   # same rule as evaluate.py
    }


# -- outputs ------------------------------------------------------------------------------
COLS = [("strategy", "Strategy"), ("score", "Score"), ("accuracy", "Accuracy"),
        ("wrong_commits", "Wrong commits"), ("earliness_pct", "Earliness %"),
        ("searches_per_request", "Searches/req"), ("latency_mean_ms_per_word", "Mean ms/word"),
        ("latency_p95_ms_per_word", "p95 ms/word")]


def markdown_table(rows):
    out = ["| " + " | ".join(h for _, h in COLS) + " |",
           "|" + "|".join(["---"] + ["---:"] * (len(COLS) - 1)) + "|"]
    for r in rows:
        cells = [r["strategy"], f"{r['score']:.1f}", f"{r['accuracy']:.3f}", str(r["wrong_commits"]),
                 f"{r['earliness_pct']:.1f}", f"{r['searches_per_request']:.2f}",
                 f"{r['latency_mean_ms_per_word']:.3f}", f"{r['latency_p95_ms_per_word']:.3f}"]
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def write_markdown(path, rows, info, counts, oracle):
    parts = ["# Ablation study: Prescient's commit rule", "",
             "Made by `python3 compare.py`. `agent.py` was not edited: each row changes one setting "
             "in memory for one run, then puts it back. Scored by the organisers' `evaluate.run()` / "
             "`evaluate.summarise()`.", "",
             "## Setup",
             f"- Machine: {info['machine']}, {info['os']}",
             f"- Python {info['python']}, standard library only",
             "- Latency: timed by `evaluate.run()` with `time.perf_counter()` around **each `observe()` "
             "call, including any search made inside it**. Mean and p95 are over all words. One run, "
             "so treat them as rough sizes.",
             f"- Requests: dev = {counts['dev']} (`dev.jsonl`), holdout = {counts['holdout']} "
             f"(`tests/holdout.jsonl`, SHA-256 checked unchanged)",
             f"- Best possible earliness (oracle): dev {oracle['dev']:.1f}%, holdout {oracle['holdout']:.1f}%",
             "- The holdout is used for reporting only. No setting was chosen by looking at it.", ""]
    for name, _ in DATASETS:
        parts += [f"## {name} ({counts[name]} requests)", "",
                  markdown_table([r for r in rows if r["dataset"] == name]), ""]
    parts += ["## What each row removes",
              "- **Baseline**: the organisers' agent. It waits for the last word.",
              "- **Eager**: commits the moment one product is left. No neutral words needed, "
              "any value may be committed on its own word, all hazards 0. Mid-sentence guards "
              "(an unfinished number, an open \"no wait\") still apply.",
              "- **− neutral-word rule**: any next word counts as moving on (a naive 1-word delay), "
              "so lead-ins, value tails and closers no longer hold the commit back.",
              "- **− instant category/price commits**: a category or price word must also be "
              "followed by a neutral word.",
              "- **− closing-word rule**: \"thanks\", \"please\", \"ji\" and the like count as neutral.",
              "- **Safe mode**: no instant commits, and 2 neutral words needed instead of 1.",
              "- **v3 (full)**: `agent.py` exactly as it is.", "",
              "Chart: `results/speed_safety.svg` (earliness on x, wrong commits on y).", ""]
    path.write_text("\n".join(parts), encoding="utf-8")


def esc(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def nice_max(v, ticks=5):
    """Round an axis maximum up so that the tick labels are whole, round numbers."""
    for step in (1, 2, 2.5, 5, 10, 20, 25, 50, 100, 200, 250, 500):
        if step * ticks >= v:
            return step * ticks
    return v


def write_svg(path, rows, shorts):
    """Two panels (dev, holdout): earliness % on x, wrong commits on y, one point per row."""
    PW, PH, margin_l, gap, top = 420, 340, 80, 150, 90     # plot area of one panel, spacing
    W, H = margin_l + 2 * PW + gap + 40, top + PH + 70
    colours = ["#6b7280", "#dc2626", "#ea580c", "#ca8a04", "#7c3aed", "#0891b2", "#16a34a"]
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
           'font-family="Helvetica, Arial, sans-serif" font-size="12">',
           f'<rect width="{W}" height="{H}" fill="white"/>',
           f'<text x="{W / 2}" y="28" text-anchor="middle" font-size="17" font-weight="bold">'
           'Speed vs safety: earliness vs wrong commits</text>',
           f'<text x="{W / 2}" y="48" text-anchor="middle" fill="#555">Best corner: bottom right '
           '(early, no wrong commits). A wrong commit costs 1.0 point; full earliness is worth at most 0.6.</text>']
    for p, (name, _) in enumerate(DATASETS):
        data = [r for r in rows if r["dataset"] == name]
        x0, y0 = margin_l + p * (PW + gap), top
        xmax = nice_max(max(r["earliness_pct"] for r in data) * 1.1)
        ymax = nice_max(max(r["wrong_commits"] for r in data) * 1.1)
        sx = lambda v: x0 + PW * v / xmax
        sy = lambda v: y0 + PH - PH * v / ymax
        out.append(f'<text x="{x0 + PW / 2}" y="{y0 - 12}" text-anchor="middle" font-size="14" '
                   f'font-weight="bold">{name} ({data[0]["requests"]} requests)</text>')
        for i in range(6):                                  # grid and tick labels
            xv, yv = xmax * i / 5, ymax * i / 5
            out.append(f'<line x1="{sx(xv):.1f}" y1="{y0}" x2="{sx(xv):.1f}" y2="{y0 + PH}" stroke="#eee"/>')
            out.append(f'<line x1="{x0}" y1="{sy(yv):.1f}" x2="{x0 + PW}" y2="{sy(yv):.1f}" stroke="#eee"/>')
            out.append(f'<text x="{sx(xv):.1f}" y="{y0 + PH + 16}" text-anchor="middle" fill="#444">{xv:g}</text>')
            out.append(f'<text x="{x0 - 6}" y="{sy(yv) + 4:.1f}" text-anchor="end" fill="#444">{yv:g}</text>')
        out.append(f'<rect x="{x0}" y="{y0}" width="{PW}" height="{PH}" fill="none" stroke="#999"/>')
        out.append(f'<text x="{x0 + PW / 2}" y="{y0 + PH + 38}" text-anchor="middle">'
                   'earliness (% of the request saved)</text>')
        out.append(f'<text transform="translate({x0 - 48},{y0 + PH / 2}) rotate(-90)" '
                   'text-anchor="middle">wrong commits</text>')

        pts = [(sx(r["earliness_pct"]), sy(r["wrong_commits"])) for r in data]
        boxes = [(px - 6, py - 6, px + 6, py + 6) for px, py in pts]   # keep labels off points

        def free(b):
            inside = x0 + 2 <= b[0] and b[2] <= x0 + PW + 30 and y0 + 2 <= b[1] and b[3] <= y0 + PH - 2
            return inside and not any(b[0] < c[2] and c[0] < b[2] and b[1] < c[3] and c[1] < b[3] for c in boxes)

        # Place labels for the most crowded points first: those lowest and furthest right.
        for k in sorted(range(len(data)), key=lambda k: (-pts[k][1], -pts[k][0])):
            r, (px, py) = data[k], pts[k]
            label = f'{shorts[r["strategy"]]} ({r["earliness_pct"]:.1f}%, {r["wrong_commits"]})'
            w, h = 6.4 * len(label), 14
            spots = [(px + 9, py - 3), (px - 9 - w, py - 3)]            # right of / left of the point
            for lift in range(18, 260, 18):                             # then higher, with a leader line
                spots += [(px + 4, py - lift), (px - w - 4, py - lift)]
            for lx, ly in spots:
                b = (lx - 2, ly - h + 2, lx + w + 2, ly + 4)
                if free(b):
                    break
            boxes.append(b)
            c = colours[[lab for lab, *_ in ABLATION].index(r["strategy"]) % len(colours)]
            if ly < py - 10:                                            # label was lifted
                ax = min(max(px, lx), lx + w)
                out.append(f'<line x1="{px:.1f}" y1="{py - 5:.1f}" x2="{ax:.1f}" y2="{ly + 3:.1f}" '
                           f'stroke="{c}" stroke-width="0.8"/>')
            out.append(f'<text x="{lx:.1f}" y="{ly:.1f}" fill="{c}">{esc(label)}</text>')
        for r, (px, py) in zip(data, pts):                              # points on top of lines
            c = colours[[lab for lab, *_ in ABLATION].index(r["strategy"]) % len(colours)]
            out.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="5" fill="{c}" stroke="white" stroke-width="1.5"/>')
    out.append("</svg>")
    path.write_text("\n".join(out), encoding="utf-8")


def ablation():
    holdout = DATASETS[1][1]
    digest = hashlib.sha256(holdout.read_bytes()).hexdigest()
    if digest != HOLDOUT_SHA256:
        sys.exit(f"tests/holdout.jsonl has changed (SHA-256 {digest}); refusing to run.")

    info = machine_info()
    rows, counts, oracle = [], {}, {}
    for name, path in DATASETS:
        requests = load(path)
        counts[name] = len(requests)
        for label, _, cls, cfg, haz, neutral in ABLATION:
            s, per_word = run_strategy(cls, cfg, haz, neutral, requests)
            oracle[name] = 100 * s["oracle_share"] if s["oracle_share"] is not None else float("nan")
            rows.append(row_from(name, label, s, per_word))

    RESULTS.mkdir(exist_ok=True)
    with open(RESULTS / "ablation.csv", "w", newline="", encoding="utf-8") as fh:
        meta = {"machine": info["machine"], "os": info["os"], "python": info["python"],
                "latency_timing": "perf_counter around each observe() call, searches included"}
        w = csv.DictWriter(fh, fieldnames=list(rows[0]) + list(meta))
        w.writeheader()
        w.writerows({**r, **meta} for r in rows)
    write_markdown(RESULTS / "ablation.md", rows, info, counts, oracle)
    write_svg(RESULTS / "speed_safety.svg", rows, {lab: short for lab, short, *_ in ABLATION})

    print(f"{info['machine']} | {info['os']} | Python {info['python']}")
    for name, _ in DATASETS:
        print(f"\n{name}: {counts[name]} requests (oracle earliness {oracle[name]:.1f}%)")
        print(markdown_table([r for r in rows if r["dataset"] == name]))
    print("\nsaved: results/ablation.csv, results/ablation.md, results/speed_safety.svg")


def strategies():
    requests = load(HERE / "dev.jsonl")
    rows = []
    for name, cls, cfg, haz, neutral in STRATEGIES:
        s, _ = run_strategy(cls, cfg, haz, neutral, requests)
        rows.append({"strategy": name, "score": round(s["score"], 1), "accuracy": round(s["accuracy"], 3),
                     "wrong_commits": s["wrong_commits"], "earliness_pct": round(100 * s["early_share"], 1),
                     "searches_per_request": round(s["searches"], 2)})
    RESULTS.mkdir(exist_ok=True)
    with open(RESULTS / "strategies.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"{len(requests)} requests from dev.jsonl\n")
    for r in rows:
        print(f"{r['strategy']:33} {r['score']:6.1f} {r['accuracy']:9.3f} {r['wrong_commits']:6d}"
              f" {r['earliness_pct']:9.1f}% {r['searches_per_request']:9.2f}")
    print("\nsaved: results/strategies.csv")


if __name__ == "__main__":
    strategies() if "--strategies" in sys.argv[1:] else ablation()
