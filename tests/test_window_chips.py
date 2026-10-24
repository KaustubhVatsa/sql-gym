"""Every window 'Try' chip is a text edit at engine-reported spans. Apply each one and demand a clean strict trace."""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gym
from engine.api import run_trace

FRAMES = ["ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW", "RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW",
          "ROWS BETWEEN 1 PRECEDING AND 1 FOLLOWING", "ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING"]
RANKS = ["ROW_NUMBER", "RANK", "DENSE_RANK"]
queries = {f"ex{e[0]}": e[4] for e in gym.EX}
queries.update(json.load(open(os.path.join(os.path.dirname(__file__), "extra_queries.json"))))
import games
queries.update({f"pred-{p['id']}": p["sql"] for p in games.PREDICT})
queries.update({f"bug-{b['id']}": b["broken"] for b in games.BUGS})

def edit(sql, span, text):
    return sql[:span[0]] + text + sql[span[1]:]

n, bad = 0, []
for name, sql in queries.items():
    r = run_trace(gym.DB, sql)
    if not r["ok"]:
        continue
    for b in r["blocks"]:
        for st in b["stages"]:
            if st["type"] != "window":
                continue
            for f in st["funcs"]:
                sp = f["spans"]
                edits = []
                if f["cat"] == "frame" and f["orderBy"] and (sp["frame"] or sp["inner"] is not None):
                    for fr in FRAMES:
                        edits.append(edit(sql, sp["frame"], fr) if sp["frame"] else edit(sql, [sp["inner"], sp["inner"]], " " + fr))
                if f["name"] in RANKS and sp["fn"]:
                    edits += [edit(sql, sp["fn"], x) for x in RANKS]
                if f["hasTies"] and f["tiebreak"] and sp["order"]:
                    edits.append(edit(sql, [sp["order"][1], sp["order"][1]], ", " + f["tiebreak"]))
                for q2 in edits:
                    n += 1
                    try:
                        r2 = run_trace(gym.DB, q2, strict=True)
                    except Exception as ex:
                        bad.append(f"{name}: {type(ex).__name__}: {ex} :: {q2[:120]!r}"); continue
                    if not r2["ok"] or r2["consistent"] is False:
                        bad.append(f"{name}: {r2.get('error') or 'inconsistent'} :: {q2[:120]!r}")
                    elif any(s2["type"] == "opaque" for b2 in r2["blocks"] for s2 in b2["stages"]):
                        bad.append(f"{name}: went opaque :: {q2[:120]!r}")
print(f"{n} chip rewrites checked, {len(bad)} problems")
for x in bad[:15]: print(" -", x)
sys.exit(1 if bad else 0)
