"""Prescient: "Your product, before your full stop." Live, judge-facing demo of our agent.

    python3 demo_app.py            # opens http://127.0.0.1:8000 in your browser
    python3 demo_app.py --port 8080 --no-browser

Standard library only, works offline (no CDN, no build step). Ctrl+C in the terminal stops it.

Six tabs
--------
  LIVE TYPE      type your own request; every time you press space, the finished word is
                 sent to a real Agent on this server (one agent per typing session).
                 Enter = the shopper has finished (the agent's finish() is called).
  EXAMPLES       four example requests, played word by word (Play / Next word / speed).
  STRATEGY DUEL  the same request through three agents side by side:
                   Baseline  = evaluate.py's BaselineAgent (waits for the whole request)
                   Eager     = agent.py with the "Eager (no safety gate)" settings from
                               compare.py's ablation table (changed in memory, put back after)
                   Prescient = agent.py exactly as it is
                 Each one is scored with evaluate.score_one(), the organisers' own formula.
  RESULTS        results/strategies.csv, results/speed_safety.svg and the headline numbers,
                 all read from the files in results/ when the page loads.
  TRADE-OFF      a 5-position slider from Eager to Extra safe. Each position is a set of
                 agent.CONFIG changes (TRADEOFF below), made in memory for ONE evaluate.run()
                 on all of dev.jsonl and then put back. Every slider move is a fresh run; the
                 page shows score, accuracy, wrong commits, earliness and a dot on a
                 speed-vs-safety chart. The server checks CONFIG is back to agent.py's values.
  EXPLORER       browse every request of dev.jsonl or the frozen holdout (tests/holdout.jsonl),
                 filter by tag and by outcome (committed early / committed late / wrong commit
                 / wrong final), search the text. Outcomes and scores are READ from the saved
                 evaluate.py logs (dev_results.jsonl, results/logs/holdout_v3.jsonl), never
                 recomputed. Clicking a request replays it word by word through agent.py with
                 the same pipeline view, next to the gold target and the agent's saved answer;
                 the page says whether the replay matches the saved log. The holdout is only
                 read: nothing here writes to it or feeds it back into agent.py.

Nothing on screen is invented
-----------------------------
This file has no shopping logic of its own. Every word is fed to the real Agent from agent.py
(the same one evaluate.py scores), through the organisers' counted evaluate.Tools, exactly
as evaluate.py does it. After each word this server records what the agent did and what is
in its own state: filters, confidence, products left, searches used, commit, the safety
checks of _confidence() (read from agent.state and agent.pool_cut), and correction cues.
The page only replays those records. The "products left" funnel shows only these per-word
counts (no made-up in-between numbers).

The "fit in catalogue" count is computed HERE, for display, with the agent's own
FilterState.matches() over catalogue.csv. The agent itself still looks products up only
through tools.search (counted). Reading catalogue.csv for display is the same as before.

Wrong commits in the duel: a commit is "wrong" if it is not the target. For the built-in
duel examples the target comes from hand-written gold filters (like dev.jsonl), checked at
start-up to match exactly one catalogue product. For a request you type yourself there is no
gold answer, so the reference is Prescient's answer after the full sentence; the page says so.
"""
import argparse
import contextlib
import copy
import csv
import json
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import agent as A
import compare                       # the Eager definition used in results/ablation.md
import evaluate
from agent import Agent, clean
from search import load_catalogue

HERE = Path(__file__).resolve().parent
CATALOGUE = load_catalogue()                                  # display only
PRODUCTS = {p["product_id"]: p for p in CATALOGUE}
SLOTS = ("category", "colour", "size", "brand", "max_price")

# agent.CONFIG and the hazard tables are module-wide; the duel changes them for one run.
# One lock around every agent run keeps a duel from ever leaking into another request.
LOCK = threading.Lock()

# Eager = exactly the "Eager (no safety gate)" row of compare.py's ablation table.
_EAGER = next(row for row in compare.ABLATION if row[1] == "Eager")
EAGER_CONFIG, EAGER_HAZARDS = _EAGER[3], _EAGER[4]

# Our own example sentences (not taken from dev.jsonl), one per kind of request.
EXAMPLES = [
    {"name": "Plain",
     "text": "I want black Pacevo running shoes size nine under four thousand for my morning runs"},
    {"name": "Correction",
     "text": "show me maroon Tanaro kurta size M, sorry, XS max 1500 for a wedding"},
    {"name": "Hindi-English",
     "text": "bhai Loomsy ke navy blue sneakers dikhao size 7 under 4500 jaldi"},
    {"name": "Late constraint",
     "text": "I need olive Urbanyx slides under seven hundred, oh and size 6 for the beach trip"},
]

# Duel examples. "gold" = what the shopper finally wants, written by hand (like dev.jsonl).
DUEL_EXAMPLES = [
    {"name": "Correction: black → white",
     "text": "show me Loomsy sneakers in black size 9, no wait, white for office wear",
     "gold": {"brand": "Loomsy", "category": "sneakers", "colour": "white", "size": "9"}},
    {"name": "Plain",
     "text": EXAMPLES[0]["text"],
     "gold": {"brand": "Pacevo", "category": "running shoes", "colour": "black", "size": "9",
              "max_price": 4000}},
    {"name": "Correction: M → XS",
     "text": EXAMPLES[1]["text"],
     "gold": {"brand": "Tanaro", "category": "kurtas", "colour": "maroon", "size": "XS",
              "max_price": 1500}},
    {"name": "Hindi-English",
     "text": EXAMPLES[2]["text"],
     "gold": {"brand": "Loomsy", "category": "sneakers", "colour": "navy", "size": "7",
              "max_price": 4500}},
]


def gold_target(gold):
    """The one catalogue product that matches the gold filters (None if not exactly one)."""
    hits = [p for p in CATALOGUE
            if all((p["price"] <= v) if k == "max_price" else (str(p[k]) == str(v))
                   for k, v in gold.items())]
    return hits[0]["product_id"] if len(hits) == 1 else None


for ex in DUEL_EXAMPLES:
    ex["target"] = gold_target(ex["gold"])
DUEL_TARGETS = {ex["text"]: ex["target"] for ex in DUEL_EXAMPLES if ex["target"]}


# ------------------------------------------------------------------------------------------
# Reading the agent's own state
# ------------------------------------------------------------------------------------------
def product_card(pid):
    p = PRODUCTS.get(pid)
    if not p:
        return None
    return {k: p[k] for k in ("product_id", "title", "brand", "category", "colour",
                              "size", "price")}


def show_value(slot, v):
    """A filter value as text. Unclear words are kept as a set ("running shoes / sneakers")."""
    if v is None:
        return None
    if isinstance(v, frozenset):
        return " / ".join(sorted(v))
    return f"₹{v:,}" if slot == "max_price" else str(v)


def corrections(before, after):
    """Slots whose value was REPLACED by this word (narrowing a set is not a correction)."""
    out = []
    for slot, old in before.items():
        new = after[slot]
        if old is None or new is None or new == old:
            continue
        if isinstance(old, frozenset) and isinstance(new, frozenset) and new < old:
            continue                         # "shoes" → "running shoes": the shopper narrowed it
        out.append({"slot": slot, "old": show_value(slot, old), "new": show_value(slot, new)})
    return out


def safety_checks(agent, word):
    """The safety checks agent._confidence() makes, read from the agent's own state.

    Each check: name (when passed), open (short name when NOT passed), ok, detail, kind.
    The order and the conditions are the same as in agent.py's _confidence().
    """
    st, cfg = agent.state, A.CONFIG
    tok = clean(word)
    pool_n = None if agent.pool is None else len(agent.pool)
    checks = [
        {"name": "Fetch list complete", "open": "Fetch list may be cut short", "kind": "risk",
         "ok": not agent.pool_cut,
         "detail": "" if not agent.pool_cut
         else f"a fetch returned the full {cfg['fetch_k']} products, so \"one left\" is no proof"
         + (f" (this list: {pool_n})" if pool_n is not None else "")},
        {"name": "No correction word open", "open": "Correction word open", "kind": "correction",
         "ok": not st.cue_open,
         "detail": "" if not st.cue_open else "heard a correction word: waiting for the new value"},
        {"name": "No number still being spoken", "open": "Number still being spoken",
         "kind": "hold", "ok": st.number is None,
         "detail": "" if st.number is None else "a number may continue (\"forty… five hundred\")"},
        {"name": "No bare number waiting", "open": "Bare number waiting for its meaning",
         "kind": "hold", "ok": st.pending_bare is None,
         "detail": "" if st.pending_bare is None else f"\"{st.pending_bare}\" could still be a size"},
        {"name": "No size or price phrase open", "open": "Size or price phrase still open",
         "kind": "hold", "ok": st.size_modifier is None and not st.price_prefix_open,
         "detail": "" if st.size_modifier is None and not st.price_prefix_open
         else "\"extra…\" or \"under…\" is waiting for its value"},
    ]
    quiet = st.neutral_quiet
    instant = quiet == 0 and st.last_slot in cfg["instant_slots"] and not st.last_punct
    moved_on = instant or quiet >= cfg["quiet_words"]
    if instant:
        detail = f"instant rule: a {st.last_slot.replace('max_price', 'price')} word with no punctuation after it"
    elif moved_on:
        detail = f"{quiet} neutral word{'s' if quiet != 1 else ''} since the last value"
    elif tok in A.CLOSING_WORDS:
        detail = f"\"{tok}\" is a polite closing word: corrections often follow it, so it does not count"
    else:
        detail = "no neutral word since the last value yet"
    checks.append({"name": "Shopper has moved on", "kind": "hold",
                   "open": "Polite closing word" if (not moved_on and tok in A.CLOSING_WORDS)
                   else "No neutral word yet",
                   "ok": moved_on, "detail": detail})
    if instant:
        hazard = A.INSTANT_HAZARD
    elif moved_on:
        hazard = A.CORRECTION_HAZARD.get(quiet, A.CORRECTION_HAZARD_TAIL)
    else:
        hazard = None
    return checks, hazard


def commit_reason(left, checks, conf):
    """Why the agent did not commit on this word (None if it could)."""
    if left is None:
        return "no search yet: category or brand not known"
    if left == 0:
        return "no product fits yet"
    if left > 1:
        return f"{left} products still fit: needs exactly 1"
    if not all(c["ok"] for c in checks):
        return "1 product fits, but a safety check is open"
    if conf < A.CONFIG["commit_threshold"]:
        return f"confidence {round(100 * conf)}% is below {round(100 * A.CONFIG['commit_threshold'])}%"
    return None


class Recorder:
    """One real Agent, fed one word at a time; records what it did after every word."""

    def __init__(self):
        self.tools = evaluate.Tools()          # the organisers' counted tools
        self.agent = Agent(self.tools)
        self.agent.start("demo")
        self.steps = []
        self.current = None                    # latest product committed (as evaluate.py tracks it)
        self.setter = {}                       # slot -> word indexes that said its value
        self.open_cues = []                    # correction-cue words not yet resolved
        self.final = None

    def observe(self, word):
        ag = self.agent
        st = ag.state
        i = len(self.steps)
        before = dict(st.values)
        phrase = st.number                     # a number phrase that may close on this word
        phrase_len = len(phrase.tokens) if phrase is not None else 0
        cue_before = st.cue_open

        out = ag.observe(word) or {}

        st = ag.state
        tok = clean(word)
        changed = [s for s in SLOTS if st.values[s] != before[s]]
        fixes = corrections(before, st.values)

        # Which words said each new value: a number phrase may span several words.
        span = [i]
        if phrase is not None and st.number is None and changed:
            end = i if len(phrase.tokens) > phrase_len else i - 1
            span = list(range(max(0, end - len(phrase.tokens) + 1), end + 1))
        struck = sorted({j for f in fixes for j in self.setter.get(f["slot"], [])})
        for s in changed:
            self.setter[s] = span

        # Correction cues, read from the agent: "no", "sorry", "I mean", "galti se"...
        cue = bool(st.cue_open) and (tok in A.CORRECTION_CUES or st._is_soft_cue(tok))
        uncue = []
        if cue:
            self.open_cues.append(i)
        elif cue_before and not st.cue_open:
            if not changed:                    # "make sure", "no more than": not a correction
                uncue = self.open_cues
            self.open_cues = []

        cands = ag._candidates()               # read-only peek, for the "products left" counter
        left = None if cands is None else len(cands)
        fit = sum(1 for p in CATALOGUE if st.matches(p))   # display only (see top of file)
        checks, hazard = safety_checks(ag, word)
        conf = out.get("confidence") or 0.0
        commit = out.get("commit")
        step = {
            "i": i, "word": word,
            "values": {s: show_value(s, v) for s, v in st.values.items()},
            "confidence": out.get("confidence"),
            "left": left, "fit": fit,
            "pool": None if ag.pool is None else len(ag.pool),
            "pool_key": None if ag.pool_key is None else list(ag.pool_key),
            "pool_cut": ag.pool_cut,
            "searches": self.tools.searches,
            "commit": commit,
            "new_commit": bool(commit) and commit != self.current,
            "changed": changed, "span": span if changed else [],
            "corrections": fixes, "struck": struck,
            "cue": cue, "uncue": uncue,
            "checks": checks, "hazard": hazard,
            "waiting": None if commit else commit_reason(left, checks, conf),
            "holding": None if commit else self.current,
        }
        if commit:
            step["product"] = product_card(commit)
            if commit != self.current:
                step["replaced"] = self.current
            self.current = commit
        self.steps.append(step)
        return step

    def finish(self):
        final = self.agent.finish() or self.current        # the same rule as evaluate.py
        n = len(self.steps)
        commits = [(s["i"] + 1, s["commit"]) for s in self.steps if s["new_commit"]]
        first = next((t for t, c in commits if c == final), None)
        self.final = {
            "final": final, "final_product": product_card(final),
            "searches": self.tools.searches, "words": n, "commits": commits,
            "first_commit_word": first,
            "words_before_end": (n - first) if first else 0,
            "values": {s: show_value(s, v) for s, v in self.agent.state.values.items()},
        }
        return self.final


def run_request(text):
    words = text.split()
    with LOCK:
        rec = Recorder()
        for w in words:
            rec.observe(w)
        final = rec.finish()
    return {"words": words, "steps": rec.steps, "final": final}


# ------------------------------------------------------------------------------------------
# Live typing: one Recorder per typing session
# ------------------------------------------------------------------------------------------
SESSIONS = {}
MAX_SESSIONS = 30


def live(action, sid=None, word=None):
    with LOCK:
        if action == "start":
            while len(SESSIONS) >= MAX_SESSIONS:
                SESSIONS.pop(next(iter(SESSIONS)))
            sid = uuid.uuid4().hex
            SESSIONS[sid] = Recorder()
            return {"session": sid}
        rec = SESSIONS.get(sid)
        if rec is None:
            return {"error": "session expired: retype the request"}
        if action == "word":
            if rec.final is not None:
                return {"error": "request already finished"}
            return {"step": rec.observe(str(word)[:60])}
        if action == "finish":
            return {"final": rec.final or rec.finish()}
    return {"error": f"unknown action {action!r}"}


# ------------------------------------------------------------------------------------------
# Strategy duel
# ------------------------------------------------------------------------------------------
@contextlib.contextmanager
def settings(config_changes, hazard_changes):
    """Change agent.CONFIG / hazard tables for one run; always put them back (as compare.py)."""
    saved_config = copy.deepcopy(A.CONFIG)
    saved_hazards = {n: copy.deepcopy(getattr(A, n)) for n in compare.HAZARD_NAMES}
    try:
        A.CONFIG.update(config_changes or {})
        for name, value in (hazard_changes or {}).items():
            setattr(A, name, value)
        yield
    finally:
        A.CONFIG.clear()
        A.CONFIG.update(saved_config)
        for name, value in saved_hazards.items():
            setattr(A, name, value)


STRATEGIES = [
    ("baseline", "Baseline", "Waits for the whole request, then searches once.",
     evaluate.BaselineAgent, None, None),
    ("eager", "Eager", "Commits the moment exactly one product is left. No safety gate.",
     Agent, EAGER_CONFIG, EAGER_HAZARDS),
    ("prescient", "Prescient", "Commits only when one product is left AND the request has settled.",
     Agent, None, None),
]


def run_lane(agent_cls, config_changes, hazard_changes, words):
    with settings(config_changes, hazard_changes):
        tools = evaluate.Tools()
        ag = agent_cls(tools)
        ag.start("duel")
        steps, commits, current, reported = [], [], None, None
        for t, w in enumerate(words, 1):
            out = ag.observe(w)
            if isinstance(out, str):
                out = {"commit": out}
            out = out or {}
            new = bool(out.get("commit")) and out["commit"] != current
            if new:
                current = out["commit"]
                commits.append((t, current))
            if out.get("filters") is not None:
                reported = out["filters"]
            cands = ag._candidates() if hasattr(ag, "_candidates") else None
            steps.append({"commit": out.get("commit"), "current": current, "new": new,
                          "product": product_card(current) if new else None,
                          "confidence": out.get("confidence"),
                          "left": None if cands is None else len(cands)})
        final = ag.finish() or current
        return steps, commits, final, tools.searches, reported


def duel(text):
    words = text.split()
    target = DUEL_TARGETS.get(text.strip())
    reference = "gold" if target else "prescient"
    lanes, raw = [], {}
    with LOCK:
        for key, name, rule, cls, cfg, hz in STRATEGIES:
            raw[key] = run_lane(cls, cfg, hz, words)
        if target is None:
            target = raw["prescient"][2]       # typed request: no gold answer
    for key, name, rule, cls, cfg, hz in STRATEGIES:
        steps, commits, final, searches, reported = raw[key]
        req = {"id": "duel", "words": words, "target": target}
        sc = evaluate.score_one(req, commits, final, searches, reported)
        for s in steps:
            s["wrong"] = bool(s["new"]) and s["current"] != target
        lanes.append({"key": key, "name": name, "rule": rule, "steps": steps,
                      "commits": commits, "final": final, "final_product": product_card(final),
                      "correct": sc["correct"], "wrong_commits": sc["wrong_commits"],
                      "early_words": sc["early_words"], "early_share": sc["early_share"],
                      "searches": searches, "score": sc["score"]})
    return {"words": words, "target": target, "target_product": product_card(target),
            "reference": reference, "lanes": lanes}


# ------------------------------------------------------------------------------------------
# Trade-off tab: one slider, five real CONFIG settings, scored live on dev.jsonl
# ------------------------------------------------------------------------------------------
DEFAULT_CONFIG = copy.deepcopy(A.CONFIG)             # agent.py's settings, to check restores
with open(HERE / "dev.jsonl", encoding="utf-8") as _fh:
    DEV = [json.loads(line) for line in _fh if line.strip()]

# (key, name, what it means, CONFIG changes). Only agent.CONFIG is changed, in memory, for
# one evaluate.run(); settings() puts it back. "Eager" gives the same dev numbers as the
# "Eager (no safety gate)" row of results/ablation.md (43.0, 21 wrong commits).
TRADEOFF = [
    ("eager", "Eager", "Commits as soon as one product is left: no quiet word needed, and any value "
     "(colour, size, brand too) may be committed on the very word it appears.",
     {"quiet_words": 0, "instant_slots": compare.ALL_SLOTS}),
    ("fast", "Fast", "Normal quiet-word rule, but every value (not only category and price) may be "
     "committed on its own word.",
     {"instant_slots": compare.ALL_SLOTS}),
    ("default", "Prescient default", "agent.py exactly as submitted.", {}),
    ("safe", "Safe", "Waits for 2 quiet words instead of 1 before committing.",
     {"quiet_words": 2}),
    ("xsafe", "Extra safe", "Waits for 2 quiet words, and never commits on the category/price word itself.",
     {"quiet_words": 2, "instant_slots": ()}),
]


def jsonable(cfg):
    return {k: list(v) if isinstance(v, tuple) else v for k, v in cfg.items()}


def tradeoff(level):
    key, name, meaning, changes = TRADEOFF[max(0, min(int(level), len(TRADEOFF) - 1))]
    with LOCK:
        with settings(changes, None):
            used = jsonable(A.CONFIG)                    # the settings this run really used
            t0 = time.perf_counter()
            results, _ = evaluate.run(Agent, DEV)
            ms = 1000 * (time.perf_counter() - t0)
        restored = A.CONFIG == DEFAULT_CONFIG            # settings() has put agent.py's back
    s = evaluate.summarise(results)
    return {"key": key, "name": name, "meaning": meaning, "changes": jsonable(changes),
            "config": used, "requests": s["requests"], "score": round(s["score"], 1),
            "accuracy": s["accuracy"], "wrong_commits": s["wrong_commits"],
            "requests_with_wrong_commit": s["requests_with_wrong_commit"],
            "earliness_pct": round(100 * s["early_share"], 1), "searches": round(s["searches"], 2),
            "ms": round(ms), "restored": restored}


# ------------------------------------------------------------------------------------------
# Results tab: read straight from results/
# ------------------------------------------------------------------------------------------
def read_csv(path):
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


def results():
    strategies = read_csv(HERE / "results" / "strategies.csv")
    ablation = read_csv(HERE / "results" / "ablation.csv")

    def pick(rows, dataset, starts):
        return next((r for r in rows if r.get("dataset", dataset) == dataset
                     and r["strategy"].startswith(starts)), None)

    return {"strategies": strategies,
            "dev": {"prescient": pick(strategies, "dev", "6 prescient"),
                    "baseline": pick(strategies, "dev", "1 baseline")},
            "holdout": {"prescient": pick(ablation, "holdout", "Prescient v3 (full)"),
                        "baseline": pick(ablation, "holdout", "Baseline"),
                        "eager": pick(ablation, "holdout", "Eager")},
            "dev_eager": pick(ablation, "dev", "Eager"),
            "has_svg": (HERE / "results" / "speed_safety.svg").exists()}


# ------------------------------------------------------------------------------------------
# Explorer tab: browse every request of a dataset with its SAVED result, replay any one
# ------------------------------------------------------------------------------------------
# Each dataset = its request file + the per-request log that evaluate.py --log wrote for it.
# The list, the outcome of each request and its score are read from that saved log, never
# recomputed here. The holdout is only ever READ: nothing in this file writes to it or feeds
# its results back into agent.py (see "Frozen holdout" in the project rules).
EXPLORE_SOURCES = {
    "dev": {"name": "Dev", "requests": "dev.jsonl", "log": "dev_results.jsonl",
            "note": "The organisers' dev set. Saved log written by "
                    "python3 evaluate.py --agent agent.py --log dev_results.jsonl"},
    "holdout": {"name": "Holdout", "requests": "tests/holdout.jsonl",
                "log": "results/logs/holdout_v3.jsonl",
                "note": "Frozen holdout: run once, view only. Its failures are never used to "
                        "change the agent. Saved log from the single v3 run (v4 gives the same "
                        "results: see results/holdout_report.md)."},
}
OUTCOMES = {"early": "Committed early", "late": "Committed late",
            "wrong_commit": "Wrong commit", "wrong_final": "Wrong final"}


def read_jsonl(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def outcome_flags(r):
    """What happened to one request, read from its saved evaluate.py log line.

    early        = final answer right, no wrong commit, the right product committed
                   before the last word (evaluate.py gave earliness credit)
    late         = final answer right, no wrong commit, but no earliness credit
                   (committed only on the last word, or answered at the full stop)
    wrong_commit = at least one committed product was not the target (−1.0 each)
    wrong_final  = the final answer was not the target
    """
    flags = []
    if r["correct"] and not r["wrong_commits"]:
        flags.append("early" if r["early_words"] > 0 else "late")
    if r["wrong_commits"]:
        flags.append("wrong_commit")
    if not r["correct"]:
        flags.append("wrong_final")
    return flags


def holdout_hash_check():
    """SHA-256 of tests/holdout.jsonl vs the value recorded in results/holdout_report.md."""
    import hashlib
    import re
    try:
        actual = hashlib.sha256((HERE / "tests" / "holdout.jsonl").read_bytes()).hexdigest()
        report = (HERE / "results" / "holdout_report.md").read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"SHA-256 = `([0-9a-f]{64})`", report)
    return {"actual": actual, "recorded": m.group(1) if m else None,
            "ok": bool(m) and m.group(1) == actual}


def load_explore(key):
    src = EXPLORE_SOURCES[key]
    try:
        requests = read_jsonl(HERE / src["requests"])
        saved = {r["id"]: r for r in read_jsonl(HERE / src["log"])}
    except OSError as exc:
        return {"error": f"cannot read {exc.filename}: {exc.strerror}"}
    rows = []
    for req in requests:
        r = saved.get(req["id"])
        if r is None:
            continue                          # no saved result: not shown (nothing is made up)
        rows.append({
            "id": req["id"], "text": req["text"], "n": len(req["words"]), "tags": req["tags"],
            "target": req["target"], "final": r["final"], "commits": r["commits"],
            "correct": r["correct"], "wrong_commits": r["wrong_commits"],
            "early_words": r["early_words"], "early_share": r["early_share"],
            "oracle_share": r["oracle_share"], "earliest": req.get("earliest"),
            "searches": r["searches"], "score": r["score"], "outcome": outcome_flags(r),
            "filters_gold": r["filters_gold"], "filters_reported": r["filters_reported"],
        })
    return {"key": key, "name": src["name"], "note": src["note"],
            "requests_file": src["requests"], "log_file": src["log"],
            "requests": requests, "rows": rows, "missing": len(requests) - len(rows),
            "hash": holdout_hash_check() if key == "holdout" else None}


EXPLORE = {}                                  # loaded on first use, then kept


def explore_data(key):
    if key not in EXPLORE_SOURCES:
        return {"error": f"unknown dataset {key!r}"}
    if key not in EXPLORE or "error" in EXPLORE[key]:
        EXPLORE[key] = load_explore(key)
    return EXPLORE[key]


def explore_list(key):
    d = explore_data(key)
    if "error" in d:
        return d
    rows = d["rows"]
    s = {"requests": len(rows),
         "score": round(100 * sum(r["score"] for r in rows) / len(rows), 1) if rows else None}
    return {k: d[k] for k in ("key", "name", "note", "requests_file", "log_file", "missing", "hash")} | {
        "rows": rows, "summary": s, "outcomes": OUTCOMES,
        "datasets": {k: v["name"] for k, v in EXPLORE_SOURCES.items()}}


def explore_replay(key, rid):
    """Replay one request word by word through agent.py (the same Recorder as EXAMPLES).

    The words come from the request file, exactly as evaluate.py feeds them. The outcome
    shown as "saved" comes from the saved log; the replay is only for the per-word view, and
    the page says whether the replay's commits and final answer match the saved log.
    """
    d = explore_data(key)
    if "error" in d:
        return d
    req = next((q for q in d["requests"] if q["id"] == rid), None)
    row = next((r for r in d["rows"] if r["id"] == rid), None)
    if req is None or row is None:
        return {"error": f"request {rid!r} not found in {d['requests_file']}"}
    words = list(req["words"])
    with LOCK:
        rec = Recorder()
        for w in words:
            rec.observe(w)
        final = rec.finish()
    replay_commits = [list(c) for c in final["commits"]]
    saved_commits = [list(c) for c in row["commits"]]
    for s in rec.steps:
        s["wrong"] = bool(s["new_commit"]) and s["commit"] != req["target"]
    return {"words": words, "steps": rec.steps, "final": final, "row": row,
            "dataset": key, "dataset_name": d["name"], "log_file": d["log_file"],
            "target_product": product_card(req["target"]),
            "saved_final_product": product_card(row["final"]),
            "replay": {"commits": replay_commits, "final": final["final"],
                       "matches_saved": replay_commits == saved_commits
                       and final["final"] == row["final"]}}


def boot():
    return {"examples": EXAMPLES,
            "duel_examples": [{"name": e["name"], "text": e["text"], "target": e["target"],
                               "gold": e["gold"]} for e in DUEL_EXAMPLES],
            "threshold": A.CONFIG["commit_threshold"], "catalogue": len(CATALOGUE),
            "fetch_k": A.CONFIG["fetch_k"], "quiet_words": A.CONFIG["quiet_words"],
            "eager_config": {k: list(v) if isinstance(v, tuple) else v
                             for k, v in EAGER_CONFIG.items()},
            "tradeoff": [{"key": k, "name": n} for k, n, _, _ in TRADEOFF],
            "dev_requests": len(DEV), "explore_datasets": list(EXPLORE_SOURCES)}


# ------------------------------------------------------------------------------------------
# HTTP
# ------------------------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            page = PAGE.replace("__BOOT__", json.dumps(boot(), ensure_ascii=False))
            self._send(200, page, "text/html; charset=utf-8")
        elif path == "/api/results":
            self._json(results())
        elif path == "/api/explore":
            q = parse_qs(urlparse(self.path).query)
            self._json(explore_list(q.get("dataset", ["dev"])[0]))
        elif path == "/results/speed_safety.svg":          # the only file served from disk
            try:
                self._send(200, (HERE / "results" / "speed_safety.svg").read_bytes(), "image/svg+xml")
            except OSError:
                self._send(404, "not found", "text/plain")
        else:
            self._send(404, "not found", "text/plain")

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            text = str(body.get("text", ""))[:1000]
            if self.path == "/api/run":
                return self._json(run_request(text))
            if self.path == "/api/duel":
                return self._json(duel(text))
            if self.path == "/api/tradeoff":
                return self._json(tradeoff(body.get("level", 2)))
            if self.path == "/api/explore/replay":
                return self._json(explore_replay(str(body.get("dataset", "")), str(body.get("id", ""))))
            if self.path == "/api/live":
                return self._json(live(body.get("action"), body.get("session"), body.get("word")))
            self._send(404, "not found", "text/plain")
        except Exception as exc:                            # show the error on the page
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def log_message(self, *args):                           # keep the terminal quiet
        pass


PAGE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Prescient — Your product, before your full stop.</title>
<style>
:root{
  --bg:#06080b; --s1:#0c1015; --s2:#11161d; --line:#1b222b; --line2:#26303b;
  --ink:#f3f6f9; --ink2:#c4ccd5; --muted:#7f8b98; --dim:#46505c; --faint:#2a323c;
  --go:#3ee08f; --go-bg:rgba(62,224,143,.10); --go-glow:rgba(62,224,143,.45);
  --amb:#ffb547; --amb-bg:rgba(255,181,71,.10);
  --red:#ff5d5d; --red-bg:rgba(255,93,93,.12);
  --t:200ms cubic-bezier(.2,.7,.2,1);
  --sans:-apple-system,BlinkMacSystemFont,"SF Pro Display","Inter","Segoe UI",system-ui,Roboto,sans-serif;
  --mono:"SF Mono",ui-monospace,Menlo,Consolas,monospace;
}
*{box-sizing:border-box}
html,body{margin:0;background:var(--bg);color:var(--ink);font:20px/1.4 var(--sans);
  -webkit-font-smoothing:antialiased}
button,input{font:inherit;color:inherit}
.wrap{max-width:1780px;margin:0 auto;padding:18px 44px 28px}
.num{font-family:var(--mono);font-variant-numeric:tabular-nums}

/* header */
header{display:flex;align-items:center;justify-content:space-between;gap:30px;
  padding-bottom:14px;border-bottom:1px solid var(--line)}
.brand{display:flex;align-items:center;gap:26px}
.brand h1{margin:0;font-size:46px;font-weight:700;letter-spacing:-.025em;line-height:1}
.brand h1 i{font-style:normal;color:var(--go)}
.brand .tag{font-size:22px;color:var(--ink2);margin-top:0;letter-spacing:-.005em}
.brand .motto{font-size:13px;color:var(--muted);margin-top:3px;letter-spacing:.14em;text-transform:uppercase}
nav{display:flex;gap:6px;background:var(--s1);border:1px solid var(--line);border-radius:14px;padding:5px}
nav button{background:none;border:0;padding:11px 20px;border-radius:10px;font-size:16px;
  font-weight:600;letter-spacing:.08em;color:var(--muted);cursor:pointer;transition:all var(--t)}
nav button:hover{color:var(--ink)}
nav button.on{background:var(--ink);color:#000}
nav kbd{font:12px var(--mono);opacity:.5;margin-left:6px}

/* controls */
.bar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:14px 0 0;min-height:52px}
.btn{background:var(--s2);border:1px solid var(--line2);border-radius:11px;padding:11px 20px;
  font-size:17px;font-weight:600;cursor:pointer;transition:all var(--t)}
.btn:hover{border-color:var(--muted)}
.btn.primary{background:var(--ink);color:#000;border-color:var(--ink)}
.btn.ex{font-weight:500;color:var(--ink2)}
.btn.ex.on{border-color:var(--ink);color:var(--ink);background:var(--s1)}
.sep{width:1px;height:32px;background:var(--line2);margin:0 6px}
.speed{display:flex;align-items:center;gap:10px;color:var(--muted);font-size:15px}
input[type=range]{accent-color:var(--ink);width:150px}
.live-in{flex:1;min-width:520px;background:var(--s1);border:1px solid var(--line2);border-radius:14px;
  padding:16px 20px;font-size:26px;outline:none;transition:border-color var(--t)}
.live-in:focus{border-color:var(--ink2)}
.hint{color:var(--muted);font-size:15px}
/* voice mode */
.mic{display:flex;align-items:center;gap:10px;background:var(--s2);border:1px solid var(--line2);border-radius:14px;
  padding:14px 22px;font-size:18px;font-weight:700;letter-spacing:.06em;cursor:pointer;transition:all var(--t)}
.mic:hover{border-color:var(--muted)}
.mic svg{width:22px;height:22px}
.mic.on{background:var(--red);border-color:var(--red);color:#fff;box-shadow:0 0 0 6px var(--red-bg)}
.mic[disabled]{opacity:.4;cursor:not-allowed}
.voice{display:none;align-items:center;gap:16px;width:100%;margin-top:4px;padding:12px 18px;
  background:var(--s1);border:1px solid var(--line);border-radius:14px}
.voice.show{display:flex}
.listen{display:flex;align-items:center;gap:9px;font-size:15px;font-weight:700;letter-spacing:.12em;
  text-transform:uppercase;color:var(--dim);white-space:nowrap}
.listen i{width:12px;height:12px;border-radius:50%;background:var(--dim)}
.voice.live .listen{color:var(--red)}
.voice.live .listen i{background:var(--red);animation:pulse 1.1s ease-in-out infinite}
@keyframes pulse{0%,100%{box-shadow:0 0 0 0 rgba(255,93,93,.6)}50%{box-shadow:0 0 0 9px rgba(255,93,93,0)}}
.tx{flex:1;font-size:22px;min-height:30px;color:var(--ink)}
.tx .interim{color:var(--muted);font-style:italic}
.tx .ph{color:var(--dim)}
.vnote{font-size:13px;color:var(--muted);max-width:330px;line-height:1.35}
.vwarn{color:var(--amb);font-size:16px;font-weight:600}
.hint b{color:var(--ink2);font-weight:600}

/* request stream */
.stream{min-height:84px;display:flex;flex-wrap:wrap;gap:8px 8px;align-items:center;
  padding:18px 0 2px}
.w{position:relative;font-size:32px;font-weight:600;letter-spacing:-.01em;padding:4px 12px;
  border-radius:12px;color:var(--faint);border:1.5px solid transparent;
  transition:color var(--t),background var(--t),border-color var(--t),box-shadow var(--t),opacity var(--t)}
.w.heard{color:var(--ink)}
.w.now{border-color:var(--line2);background:var(--s2)}
.w.cue{color:var(--amb);background:var(--amb-bg);border-color:rgba(255,181,71,.35)}
.w.fix{color:var(--amb)}
.w.struck{color:var(--dim);text-decoration:line-through;text-decoration-color:var(--amb);
  text-decoration-thickness:3px}
.w.commit{color:#04140b;background:var(--go);border-color:var(--go);
  box-shadow:0 0 0 6px var(--go-bg),0 0 38px var(--go-glow)}
.w.wrongc{color:#fff;background:var(--red);border-color:var(--red);box-shadow:0 0 30px rgba(255,93,93,.5)}
.w.old-commit{box-shadow:none;background:transparent;color:var(--go);border-color:var(--go)}
.w .flag{position:absolute;left:50%;top:-22px;transform:translateX(-50%);font:700 11px var(--sans);
  letter-spacing:.14em;color:var(--go);white-space:nowrap}
.empty-note{color:var(--dim);font-size:26px}

/* correction banner */
.banner-slot{height:60px;position:relative;overflow:hidden;margin-bottom:4px}
.banner{position:absolute;inset:6px 0;display:flex;align-items:center;gap:16px;padding:0 22px;
  border-radius:14px;background:var(--amb-bg);border:1px solid rgba(255,181,71,.4);color:var(--amb);
  font-size:22px;font-weight:600;transform:translateY(-120%);opacity:0;
  transition:transform 220ms cubic-bezier(.2,.8,.2,1),opacity 220ms}
.banner.show{transform:none;opacity:1}
.banner .tag2{font-size:13px;letter-spacing:.16em;text-transform:uppercase;background:var(--amb);
  color:#1d1200;padding:4px 9px;border-radius:7px}
.banner s{color:var(--ink2);text-decoration-thickness:2px}
.banner .arrow{color:var(--ink2)}
.banner .sm{margin-left:auto;font-size:15px;font-weight:500;color:var(--muted)}

/* pipeline */
.pipe{display:grid;grid-template-columns:1.35fr 34px 1fr 34px .95fr;align-items:stretch}
.stage{background:var(--s1);border:1.5px solid var(--line);border-radius:18px;padding:16px 20px;
  min-height:214px;transition:border-color var(--t),background var(--t),box-shadow var(--t),opacity var(--t);opacity:.55}
.stage.done{opacity:.85}
.stage.on{opacity:1;border-color:var(--ink2);box-shadow:0 0 0 1px var(--ink2) inset}
.stage.on.amb{border-color:var(--amb);box-shadow:0 0 0 1px var(--amb) inset,0 0 40px rgba(255,181,71,.12)}
.stage.on.risk{border-color:var(--red);box-shadow:0 0 0 1px var(--red) inset}
.stage.on.go{border-color:var(--go);background:linear-gradient(180deg,rgba(62,224,143,.10),var(--s1) 70%);
  box-shadow:0 0 0 1px var(--go) inset,0 0 50px rgba(62,224,143,.18)}
.stage h3{margin:0;display:flex;align-items:baseline;gap:12px;font-size:16px;letter-spacing:.2em;
  font-weight:700;color:var(--ink2)}
.stage h3 .n{font:600 14px var(--mono);color:var(--muted);letter-spacing:0}
.stage .sub{font-size:15px;color:var(--muted);margin:3px 0 12px}
.link{display:flex;align-items:center;justify-content:center}
.link span{display:block;width:100%;height:2px;background:var(--line2);position:relative;transition:background var(--t)}
.link span::after{content:"";position:absolute;right:-1px;top:-5px;border:6px solid transparent;
  border-left:8px solid var(--line2);border-right:0;transition:border-color var(--t)}
.link.lit span{background:var(--ink2)} .link.lit span::after{border-left-color:var(--ink2)}
.link.go span{background:var(--go)} .link.go span::after{border-left-color:var(--go)}

.chips{display:grid;grid-template-columns:1.25fr 1fr .8fr 1fr 1fr;gap:8px}
.chip{background:var(--s2);border:1.5px solid var(--line);border-radius:12px;padding:9px 10px;
  min-height:72px;transition:border-color var(--t),background var(--t)}
.chip .k{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted)}
.chip .v{font-size:19px;font-weight:650;margin-top:5px;line-height:1.15;overflow-wrap:normal;word-break:normal;hyphens:none;transition:color var(--t)}
.chip .o{font-size:14px;color:var(--muted);text-decoration:line-through;text-decoration-color:var(--amb);margin-top:2px}
.chip.empty .v{color:var(--dim);font-weight:400}
.chip.set{border-color:var(--ink2);background:#151b23}
.chip.fixed{border-color:var(--amb)} .chip.fixed .v{color:var(--amb)}
.pool{margin-top:12px;font-size:15px;color:var(--muted)}
.pool b{color:var(--ink2);font-weight:600}

.hold{font-size:26px;font-weight:700;letter-spacing:-.01em;line-height:1.15;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hold.amb{color:var(--amb)} .hold.go{color:var(--go)} .hold.risk{color:var(--red)} .hold.idle{color:var(--dim)}
.hold-d{font-size:15px;color:var(--muted);margin:5px 0 12px;min-height:21px}
.checks{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:1fr 1fr;gap:7px 14px;align-content:start}
.checks li{font-size:14px;color:var(--muted);display:flex;gap:8px;align-items:center;transition:color var(--t)}
.checks li i{width:9px;height:9px;border-radius:50%;background:var(--dim);flex:none;transition:background var(--t)}
.checks li.ok i{background:var(--go)} .checks li.ok{color:var(--ink2)}
.checks li.no i{background:var(--amb)} .checks li.no{color:var(--amb)}
.checks li.no.risk i{background:var(--red)} .checks li.no.risk{color:var(--red)}

.conf-row{display:flex;align-items:baseline;gap:12px}
.conf{font:700 54px var(--mono);letter-spacing:-.04em;line-height:1;transition:color var(--t)}
.conf.go{color:var(--go)}
.conf-l{font-size:15px;color:var(--muted)}
.meter{position:relative;height:12px;border-radius:6px;background:var(--s2);margin:26px 0 4px;overflow:visible}
.meter>div{height:100%;width:0;border-radius:6px;background:var(--ink2);transition:width var(--t),background var(--t)}
.meter.go>div{background:var(--go)}
.meter .thr{position:absolute;top:-6px;bottom:-6px;width:2px;background:var(--ink);opacity:.7}
.meter .thr b{position:absolute;top:-20px;left:50%;transform:translateX(-50%);font:600 12px var(--mono);color:var(--muted)}
.cstat{font-size:22px;font-weight:700;margin-top:12px;min-height:30px;transition:color var(--t)}
.cstat.go{color:var(--go)}
.cwhy{font-size:15px;color:var(--muted);margin-top:4px;min-height:20px}

/* funnel + chart */
.row2{display:grid;grid-template-columns:1fr 1.9fr;gap:16px;margin-top:14px}
.panel{background:var(--s1);border:1.5px solid var(--line);border-radius:18px;padding:14px 22px}
.panel h4{margin:0 0 6px;font-size:14px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted);font-weight:700}
.funnel-n{display:flex;align-items:baseline;gap:16px}
.funnel-n .big{font:700 66px var(--mono);letter-spacing:-.05em;line-height:1;transition:color var(--t)}
.funnel-n .big.go{color:var(--go)}
.funnel-n .lbl{font-size:20px;color:var(--ink2)}
.fbar{height:10px;background:var(--s2);border-radius:5px;margin:10px 0 10px;overflow:hidden}
.fbar>div{height:100%;width:100%;background:var(--ink2);border-radius:5px;transition:width 240ms cubic-bezier(.2,.7,.2,1),background var(--t)}
.fbar.go>div{background:var(--go)}
.trail{font:500 19px var(--mono);color:var(--dim);display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center}
.trail b{color:var(--ink2);font-weight:600} .trail b.last{color:var(--ink)} .trail b.last.go{color:var(--go)}
.trail em{font-style:normal;color:var(--dim)}
.fnote{font-size:14px;color:var(--muted);margin-top:10px}
.legend{display:flex;gap:22px;font-size:14px;color:var(--muted);margin-bottom:4px}
.legend i{display:inline-block;width:18px;height:3px;vertical-align:middle;margin-right:7px;border-radius:2px}
svg.chart{width:100%;height:150px;display:block}

/* reveal */
.reveal{margin-top:14px;border-radius:22px;border:1.5px solid var(--go);
  background:radial-gradient(1200px 300px at 0% 0%,rgba(62,224,143,.13),transparent 60%),var(--s1);
  padding:16px 26px;display:none;opacity:0;transform:translateY(10px);
  transition:opacity 240ms,transform 240ms cubic-bezier(.2,.8,.2,1)}
.reveal.show{display:block}
.reveal.in{opacity:1;transform:none}
.reveal.neutral{border-color:var(--ink2);background:var(--s1)}
.reveal .kick{font-size:14px;letter-spacing:.2em;text-transform:uppercase;font-weight:700;color:var(--go)}
.reveal.neutral .kick{color:var(--ink2)}
.rv-top{display:grid;grid-template-columns:1.15fr 1fr;gap:30px;align-items:center}
.reveal .found{font-size:34px;font-weight:700;letter-spacing:-.02em;margin:2px 0 0;line-height:1.15}
.reveal .found em{font-style:normal;color:var(--go)}
.reveal.neutral .found em{color:var(--ink)}
.prod{display:flex;align-items:center;justify-content:space-between;gap:24px;
  padding-left:30px;border-left:1px solid var(--line)}
.prod .t{font-size:26px;font-weight:650;line-height:1.2}
.prod .m{display:flex;gap:10px;margin-top:8px;flex-wrap:wrap}
.pill{border:1px solid var(--line2);border-radius:999px;padding:3px 12px;font-size:16px;color:var(--ink2)}
.prod .price{font:700 40px var(--mono);letter-spacing:-.03em;white-space:nowrap}
.prod .pid{font:500 15px var(--mono);color:var(--muted);text-align:right}
.receipt{display:grid;grid-template-columns:1fr 2.7fr .7fr .7fr;gap:12px;margin-top:12px}
.receipt>div{background:var(--s2);border:1px solid var(--line);border-radius:14px;padding:10px 16px}
.receipt .k{font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:var(--muted);margin-bottom:5px}
.receipt .v{font-size:15px;color:var(--ink2);line-height:1.5}
.receipt .two{columns:2;column-gap:18px}
.receipt .v b{color:var(--ink);font-weight:600}
.receipt .ok{color:var(--go)}
.receipt .nw{white-space:nowrap;margin-right:10px}
.receipt .bigv{font:700 26px var(--mono);color:var(--ink);line-height:1.1}
.receipt .bigv small{font:500 14px var(--sans);color:var(--muted);margin-left:8px}
.fin{margin-top:4px;font-size:16px;color:var(--ink2);min-height:22px}
.fin .ok{color:var(--go);font-weight:700} .fin .bad{color:var(--red);font-weight:700}

/* duel */
.duel-ref{font-size:16px;color:var(--muted);margin:6px 0 14px}
.duel-ref b{color:var(--ink2);font-weight:600}
.lanes{display:grid;gap:16px}
.lane{display:grid;grid-template-columns:270px 1fr 300px;gap:24px;align-items:center;background:var(--s1);
  border:1.5px solid var(--line);border-radius:18px;padding:20px 24px;transition:border-color var(--t),box-shadow var(--t),background var(--t)}
.lane.go{border-color:var(--go)}
.lane.flash{animation:flash 900ms ease-out}
@keyframes flash{0%{background:rgba(255,93,93,.35);border-color:var(--red)}100%{background:var(--s1)}}
.lane.bad{border-color:var(--red)}
.lane h5{margin:0;font-size:26px;letter-spacing:-.01em}
.lane .r{font-size:14px;color:var(--muted);margin-top:4px;line-height:1.35}
.lane .ws{display:flex;flex-wrap:wrap;gap:6px}
.lane .w{font-size:21px;padding:3px 9px;border-radius:9px}
.lane .w.commit{box-shadow:0 0 0 4px var(--go-bg),0 0 22px var(--go-glow)}
.status{text-align:right}
.status .s1{font-size:22px;font-weight:700;transition:color var(--t)}
.status .s2{font-size:14px;color:var(--muted);margin-top:4px;min-height:18px}
.status .go{color:var(--go)} .status .bad{color:var(--red)} .status .idle{color:var(--dim)}
.wrongtag{display:inline-block;background:var(--red);color:#fff;font-weight:800;border-radius:8px;
  padding:3px 10px;font-size:17px;margin-top:6px}
.score-row{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:16px}
.sc{background:var(--s1);border:1.5px solid var(--line);border-radius:16px;padding:16px 20px;opacity:0;transform:translateY(6px);
  transition:opacity 240ms,transform 240ms}
.sc.in{opacity:1;transform:none}
.sc .k{font-size:13px;letter-spacing:.16em;text-transform:uppercase;color:var(--muted)}
.sc .bigv{font:700 44px var(--mono);letter-spacing:-.03em;margin:4px 0}
.sc .d{font-size:15px;color:var(--ink2);line-height:1.55}
.sc.win{border-color:var(--go)} .sc.win .bigv{color:var(--go)}
.sc .bad{color:var(--red)} .sc .ok{color:var(--go)}
.own{display:flex;gap:10px;margin-top:4px}
.own input{flex:1;background:var(--s1);border:1px solid var(--line2);border-radius:11px;padding:10px 16px;font-size:17px;outline:none}

/* results */
.hero{display:grid;grid-template-columns:1.4fr 1fr 1fr 1fr;gap:18px;margin-top:22px}
.hcard{background:var(--s1);border:1.5px solid var(--line);border-radius:20px;padding:24px 26px}
.hcard.main{border-color:var(--go);background:radial-gradient(600px 200px at 0 0,rgba(62,224,143,.12),transparent 70%),var(--s1)}
.hcard .k{font-size:13px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted);font-weight:700}
.hcard .v{font:700 76px var(--mono);letter-spacing:-.05em;line-height:1.05;margin-top:8px}
.hcard.main .v{color:var(--go)}
.hcard .v small{display:block;font:600 22px var(--sans);color:var(--muted);letter-spacing:0;margin-top:2px}
.hcard .d{font-size:16px;color:var(--ink2);margin-top:6px}
.honest{margin-top:16px;font-size:17px;color:var(--ink2);background:var(--s1);border:1px solid var(--line);
  border-left:4px solid var(--amb);border-radius:12px;padding:14px 20px}
.honest b{color:var(--ink)}
.rgrid{display:grid;grid-template-columns:1.05fr 1fr;gap:20px;margin-top:18px}
table.st{width:100%;border-collapse:collapse;font-size:17px}
table.st th{text-align:left;font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);
  font-weight:700;padding:8px 10px;border-bottom:1px solid var(--line2)}
table.st td{padding:10px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
table.st td.n{font-family:var(--mono);text-align:right}
table.st th.n{text-align:right}
table.st tr.me td{color:var(--go);font-weight:700}
table.st td.bad{color:var(--red)}
.sbar{height:8px;border-radius:4px;background:var(--ink2);opacity:.6}
tr.me .sbar{background:var(--go);opacity:1}
.paper{background:#fff;border-radius:14px;padding:10px}
.paper img{width:100%;display:block}
.src{font-size:13px;color:var(--muted);margin-top:10px;font-family:var(--mono)}

/* trade-off */
.to-top{display:grid;grid-template-columns:1fr;gap:6px;margin-top:22px}
.to-q{font-size:30px;font-weight:700;letter-spacing:-.02em}
.to-q em{font-style:normal;color:var(--go)}
.to-sl{position:relative;margin:22px 8px 0}
.to-sl input{width:100%;accent-color:var(--go);height:28px;cursor:pointer}
.to-ticks{display:grid;grid-template-columns:repeat(5,1fr);margin:6px -8px 0;text-align:center}
.to-ticks button{background:none;border:0;color:var(--muted);font:600 16px var(--sans);cursor:pointer;padding:6px 2px}
.to-ticks button.on{color:var(--ink)}
.to-ticks button .mk{display:block;font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--go);margin-top:2px}
.to-ends{display:flex;justify-content:space-between;font-size:13px;letter-spacing:.16em;text-transform:uppercase;color:var(--dim);margin-top:2px}
.to-mean{margin-top:14px;font-size:18px;color:var(--ink2);min-height:26px}
.to-mean code{font:500 16px var(--mono);color:var(--ink);background:var(--s2);border:1px solid var(--line2);border-radius:7px;padding:1px 7px}
.to-grid{display:grid;grid-template-columns:1fr 1.25fr;gap:20px;margin-top:18px;align-items:start}
.to-cards{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.to-card{background:var(--s1);border:1.5px solid var(--line);border-radius:16px;padding:18px 20px 20px}
.to-card .k{font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:var(--muted);font-weight:700}
.to-card .v{font:700 54px var(--mono);letter-spacing:-.04em;line-height:1.1;margin-top:6px;transition:color var(--t)}
.to-card .d{font-size:14px;color:var(--muted)}
.to-card .v.go{color:var(--go)} .to-card .v.bad{color:var(--red)}
.to-cards.busy .v{opacity:.45}
svg.to-chart{width:100%;height:auto;display:block}
#to-dot{transition:transform 450ms cubic-bezier(.2,.8,.2,1)}
.view{display:none} .view.on{display:block}
/* EXPLORER */
.xp-bar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:16px}
.seg{display:flex;gap:4px;background:var(--s1);border:1px solid var(--line2);border-radius:12px;padding:4px}
.seg button{background:none;border:0;border-radius:9px;padding:8px 16px;font-size:16px;font-weight:600;color:var(--muted);cursor:pointer}
.seg button.on{background:var(--ink);color:#000}
.xp-bar select,.xp-bar input{background:var(--s1);border:1px solid var(--line2);border-radius:11px;padding:10px 14px;font-size:16px;outline:none;color:var(--ink)}
.xp-bar input{flex:1;min-width:260px}
.ochips{display:flex;gap:8px;flex-wrap:wrap}
.ochip{border:1px solid var(--line2);background:var(--s1);border-radius:999px;padding:7px 14px;font-size:15px;font-weight:600;color:var(--ink2);cursor:pointer}
.ochip b{font-family:var(--mono);margin-left:6px;color:var(--muted)}
.ochip.on{border-color:var(--ink);color:var(--ink);background:var(--s2)}
.ochip.early.on{border-color:var(--go);color:var(--go)} .ochip.late.on{border-color:var(--ink2)}
.ochip.wrong_commit.on,.ochip.wrong_final.on{border-color:var(--red);color:var(--red)}
.xp-note{margin-top:12px;font-size:15px;color:var(--ink2);background:var(--s1);border:1px solid var(--line);border-radius:12px;padding:10px 16px}
.xp-note.frozen{border-color:rgba(255,181,71,.45);background:var(--amb-bg)}
.xp-note b{color:var(--ink)} .xp-note .ok{color:var(--go)} .xp-note .bad{color:var(--red)}
.xp-sum{margin:12px 2px 6px;font-size:15px;color:var(--muted)}
.xp-sum b{color:var(--ink2);font-family:var(--mono)}
.xp-list{background:var(--s1);border:1.5px solid var(--line);border-radius:16px;max-height:calc(100vh - 360px);min-height:300px;overflow:auto}
table.xp{width:100%;border-collapse:collapse;font-size:16px}
table.xp th{position:sticky;top:0;background:var(--s2);text-align:left;font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);padding:10px 12px;z-index:1}
table.xp td{padding:9px 12px;border-top:1px solid var(--line);vertical-align:top}
table.xp tr.r{cursor:pointer} table.xp tr.r:hover td{background:var(--s2)}
table.xp td.id,table.xp td.n{font-family:var(--mono);white-space:nowrap;color:var(--ink2)}
table.xp td.n{text-align:right} table.xp th.n{text-align:right}
table.xp td.tx{color:var(--ink);line-height:1.35}
table.xp mark{background:rgba(255,181,71,.3);color:inherit;border-radius:3px}
.tg{display:inline-block;font-size:12px;color:var(--muted);border:1px solid var(--line2);border-radius:999px;padding:1px 8px;margin:3px 4px 0 0}
.ob{display:inline-block;font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;border-radius:7px;padding:3px 8px;margin:0 4px 3px 0;white-space:nowrap}
.ob.early{background:var(--go-bg);color:var(--go)} .ob.late{background:var(--s2);color:var(--ink2);border:1px solid var(--line2)}
.ob.wrong_commit,.ob.wrong_final{background:var(--red-bg);color:var(--red)}
/* case card above the replay */
.case{display:none;margin-top:10px;background:var(--s1);border:1.5px solid var(--line);border-radius:18px;padding:12px 20px}
.case.show{display:block}
.case-h{display:flex;align-items:center;gap:12px;flex-wrap:wrap;font-size:15px;color:var(--muted)}
.case-h .cid{font:700 18px var(--mono);color:var(--ink)}
.case-g{display:grid;grid-template-columns:1fr 1fr 1.25fr;gap:12px;margin-top:10px}
.case-g>div{background:var(--s2);border:1px solid var(--line);border-radius:14px;padding:10px 14px}
.case-g .k{font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:var(--muted);margin-bottom:4px}
.case-g .t{font-size:18px;font-weight:650;line-height:1.25}
.case-g .m{font-size:14px;color:var(--ink2);margin-top:3px}
.case-g .pid{font:600 14px var(--mono);color:var(--muted)}
.case-g .ag.ok{border-color:rgba(62,224,143,.5)} .case-g .ag.bad{border-color:var(--red)}
.case-g .verdict{font-weight:700;font-size:14px;margin-top:4px} .verdict.ok{color:var(--go)} .verdict.bad{color:var(--red)}
table.fs{width:100%;border-collapse:collapse;font-size:14px}
table.fs td{padding:2px 6px 2px 0;color:var(--ink2);white-space:nowrap}
table.fs td.k2{color:var(--muted)} table.fs td.bad{color:var(--red);font-weight:700}
.case-f{font-size:14px;color:var(--muted);margin-top:8px}
.case-f .ok{color:var(--go)} .case-f .bad{color:var(--red)} .case-f b{color:var(--ink2)}
.reveal.bad{border-color:var(--red);background:var(--red-bg)}
.reveal.bad .kick,.reveal.bad .found em{color:var(--red)}
.fin .gold{color:var(--muted)}
@media (prefers-reduced-motion: reduce){*,*::before,*::after{transition:none!important;animation:none!important}}
.err{color:var(--red);font-size:18px}
</style></head>
<body><div class="wrap">

<header>
  <div class="brand">
    <h1>Prescient<i>.</i></h1>
    <div><div class="tag">Your product, before your full stop.</div>
    <div class="motto">Prediction is not permission to act.</div></div>
  </div>
  <nav id="tabs">
    <button data-v="live">LIVE TYPE<kbd>1</kbd></button>
    <button data-v="examples" class="on">EXAMPLES<kbd>2</kbd></button>
    <button data-v="duel">STRATEGY DUEL<kbd>3</kbd></button>
    <button data-v="results">RESULTS<kbd>4</kbd></button>
    <button data-v="tradeoff">TRADE-OFF<kbd>5</kbd></button>
    <button data-v="explore">EXPLORER<kbd>6</kbd></button>
  </nav>
</header>

<!-- ===================== single-agent view (LIVE TYPE + EXAMPLES) ===================== -->
<section class="view on" id="v-single">
  <div class="bar" id="bar-examples">
    <span id="ex-buttons" style="display:flex;gap:8px;flex-wrap:wrap"></span>
    <button class="btn" id="xp-back" style="display:none">&larr; All requests</button>
    <span class="sep"></span>
    <button class="btn primary" id="play">&#9654;&nbsp; Play</button>
    <button class="btn" id="next">Next word &rarr;</button>
    <button class="btn" id="reset">Reset</button>
    <label class="speed">Speed <input type="range" id="speed" min="150" max="1500" step="50" value="650"><span id="speedv" class="num"></span></label>
  </div>
  <div class="bar" id="bar-live" style="display:none">
    <input class="live-in" id="live-in" placeholder="Type a request… e.g. white Loomsy sneakers size 9 for office" autocomplete="off" spellcheck="false">
    <button class="mic" id="mic" title="Speak your request (voice mode)"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v4"/></svg><span id="mic-t">VOICE</span></button>
    <div class="hint"><b>Space</b> sends each finished word<br><b>Enter</b> = full stop &nbsp;·&nbsp; <b>Esc</b> clears</div>
    <div class="voice" id="voice">
      <div class="listen" id="listen"><i></i><span id="listen-t">Mic off</span></div>
      <div class="tx" id="tx"><span class="ph">Live transcript appears here…</span></div>
      <div class="vnote">Voice uses the browser's own speech-to-text (Chrome, needs internet). The agent itself stays fully offline: it only ever receives the finished words.</div>
    </div>
  </div>

  <div class="case" id="case"></div>
  <div class="stream" id="stream"></div>
  <div class="banner-slot"><div class="banner" id="banner"></div></div>

  <div class="pipe">
    <div class="stage" id="st-a">
      <h3><span class="n">01</span>ANTICIPATE</h3>
      <div class="sub">Filters predicted from every word — never tighter than what was said</div>
      <div class="chips" id="chips"></div>
      <div class="pool" id="pool">No search yet.</div>
    </div>
    <div class="link" id="l1"><span></span></div>
    <div class="stage" id="st-s">
      <h3><span class="n">02</span>STABILIZE</h3>
      <div class="sub">Has the request settled? Every check from the agent's own state</div>
      <div class="hold idle" id="hold">Listening…</div>
      <div class="hold-d" id="hold-d"></div>
      <ul class="checks" id="checks"></ul>
    </div>
    <div class="link" id="l2"><span></span></div>
    <div class="stage" id="st-c">
      <h3><span class="n">03</span>COMMIT</h3>
      <div class="sub">Exactly 1 product left and confidence &ge; <span id="thr-t"></span></div>
      <div class="conf-row"><div class="conf" id="conf">0%</div><div class="conf-l">confidence</div></div>
      <div class="meter" id="meter"><div></div><span class="thr" id="thr"><b></b></span></div>
      <div class="cstat" id="cstat">Waiting</div>
      <div class="cwhy" id="cwhy"></div>
    </div>
  </div>

  <div class="row2">
    <div class="panel">
      <h4>Products that still fit</h4>
      <div class="funnel-n"><div class="big num" id="fn">–</div><div class="lbl" id="fl">of <span class="num" id="fcat"></span> in the catalogue</div></div>
      <div class="fbar" id="fbar"><div></div></div>
      <div class="trail" id="trail"></div>
      <div class="fnote" id="fnote"></div>
    </div>
    <div class="panel">
      <h4>Per word</h4>
      <div class="legend"><span><i style="background:var(--ink2)"></i>products left (log scale)</span>
        <span><i style="background:var(--go)"></i>confidence</span>
        <span><i style="background:var(--go);opacity:.4;height:1px;border-top:1px dashed var(--go)"></i>commit threshold</span>
        <span><i style="background:var(--amb);width:9px;height:9px;border-radius:50%"></i>correction</span></div>
      <svg class="chart" id="chart" viewBox="0 0 1100 150" preserveAspectRatio="none"></svg>
    </div>
  </div>

  <div class="reveal" id="reveal">
    <div class="rv-top">
      <div><div class="kick" id="rv-kick">Committed</div><div class="found" id="rv-found"></div><div class="fin" id="rv-fin"></div></div>
      <div class="prod">
        <div><div class="t" id="rv-title"></div><div class="m" id="rv-meta"></div></div>
        <div><div class="price" id="rv-price"></div><div class="pid" id="rv-pid"></div></div>
      </div>
    </div>
    <div class="receipt" id="rv-receipt"></div>
  </div>
</section>

<!-- ===================== STRATEGY DUEL ===================== -->
<section class="view" id="v-duel">
  <div class="bar">
    <span id="duel-buttons" style="display:flex;gap:8px;flex-wrap:wrap"></span>
    <span class="sep"></span>
    <button class="btn primary" id="d-play">&#9654;&nbsp; Play</button>
    <button class="btn" id="d-next">Next word &rarr;</button>
    <button class="btn" id="d-reset">Reset</button>
    <label class="speed">Speed <input type="range" id="d-speed" min="150" max="1500" step="50" value="650"><span id="d-speedv" class="num"></span></label>
  </div>
  <div class="own"><input id="d-own" placeholder="…or type your own request and press Enter to duel it" spellcheck="false"></div>
  <div class="duel-ref" id="d-ref"></div>
  <div class="lanes" id="lanes"></div>
  <div class="score-row" id="scores"></div>
</section>

<!-- ===================== RESULTS ===================== -->
<section class="view" id="v-results">
  <div class="hero" id="hero"></div>
  <div class="honest" id="honest"></div>
  <div class="rgrid">
    <div class="panel"><h4>Commit strategies on dev (300 requests)</h4>
      <table class="st" id="st-table"></table>
      <div class="src">source: results/strategies.csv · made by python3 compare.py --strategies</div></div>
    <div class="panel"><h4>Speed vs safety</h4>
      <div class="paper"><img id="svg" alt="Speed vs safety chart"></div>
      <div class="src">source: results/speed_safety.svg · made by python3 compare.py</div></div>
  </div>
</section>

<!-- ===================== TRADE-OFF ===================== -->
<section class="view" id="v-tradeoff">
  <div class="to-top">
    <div class="to-q">How careful should the agent be? <em>Move the slider.</em></div>
    <div class="fnote">Each position changes <code>agent.CONFIG</code> in memory only, runs the organisers' <code>evaluate.run()</code> on all of dev.jsonl, then puts agent.py's settings back.</div>
  </div>
  <div class="to-sl">
    <input type="range" id="to-range" min="0" max="4" step="1" value="2" aria-label="Eager to extra safe">
    <div class="to-ends"><span>&larr; faster</span><span>safer &rarr;</span></div>
    <div class="to-ticks" id="to-ticks"></div>
  </div>
  <div class="to-mean" id="to-mean"></div>
  <div class="to-grid">
    <div class="to-cards" id="to-cards">
      <div class="to-card"><div class="k">Score</div><div class="v" id="to-score">–</div><div class="d">evaluate.py's SCORE</div></div>
      <div class="to-card"><div class="k">Accuracy</div><div class="v" id="to-acc">–</div><div class="d">final answer right</div></div>
      <div class="to-card"><div class="k">Wrong commits</div><div class="v" id="to-wrong">–</div><div class="d" id="to-wrong-d">each one costs 1.0 point</div></div>
      <div class="to-card"><div class="k">Earliness</div><div class="v" id="to-early">–</div><div class="d">of the request saved</div></div>
    </div>
    <div class="panel"><h4>Speed vs safety · dev · best = bottom right</h4>
      <svg class="to-chart" id="to-chart" viewBox="0 0 640 380"></svg>
    </div>
  </div>
  <div class="src" id="to-src"></div>
</section>

<!-- ===================== EXPLORER ===================== -->
<section class="view" id="v-explore">
  <div class="xp-bar">
    <div class="seg" id="xp-ds"></div>
    <select id="xp-tag" aria-label="Filter by tag"></select>
    <input id="xp-q" placeholder="Search the request text or id…" spellcheck="false" autocomplete="off">
  </div>
  <div class="xp-bar"><div class="ochips" id="xp-out"></div></div>
  <div class="xp-note" id="xp-note"></div>
  <div class="xp-sum" id="xp-sum"></div>
  <div class="xp-list"><table class="xp" id="xp-table"></table></div>
  <div class="src" id="xp-src"></div>
</section>

</div>
<script>
const B = __BOOT__;
const $ = id => document.getElementById(id);
const SLOTS = [["category","Category"],["colour","Colour"],["size","Size"],["brand","Brand"],["max_price","Max price"]];
const SLOT_NAME = Object.fromEntries(SLOTS);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const rupees = n => "₹" + Number(n).toLocaleString("en-IN");
const pct = c => Math.round((c || 0) * 100) + "%";
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
async function post(url, body) {
  const r = await fetch(url, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
  return r.json();
}
const later = (el, cls) => requestAnimationFrame(() => requestAnimationFrame(() => el.classList.add(cls)));

/* ============================== single-agent view ============================== */
const V = { words: [], steps: [], shown: 0, final: null, trail: [] };

function buildChips() {
  $("chips").innerHTML = SLOTS.map(([k, n]) =>
    `<div class="chip empty" id="c-${k}"><div class="k">${n}</div><div class="v">—</div><div class="o"></div></div>`).join("");
}
function resetView(words) {
  V.words = words.slice(); V.steps = []; V.shown = 0; V.final = null; V.trail = [];
  $("stream").innerHTML = words.length ? words.map((w, i) => `<span class="w" id="w${i}">${esc(w)}</span>`).join("")
                                       : `<span class="empty-note">Start typing — each word goes to the agent as you press space.</span>`;
  buildChips();
  $("banner").classList.remove("show");
  for (const id of ["st-a","st-s","st-c"]) $(id).className = "stage";
  $("l1").className = "link"; $("l2").className = "link";
  $("pool").innerHTML = "No search yet.";
  $("hold").className = "hold idle"; $("hold").textContent = "Listening…"; $("hold-d").textContent = "";
  $("checks").innerHTML = "";
  $("conf").textContent = "0%"; $("conf").className = "conf";
  $("meter").className = "meter"; $("meter").firstElementChild.style.width = "0";
  $("cstat").textContent = "Waiting"; $("cstat").className = "cstat"; $("cwhy").textContent = "";
  $("fn").textContent = B.catalogue; $("fn").className = "big num";
  $("fbar").className = "fbar"; $("fbar").firstElementChild.style.width = "100%";
  $("trail").innerHTML = `<b class="last">${B.catalogue}</b>`; $("fnote").textContent = "Nothing said yet: the whole catalogue fits.";
  const rv = $("reveal"); rv.className = "reveal";
  window.scrollTo({top: 0});
  drawChart();
}
function addWord(w) {                       // live mode: a chip appears when the word is sent
  if (!V.words.length) $("stream").innerHTML = "";
  const i = V.words.length; V.words.push(w);
  const s = document.createElement("span"); s.className = "w"; s.id = "w" + i; s.textContent = w;
  $("stream").appendChild(s);
}

function applyStep(s) {
  const i = s.i, n = V.words.length;
  V.steps.push(s); V.shown = V.steps.length;
  // 1. request stream
  document.querySelectorAll("#stream .w.now").forEach(e => e.classList.remove("now"));
  const we = $("w" + i); we.classList.add("heard", "now");
  if (s.cue) we.classList.add("cue");
  for (const j of s.uncue) $("w" + j)?.classList.remove("cue");
  for (const j of s.struck) { const e = $("w" + j); e?.classList.add("struck"); e?.classList.remove("fix"); }
  if (s.corrections.length) for (const j of s.span) $("w" + j)?.classList.add("fix");
  if (s.new_commit) {
    document.querySelectorAll("#stream .w.commit").forEach(e => { e.classList.remove("commit"); e.classList.add("old-commit"); e.querySelector(".flag")?.remove(); });
    we.classList.add(s.wrong ? "wrongc" : "commit");             // s.wrong: Explorer only, vs the gold target
    we.insertAdjacentHTML("beforeend", `<span class="flag">${s.wrong ? "WRONG" : "COMMIT"}</span>`);
  }
  // 2. correction banner
  if (s.corrections.length) {
    const c = s.corrections[s.corrections.length - 1];
    $("banner").classList.remove("show");
    $("banner").innerHTML = `<span class="tag2">Correction detected</span>${esc(SLOT_NAME[c.slot])}: <s>${esc(cap(c.old))}</s> <span class="arrow">&rarr;</span> ${esc(cap(c.new))}` +
      `<span class="sm">word ${i + 1} · "${esc(s.word)}" · the agent replaced the old value, never added to it</span>`;
    later($("banner"), "show");
  }
  // 3. ANTICIPATE chips
  for (const [k] of SLOTS) {
    const el = $("c-" + k), val = s.values[k];
    el.querySelector(".v").textContent = val == null ? "—" : cap(val);
    el.classList.toggle("empty", val == null);
    el.classList.toggle("set", val != null);
  }
  for (const c of s.corrections) { const el = $("c-" + c.slot); el.classList.add("fixed"); el.querySelector(".o").textContent = cap(c.old); }
  const pk = s.pool_key;
  $("pool").innerHTML = s.pool == null ? "No search yet: waiting for a category or brand."
    : `<b>${plural(s.searches, "search")}</b> used · fetched <b>${s.pool}</b> ${esc(pk ? pk[1] : "")} products` +
      (s.left != null ? ` · <b>${s.left}</b> fit` : "") +
      (s.pool_cut ? ` · <span style="color:var(--red)">list may be cut short</span>` : "");
  // 4. STABILIZE
  const open = s.checks.filter(c => !c.ok);
  const corrOpen = open.some(c => c.kind === "correction"), risk = open.some(c => c.kind === "risk");
  $("checks").innerHTML = s.checks.map(c => `<li class="${c.ok ? "ok" : "no"} ${c.kind}"><i></i>${esc(c.ok ? c.name : c.open)}</li>`).join("");
  let holdTxt, holdCls, holdD;
  if (s.left == null) { holdTxt = "Not searched yet"; holdCls = "idle"; holdD = "needs a category or brand to search"; }
  else if (open.length) { holdTxt = "Holding: " + open[0].open; holdCls = risk ? "risk" : (corrOpen ? "amb" : "amb"); holdD = open[0].detail; }
  else { holdTxt = "All clear"; holdCls = "go"; holdD = s.hazard != null ? `chance a correction is still coming: ${(100 * s.hazard).toFixed(1)}% (measured on dev)` : ""; }
  $("hold").textContent = holdTxt; $("hold").className = "hold " + holdCls; $("hold-d").textContent = holdD || "";
  // 5. COMMIT
  const c = s.confidence || 0, hot = c >= B.threshold;
  $("conf").textContent = pct(c); $("conf").className = "conf" + (hot ? " go" : "");
  $("meter").className = "meter" + (hot ? " go" : ""); $("meter").firstElementChild.style.width = pct(c);
  if (s.commit) { $("cstat").textContent = "Committed " + s.commit + (s.new_commit ? "" : " · still holds"); $("cstat").className = "cstat go";
                  $("cwhy").textContent = s.new_commit ? "1 product left · all checks passed · confidence ≥ " + pct(B.threshold) : "re-checked on this word: all checks still pass"; }
  else if (s.holding) { $("cstat").textContent = "Holding commit " + s.holding; $("cstat").className = "cstat go";
                  $("cwhy").textContent = "not re-confirmed on this word: " + (s.waiting || ""); }
  else { $("cstat").textContent = "Waiting"; $("cstat").className = "cstat"; $("cwhy").textContent = s.waiting || ""; }
  // stage lighting
  const committed = !!(s.commit || s.holding);
  $("st-a").className = "stage " + (committed || s.left === 1 ? "done" : "on");
  $("st-s").className = "stage " + (committed ? "done" : (s.left === 1 ? "on " + (risk ? "risk" : (open.length ? "amb" : "")) : ""));
  $("st-c").className = "stage " + (committed ? "on go" : "");
  $("l1").className = "link" + (committed ? " go" : (s.left === 1 ? " lit" : ""));
  $("l2").className = "link" + (committed ? " go" : "");
  // 6. funnel
  $("fn").textContent = s.fit; $("fn").className = "big num" + (s.fit === 1 && committed ? " go" : "");
  const w = s.fit <= 0 ? 0 : Math.max(1.5, 100 * Math.log10(s.fit + 1) / Math.log10(B.catalogue + 1));
  $("fbar").firstElementChild.style.width = w + "%"; $("fbar").className = "fbar" + (s.fit === 1 && committed ? " go" : "");
  if (V.trail[V.trail.length - 1] !== s.fit) V.trail.push(s.fit);
  if (!V.trail.length || V.trail[0] !== B.catalogue) V.trail.unshift(B.catalogue);
  $("trail").innerHTML = V.trail.map((v, k) => `<b class="${k === V.trail.length - 1 ? "last" + (v === 1 && committed ? " go" : "") : ""}">${v}</b>`).join("<em>→</em>");
  $("fnote").textContent = s.left == null ? "Counted over the catalogue with the agent's own filters (display only)."
    : `The agent's own count, from its ${plural(s.searches, "search")}: ${s.left}.`;
  drawChart();
  // 7. reveal on commit
  if (s.new_commit) showReveal(s, i);
}
const cap = v => v == null ? v : String(v).replace(/^\w/, ch => ch.toUpperCase());

function showReveal(s, i) {
  const n = V.words.length, p = s.product || {}, left = n - i - 1, rv = $("reveal");
  rv.className = "reveal show";
  $("rv-kick").textContent = s.replaced ? `Committed · replaced earlier commit ${s.replaced}` : "Committed";
  const live = !!LIVE.sid && MODE === "live";
  $("rv-found").innerHTML = live ? `Found at word <em>${i + 1}</em> — before you finished typing`
    : (left > 0 ? `Found at word <em>${i + 1}</em> of ${n} — <em>${plural(left, "word")}</em> before the full stop`
                : `Found at word <em>${i + 1}</em> of ${n} — on the last word`);
  fillProduct(p, s.commit);
  const checks = s.checks.map(c => `<span class="nw"><span class="ok">✓</span> ${esc(c.name)}</span>`).join(" ");
  $("rv-receipt").innerHTML =
    `<div><div class="k">Filters</div><div class="v">${SLOTS.filter(([k]) => s.values[k] != null).map(([k]) => `<b>${esc(cap(s.values[k]))}</b>`).join(" · ")}</div></div>` +
    `<div><div class="k">Checks passed</div><div class="v"><span class="nw"><span class="ok">✓</span> Exactly 1 product left</span> ${checks}</div></div>` +
    `<div><div class="k">Confidence</div><div class="bigv">${pct(s.confidence)}<small>needs ${pct(B.threshold)}</small></div></div>` +
    `<div><div class="k">Searches used</div><div class="bigv">${s.searches}<small>counted</small></div></div>`;
  $("rv-fin").innerHTML = "";
  if (MODE === "explore" && XP.cur && s.commit !== XP.cur.row.target) {      // compared with the gold target
    rv.classList.add("bad");
    $("rv-kick").textContent = `Wrong commit · not the gold target ${XP.cur.row.target} · −1.0`;
  }
  later(rv, "in");
  revealIntoView();
}
function revealIntoView() {           // only if the screen is too small to show it; never while typing
  if (MODE === "live") return;
  setTimeout(() => { const r = $("reveal").getBoundingClientRect();
    if (r.bottom > innerHeight) $("reveal").scrollIntoView({behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "end"}); }, 60);
}
function fillProduct(p, pid) {
  $("rv-title").textContent = p.title || pid || "—";
  $("rv-meta").innerHTML = p.brand ? [p.brand, cap(p.colour), p.size ? "Size " + p.size : null, cap(p.category)]
    .filter(Boolean).map(x => `<span class="pill">${esc(x)}</span>`).join("") : "";
  $("rv-price").textContent = p.price ? rupees(p.price) : "";
  $("rv-pid").textContent = pid || "";
}
function showFinal(f) {
  V.final = f;
  document.querySelectorAll("#stream .w.now").forEach(e => e.classList.remove("now"));
  const rv = $("reveal");
  if (!f.first_commit_word) {          // never committed early: show the final answer, plainly
    rv.className = "reveal show neutral";
    $("rv-kick").textContent = "Answer at the full stop";
    $("rv-found").innerHTML = f.final ? `Waited for the full stop — <em>never safe to commit earlier</em>` : `No product found`;
    fillProduct(f.final_product || {}, f.final);
    $("rv-receipt").innerHTML = `<div><div class="k">Filters at the end</div><div class="v">${SLOTS.filter(([k]) => f.values[k] != null).map(([k]) => `<b>${esc(cap(f.values[k]))}</b>`).join(" · ")}</div></div>` +
      `<div><div class="k">Why no early commit</div><div class="v">${esc(V.steps.length ? (V.steps[V.steps.length - 1].waiting || "") : "")}</div></div>` +
      `<div><div class="k">Words</div><div class="bigv">${f.words}</div></div><div><div class="k">Searches used</div><div class="bigv">${f.searches}<small>counted</small></div></div>`;
    $("rv-fin").innerHTML = goldLine(f.final);
    later(rv, "in");
    revealIntoView();
    return;
  }
  const same = f.commits.length && f.commits[f.commits.length - 1][1] === f.final;
  $("rv-fin").innerHTML = `Full stop · final answer <b>${esc(f.final)}</b> ` +
    (same ? `<span class="ok">✓ the product it committed to</span>` : `<span class="bad">differs from its last commit</span>`) +
    ` · ${plural(f.searches, "search")} in total` + (goldLine(f.final) ? " · " + goldLine(f.final) : "");
}
function goldLine(final) {               // Explorer only: the final answer against the gold target
  if (MODE !== "explore" || !XP.cur) return "";
  const t = XP.cur.row.target;
  return final === t ? `<span class="ok">✓ matches the gold target ${esc(t)}</span>`
                     : `<span class="bad">✗ gold target was ${esc(t)}</span>`;
}

/* chart: products left (log) + confidence, per word — straight from the steps */
function drawChart() {
  const svg = $("chart"), W = 1100, H = 150, L = 52, R = 52, T = 8, Bm = 24;
  const n = Math.max(V.words.length, MODE === "live" ? 12 : 2), st = V.steps;
  const x = i => L + (n <= 1 ? 0 : i * (W - L - R) / (n - 1));
  const lo = Math.log10(0.4), lmax = Math.log10(B.catalogue);      // "1 left" sits above the 0% line
  const yP = v => T + (1 - (v <= 0 ? 0 : (Math.log10(v) - lo) / (lmax - lo))) * (H - T - Bm);
  const yC = c => T + (1 - c) * (H - T - Bm);
  let g = "";
  for (const v of [1, 10, 100, 1000]) g += `<line x1="${L}" x2="${W - R}" y1="${yP(v)}" y2="${yP(v)}" stroke="#1b222b"/><text x="${L - 10}" y="${yP(v) + 5}" fill="#7f8b98" font-size="14" text-anchor="end" font-family="ui-monospace,Menlo">${v}</text>`;
  for (const c of [0, .5, 1]) g += `<text x="${W - R + 10}" y="${yC(c) + 5}" fill="#3ee08f" font-size="14" font-family="ui-monospace,Menlo" opacity=".8">${Math.round(c * 100)}%</text>`;
  g += `<line x1="${L}" x2="${W - R}" y1="${yC(B.threshold)}" y2="${yC(B.threshold)}" stroke="#3ee08f" stroke-dasharray="5 6" opacity=".45"/>`;
  for (let i = 0; i < V.words.length; i++) g += `<text x="${x(i)}" y="${H - 8}" fill="${i < st.length ? "#7f8b98" : "#2a323c"}" font-size="13" text-anchor="middle" font-family="ui-monospace,Menlo">${i + 1}</text>`;
  if (st.length) {
    const pp = st.map(s => `${x(s.i)},${yP(s.fit)}`).join(" ");
    const cp = st.map(s => `${x(s.i)},${yC(s.confidence || 0)}`).join(" ");
    for (const s of st) if (s.corrections.length) g += `<circle cx="${x(s.i)}" cy="${H - Bm}" r="7" fill="#ffb547"/>`;
    for (const s of st) if (s.new_commit) g += `<line x1="${x(s.i)}" x2="${x(s.i)}" y1="${T}" y2="${H - Bm}" stroke="#3ee08f" stroke-width="2" opacity=".55"/>`;
    g += `<polyline points="${pp}" fill="none" stroke="#c4ccd5" stroke-width="3" stroke-linejoin="round"/>`;
    g += `<polyline points="${cp}" fill="none" stroke="#3ee08f" stroke-width="3" stroke-linejoin="round"/>`;
    for (const s of st) g += `<circle cx="${x(s.i)}" cy="${yP(s.fit)}" r="4" fill="#c4ccd5"/><circle cx="${x(s.i)}" cy="${yC(s.confidence || 0)}" r="${s.new_commit ? 7 : 4}" fill="#3ee08f"/>`;
  }
  svg.innerHTML = g;
}

/* ============================== EXAMPLES mode ============================== */
const EX = { data: null, timer: null, idx: 0 };
function exButtons() {
  $("ex-buttons").innerHTML = B.examples.map((e, k) => `<button class="btn ex${k === EX.idx ? " on" : ""}" data-k="${k}">${esc(e.name)}</button>`).join("");
  $("ex-buttons").querySelectorAll("button").forEach(b => b.onclick = () => { EX.idx = +b.dataset.k; exButtons(); loadExample(true); });
}
async function loadExample(autoplay) {
  stopEx();
  const text = B.examples[EX.idx].text;
  const d = await post("/api/run", {text});
  if (d.error) { $("stream").innerHTML = `<span class="err">${esc(d.error)}</span>`; return; }
  EX.data = d; EX.source = "example"; resetView(d.words);
  if (autoplay) playEx();
}
function exNext() {
  const d = EX.data; if (!d) return false;
  if (V.shown >= d.steps.length) return false;
  applyStep(d.steps[V.shown]);
  if (V.shown >= d.steps.length) { showFinal(d.final); stopEx(); }
  return true;
}
function playEx() {
  if (!EX.data) return;
  if (V.shown >= EX.data.steps.length) resetView(EX.data.words);
  $("play").innerHTML = "&#10073;&#10073;&nbsp; Pause";
  exNext();
  EX.timer = setInterval(() => { if (!exNext()) stopEx(); }, +$("speed").value);
}
function stopEx() { clearInterval(EX.timer); EX.timer = null; $("play").innerHTML = (EX.data && V.shown >= EX.data.steps.length) ? "&#8635;&nbsp; Replay" : "&#9654;&nbsp; Play"; }
$("play").onclick = () => EX.timer ? stopEx() : playEx();
$("next").onclick = () => { stopEx(); if (EX.data && V.shown >= EX.data.steps.length) resetView(EX.data.words); exNext(); };
$("reset").onclick = () => { stopEx(); if (EX.data) resetView(EX.data.words); };
function speedLabel(inp, out) { out.textContent = (+inp.value / 1000).toFixed(2) + " s/word"; }
$("speed").oninput = () => { speedLabel($("speed"), $("speedv")); if (EX.timer) { clearInterval(EX.timer); EX.timer = setInterval(() => { if (!exNext()) stopEx(); }, +$("speed").value); } };

/* ============================== LIVE TYPE mode ============================== */
const LIVE = { sid: null, sent: [], queue: Promise.resolve(), finished: false };
function liveSync(finish) {
  LIVE.queue = LIVE.queue.then(async () => {
    const text = $("live-in").value;
    const toks = text.split(/\s+/).filter(Boolean);
    const done = finish ? toks : (/\s$/.test(text) ? toks : toks.slice(0, -1));
    const prefixOk = done.length >= LIVE.sent.length && LIVE.sent.every((w, k) => done[k] === w);
    const more = done.length > LIVE.sent.length;
    if (!LIVE.sid || !prefixOk || (LIVE.finished && more)) {   // edited earlier words: start a fresh agent
      const r = await post("/api/live", {action: "start"});
      LIVE.sid = r.session; LIVE.sent = []; LIVE.finished = false; resetView([]);
    }
    if (LIVE.finished) return;
    for (let k = LIVE.sent.length; k < done.length; k++) {
      addWord(done[k]);
      const r = await post("/api/live", {action: "word", session: LIVE.sid, word: done[k]});
      if (r.error) { $("hold-d").textContent = r.error; return; }
      LIVE.sent.push(done[k]);
      applyStep(r.step);
    }
    if (finish && LIVE.sent.length && !LIVE.finished) {
      const r = await post("/api/live", {action: "finish", session: LIVE.sid});
      LIVE.finished = true;
      if (r.final) showFinal(r.final);
    }
  }).catch(e => { $("hold-d").textContent = String(e); });
}
$("live-in").addEventListener("input", () => liveSync(false));
$("live-in").addEventListener("keydown", e => {
  if (e.key === "Enter") { e.preventDefault(); liveSync(true); }
  if (e.key === "Escape") { voiceStop(false); $("live-in").value = ""; LIVE.sid = null; LIVE.sent = []; LIVE.finished = false; resetView([]); }
});

/* ============================== VOICE mode (inside LIVE TYPE) ==============================
   The browser's speech recogniser turns speech into text (Chrome sends the audio to its own
   speech service, so it needs internet). Each FINALISED word is written into the same input box
   and sent through liveSync(), exactly as if it had been typed and followed by a space.
   The agent never sees audio or interim guesses: only finished words, as in typed mode.     */
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const VOICE = { rec: null, on: false, finals: "", interim: "", cancel: false };
function voiceUI() {
  $("voice").classList.add("show");
  $("voice").classList.toggle("live", VOICE.on);
  $("mic").classList.toggle("on", VOICE.on);
  $("mic-t").textContent = VOICE.on ? "STOP" : "VOICE";
  $("listen-t").textContent = VOICE.on ? "Listening…" : "Mic off";
  const f = VOICE.finals.trim(), it = VOICE.interim.trim();
  $("tx").innerHTML = (f || it) ? `${esc(f)} <span class="interim">${esc(it)}</span>`
    : `<span class="ph">${VOICE.on ? "Speak now — e.g. “black Stridex running shoes size 9 under 3000”" : "Live transcript appears here…"}</span>`;
}
function voiceWarn(msg) {
  $("voice").classList.add("show"); $("voice").classList.remove("live");
  $("listen-t").textContent = "Voice off";
  $("tx").innerHTML = `<span class="vwarn">${esc(msg)}</span>`;
}
function voiceStart() {
  if (!SR) return voiceWarn("Voice needs the browser's speech recognition, which this browser does not have. Use Chrome.");
  // fresh request: clear the box and start a new agent session
  $("live-in").value = ""; LIVE.sid = null; LIVE.sent = []; LIVE.finished = false; resetView([]);
  $("stream").innerHTML = `<span class="empty-note">Listening — each word goes to the agent as soon as the recogniser finalises it.</span>`;
  const rec = new SR();
  rec.lang = "en-IN";               // Indian English: copes with Hinglish ("kaale joote", "2000 tak")
  rec.continuous = true;            // keep listening across pauses
  rec.interimResults = true;        // show guesses live (grey); only final words reach the agent
  rec.maxAlternatives = 1;
  rec.onresult = ev => {
    let interim = "";
    for (let k = ev.resultIndex; k < ev.results.length; k++) {
      const res = ev.results[k], t = res[0].transcript;
      if (res.isFinal) {
        VOICE.finals += " " + t;
        const words = t.trim().split(/\s+/).filter(Boolean);
        if (words.length) {           // same path as typing: append the words + a space, then sync
          const box = $("live-in");
          box.value = (box.value && !/\s$/.test(box.value) ? box.value + " " : box.value) + words.join(" ") + " ";
          liveSync(false);
        }
      } else interim += t;
    }
    VOICE.interim = interim; voiceUI();
  };
  rec.onerror = ev => {
    const why = {"not-allowed": "Microphone permission was refused. Allow the mic for this page and try again.",
                 "service-not-allowed": "Microphone permission was refused. Allow the mic for this page and try again.",
                 "network": "Speech-to-text needs internet (the browser's recogniser is online). The agent itself still works offline: type instead.",
                 "no-speech": "No speech heard. Click VOICE and try again.",
                 "audio-capture": "No microphone found."}[ev.error];
    if (why) { VOICE.cancel = ev.error !== "no-speech" || !VOICE.finals.trim(); VOICE.on = false; voiceWarn(why); }
  };
  rec.onend = () => {                // stopped by the button or by Chrome after a long silence:
    const cancel = VOICE.cancel;     // treat it as the shopper's full stop
    VOICE.on = false; VOICE.rec = null; VOICE.interim = ""; VOICE.cancel = false;
    if (!cancel && $("live-in").value.trim()) liveSync(true);
    if (!$("tx").querySelector(".vwarn")) voiceUI();
  };
  VOICE.rec = rec; VOICE.on = true; VOICE.finals = ""; VOICE.interim = ""; VOICE.cancel = false;
  try { rec.start(); } catch (e) { VOICE.on = false; return voiceWarn(String(e)); }
  voiceUI();
}
function voiceStop(fullStop) {
  if (!VOICE.rec) return;
  VOICE.cancel = !fullStop;
  VOICE.rec.stop();                  // onend fires after any last final words arrive
}
$("mic").onclick = () => VOICE.on ? voiceStop(true) : voiceStart();
if (!SR) $("mic").title = "Voice needs Chrome (browser speech recognition not available here)";

/* ============================== STRATEGY DUEL ============================== */
const D = { data: null, timer: null, idx: 0, shown: 0, custom: null };
function duelButtons() {
  $("duel-buttons").innerHTML = B.duel_examples.map((e, k) => `<button class="btn ex${k === D.idx && !D.custom ? " on" : ""}" data-k="${k}">${esc(e.name)}</button>`).join("");
  $("duel-buttons").querySelectorAll("button").forEach(b => b.onclick = () => { D.idx = +b.dataset.k; D.custom = null; duelButtons(); loadDuel(true); });
}
async function loadDuel(autoplay) {
  stopDuel();
  const text = D.custom || B.duel_examples[D.idx].text;
  const d = await post("/api/duel", {text});
  if (d.error) { $("lanes").innerHTML = `<div class="err">${esc(d.error)}</div>`; return; }
  D.data = d; resetDuel();
  if (autoplay) playDuel();
}
function resetDuel() {
  const d = D.data; D.shown = 0;
  const tp = d.target_product;
  $("d-ref").innerHTML = (d.reference === "gold"
      ? `Target (from this example's hand-written gold filters): `
      : `Your own request has no gold answer, so the reference is <b>Prescient's answer after the full sentence</b>: `) +
    (tp ? `<b>${esc(tp.title)}</b> · size ${esc(tp.size || "–")} · ${rupees(tp.price)} · <span class="num">${esc(d.target)}</span>` : "<b>none</b>") +
    ` &nbsp;·&nbsp; Eager = the "Eager (no safety gate)" row of the ablation table.`;
  $("lanes").innerHTML = d.lanes.map(l => `
    <div class="lane" id="ln-${l.key}">
      <div><h5>${esc(l.name)}</h5><div class="r">${esc(l.rule)}</div></div>
      <div class="ws">${d.words.map((w, i) => `<span class="w" id="ln-${l.key}-${i}">${esc(w)}</span>`).join("")}</div>
      <div class="status"><div class="s1 idle" id="ln-${l.key}-s1">Listening…</div><div class="s2" id="ln-${l.key}-s2"></div><div id="ln-${l.key}-tag"></div></div>
    </div>`).join("");
  $("scores").innerHTML = "";
  $("d-play").innerHTML = "&#9654;&nbsp; Play";
}
function duelNext() {
  const d = D.data; if (!d || D.shown >= d.words.length) return false;
  const i = D.shown, last = i === d.words.length - 1;
  for (const l of d.lanes) {
    const s = l.steps[i], el = $(`ln-${l.key}-${i}`), lane = $("ln-" + l.key);
    document.querySelectorAll(`#ln-${l.key} .w.now`).forEach(e => e.classList.remove("now"));
    el.classList.add("heard", "now");
    const s1 = $(`ln-${l.key}-s1`), s2 = $(`ln-${l.key}-s2`);
    if (s.new) {
      if (s.wrong) {
        el.classList.add("wrongc");
        lane.classList.remove("flash"); void lane.offsetWidth; lane.classList.add("flash", "bad");
        s1.className = "s1 bad"; s1.textContent = "Committed " + s.current;
        const wp = s.product || {};
        s2.textContent = wp.title ? `showed ${wp.title}${wp.size ? ", size " + wp.size : ""} — not what the shopper wants` : "not the target";
        $(`ln-${l.key}-tag`).innerHTML = `<span class="wrongtag">Wrong commit −1.0</span>` +
          `<div class="s2">showed ${esc(s.current)}${wp.title ? " · " + esc(wp.title) : ""}</div>`;
      } else {
        el.classList.add("commit");
        lane.classList.add("go");
        s1.className = "s1 go"; s1.textContent = "Committed " + s.current;
        const hadWrong = l.steps.slice(0, i).some(x => x.wrong);
        s2.textContent = hadWrong ? `at word ${i + 1} · too late: the wrong commit already cost −1.0, no earliness credit`
                                  : `at word ${i + 1} · ${plural(d.words.length - i - 1, "word")} before the full stop`;
      }
    } else if (!s.current) {
      s1.className = "s1 idle"; s1.textContent = l.key === "baseline" ? "Waiting for the full stop" : "Waiting";
      s2.textContent = s.left != null ? `${s.left} left · confidence ${pct(s.confidence)}` : "";
    }
  }
  D.shown++;
  if (last) duelEnd();
  return true;
}
function duelEnd() {
  const d = D.data;
  stopDuel(); $("d-play").innerHTML = "&#8635;&nbsp; Replay";
  document.querySelectorAll("#lanes .w.now").forEach(e => e.classList.remove("now"));
  const best = Math.max(...d.lanes.map(l => l.score));
  for (const l of d.lanes) {
    if (l.key === "baseline" || !l.commits.length) {
      const s1 = $(`ln-${l.key}-s1`);
      s1.className = "s1 " + (l.correct ? "" : "bad");
      s1.textContent = l.final ? "Answer " + l.final : "No answer";
      $(`ln-${l.key}-s2`).textContent = "at the full stop · 0 words early";
    }
  }
  $("scores").innerHTML = d.lanes.map(l => `
    <div class="sc ${l.score === best && l.correct && !l.wrong_commits ? "win" : ""}">
      <div class="k">${esc(l.name)} · score for this request</div>
      <div class="bigv">${l.score >= 0 ? "+" : "−"}${Math.abs(l.score).toFixed(2)}</div>
      <div class="d">Final answer ${l.correct ? `<span class="ok">✓ right</span>` : `<span class="bad">✗ wrong</span>`} (${esc(l.final || "none")})<br>
        Wrong commits: <span class="${l.wrong_commits ? "bad" : ""}">${l.wrong_commits}</span>${l.wrong_commits ? ` (−${l.wrong_commits.toFixed(1)})` : ""}
        · earliness ${Math.round(l.early_share * 100)}% · ${plural(l.searches, "search")}</div>
    </div>`).join("");
  document.querySelectorAll("#scores .sc").forEach((e, k) => setTimeout(() => e.classList.add("in"), 60 * k));
}
function playDuel() {
  if (!D.data) return;
  if (D.shown >= D.data.words.length) resetDuel();
  $("d-play").innerHTML = "&#10073;&#10073;&nbsp; Pause";
  duelNext();
  D.timer = setInterval(() => { if (!duelNext()) stopDuel(); }, +$("d-speed").value);
}
function stopDuel() { clearInterval(D.timer); D.timer = null; if (D.data && D.shown < D.data.words.length) $("d-play").innerHTML = "&#9654;&nbsp; Play"; }
$("d-play").onclick = () => D.timer ? stopDuel() : playDuel();
$("d-next").onclick = () => { stopDuel(); if (D.data && D.shown >= D.data.words.length) resetDuel(); duelNext(); };
$("d-reset").onclick = () => { stopDuel(); if (D.data) resetDuel(); };
$("d-speed").oninput = () => { speedLabel($("d-speed"), $("d-speedv")); if (D.timer) { clearInterval(D.timer); D.timer = setInterval(() => { if (!duelNext()) stopDuel(); }, +$("d-speed").value); } };
$("d-own").addEventListener("keydown", e => { if (e.key === "Enter" && $("d-own").value.trim()) { D.custom = $("d-own").value.trim(); duelButtons(); loadDuel(true); } });

/* ============================== RESULTS ============================== */
let RESULTS_LOADED = false;
async function loadResults() {
  if (RESULTS_LOADED) return; RESULTS_LOADED = true;
  const r = await fetch("/api/results").then(x => x.json());
  const p = r.dev.prescient, b = r.dev.baseline, h = r.holdout, e = r.dev_eager;
  if (!p || !b) { $("hero").innerHTML = `<div class="err">results/strategies.csv not found: run python3 compare.py --strategies</div>`; return; }
  $("hero").innerHTML = `
    <div class="hcard main"><div class="k">Score on dev · 300 requests</div><div class="v">${p.score}<small>vs ${b.score} for the organisers' baseline</small></div>
      <div class="d">scored by the organisers' evaluate.py</div></div>
    <div class="hcard"><div class="k">Accuracy</div><div class="v">${Math.round(p.accuracy * 100)}%</div><div class="d">final answer right (baseline ${Math.round(b.accuracy * 1000) / 10}%)</div></div>
    <div class="hcard"><div class="k">Wrong commits</div><div class="v">${p.wrong_commits}</div><div class="d">${e ? `Eager makes ${e.wrong_commits} on the same 300` : "on dev"}</div></div>
    <div class="hcard"><div class="k">Searches / request</div><div class="v">${p.searches_per_request}</div><div class="d">shown ${p.earliness_pct}% of the request early</div></div>`;
  $("honest").innerHTML = h.prescient
    ? `<b>Honest check on new wording.</b> Frozen 250-request holdout, run once and never used for fixes: score <b>${h.prescient.score}</b> vs ${h.baseline ? h.baseline.score : "–"} baseline, accuracy <b>${Math.round(h.prescient.accuracy * 1000) / 10}%</b>, <b>${h.prescient.wrong_commits}</b> wrong commits${h.eager ? ` (Eager: ${h.eager.wrong_commits})` : ""}. Source: results/ablation.csv.`
    : "Holdout numbers: run python3 compare.py to make results/ablation.csv.";
  const rows = r.strategies, max = Math.max(...rows.map(x => +x.score));
  $("st-table").innerHTML = `<tr><th>Strategy</th><th style="width:22%"></th><th class="n">Score</th><th class="n">Accuracy</th><th class="n">Wrong</th><th class="n">Early %</th></tr>` +
    rows.map(x => `<tr class="${x.strategy.includes("prescient") ? "me" : ""}"><td>${esc(x.strategy.replace(/^\d+\s*/, ""))}</td>
      <td><div class="sbar" style="width:${100 * (+x.score) / max}%"></div></td><td class="n">${x.score}</td>
      <td class="n">${(+x.accuracy).toFixed(3)}</td><td class="n ${+x.wrong_commits ? "bad" : ""}">${x.wrong_commits}</td><td class="n">${x.earliness_pct}</td></tr>`).join("");
  if (r.has_svg) $("svg").src = "/results/speed_safety.svg";
}

/* ============================== TRADE-OFF ============================== */
const TO = {runs: {}, seq: 0, timer: null, started: false};
const TO_X = {L: 64, R: 616, T: 20, B: 330, xmax: 20, ymax: 25};
const toX = e => TO_X.L + (TO_X.R - TO_X.L) * Math.min(e, TO_X.xmax) / TO_X.xmax;
const toY = w => TO_X.B - (TO_X.B - TO_X.T) * Math.min(w, TO_X.ymax) / TO_X.ymax;
function toTicks() {
  const lv = +$("to-range").value;
  $("to-ticks").innerHTML = B.tradeoff.map((t, i) => `<button data-i="${i}" class="${i === lv ? "on" : ""}">${esc(t.name)}${t.key === "default" ? `<span class="mk">&#9733; submitted</span>` : ""}</button>`).join("");
  $("to-ticks").querySelectorAll("button").forEach(b => b.onclick = () => { $("to-range").value = b.dataset.i; toMove(0); });
}
function toChart(cur) {
  let g = "";
  for (let e = 0; e <= TO_X.xmax; e += 5) g += `<line x1="${toX(e)}" y1="${TO_X.T}" x2="${toX(e)}" y2="${TO_X.B}" stroke="#1b222b"/><text x="${toX(e)}" y="${TO_X.B + 20}" fill="#7f8b98" font-size="13" text-anchor="middle">${e}%</text>`;
  for (let w = 0; w <= TO_X.ymax; w += 5) g += `<line x1="${TO_X.L}" y1="${toY(w)}" x2="${TO_X.R}" y2="${toY(w)}" stroke="#1b222b"/><text x="${TO_X.L - 10}" y="${toY(w) + 4}" fill="#7f8b98" font-size="13" text-anchor="end">${w}</text>`;
  g += `<text x="${(TO_X.L + TO_X.R) / 2}" y="${TO_X.B + 44}" fill="#c4ccd5" font-size="14" text-anchor="middle">earliness (% of the request saved) &rarr;</text>`;
  g += `<text transform="translate(18,${(TO_X.T + TO_X.B) / 2}) rotate(-90)" fill="#c4ccd5" font-size="14" text-anchor="middle">wrong commits</text>`;
  const done = B.tradeoff.map((t, i) => TO.runs[i] ? [i, TO.runs[i]] : null).filter(Boolean);
  if (done.length > 1) g += `<polyline fill="none" stroke="#46505c" stroke-width="1.5" stroke-dasharray="4 4" points="${done.map(([, r]) => `${toX(r.earliness_pct)},${toY(r.wrong_commits)}`).join(" ")}"/>`;
  let zero = 0;                                  // points on the 0 line: stagger their labels
  for (const [i, r] of done) {
    const x = toX(r.earliness_pct), y = toY(r.wrong_commits), left = x > TO_X.R - 150;
    g += `<circle cx="${x}" cy="${y}" r="5" fill="${r.wrong_commits ? "#ff5d5d" : "#7f8b98"}" opacity=".7"/>`;
    const lbl = `${esc(r.name)} (${r.earliness_pct}%, ${r.wrong_commits})`;
    if (r.wrong_commits) g += `<text x="${x + (left ? -10 : 10)}" y="${y - 10}" fill="#7f8b98" font-size="13" text-anchor="${left ? "end" : "start"}">${lbl}</text>`;
    else { const ly = y - 26 - 22 * (zero++ % 3);
      g += `<line x1="${x}" y1="${y - 7}" x2="${x}" y2="${ly + 4}" stroke="#2a323c"/><text x="${x}" y="${ly}" fill="#7f8b98" font-size="13" text-anchor="middle">${lbl}</text>`; }
  }
  const c = cur || TO.runs[+$("to-range").value];
  const tx = c ? toX(c.earliness_pct) : toX(0), ty = c ? toY(c.wrong_commits) : toY(0);
  const col = c && c.wrong_commits ? "#ff5d5d" : "#3ee08f";
  g += `<g id="to-dot" style="transform:translate(${TO.lastX ?? tx}px,${TO.lastY ?? ty}px);opacity:${c ? 1 : 0}"><circle r="17" fill="${col}" opacity=".18"/><circle r="9" fill="${col}" stroke="#06080b" stroke-width="2"/></g>`;
  $("to-chart").innerHTML = g;
  if (c) requestAnimationFrame(() => requestAnimationFrame(() => { const d = $("to-dot"); if (d) d.style.transform = `translate(${tx}px,${ty}px)`; }));
  TO.lastX = tx; TO.lastY = ty;
}
function toShow(r) {
  $("to-score").textContent = r.score.toFixed(1);
  $("to-acc").textContent = r.accuracy.toFixed(3);
  $("to-acc").className = "v " + (r.accuracy === 1 ? "go" : "bad");
  $("to-wrong").textContent = r.wrong_commits;
  $("to-wrong").className = "v " + (r.wrong_commits ? "bad" : "go");
  $("to-wrong-d").textContent = r.wrong_commits ? `in ${r.requests_with_wrong_commit} of ${r.requests} requests · 1.0 point each` : "each one would cost 1.0 point";
  $("to-early").textContent = r.earliness_pct.toFixed(1) + "%";
  const ch = Object.entries(r.changes);
  $("to-mean").innerHTML = `<b>${esc(r.name)}</b> — ${esc(r.meaning)} ` +
    (ch.length ? ch.map(([k, v]) => `<code>${esc(k)} = ${esc(Array.isArray(v) ? (v.length ? v.join(", ") : "none") : v)}</code>`).join(" ") : `<code>no changes</code>`);
  $("to-src").innerHTML = `evaluate.run(Agent, dev.jsonl) · ${r.requests} requests · ${r.searches} searches/request · ${r.ms} ms · ` +
    (r.restored ? `agent.CONFIG restored to agent.py's defaults &#10003;` : `<span class="err">CONFIG NOT restored</span>`);
}
async function toRun(i) {
  const r = await post("/api/tradeoff", {level: i});
  if (r.error) throw new Error(r.error);
  TO.runs[i] = r; return r;
}
async function toMove(delay) {
  const i = +$("to-range").value, my = ++TO.seq;
  toTicks(); $("to-cards").classList.add("busy");
  $("to-mean").innerHTML = `<b>${esc(B.tradeoff[i].name)}</b> — running evaluate.run on ${B.dev_requests} dev requests…`;
  clearTimeout(TO.timer);
  TO.timer = setTimeout(async () => {
    try {
      const r = await toRun(i);                  // always a fresh run, never a cached number
      if (my !== TO.seq) { toChart(); return; }
      $("to-cards").classList.remove("busy"); toShow(r); toChart(r);
    } catch (e) { $("to-mean").innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  }, delay);
}
async function loadTradeoff() {
  toTicks(); toChart();
  if (TO.started) return; TO.started = true;
  // First visit: run every position once (real runs) so the whole curve is on the chart,
  // then land on the submitted default.
  $("to-cards").classList.add("busy");
  try {
    for (let i = 0; i < B.tradeoff.length; i++) {
      $("to-mean").innerHTML = `Running evaluate.run on ${B.dev_requests} dev requests: <b>${esc(B.tradeoff[i].name)}</b> (${i + 1}/${B.tradeoff.length})…`;
      await toRun(i); toChart();
    }
  } catch (e) { $("to-mean").innerHTML = `<span class="err">${esc(e.message)}</span>`; return; }
  const i = +$("to-range").value;
  $("to-cards").classList.remove("busy"); toShow(TO.runs[i]); toChart(TO.runs[i]);
}
$("to-range").oninput = () => toMove(120);

/* ============================== tabs + keys ============================== */
/* ============================== EXPLORER ============================== */
// Every row, outcome and score below comes from the dataset's saved evaluate.py log
// (/api/explore). Clicking a row replays that request through agent.py (/api/explore/replay)
// for the word-by-word view; the case card says whether the replay matches the saved log.
const XP = { ds: "dev", data: {}, tag: "", out: new Set(), q: "", replay: false, cur: null };
const OUT_ORDER = ["early", "late", "wrong_commit", "wrong_final"];
async function loadExplore(ds) {
  XP.ds = ds;
  if (!XP.data[ds]) {
    $("xp-table").innerHTML = `<tr><td class="hint">Loading…</td></tr>`;
    const d = await fetch("/api/explore?dataset=" + encodeURIComponent(ds)).then(x => x.json());
    if (d.error) { $("xp-table").innerHTML = `<tr><td class="err">${esc(d.error)}</td></tr>`; return; }
    XP.data[ds] = d;
  }
  const d = XP.data[ds];
  $("xp-ds").innerHTML = Object.entries(d.datasets).map(([k, n]) =>
    `<button data-k="${k}" class="${k === ds ? "on" : ""}">${esc(n)}</button>`).join("");
  $("xp-ds").querySelectorAll("button").forEach(b => b.onclick = () => { XP.tag = ""; loadExplore(b.dataset.k); });
  // tags: the "order:…" tags of the holdout are one per word order, too many for a menu
  const tags = {};
  for (const r of d.rows) for (const t of r.tags) if (!t.startsWith("order:")) tags[t] = (tags[t] || 0) + 1;
  const none = d.rows.filter(r => !r.tags.length).length;
  if (XP.tag && XP.tag !== "(none)" && !tags[XP.tag]) XP.tag = "";
  $("xp-tag").innerHTML = `<option value="">All tags</option>` +
    Object.keys(tags).sort().map(t => `<option value="${esc(t)}">${esc(t)} (${tags[t]})</option>`).join("") +
    (none ? `<option value="(none)">no tag (${none})</option>` : "");
  $("xp-tag").value = XP.tag;
  const h = d.hash;
  $("xp-note").className = "xp-note" + (ds === "holdout" ? " frozen" : "");
  $("xp-note").innerHTML = `<b>${esc(d.name)}.</b> ${esc(d.note)}` + (h ? ` · SHA-256 of ${esc(d.requests_file)}: ` +
    (h.ok ? `<span class="ok">✓ matches the value recorded in results/holdout_report.md</span>`
          : `<span class="bad">✗ does not match the recorded value</span>`) : "");
  $("xp-src").textContent = `requests: ${d.requests_file} · saved results: ${d.log_file}` +
    (d.missing ? ` · ${d.missing} request(s) have no saved result and are not shown` : "");
  renderExplore();
}
function xpRows() {
  const d = XP.data[XP.ds], q = XP.q.trim().toLowerCase();
  return d.rows.filter(r =>
    (!XP.tag || (XP.tag === "(none)" ? !r.tags.length : r.tags.includes(XP.tag))) &&
    (!XP.out.size || r.outcome.some(o => XP.out.has(o))) &&
    (!q || r.text.toLowerCase().includes(q) || r.id.toLowerCase().includes(q)));
}
function hl(text) {
  const q = XP.q.trim();
  if (!q) return esc(text);
  const i = text.toLowerCase().indexOf(q.toLowerCase());
  return i < 0 ? esc(text) : esc(text.slice(0, i)) + `<mark>${esc(text.slice(i, i + q.length))}</mark>` + esc(text.slice(i + q.length));
}
const obadge = o => `<span class="ob ${o}">${esc(XP.data[XP.ds].outcomes[o])}</span>`;
function renderExplore() {
  const d = XP.data[XP.ds]; if (!d) return;
  // outcome chip counts follow the tag and text filters (not the outcome filter itself)
  const base = d.rows.filter(r => (!XP.tag || (XP.tag === "(none)" ? !r.tags.length : r.tags.includes(XP.tag))) &&
    (!XP.q.trim() || r.text.toLowerCase().includes(XP.q.trim().toLowerCase()) || r.id.toLowerCase().includes(XP.q.trim().toLowerCase())));
  $("xp-out").innerHTML = OUT_ORDER.map(o => `<button class="ochip ${o}${XP.out.has(o) ? " on" : ""}" data-o="${o}">${esc(d.outcomes[o])}<b>${base.filter(r => r.outcome.includes(o)).length}</b></button>`).join("");
  $("xp-out").querySelectorAll("button").forEach(b => b.onclick = () => { const o = b.dataset.o; XP.out.has(o) ? XP.out.delete(o) : XP.out.add(o); renderExplore(); });
  const rows = xpRows(), n = rows.length;
  const score = n ? (100 * rows.reduce((a, r) => a + r.score, 0) / n).toFixed(1) : "–";
  const acc = n ? (100 * rows.filter(r => r.correct).length / n).toFixed(1) + "%" : "–";
  const wc = rows.reduce((a, r) => a + r.wrong_commits, 0);
  const early = n ? (100 * rows.reduce((a, r) => a + r.early_share, 0) / n).toFixed(1) + "%" : "–";
  $("xp-sum").innerHTML = `Showing <b>${n}</b> of <b>${d.rows.length}</b> requests · saved results for these: score <b>${score}</b> · accuracy <b>${acc}</b> · wrong commits <b>${wc}</b> · earliness <b>${early}</b>` +
    (n ? " · click a request to replay it word by word" : "");
  $("xp-table").innerHTML = `<tr><th>ID</th><th>Request</th><th>Outcome</th><th class="n">First commit</th><th class="n">Score</th></tr>` +
    (n ? rows.map(r => {
      const fc = r.commits.length ? `word ${r.commits[0][0]} / ${r.n}` : `— / ${r.n}`;
      return `<tr class="r" data-id="${esc(r.id)}"><td class="id">${esc(r.id)}</td>` +
        `<td class="tx">${hl(r.text)}<div>${r.tags.filter(t => !t.startsWith("order:")).map(t => `<span class="tg">${esc(t)}</span>`).join("")}</div></td>` +
        `<td>${r.outcome.map(obadge).join("")}</td><td class="n">${fc}</td>` +
        `<td class="n" style="color:${r.score < 0 ? "var(--red)" : "inherit"}">${r.score >= 0 ? "+" : "−"}${Math.abs(r.score).toFixed(2)}</td></tr>`;
    }).join("") : `<tr><td colspan="5" class="hint">No request matches these filters.</td></tr>`);
  $("xp-table").querySelectorAll("tr.r").forEach(tr => tr.onclick = () => openReplay(XP.ds, tr.dataset.id, true));
}
$("xp-tag").onchange = () => { XP.tag = $("xp-tag").value; renderExplore(); };
$("xp-q").oninput = () => { XP.q = $("xp-q").value; renderExplore(); };

const prodLine = p => p ? `<div class="t">${esc(p.title)}</div><div class="m">${esc([p.brand, cap(p.colour), p.size ? "size " + p.size : null, cap(p.category), rupees(p.price)].filter(Boolean).join(" · "))}</div><div class="pid">${esc(p.product_id)}</div>`
                        : `<div class="t">No product</div>`;
function caseCard(c) {
  const r = c.row, right = r.correct;
  const fg = r.filters_gold || {}, fr = r.filters_reported || {};
  const fv = (k, v) => v == null ? "—" : (k === "max_price" ? rupees(v) : String(v));
  const frows = SLOTS.map(([k, n]) => {
    const same = String(fg[k] ?? "").toLowerCase() === String(fr[k] ?? "").toLowerCase();
    return `<tr><td class="k2">${esc(n)}</td><td>${esc(fv(k, fg[k]))}</td><td class="${same ? "" : "bad"}">${esc(fv(k, fr[k]))}</td></tr>`;
  }).join("");
  const commits = r.commits.length ? r.commits.map(([t, p]) => `<b>${esc(p)}</b> at word ${t}${p === r.target ? ` <span class="ok">✓</span>` : ` <span class="bad">✗ wrong (−1.0)</span>`}`).join(" → ") : "none (answered at the full stop)";
  const oracle = r.earliest != null ? ` · earliest safe word ${r.earliest} of ${r.n}` : "";
  $("case").innerHTML = `
    <div class="case-h"><span class="cid">${esc(r.id)}</span><span class="pill">${esc(c.dataset_name)}</span>${r.outcome.map(obadge).join("")}
      ${r.tags.filter(t => !t.startsWith("order:")).map(t => `<span class="tg">${esc(t)}</span>`).join("")}
      <span>saved score <b class="num" style="color:var(--ink)">${r.score >= 0 ? "+" : "−"}${Math.abs(r.score).toFixed(2)}</b></span></div>
    <div class="case-g">
      <div><div class="k">Gold target</div>${prodLine(c.target_product)}</div>
      <div class="ag ${right ? "ok" : "bad"}"><div class="k">Agent's final answer (saved)</div>${prodLine(c.saved_final_product)}
        <div class="verdict ${right ? "ok" : "bad"}">${right ? "✓ right" : "✗ wrong final answer"}</div></div>
      <div><div class="k">Filters · gold vs agent's last report</div><table class="fs"><tr><td></td><td class="k2">gold</td><td class="k2">agent</td></tr>${frows}</table></div>
    </div>
    <div class="case-f">Saved commits: ${commits}${oracle} · ${plural(r.searches, "search")} ·
      replay through agent.py now: ${c.replay.matches_saved ? `<span class="ok">✓ same commits and final answer as the saved log</span>`
        : `<span class="bad">✗ differs from the saved log (replay final ${esc(c.replay.final || "none")})</span>`}
      <span class="src" style="margin-left:6px">${esc(c.log_file)}</span></div>`;
}
async function openReplay(ds, id, autoplay) {
  stopEx();
  const c = await post("/api/explore/replay", {dataset: ds, id});
  if (c.error) { XP.replay = false; setMode("explore"); $("xp-sum").innerHTML = `<span class="err">${esc(c.error)}</span>`; return; }
  if (!XP.data[ds]) await loadExplore(ds);
  XP.ds = ds; XP.cur = c; XP.replay = true;
  setMode("explore");
  EX.data = {words: c.words, steps: c.steps, final: c.final}; EX.source = "explore";
  caseCard(c);
  resetView(c.words);
  if (autoplay) playEx();
}
function xpBack() { stopEx(); XP.replay = false; XP.cur = null; setMode("explore"); }
$("xp-back").onclick = xpBack;

let MODE = "examples";
function setMode(m, noLoad) {
  MODE = m;
  document.querySelectorAll("#tabs button").forEach(b => b.classList.toggle("on", b.dataset.v === m));
  const replay = m === "explore" && XP.replay;             // Explorer: list view or one replay
  $("v-single").classList.toggle("on", m === "live" || m === "examples" || replay);
  $("v-duel").classList.toggle("on", m === "duel");
  $("v-results").classList.toggle("on", m === "results");
  $("v-tradeoff").classList.toggle("on", m === "tradeoff");
  $("v-explore").classList.toggle("on", m === "explore" && !replay);
  $("bar-examples").style.display = m === "examples" || replay ? "" : "none";
  $("ex-buttons").style.display = replay ? "none" : "flex";
  $("xp-back").style.display = replay ? "" : "none";
  $("case").classList.toggle("show", replay);
  $("bar-live").style.display = m === "live" ? "" : "none";
  stopEx(); stopDuel(); if (m !== "live") voiceStop(false);
  if (m === "examples" && !noLoad) { if (EX.data && EX.source === "example") resetView(EX.data.words); else loadExample(false); }
  if (m === "explore" && !replay) loadExplore(XP.ds);
  if (m === "live") { $("live-in").value = ""; LIVE.sid = null; LIVE.sent = []; LIVE.finished = false; resetView([]); setTimeout(() => $("live-in").focus(), 50); }
  if (m === "duel" && !D.data && !noLoad) loadDuel(false);
  if (m === "results") loadResults();
  if (m === "tradeoff") loadTradeoff();
}
document.querySelectorAll("#tabs button").forEach(b => b.onclick = () => { XP.replay = false; setMode(b.dataset.v); });
document.addEventListener("keydown", e => {
  if (e.target.tagName === "INPUT") return;
  if (["1","2","3","4","5","6"].includes(e.key)) { XP.replay = false; setMode(["live","examples","duel","results","tradeoff","explore"][+e.key - 1]); }
  if (e.key === "Escape" && MODE === "explore" && XP.replay) xpBack();
  if (e.key === " " || e.key === "ArrowRight") {
    e.preventDefault();
    if (MODE === "examples" || (MODE === "explore" && XP.replay)) (e.key === " " ? $("play") : $("next")).click();
    if (MODE === "duel") (e.key === " " ? $("d-play") : $("d-next")).click();
  }
});

$("thr-t").textContent = pct(B.threshold);
$("thr").style.left = pct(B.threshold); $("thr").querySelector("b").textContent = pct(B.threshold);
$("fcat").textContent = B.catalogue;
speedLabel($("speed"), $("speedv")); speedLabel($("d-speed"), $("d-speedv"));
exButtons(); duelButtons(); buildChips();
// Optional deep links (handy for recording): #live  #examples/1  #examples/1/play  #duel/0/play  #results  #tradeoff
(async () => {
  const [m, k, act, act2] = location.hash.replace("#", "").split("/");
  const mode = ["live", "examples", "duel", "results", "tradeoff", "explore"].includes(m) ? m : "examples";
  if (mode === "explore") {               // #explore  #explore/holdout  #explore/dev/dev-0003/play
    if (k && B.explore_datasets.includes(k)) XP.ds = k;
    if (act) { await openReplay(XP.ds, decodeURIComponent(act), act2 === "play"); return; }
    setMode("explore"); return;
  }
  if (mode === "examples" && k != null) { EX.idx = Math.min(+k || 0, B.examples.length - 1); exButtons(); }
  if (mode === "duel" && k != null) { D.idx = Math.min(+k || 0, B.duel_examples.length - 1); duelButtons(); }
  setMode(mode, act === "play");
  if (act === "play") {
    if (mode === "examples") await loadExample(true);
    if (mode === "duel") await loadDuel(true);
  }
})();
</script>
</body></html>
"""


def main():
    ap = argparse.ArgumentParser(description="Live demo of the shopping agent.")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    args = ap.parse_args()
    missing = [e["name"] for e in DUEL_EXAMPLES if not e["target"]]
    if missing:
        print(f"note: duel example(s) without a unique gold product: {', '.join(missing)}")
    server = None
    for port in range(args.port, args.port + 20):     # skip ports already in use
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    if server is None:
        raise SystemExit(f"No free port between {args.port} and {args.port + 19}.")
    url = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"Prescient demo running at {url}   (press Ctrl+C to stop)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
