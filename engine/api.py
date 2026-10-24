"""Glue: run a query, trace it, and package everything as JSON-ready data."""
import difflib
import os
import re
import time
from functools import lru_cache

from .db import DB, QueryError, clean
from .sqlparse import ParseError, Unsupported
from .trace import Degrade, trace_query

DISPLAY_ROWS = 500


def schema_info(db):
    """Tables, columns, row counts and guessed relationships (this schema declares no foreign keys)."""
    _, tabs = db.q("SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name")
    out, pk_names = [], {}
    for (name,) in tabs:
        _, info = db.q(f'PRAGMA table_info("{name}")')
        _, cnt = db.q(f'SELECT COUNT(*) FROM "{name}"')
        cols = [{"name": r[1], "type": r[2] or "", "pk": bool(r[5])} for r in info]
        out.append({"name": name, "cols": cols, "rows": cnt[0][0]})
        for c in cols:
            if c["pk"]:
                pk_names[c["name"].lower()] = name
    for t in out:
        for c in t["cols"]:
            tgt = pk_names.get(c["name"].lower())
            if tgt and tgt != t["name"] and not c["pk"]:
                c["ref"] = f"{tgt}.{c['name']}"
            if c["name"].lower() in ("manager_id", "parent_id") and any(x["name"] == t["name"] for x in out):
                pk = next((x["name"] for x in t["cols"] if x["pk"]), None)
                if pk:
                    c["ref"] = f"{t['name']}.{pk}"
    return out


@lru_cache(maxsize=4)
def _pks_cached(path, mtime):
    adm = DB(path, sandbox=False)
    try:
        return {t["name"].lower(): [c["name"] for c in t["cols"] if c["pk"]] for t in schema_info(adm)}
    finally:
        adm.close()


def primary_keys(path):
    """{table_lower: [pk columns]}, cached per database file version."""
    return _pks_cached(path, os.stat(path).st_mtime_ns)


def table_rows(db, name, limit=200):
    names = {t[0] for t in db.q("SELECT name FROM sqlite_master WHERE type IN ('table','view')")[1]}
    if name not in names:
        raise QueryError(f"no such table: {name}")
    cols, rows = db.q(f'SELECT * FROM "{name}" LIMIT {int(limit)}')
    return {"cols": cols, "rows": clean(rows)}


def hint_for(db, msg):
    m = re.match(r"no such (column|table): (.+)", msg)
    if not m:
        return None
    kind, word = m.group(1), m.group(2).split(".")[-1].strip('"')
    try:
        adm = DB(db.path, sandbox=False)
        try:
            sch = schema_info(adm)
        finally:
            adm.close()
    except Exception:
        return None
    if kind == "table":
        pool = [t["name"] for t in sch]
    else:
        pool = sorted({c["name"] for t in sch for c in t["cols"]})
    best = difflib.get_close_matches(word, pool, n=2, cutoff=0.6)
    if best:
        return "Did you mean " + " or ".join(f"'{b}'" for b in best) + "?"
    return None


def query_plan(db, text):
    """EXPLAIN QUERY PLAN as [{id, parent, detail}] (how SQLite physically runs the query)."""
    try:
        _, rows = db.q("EXPLAIN QUERY PLAN " + text.strip().rstrip(";"))
        return [{"id": r[0], "parent": r[1], "detail": r[3]} for r in rows]
    except QueryError:
        return []


def run_trace(db_path, sql, strict=False):
    t0 = time.perf_counter()
    db = DB(db_path)
    try:
        text = sql.strip()
        if not text:
            return {"ok": False, "error": "Write a query to see it run.", "empty": True}
        try:
            cols, rows = db.q(text.rstrip(";").strip() if text.rstrip().endswith(";") and text.count(";") == 1 else text)
        except QueryError as e:
            msg = str(e)
            return {"ok": False, "error": msg, "hint": hint_for(db, msg)}
        result = {"cols": cols, "rows": clean(rows[:DISPLAY_ROWS]), "total": len(rows), "truncated": len(rows) > DISPLAY_ROWS}
        blocks, notes = [], []
        try:
            blocks, notes = trace_query(db, text, strict=strict, pks=primary_keys(db_path))
        except (ParseError, Unsupported) as e:
            notes.append(f"Step-by-step view isn't available for this query yet ({e}). The result is still correct.")
        except Degrade as e:
            notes.append(f"Step-by-step view unavailable: {e}.")
        except QueryError as e:
            if strict:
                raise
            notes.append(f"Step-by-step view unavailable: {e}.")
        except Exception as e:  # never let the visualiser take the editor down
            if strict:
                raise
            notes.append("Step-by-step view unavailable for this query.")
        consistent = None
        if blocks:
            fin = blocks[-1]["rows"]
            consistent = sorted(map(repr, fin)) == sorted(map(repr, rows[:len(fin)])) if len(rows) == len(fin) else None
        return clean({"ok": True, "sql": text, "result": result, "blocks": blocks, "notes": notes, "plan": query_plan(db, text),
                      "consistent": consistent, "ms": round((time.perf_counter() - t0) * 1000, 1), "queries": db.queries})
    finally:
        db.close()
