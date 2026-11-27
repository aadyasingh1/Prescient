"""Latency of api.py, measured over real HTTP on this machine.

    python3 tests/bench_api.py            # all of dev.jsonl -> results/api_latency.md

Starts api.py's server in this process (a free port on 127.0.0.1), then plays every dev
request through it as a client would: POST /session, one POST /observe per word,
POST /finish, over one keep-alive connection. Each call is timed from just before the
request is sent to just after the full JSON reply is read (round trip: HTTP, JSON and the
agent's own work, searches included).

It also scores the API's answers with the organisers' evaluate.score_one() and checks
they equal evaluate.run() on agent.py directly: the API must change nothing.
dev.jsonl is read here, by the benchmark, never by the agent or the API.
"""
import http.client
import json
import platform
import statistics
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import api        # noqa: E402
import evaluate   # noqa: E402
from agent import Agent  # noqa: E402


def stats(ms):
    ms = sorted(ms)
    return {"calls": len(ms), "mean": statistics.mean(ms), "p50": ms[len(ms) // 2],
            "p95": ms[int(0.95 * (len(ms) - 1))], "max": ms[-1]}


def main():
    with open(HERE / "dev.jsonl", encoding="utf-8") as fh:
        reqs = [json.loads(line) for line in fh if line.strip()]
    server = api.make_server("127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1])

    def call(path, body):
        data = json.dumps(body)
        t0 = time.perf_counter()
        conn.request("POST", path, data, {"Content-Type": "application/json"})
        resp = conn.getresponse()
        out = json.loads(resp.read())
        ms = 1000 * (time.perf_counter() - t0)
        if resp.status != 200:
            raise SystemExit(f"{path} -> {resp.status}: {out}")
        return out, ms

    times = {"/session": [], "/observe": [], "/finish": []}
    via_api, per_request = [], []
    for _ in range(2):                                   # warm-up (not timed)
        sid = call("/session", {})[0]["session"]
        call("/observe", {"session": sid, "word": "hello"})
        call("/finish", {"session": sid})
    t_all = time.perf_counter()
    for req in reqs:
        t_req = time.perf_counter()
        out, ms = call("/session", {})
        times["/session"].append(ms)
        sid = out["session"]
        reported = None
        for w in req["words"]:
            out, ms = call("/observe", {"session": sid, "word": w})
            times["/observe"].append(ms)
            if out["filters"] is not None:
                reported = out["filters"]
        fin, ms = call("/finish", {"session": sid})
        times["/finish"].append(ms)
        per_request.append(1000 * (time.perf_counter() - t_req))
        via_api.append(evaluate.score_one(req, [tuple(c) for c in fin["commits"]], fin["final"],
                                          fin["searches"], reported))
    total_s = time.perf_counter() - t_all
    conn.close()
    server.shutdown()

    direct, _ = evaluate.run(Agent, reqs)
    a, d = evaluate.summarise(via_api), evaluate.summarise(direct)
    same = all((x["final"], x["commits"], x["searches"]) == (y["final"], y["commits"], y["searches"])
               for x, y in zip(via_api, direct))

    rows = {k: stats(v) for k, v in times.items()}
    rows["whole request"] = stats(per_request)
    lines = [
        "# api.py latency (measured)",
        "",
        f"Made by `python3 tests/bench_api.py` on {time.strftime('%Y-%m-%d')}: all {len(reqs)} dev requests "
        f"({len(times['/observe'])} words) through `api.py` over HTTP on 127.0.0.1, one keep-alive "
        "connection, one call at a time. Round trip per call: HTTP + JSON + the agent's own work "
        "(searches included).",
        "",
        f"Machine: {platform.machine()}, {platform.system()} {platform.release()}, "
        f"Python {platform.python_version()}.",
        "",
        "| Call | Calls | Mean ms | p50 ms | p95 ms | Max ms |",
        "|---|---|---|---|---|---|",
    ]
    for k, s in rows.items():
        lines.append(f"| `{k}` | {s['calls']} | {s['mean']:.3f} | {s['p50']:.3f} | {s['p95']:.3f} | {s['max']:.3f} |"
                     if k.startswith("/") else
                     f"| {k} (session + words + finish) | {s['calls']} | {s['mean']:.2f} | {s['p50']:.2f} | {s['p95']:.2f} | {s['max']:.2f} |")
    lines += [
        "",
        f"All calls together: {sum(len(v) for v in times.values())} calls in {total_s:.2f} s "
        f"({sum(len(v) for v in times.values()) / total_s:.0f} calls/s on one connection).",
        "",
        "## The API changes nothing",
        "",
        "| | Score | Accuracy | Wrong commits | Earliness | Searches/request |",
        "|---|---|---|---|---|---|",
        f"| through api.py | {a['score']:.1f} | {a['accuracy']:.3f} | {a['wrong_commits']} | {100 * a['early_share']:.1f}% | {a['searches']:.2f} |",
        f"| evaluate.run(agent.py) | {d['score']:.1f} | {d['accuracy']:.3f} | {d['wrong_commits']} | {100 * d['early_share']:.1f}% | {d['searches']:.2f} |",
        "",
        f"Same final answer, commits and search count on every request: **{'yes' if same else 'NO'}**.",
        "",
    ]
    out = HERE / "results" / "api_latency.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"written: {out.relative_to(HERE)}")
    if not same:
        raise SystemExit("API results differ from evaluate.run()")


if __name__ == "__main__":
    main()
