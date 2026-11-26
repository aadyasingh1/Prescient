"""Measure Prescient's real compute cost on dev (writes nothing; prints JSON for results/cost.md).

Runs all dev requests REPEATS times through the same Tools the scorer uses and times, per
request, the CPU time of start() + every observe() + finish(). Searches happen inside those
calls, so they are included. Search time is also timed on its own to show its share.
Run: python3 tests/measure_cost.py
"""
import json
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import evaluate  # noqa: E402  (the organisers' Tools and agent loader, unchanged)

REPEATS = 5


class TimedTools(evaluate.Tools):
    """Same as the scorer's Tools, plus a CPU-time counter for search calls."""
    def __init__(self):
        super().__init__()
        self.search_cpu = 0.0

    def search(self, query="", filters=None, k=10):
        t0 = time.process_time()
        try:
            return super().search(query, filters, k)
        finally:
            self.search_cpu += time.process_time() - t0


def sh(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:
        return ""


def machine():
    hw = sh(["system_profiler", "SPHardwareDataType"])
    info = {k.strip(): v.strip() for k, v in (l.split(":", 1) for l in hw.splitlines() if ":" in l)}
    return {
        "model_name": info.get("Model Name", ""),
        "model_id": sh(["sysctl", "-n", "hw.model"]),
        "chip": info.get("Chip", "") or sh(["sysctl", "-n", "machdep.cpu.brand_string"]),
        "cores": info.get("Total Number of Cores", ""),
        "memory": info.get("Memory", ""),
        "macos": platform.mac_ver()[0],
        "python": platform.python_version(),
        "python_impl": platform.python_implementation(),
    }


def main():
    requests = [json.loads(l) for l in open(ROOT / "dev.jsonl") if l.strip()]
    t0 = time.process_time()
    agent_cls = evaluate.load_agent(str(ROOT / "agent.py"))
    tools = TimedTools()
    agent = agent_cls(tools)
    setup_cpu = time.process_time() - t0              # one-off: import + vocabularies

    per_req, search_cpu, searches, words, runs = [], [], 0, 0, []
    for _ in range(REPEATS):
        run_cpu, w0 = 0.0, time.perf_counter()
        for req in requests:
            tools.searches, tools.search_cpu = 0, 0.0
            c0 = time.process_time()
            agent.start(req["id"])
            for word in req["words"]:
                agent.observe(word)
            agent.finish()
            c = time.process_time() - c0
            per_req.append(c); search_cpu.append(tools.search_cpu)
            run_cpu += c; searches += tools.searches; words += len(req["words"])
        runs.append({"cpu_s": run_cpu, "wall_s": time.perf_counter() - w0})

    n = len(per_req)
    q = statistics.quantiles(per_req, n=100)
    print(json.dumps({
        "machine": machine(),
        "requests": len(requests), "repeats": REPEATS, "timed_requests": n,
        "setup_cpu_ms": setup_cpu * 1e3,
        "mean_cpu_ms": statistics.mean(per_req) * 1e3,
        "median_cpu_ms": statistics.median(per_req) * 1e3,
        "p95_cpu_ms": q[94] * 1e3, "max_cpu_ms": max(per_req) * 1e3,
        "mean_search_cpu_ms": statistics.mean(search_cpu) * 1e3,
        "searches_per_request": searches / n,
        "mean_cpu_per_word_ms": sum(per_req) / words * 1e3,
        "words_per_request": words / n,
        "runs": runs,
        "run_mean_cpu_ms_spread": [r["cpu_s"] / len(requests) * 1e3 for r in runs],
    }, indent=2))


if __name__ == "__main__":
    main()
