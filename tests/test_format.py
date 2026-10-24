"""The formatter may only change whitespace and keyword case. Format every known query in Node, then execute the
original and the formatted version in SQLite and demand identical rows."""
import json, os, sqlite3, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gym, games

HERE = os.path.dirname(os.path.abspath(__file__))
queries = {f"ex{e[0]}": e[4] for e in gym.EX}
queries.update(json.load(open(os.path.join(HERE, "extra_queries.json"))))
queries.update({f"pred-{p['id']}": p["sql"] for p in games.PREDICT})
queries.update({f"bug-{b['id']}": b["broken"] for b in games.BUGS})
queries.update({f"match-{m['id']}": m["solution"] for m in games.MATCH})
queries["messy-comments"] = "-- leading\nSELECT name, -- trailing\n  salary /* mid */ FROM employees\n-- own line\nWHERE salary > 1 -- end"
queries["unary-minus"] = "SELECT -salary, 3 - -2, +1, salary*-1 FROM employees WHERE salary BETWEEN -5 AND 999999 AND dept_id IN (1,2)"
queries["long-case"] = "SELECT name, CASE WHEN salary >= 150000 THEN 'very high earner band' WHEN salary >= 100000 THEN 'middle earner band' ELSE 'low earner band' END AS band FROM employees"
queries = {k: v for k, v in queries.items() if ";" not in v.rstrip().rstrip(";")}

res = subprocess.run(["node", os.path.join(HERE, "format_cli.mjs")], input=json.dumps(queries), capture_output=True, text=True)
if res.returncode:
    print(res.stderr); sys.exit(1)
out = json.loads(res.stdout)
con = sqlite3.connect(f"file:{gym.DB}?mode=ro", uri=True)
bad = []
for k, sql in queries.items():
    f = out[k]
    if not f["idempotent"]: bad.append(f"{k}: formatting twice changes the text")
    if not f["sameTokens"]: bad.append(f"{k}: tokens changed")
    def outcome(q):                       # rows, or the error message (some Bug Hunt queries fail on purpose)
        try:
            return ("rows", con.execute(q).fetchall())
        except sqlite3.Error as e:
            return ("error", str(e))
    if outcome(sql) != outcome(f["fmt"]): bad.append(f"{k}: formatted query behaves differently")
print(f"{len(queries)} queries formatted and executed; problems: {len(bad)}")
for b in bad[:15]: print(" -", b)
sys.exit(1 if bad else 0)
