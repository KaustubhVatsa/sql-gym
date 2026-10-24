"""Dump traces for every query we know, then verify the row-lineage graph in Node."""
import os, sys, json, subprocess, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gym, games
from engine.api import run_trace

q = {f"ex{e[0]}": e[4] for e in gym.EX}
q.update(json.load(open(os.path.join(os.path.dirname(__file__), "extra_queries.json"))))
q.update({f"pred-{p['id']}": p["sql"] for p in games.PREDICT})
q.update({f"blank-{b['id']}": b["sql"].replace("___", b["fill"]) for b in games.BLANKS})
q.update({f"bug-{b['id']}": b["broken"] for b in games.BUGS})
q.update({f"fix-{b['id']}": b["solution"] for b in games.BUGS})
q.update({f"match-{m['id']}": m["solution"] for m in games.MATCH})
q["trap"] = "SELECT d.dept_name, e.name FROM departments d LEFT JOIN employees e ON e.dept_id = d.dept_id WHERE e.salary > 100000"
out = {}
for k, sql in q.items():
    r = run_trace(gym.DB, sql)
    if r["ok"]:
        out[k] = {"sql": sql, "blocks": r["blocks"]}
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
    json.dump(out, f)
res = subprocess.run(["node", os.path.join(os.path.dirname(__file__), "test_lineage.mjs"), f.name])
os.unlink(f.name)
sys.exit(res.returncode)
