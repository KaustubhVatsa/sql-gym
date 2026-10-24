import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gym, games, server
from engine.db import DB
from engine.api import run_trace
bad = []
db = DB(gym.DB)
for e in games.PREDICT:
    for seed in (1, 2, 3, 99):
        q = games.build_question(db, e, seed)
        if len(q["options"]) < 4 or len(set(q["options"])) != len(q["options"]): bad.append(f"{e['id']}/{seed}: options {q['options']}")
        if q["answer"] not in q["options"]: bad.append(f"{e['id']}: answer missing")
    q1, q2 = games.build_question(db, e, 5), games.build_question(db, e, 5)
    if q1 != q2: bad.append(f"{e['id']}: not deterministic")
    r = run_trace(gym.DB, e["sql"], strict=True)
    if not r["ok"] or r["notes"]: bad.append(f"{e['id']}: trace {r.get('error') or r['notes']}")
    if not games.answer_question(db, e["id"], 5, q1["answer"])["correct"]: bad.append(f"{e['id']}: right answer rejected")
    wrong = next(o for o in q1["options"] if o != q1["answer"])
    if games.answer_question(db, e["id"], 5, wrong)["correct"]: bad.append(f"{e['id']}: wrong answer accepted")
nq = games.new_question(db, 7)
if "answer" in nq or "explain" in nq: bad.append("new_question leaks the answer")
for b in games.BUGS:
    broken = server.check_bug(b["id"], b["broken"]); fixed = server.check_bug(b["id"], b["solution"])
    if broken["status"] == "pass": bad.append(f"bug {b['id']}: broken already passes")
    if fixed["status"] != "pass": bad.append(f"bug {b['id']}: solution fails: {fixed}")
    for k in ("broken", "solution"):
        r = run_trace(gym.DB, b[k], strict=True)
        if k == "solution" and (not r["ok"] or r["notes"]): bad.append(f"bug {b['id']}: solution trace {r.get('error') or r['notes']}")
print(f"{len(games.PREDICT)} questions x 4 seeds, {len(games.BUGS)} bugs checked; problems: {len(bad)}")
for x in bad: print(" -", x)


# ---- v2: blanks, join-kind, match-the-output, daily determinism
for e in games.BLANKS:
    for seed in (1, 2, 3):
        q = games.build_question(db, e, seed)
        if len(q["options"]) < 3 or len(set(q["options"])) != len(q["options"]): bad.append(f"{e['id']}: options {q['options']}")
        if e["fill"] not in q["options"]: bad.append(f"{e['id']}: right fragment missing")
        if "___" not in q["sql"] or "___" in q["fullSql"]: bad.append(f"{e['id']}: blank handling")
    full = e["sql"].replace("___", e["fill"])
    if not run_trace(gym.DB, full, strict=True)["ok"]: bad.append(f"{e['id']}: filled query fails")
    right = games.answer_question(db, e["id"], 1, e["fill"])
    if not right["correct"] or not right["fullSql"]: bad.append(f"{e['id']}: right answer rejected")
for e in games.JOINQ:
    kinds = set()
    for seed in range(1, 40):
        q = games.build_question(db, dict(e, kind="join"), seed)
        kinds.add(q["answer"])
        if sorted(q["options"]) != sorted(k + " JOIN" for k in games.JOIN_KINDS): bad.append(f"{e['id']}: options")
        if "___" not in q["sql"] or "___" in q["fullSql"]: bad.append(f"{e['id']}: join blank")
        if q["answer"].split()[0] not in q["explain"]: bad.append(f"{e['id']}: explanation")
    if len(kinds) != 4: bad.append(f"{e['id']}: only produced kinds {kinds}")
    outs = {k: sorted(map(repr, db.q(e["t"].replace("{J}", k + " JOIN"))[1])) for k in games.JOIN_KINDS}
    if len({tuple(v) for v in outs.values()}) != 4: bad.append(f"{e['id']}: the four join kinds are not all distinguishable")
for seed in (20261024, 20261025):          # the daily challenge: same seed -> same question, no storage involved
    a, b2 = games.new_question(db, seed * 10), games.new_question(db, seed * 10)
    if a != b2: bad.append("daily question not deterministic")
for m in games.MATCH:
    tgt = server.match_target(m["id"])
    if tgt["total"] == 0 or tgt["total"] > 30: bad.append(f"match {m['id']}: target size {tgt['total']}")
    if server.check_match(m["id"], m["solution"])["status"] != "pass": bad.append(f"match {m['id']}: solution fails")
    if server.check_match(m["id"], "SELECT 1")["status"] == "pass": bad.append(f"match {m['id']}: trivially passes")
    if not run_trace(gym.DB, m["solution"], strict=True)["ok"]: bad.append(f"match {m['id']}: trace")
print(f"v2: {len(games.BLANKS)} blanks, {len(games.JOINQ)} join templates, {len(games.MATCH)} matches; total problems: {len(bad)}")
for x in bad: print(" -", x)
sys.exit(1 if bad else 0)
