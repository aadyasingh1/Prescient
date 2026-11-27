"""Prescient as a small JSON web API. Standard library only (http.server), no API key.

    python3 api.py                      # serves on http://127.0.0.1:8100
    python3 api.py --port 9000 --host 0.0.0.0

Endpoints (all POST, JSON in, JSON out)
---------------------------------------
  /session   {}                       -> {"session": "<id>"}
  /observe   {"session": id, "word": "black"}
             -> {"session", "word_index", "word", "filters", "confidence", "commit",
                 "product", "current_commit", "searches"}
  /finish    {"session": id}          -> {"session", "final", "product", "commits", "words",
                                          "searches"}
  GET /health                          -> {"ok": true, "sessions": n}

How it uses the agent
---------------------
Each session is one real Agent from agent.py (unchanged), with its own counted
evaluate.Tools, fed exactly as evaluate.py feeds it: start() once, observe() once per word,
finish() once. "commit" is what observe() returned for this word (null if it did not commit).
"current_commit" is the latest product committed so far, and the final answer is
`agent.finish() or current_commit`: both are the same rules evaluate.py uses to score.
"product" is the catalogue row of that product id, for display.

Limits: words are cut to 60 characters, request bodies to 16 KB. At most MAX_SESSIONS live
sessions (the oldest is dropped first) and a session is dropped after SESSION_TTL seconds
without a call. A finished session answers 409 to further /observe calls.
"""
import argparse
import json
import threading
import time
import uuid
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import evaluate
from agent import Agent
from search import load_catalogue

PRODUCTS = {p["product_id"]: p for p in load_catalogue()}     # display only
CARD_KEYS = ("product_id", "title", "brand", "category", "colour", "size", "price")
MAX_SESSIONS = 1000
SESSION_TTL = 30 * 60
MAX_BODY = 16 * 1024
MAX_WORD = 60


def card(pid):
    p = PRODUCTS.get(pid)
    return {k: p[k] for k in CARD_KEYS} if p else None


class Session:
    def __init__(self):
        self.tools = evaluate.Tools()          # the organisers' counted tools
        self.agent = Agent(self.tools)
        self.agent.start("api")
        self.lock = threading.Lock()           # one call at a time per session
        self.words = 0
        self.current = None                    # latest product committed (as evaluate.py tracks it)
        self.commits = []
        self.final = None
        self.seen = time.monotonic()


SESSIONS = OrderedDict()
SESSIONS_LOCK = threading.Lock()


class ApiError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def new_session():
    s = Session()                              # built outside the lock (reads the catalogue)
    sid = uuid.uuid4().hex
    with SESSIONS_LOCK:
        now = time.monotonic()
        for old in [k for k, v in SESSIONS.items() if now - v.seen > SESSION_TTL]:
            del SESSIONS[old]
        while len(SESSIONS) >= MAX_SESSIONS:
            SESSIONS.popitem(last=False)
        SESSIONS[sid] = s
    return {"session": sid}


def get_session(body):
    sid = body.get("session")
    if not isinstance(sid, str) or not sid:
        raise ApiError(400, 'missing "session"')
    with SESSIONS_LOCK:
        s = SESSIONS.get(sid)
        if s is None or time.monotonic() - s.seen > SESSION_TTL:
            SESSIONS.pop(sid, None)
            raise ApiError(404, "unknown or expired session: POST /session for a new one")
        s.seen = time.monotonic()
        SESSIONS.move_to_end(sid)
    return sid, s


def observe(body):
    sid, s = get_session(body)
    word = body.get("word")
    if not isinstance(word, str) or not word.strip():
        raise ApiError(400, 'missing "word" (one word per call)')
    word = word.strip()[:MAX_WORD]
    with s.lock:
        if s.final is not None:
            raise ApiError(409, "session already finished")
        out = s.agent.observe(word)
        if isinstance(out, str):
            out = {"commit": out}
        out = out or {}
        s.words += 1
        commit = out.get("commit")
        if commit and commit != s.current:
            s.current = commit
            s.commits.append([s.words, commit])
        return {"session": sid, "word_index": s.words, "word": word,
                "filters": out.get("filters"), "confidence": out.get("confidence"),
                "commit": commit, "product": card(commit) if commit else None,
                "current_commit": s.current, "searches": s.tools.searches}


def finish(body):
    sid, s = get_session(body)
    with s.lock:
        if s.final is None:                    # finish() runs once; repeats return the same answer
            final = s.agent.finish() or s.current          # the same rule as evaluate.py
            s.final = {"session": sid, "final": final, "product": card(final),
                       "commits": s.commits, "words": s.words, "searches": s.tools.searches}
        return s.final


ROUTES = {"/session": lambda body: new_session(), "/observe": observe, "/finish": finish}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"              # keep-alive: one connection for a whole request
    server_version = "Prescient"

    def _json(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.split("?")[0] == "/health":
            with SESSIONS_LOCK:
                return self._json(200, {"ok": True, "sessions": len(SESSIONS)})
        self._json(404, {"error": "not found: POST /session, /observe, /finish"})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self.close_connection = True   # body not read: do not reuse the connection
                raise ApiError(413, "request body too large")
            raw = self.rfile.read(length) if length else b""
            route = ROUTES.get(self.path.split("?")[0])
            if route is None:
                raise ApiError(404, "not found: POST /session, /observe, /finish")
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                raise ApiError(400, "body is not valid JSON")
            if not isinstance(body, dict):
                raise ApiError(400, "body must be a JSON object")
            self._json(200, route(body))
        except ApiError as e:
            self._json(e.code, {"error": str(e)})
        except Exception as e:                 # never crash the server on one bad call
            self._json(500, {"error": f"{type(e).__name__}: {e}"})

    def log_message(self, *args):              # keep the terminal quiet
        pass


def make_server(host="127.0.0.1", port=8100):
    return ThreadingHTTPServer((host, port), Handler)


def main():
    ap = argparse.ArgumentParser(description="Prescient JSON API (standard library only).")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8100)
    args = ap.parse_args()
    server = make_server(args.host, args.port)
    print(f"Prescient API on http://{args.host}:{server.server_address[1]}  "
          "(POST /session, /observe, /finish; Ctrl+C stops)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
