"""Independent checks that what the UI *shows* in intermediate stages is correct."""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gym
from engine.api import run_trace

def close(a, b):
    if a is None or b is None: return a is None and b is None
    try: return abs(float(a) - float(b)) < 1e-6
    except (TypeError, ValueError): return a == b

def agg(name, vals):
    nn = [v for v in vals if v is not None]
    if name == "COUNT": return len(nn)
    if name == "SUM": return sum(nn) if nn else None
    if name == "TOTAL": return float(sum(nn))
    if name == "AVG": return sum(nn) / len(nn) if nn else None
    if name == "MIN": return min(nn) if nn else None
    if name == "MAX": return max(nn) if nn else None
    raise KeyError(name)

problems, checked = [], 0
def bad(q, msg): problems.append(f"{q}: {msg}")

def check_stage(q, st):
    global checked
    t = st["type"]
    if t == "window":
        for f in st["funcs"]:
            if f["cat"] == "frame" and f["name"] in ("COUNT", "SUM", "AVG", "MIN", "MAX", "TOTAL"):
                for p in f["partitions"]:
                    for pos, e in enumerate(p["rows"]):
                        ent = f["entries"][e]
                        s, en = ent["frame"]; ex = set(ent.get("excl", []))
                        vals = [f["entries"][p["rows"][x]]["arg"] for x in range(s, en + 1) if x not in ex]
                        if f["arg"] is None: vals = [1] * len(vals)   # COUNT(*)
                        got = agg(f["name"], vals) if (vals or f["name"] in ("COUNT", "TOTAL")) else None
                        checked += 1
                        if not close(got, ent["value"]): bad(q, f"{f['text']} row {ent['row']}: frame says {got}, SQLite says {ent['value']}")
            if f["cat"] == "offset":
                for p in f["partitions"]:
                    for pos, e in enumerate(p["rows"]):
                        ent = f["entries"][e]
                        if ent["src"] is not None:
                            checked += 1
                            exp = f["entries"][p["rows"][ent["src"]]]["arg"]
                            if not close(exp, ent["value"]): bad(q, f"{f['text']} src value {exp} != {ent['value']}")
    elif t == "group":
        argpos = {}; j = 0
        for ai, a in enumerate(st["aggs"]):
            if a["arg"]: argpos[ai] = j; j += 1
        for g in st["groups"]:
            for ai, a in enumerate(st["aggs"]):
                if a["name"] not in ("COUNT", "SUM", "AVG", "MIN", "MAX", "TOTAL"): continue
                vals = [st["rowArgs"][m][argpos[ai]] for m in g["members"]] if ai in argpos else [1] * len(g["members"])
                if a["distinct"]: vals = list(dict.fromkeys(vals))
                checked += 1
                if not close(agg(a["name"], vals), g["aggs"][ai]): bad(q, f"{a['text']}: recomputed {agg(a['name'], vals)} vs {g['aggs'][ai]}")
        if sum(len(g["members"]) for g in st["groups"]) != len(st["rows"]): bad(q, "group members don't cover input rows")
    if t == "where":
        checked += 1
        if sum(st["keep"]) != st["rowsOut"] or len(st["rows"]) != st["rowsIn"]: bad(q, "where counts")
        if st.get("combine"):                       # three-valued logic recomputed independently from the per-term verdicts
            for i, vs in enumerate(st["verdicts"]):
                if st["combine"] == "AND": exp = 0 if 0 in vs else (None if None in vs else 1)
                else: exp = 1 if 1 in vs else (None if None in vs else 0)
                checked += 1
                if exp != st["wholeV"][i]: bad(q, f"{st['combine']} of {vs} should be {exp}, trace says {st['wholeV'][i]}")
    if t in ("where", "select") and st.get("subs"):
        for sb in st["subs"]:
            if not sb["correlated"]:
                continue
            per = sb["per"]
            if t == "where" and len(st["preds"]) == 1 and sb["kind"] in ("EXISTS", "NOT EXISTS") and st["preds"][0].upper().startswith(sb["kind"]):
                for i, d in enumerate(per):               # EXISTS <=> rows returned
                    exp = 1 if (d["n"] > 0) == (sb["kind"] == "EXISTS") else 0
                    checked += 1
                    if st["verdicts"][i][0] != exp: bad(q, f"{sb['kind']} row {i}: n={d['n']} but verdict {st['verdicts'][i][0]}")
            if t == "select" and sb["kind"] == "SCALAR":
                j = next((k for k, e in enumerate(st["exprs"]) if e["text"].replace(" ", "") == "(" + sb["text"].replace(" ", "") + ")"), None)
                if j is not None:
                    for o, p in enumerate(st["src"]):    # the output cell is the first returned value (NULL when no rows)
                        d = per[p]; exp = d["rows"][0][0] if d["n"] and d["rows"] else None
                        checked += 1
                        if not close(exp, st["rows"][o][j]): bad(q, f"scalar sub-query output {st['rows'][o][j]} != first row {exp}")
    elif t == "join":
        checked += 1
        k = st["joinType"]; out = st["out"]
        pairs = {tuple(p) for p in st["pairs"]}
        n_expected = len(pairs)
        if k in ("LEFT", "FULL"): n_expected += len(st["leftUnmatched"])
        if k in ("RIGHT", "FULL"): n_expected += len(st["rightUnmatched"])
        if len(out) != n_expected: bad(q, f"{k} join: {len(out)} rows, expected {n_expected}")
        for li, ri, row in zip(st["outLi"], st["outRi"], out):
            nl = len(st["leftCols"])
            if li is not None and row[:nl] != st["left"][li]: bad(q, "left row mismatch")
            if ri is not None and row[nl:] != st["right"][ri]: bad(q, "right row mismatch")
            if li is not None and ri is not None and (li, ri) not in pairs: bad(q, "pair missing")
    elif t == "order":
        checked += 1
        if [st["before"][b] if b is not None else None for b in st["from"]] != st["rows"]: bad(q, "order mapping")

queries = {f"ex{e[0]}": e[4] for e in gym.EX}
queries.update(json.load(open(os.path.join(os.path.dirname(__file__), "extra_queries.json"))))
for name, sql in queries.items():
    r = run_trace(gym.DB, sql, strict=True)
    for b in r["blocks"]:
        for st in b["stages"]:
            check_stage(name, st)
print(f"{checked} invariants checked, {len(problems)} problems")
for p in problems[:30]: print(" -", p)

# ---- join-kind strip: the counts shown for INNER/LEFT/RIGHT/FULL must equal what the rewritten join really returns
def join_kind_check():
    n_ok, probs = 0, []
    for name, sql in queries.items():
        r = run_trace(gym.DB, sql)
        if not r["ok"]:
            continue
        for b in r["blocks"]:
            for st in b["stages"]:
                if st["type"] != "join" or not st.get("kwSpan") or not (st["on"] or st["using"]):
                    continue
                a, z = st["kwSpan"]
                for k in ("INNER", "LEFT", "RIGHT", "FULL"):
                    n = len(st["pairs"]) + (len(st["leftUnmatched"]) if k in ("LEFT", "FULL") else 0) + (len(st["rightUnmatched"]) if k in ("RIGHT", "FULL") else 0)
                    # rewrite this join's keyword in the *whole* query and read the same join stage back
                    alt = run_trace(gym.DB, sql[:a] + k + " JOIN" + sql[z:], strict=True)
                    if not alt["ok"]:          # e.g. an outer join inside a recursive CTE recurses forever: SQLite rejects it too
                        continue
                    js = [s2 for b2 in alt["blocks"] if b2["id"] == b["id"] and b2["title"] == b["title"] for s2 in b2["stages"]
                          if s2["type"] == "join" and s2["rightAlias"] == st["rightAlias"] and s2["index"] == st["index"]]
                    if not js and len(alt["blocks"]) != len(r["blocks"]):
                        continue                 # the rewrite changed the block structure (e.g. recursion depth)
                    n_ok += 1
                    if not js or len(js[0]["out"]) != n:
                        probs.append(f"{name}: {k} JOIN {st['rightAlias']} expected {n}, got {len(js[0]['out']) if js else None}")
    return n_ok, probs
n_ok, probs = join_kind_check()
print(f"{n_ok} join-kind counts checked, {len(probs)} problems")
for x in probs[:20]: print(" -", x)
sys.exit(1 if (problems or probs) else 0)
