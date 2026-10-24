#!/usr/bin/env python3
"""Run every test suite:  python3 tests/run_all.py   (Node is needed only for the frontend-logic suites)."""
import os, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
SUITES = [
    ("engine: strict trace of every query", [sys.executable, "test_engine.py"], False),
    ("engine: independent invariants", [sys.executable, "test_invariants.py"], False),
    ("engine: insights (+ false-positive sweep)", [sys.executable, "test_insights.py"], False),
    ("games: questions, bugs, matches", [sys.executable, "test_games.py"], False),
    ("window chips: every rewrite traces", [sys.executable, "test_window_chips.py"], False),
    ("row lineage graph", [sys.executable, "test_lineage.py"], True),
    ("formatter: executes before/after", [sys.executable, "test_format.py"], True),
    ("frontend logic", ["node", "test_frontend.mjs"], True),
]
failed = []
for name, cmd, needs_node in SUITES:
    if needs_node and not shutil.which("node"):
        print(f"SKIP  {name} (node not found)")
        continue
    t = time.time()
    r = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True)
    last = (r.stdout.strip().splitlines() or [""])[-1]
    print(f"{'ok  ' if r.returncode == 0 else 'FAIL'}  {name:46} {time.time() - t:5.1f}s  {last[:70]}")
    if r.returncode:
        failed.append(name)
        print(r.stdout[-1500:], r.stderr[-800:])
print("\nall suites passed" if not failed else f"\nFAILED: {', '.join(failed)}")
sys.exit(1 if failed else 0)
