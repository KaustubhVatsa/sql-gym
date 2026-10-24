"""Run every exercise solution (and extra edge-case queries) through the tracer in strict mode and
check that the traced result matches what SQLite returns."""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gym
from engine.api import run_trace

DB = gym.DB
fails = 0
EXPECT_NOTES = {'rec_limit_hits_anchor'}
EXPECT_OPAQUE = {'values', 'rec_limit_hits_anchor'}

def check(name, sql, expect_blocks=True):
    global fails
    try:
        r = run_trace(DB, sql, strict=True)
    except Exception as e:
        import traceback
        tb = traceback.extract_tb(e.__traceback__)
        where = [f"{f.name}:{f.lineno}" for f in tb if 'engine' in f.filename][-2:]
        print(f"CRASH {name}: {type(e).__name__}: {e}  @ {' <- '.join(reversed(where))}"); fails += 1; return None
    if not r["ok"]:
        print(f"ERR   {name}: {r['error']}"); fails += 1; return r
    bad = []
    if expect_blocks and not r["blocks"]: bad.append("no blocks")
    if r["notes"] and name not in EXPECT_NOTES: bad.append("notes: " + "; ".join(r["notes"]))
    if r["consistent"] is False: bad.append("INCONSISTENT with real result")
    opaque = [s["message"] for b in r["blocks"] for s in b["stages"] if s["type"] == "opaque"]
    if opaque and name not in EXPECT_OPAQUE: bad.append("opaque: " + "; ".join(opaque))
    if bad:
        fails += 1; print(f"FAIL  {name}: " + " | ".join(bad))
    else:
        st = [s["type"] for b in r["blocks"] for s in b["stages"]]
        print(f"ok    {name}: {len(r['blocks'])} blocks, stages={','.join(st)}")
    return r

if __name__ == "__main__":
    for e in gym.EX:
        check(f"ex{e[0]}", e[4])
    extra = json.load(open(os.path.join(os.path.dirname(__file__), "extra_queries.json")))
    for name, sql in extra.items():
        check(name, sql)
    print("\nFAILURES:", fails)
    sys.exit(1 if fails else 0)
