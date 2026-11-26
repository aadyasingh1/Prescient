"""Prescient: "Your product, before your full stop." Live demo of our shopping agent: type a request, watch it being read word by word.

    python3 demo_app.py            # opens http://127.0.0.1:8000 in your browser
    python3 demo_app.py --port 8080 --no-browser

Standard library only. Nothing to install. Press Ctrl+C in the terminal to stop it.

How it works
------------
This file does NOT contain any shopping logic of its own. When you press Play, the page
sends your sentence to this small local server. The server creates the real `Agent` from
agent.py (the same one evaluate.py scores), feeds it the words one at a time exactly like
evaluate.py does, and records what the agent said after each word: its filters, its
confidence, how many products still fit, how many searches it has used, and whether it
committed. The page then replays those steps word by word, at the speed you choose.

The screen shows three stages for every word ("Prediction is not permission to act"):
  1 ANTICIPATE  the agent's filters and how many products still fit
  2 STABILIZE   every safety check from agent.py's _confidence(): fetch list may be cut
                short (v4 guard), correction word open, number still being spoken, phrase
                still open, no neutral word yet / polite closing word. Each is read from
                the agent's own state (agent.pool_cut, agent.state), not re-computed.
  3 COMMIT      committed, holding an earlier commit, or why it is waiting
A replaced value shows "Correction detected: old → new". A commit shows a receipt with
the real filters, products left, word number, confidence, searches and checks passed.

The catalogue is read here only to show the product's name and price on screen.
The agent itself still looks products up only through tools.search (counted).
"""
import argparse
import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import agent as A
from agent import Agent, clean
from search import load_catalogue, search

PRODUCTS = {p["product_id"]: p for p in load_catalogue()}   # display only

# Our own example sentences (not taken from dev.jsonl), one per kind of request.
EXAMPLES = [
    ("Plain", "I want black Pacevo running shoes size nine under four thousand for my morning runs"),
    ("Correction", "show me maroon Tanaro kurta size M, sorry, XS max 1500 for a wedding"),
    ("Hindi-English", "bhai Loomsy ke navy blue sneakers dikhao size 7 under 4500 jaldi"),
    ("Late constraint", "I need olive Urbanyx slides under seven hundred, oh and size 6 for the beach trip"),
]


class Tools:
    """The same counted search that evaluate.py gives the agent."""

    def __init__(self):
        self.searches = 0

    def search(self, query="", filters=None, k=10):
        self.searches += 1
        return search(query, filters, k)

    def record_search(self, n=1):
        self.searches += n


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

    Each check: name (when passed), open (short name when NOT passed), ok, detail.
    The order and the conditions are the same as in agent.py's _confidence().
    """
    st, cfg = agent.state, A.CONFIG
    tok = clean(word)
    pool_n = None if agent.pool is None else len(agent.pool)
    checks = [
        {"name": "Fetch list complete (not cut short)", "open": "Fetch list may be cut short",
         "ok": not agent.pool_cut,
         "detail": "" if not agent.pool_cut
         else f"a fetch returned the full {cfg['fetch_k']} products, so \"one left\" is no proof"
         + (f" (this list: {pool_n})" if pool_n is not None else "")},
        {"name": "No correction word open", "open": "Correction word", "ok": not st.cue_open,
         "detail": "" if not st.cue_open else "heard a correction word: waiting for the new value"},
        {"name": "No number still being spoken", "open": "Number still being spoken",
         "ok": st.number is None,
         "detail": "" if st.number is None else "a number may continue (\"forty… five hundred\")"},
        {"name": "No bare number waiting for its meaning", "open": "Number still being spoken",
         "ok": st.pending_bare is None,
         "detail": "" if st.pending_bare is None else f"\"{st.pending_bare}\" could still be a size"},
        {"name": "No size or price phrase still open", "open": "Phrase still open",
         "ok": st.size_modifier is None and not st.price_prefix_open,
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
    checks.append({"name": "Shopper has moved on (neutral word)",
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


def run_request(text):
    """Feed the sentence to a fresh Agent word by word and record every step."""
    words = text.split()
    tools = Tools()
    agent = Agent(tools)
    agent.start("demo")
    steps = []
    first_commit = None
    current = None
    for i, word in enumerate(words):
        before = dict(agent.state.values)
        out = agent.observe(word) or {}
        cands = agent._candidates()          # read-only peek, for the "products left" counter
        left = None if cands is None else len(cands)
        checks, hazard = safety_checks(agent, word)
        conf = out.get("confidence") or 0.0
        step = {
            "word": word,
            "filters": out.get("filters"),
            "values": {s: show_value(s, v) for s, v in agent.state.values.items()},
            "confidence": out.get("confidence"),
            "left": left,
            "searches": tools.searches,
            "commit": out.get("commit"),
            "new_commit": bool(out.get("commit")) and out.get("commit") != current,
            "corrections": corrections(before, agent.state.values),
            "checks": checks,
            "hazard": hazard,
            "waiting": None if out.get("commit") else commit_reason(left, checks, conf),
            "holding": None if out.get("commit") else current,   # an earlier commit still stands
        }
        if step["commit"]:
            step["product"] = product_card(step["commit"])
            current = step["commit"]
            if first_commit is None:
                first_commit = i
        steps.append(step)
    final = agent.finish()
    if final is None:
        final = agent.committed
    early = None
    if first_commit is not None and words:
        to_come = len(words) - first_commit - 1
        early = {"word_index": first_commit, "words_to_come": to_come,
                 "percent": round(100 * to_come / len(words), 1)}
    return {"words": words, "steps": steps, "final": final,
            "final_product": product_card(final), "searches": tools.searches,
            "early": early, "threshold": A.CONFIG["commit_threshold"]}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            page = PAGE.replace("__EXAMPLES__", json.dumps(EXAMPLES))
            self._send(200, page, "text/html; charset=utf-8")
        else:
            self._send(404, "not found", "text/plain")

    def do_POST(self):
        if self.path != "/api/run":
            return self._send(404, "not found", "text/plain")
        try:
            length = int(self.headers.get("Content-Length", 0))
            text = json.loads(self.rfile.read(length) or b"{}").get("text", "")
            result = run_request(str(text)[:1000])
            self._send(200, json.dumps(result), "application/json")
        except Exception as exc:                       # show the error on the page
            self._send(500, json.dumps({"error": f"{type(exc).__name__}: {exc}"}),
                       "application/json")

    def log_message(self, *args):                      # keep the terminal quiet
        pass


PAGE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Prescient: your product, before your full stop</title>
<style>
  :root { --bg:#f6f7f9; --card:#fff; --ink:#1d232b; --muted:#6b7480; --line:#e2e6eb;
          --accent:#2457d6; --ok:#17864a; --warn:#b86e00; }
  * { box-sizing:border-box; }
  body { margin:0; font:16px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
         background:var(--bg); color:var(--ink); }
  main { max-width:980px; margin:0 auto; padding:28px 20px 60px; }
  h1 { font-size:32px; margin:0; letter-spacing:-.01em; }
  .tagline { font-size:18px; font-style:italic; color:var(--accent); margin:2px 0 10px; }
  .sub { color:var(--muted); margin:0 0 20px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px;
          padding:16px 18px; margin-bottom:16px; }
  textarea { width:100%; font:inherit; font-size:18px; padding:10px 12px; border-radius:8px;
             border:1px solid var(--line); resize:vertical; min-height:56px; }
  .row { display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin-top:10px; }
  button { font:inherit; padding:8px 16px; border-radius:8px; border:1px solid var(--line);
           background:#fff; cursor:pointer; }
  button.primary { background:var(--accent); color:#fff; border-color:var(--accent); }
  button.ex { font-size:14px; padding:5px 10px; color:var(--muted); }
  label { color:var(--muted); font-size:14px; }
  .words { font-size:22px; line-height:1.9; min-height:44px; }
  .w { padding:2px 4px; border-radius:5px; color:#c3c9d1; transition:all .15s; }
  .w.seen { color:var(--ink); }
  .w.now { background:#dfe8ff; }
  .w.commit { background:var(--ok); color:#fff; }
  .grid { display:grid; grid-template-columns:repeat(5,1fr); gap:10px; }
  .slot { border:1px solid var(--line); border-radius:10px; padding:10px; min-height:70px; }
  .slot .k { font-size:12px; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); }
  .slot .v { font-size:18px; font-weight:600; margin-top:4px; word-break:break-word; }
  .slot.empty .v { color:#c3c9d1; font-weight:400; }
  .slot.flash { animation:flash .8s; }
  .slot.changed { border-color:var(--warn); }
  .slot.changed .k::after { content:" · corrected"; color:var(--warn); text-transform:none; }
  @keyframes flash { from { background:#fff4c2; } to { background:#fff; } }
  .stats { display:grid; grid-template-columns:2fr 1fr 1fr; gap:10px; margin-top:12px; }
  .bar { height:14px; background:#edf0f3; border-radius:7px; overflow:hidden; margin-top:6px; }
  .bar > div { height:100%; width:0; background:var(--accent); transition:width .2s; }
  .bar.hot > div { background:var(--ok); }
  .big { font-size:22px; font-weight:600; }
  .muted { color:var(--muted); font-size:14px; }
  .commit-box { border:2px solid var(--ok); background:#eefaf2; }
  .commit-box h2 { margin:0 0 4px; font-size:18px; color:var(--ok); }
  .hidden { display:none; }
  table { border-collapse:collapse; width:100%; font-size:14px; }
  th, td { text-align:left; padding:5px 8px; border-bottom:1px solid var(--line); }
  th { color:var(--muted); font-weight:500; }
  tr.c td { background:#eefaf2; font-weight:600; }
  .err { color:#b3261e; }
  .principle { font-weight:600; margin:0 0 6px; }
  .stages { display:grid; grid-template-columns:1.6fr 1fr 1fr; gap:12px; margin-bottom:16px; }
  .stage { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px 16px;
           border-top:5px solid var(--line); }
  .stage h3 { margin:0 0 8px; font-size:13px; letter-spacing:.08em; color:var(--muted); }
  .stage.on-a { border-top-color:var(--accent); }
  .stage.on-s { border-top-color:var(--warn); }
  .stage.on-c { border-top-color:var(--ok); }
  .stage .grid { grid-template-columns:repeat(3,1fr); gap:8px; }
  .stage .slot { min-height:58px; padding:8px; }
  .stage .slot .v { font-size:15px; }
  ul.checks { list-style:none; padding:0; margin:0; font-size:14px; }
  ul.checks li { padding:3px 0; }
  ul.checks li.open { color:var(--warn); font-weight:600; }
  ul.checks li.ok { color:var(--muted); }
  ul.checks .d { display:block; font-weight:400; color:var(--muted); font-size:12px; margin-left:20px; }
  .corr { background:#fff4e0; border:1px solid #f0c27a; color:#7a4a00; border-radius:10px; padding:10px 14px;
          margin-bottom:16px; font-weight:600; }
  .receipt table td:first-child { color:var(--muted); width:38%; }
  .receipt td.pass { color:var(--ok); }
  @media (max-width:800px) { .stages { grid-template-columns:1fr; } }
  @media (max-width:700px) { .grid, .stage .grid { grid-template-columns:repeat(2,1fr); } }
</style></head>
<body><main>
  <h1>Prescient</h1>
  <p class="tagline">Your product, before your full stop.</p>
  <p class="principle">Prediction is not permission to act.</p>
  <p class="sub">Type what a shopper would say. The agent hears it one word at a time and shows the
  product as soon as it is safe to, often before the sentence ends.</p>

  <div class="card">
    <textarea id="text" rows="2"></textarea>
    <div class="row">
      <button class="primary" id="play">&#9654; Play</button>
      <button id="step">Next word</button>
      <button id="all">Show all</button>
      <label>Speed <input type="range" id="speed" min="150" max="1500" step="50" value="600">
        <span id="speedv"></span></label>
    </div>
    <div class="row" id="examples"><span class="muted">Try:</span></div>
  </div>

  <div class="card">
    <div class="muted">What the agent has heard so far</div>
    <div class="words" id="words"></div>
  </div>

  <div class="corr hidden" id="corr"></div>

  <div class="stages">
    <div class="stage" id="st-a">
      <h3>1 · ANTICIPATE</h3>
      <div class="grid" id="slots"></div>
      <div class="stats" style="grid-template-columns:1fr 1fr">
        <div><div class="muted">Products that still fit</div><div class="big" id="left">&ndash;</div></div>
        <div><div class="muted">Searches used</div><div class="big" id="searches">0</div></div>
      </div>
    </div>
    <div class="stage" id="st-s">
      <h3>2 · STABILIZE</h3>
      <div class="muted" id="s-summary" style="margin-bottom:6px">&ndash;</div>
      <ul class="checks" id="checks"></ul>
    </div>
    <div class="stage" id="st-c">
      <h3>3 · COMMIT</h3>
      <div class="big" id="c-status">&ndash;</div>
      <div class="muted" id="c-why" style="margin:4px 0 8px"></div>
      <div class="muted" id="thr-label">Confidence</div>
      <div class="bar" id="bar"><div></div></div><div id="confv" class="muted">&ndash;</div>
    </div>
  </div>

  <div class="card commit-box receipt hidden" id="commit">
    <h2 id="commit-h"></h2>
    <div class="big" id="commit-title"></div>
    <div class="muted" id="commit-meta" style="margin-bottom:10px"></div>
    <table id="receipt"></table>
  </div>

  <div class="card hidden" id="final"></div>

  <div class="card">
    <div class="muted" style="margin-bottom:6px">Step-by-step log</div>
    <table><thead><tr><th>#</th><th>Word</th><th>Category</th><th>Colour</th><th>Size</th>
      <th>Brand</th><th>Max price</th><th>Fit</th><th>Open checks</th><th>Conf.</th><th>Action</th></tr></thead>
      <tbody id="log"></tbody></table>
  </div>
</main>
<script>
const EXAMPLES = __EXAMPLES__;
const SLOTS = [["category","Category"],["colour","Colour"],["size","Size"],
               ["brand","Brand"],["max_price","Max price"]];
const SLOT_NAME = Object.fromEntries(SLOTS);
const $ = id => document.getElementById(id);
let data = null, pos = 0, timer = null, prev = {};

function mark(ok) { return ok ? `<span class="pass">✓ passed</span>` : `<span class="err">✗ not passed</span>`; }
function rupees(n) { return "₹" + Number(n).toLocaleString("en-IN"); }
function show(k, v) { return v == null ? "–" : (k === "max_price" ? rupees(v) : v); }

function buildSlots() {
  $("slots").innerHTML = SLOTS.map(([k, n]) =>
    `<div class="slot empty" id="s-${k}"><div class="k">${n}</div><div class="v">not said</div></div>`).join("");
}
function esc(s) { return String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }

function reset() {
  clearInterval(timer); timer = null; pos = 0; prev = {};
  $("play").innerHTML = "&#9654; Play";
  buildSlots();
  $("words").innerHTML = data ? data.words.map((w, i) => `<span class="w" id="w${i}">${esc(w)}</span>`).join(" ") : "";
  $("bar").firstElementChild.style.width = "0"; $("bar").classList.remove("hot");
  $("confv").textContent = "–"; $("left").textContent = "–"; $("searches").textContent = "0";
  $("commit").classList.add("hidden"); $("final").classList.add("hidden"); $("log").innerHTML = "";
  $("corr").classList.add("hidden"); $("checks").innerHTML = ""; $("s-summary").textContent = "–";
  $("c-status").textContent = "–"; $("c-why").textContent = "";
  for (const id of ["st-a", "st-s", "st-c"]) $(id).className = "stage";
  if (data) $("thr-label").textContent = `Confidence (commits at ${Math.round(data.threshold * 100)}% or more, with exactly 1 product left)`;
}

async function load() {
  const text = $("text").value.trim();
  if (!text) return false;
  if (data && data.text === text) return true;
  const r = await fetch("/api/run", {method:"POST", headers:{"Content-Type":"application/json"},
                                      body: JSON.stringify({text})});
  const j = await r.json();
  if (j.error) { $("words").innerHTML = `<span class="err">${esc(j.error)}</span>`; data = null; return false; }
  data = j; data.text = text; reset(); return true;
}

function stepOnce() {
  if (!data || pos >= data.steps.length) { finish(); return false; }
  const s = data.steps[pos], i = pos;
  document.querySelectorAll(".w.now").forEach(e => e.classList.remove("now"));
  const we = $("w" + i); we.classList.add("seen", "now");
  const f = s.filters || {}, v = s.values || {};
  // 1 ANTICIPATE: the agent's filter state. Sets ("running shoes / sneakers") stay sets.
  for (const [k] of SLOTS) {
    const el = $("s-" + k), val = v[k] != null ? v[k] : (f[k] != null ? show(k, f[k]) : null);
    if (val !== prev[k]) {
      el.querySelector(".v").textContent = val == null ? "not said" : val;
      el.classList.toggle("empty", val == null);
      el.classList.remove("flash"); void el.offsetWidth; if (val != null) el.classList.add("flash");
    }
    prev[k] = val;
  }
  for (const c of s.corrections) $("s-" + c.slot).classList.add("changed");
  if (s.corrections.length) {
    $("corr").innerHTML = s.corrections.map(c =>
      `Correction detected: ${esc(SLOT_NAME[c.slot])} ${esc(c.old)} &rarr; ${esc(c.new)} <span class="muted">(word ${i + 1}, "${esc(s.word)}")</span>`).join("<br>");
    $("corr").classList.remove("hidden");
  }
  $("left").textContent = s.left == null ? "not searched yet" : s.left;
  $("searches").textContent = s.searches;
  // 2 STABILIZE: every safety check the agent made on this word
  const open = s.checks.filter(c => !c.ok);
  $("s-summary").textContent = open.length
    ? `Waiting: ${[...new Set(open.map(c => c.open))].join(", ")}`
    : "All safety checks passed";
  $("checks").innerHTML = s.checks.map(c =>
    `<li class="${c.ok ? "ok" : "open"}">${c.ok ? "✓" : "⏸"} ${esc(c.ok ? c.name : c.open)}` +
    (c.detail ? `<span class="d">${esc(c.detail)}</span>` : "") + `</li>`).join("");
  // 3 COMMIT
  const c = s.confidence || 0;
  $("bar").firstElementChild.style.width = Math.round(c * 100) + "%";
  $("bar").classList.toggle("hot", c >= data.threshold);
  $("confv").textContent = Math.round(c * 100) + "%" +
    (s.hazard != null ? ` · chance a correction is still coming: ${(100 * s.hazard).toFixed(1)}%` : "");
  if (s.commit) {
    $("c-status").textContent = s.new_commit ? `COMMITTED ${s.commit}` : `Still committed ${s.commit}`;
    $("c-why").textContent = "1 product left, all checks passed, confidence high enough";
  } else if (s.holding) {
    $("c-status").textContent = `Holding ${s.holding}`;
    $("c-why").textContent = `earlier commit still stands; not re-confirmed on this word: ${s.waiting}`;
  } else {
    $("c-status").textContent = "Waiting";
    $("c-why").textContent = s.waiting || "";
  }
  $("st-a").className = "stage on-a";
  $("st-s").className = "stage" + (open.length ? " on-s" : "");
  $("st-c").className = "stage" + (s.commit ? " on-c" : "");
  let action = "";
  if (s.commit) {
    we.classList.add("commit");
    action = (s.new_commit ? "commit " : "still ") + s.commit;
  }
  if (s.new_commit) {
    const p = s.product || {}, toCome = data.words.length - i - 1;
    $("commit").classList.remove("hidden");
    $("commit-h").textContent = toCome > 0
      ? `Receipt: committed after word ${i + 1} of ${data.words.length}, ${toCome} word${toCome > 1 ? "s" : ""} before the shopper finished`
      : `Receipt: committed on the last word`;
    $("commit-title").textContent = p.title || s.commit;
    $("commit-meta").textContent = p.price ? `${s.commit} · ${p.brand} · ${p.category} · ${p.colour} · size ${p.size || "-"} · ${rupees(p.price)}` : s.commit;
    const filt = SLOTS.map(([k, n]) => `${n}: ${v[k] != null ? v[k] : "–"}`).join(" · ");
    const rows = [
      ["Filters", esc(filt)],
      ["Products left", s.left],
      ["Word number", `${i + 1} of ${data.words.length} ("${esc(s.word)}")`],
      ["Confidence", `${Math.round(c * 100)}% (needs ${Math.round(data.threshold * 100)}%)` +
        (s.hazard != null ? ` · correction chance ${(100 * s.hazard).toFixed(1)}%` : "")],
      ["Searches", s.searches],
      ...s.checks.map(k => [esc(k.name), mark(k.ok) + (k.detail ? ` <span class="muted">(${esc(k.detail)})</span>` : "")]),
      ["Exactly one product left", mark(s.left === 1)],
      ["Confidence ≥ threshold", mark(c >= data.threshold)],
    ];
    $("receipt").innerHTML = rows.map(([a, b]) => `<tr><td>${a}</td><td>${b}</td></tr>`).join("");
  }
  const tr = document.createElement("tr"); if (s.commit) tr.className = "c";
  tr.innerHTML = `<td>${i + 1}</td><td>${esc(s.word)}</td>` +
    SLOTS.map(([k]) => `<td>${esc(v[k] != null ? v[k] : show(k, f[k]))}</td>`).join("") +
    `<td>${s.left == null ? "–" : s.left}</td><td>${esc([...new Set(open.map(k => k.open))].join(", "))}</td>` +
    `<td>${Math.round(c * 100)}%</td><td>${esc(action)}</td>`;
  $("log").appendChild(tr);
  pos++;
  if (pos >= data.steps.length) finish();
  return true;
}

function finish() {
  clearInterval(timer); timer = null; $("play").innerHTML = "&#8635; Replay";
  document.querySelectorAll(".w.now").forEach(e => e.classList.remove("now"));
  const p = data.final_product, el = $("final");
  el.classList.remove("hidden");
  let html = `<div class="muted">Sentence finished. Final answer</div>`;
  html += p ? `<div class="big">${esc(p.title)}</div><div class="muted">${p.product_id} · size ${p.size || "-"} · ${rupees(p.price)}</div>`
            : `<div class="big">No product found</div>`;
  html += `<div class="muted" style="margin-top:6px">${data.searches} search${data.searches === 1 ? "" : "es"} used. `;
  html += data.early ? `First shown with ${data.early.words_to_come} of ${data.words.length} words still to come (${data.early.percent}% early).`
                     : `The agent waited for the whole sentence (not sure enough earlier).`;
  html += `</div>`;
  el.innerHTML = html;
}

$("play").onclick = async () => {
  if (timer) { clearInterval(timer); timer = null; $("play").innerHTML = "&#9654; Play"; return; }
  if (!(await load())) return;
  if (pos >= data.steps.length) reset();
  $("play").innerHTML = "&#10073;&#10073; Pause";
  stepOnce();
  timer = setInterval(stepOnce, +$("speed").value);
};
$("step").onclick = async () => { if (await load()) { if (pos >= data.steps.length) reset(); stepOnce(); } };
$("all").onclick = async () => { if (await load()) { reset(); while (pos < data.steps.length) stepOnce(); } };
$("speed").oninput = () => {
  $("speedv").textContent = (+$("speed").value / 1000).toFixed(2) + " s/word";
  if (timer) { clearInterval(timer); timer = setInterval(stepOnce, +$("speed").value); }
};
$("text").addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("play").click(); } });
$("text").addEventListener("input", () => { if (timer) { clearInterval(timer); timer = null; } $("play").innerHTML = "&#9654; Play"; });
for (const [name, text] of EXAMPLES) {
  const b = document.createElement("button"); b.className = "ex"; b.textContent = name;
  b.onclick = () => { $("text").value = text; $("play").click(); };
  $("examples").appendChild(b);
}
$("text").value = EXAMPLES[0][1];
$("speed").oninput(); buildSlots();
</script>
</body></html>
"""


def main():
    ap = argparse.ArgumentParser(description="Live demo of the shopping agent.")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    args = ap.parse_args()
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
    print(f"Demo running at {url}   (press Ctrl+C to stop)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
