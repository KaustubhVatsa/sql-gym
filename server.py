#!/usr/bin/env python3
"""SQL Gym UI server (stdlib only).  python3 server.py [--port 8765] [--no-open]"""
import json, mimetypes, os, sys, threading, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import gym
import games
from engine.api import run_trace, schema_info, table_rows
from engine.db import DB, QueryError, clean

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, "static")


def norm(rows):
    return gym.norm(rows)


def check_sql(sql, want_sql, ordered):
    db = DB(gym.DB)
    try:
        try:
            mcols, mine = db.q(sql)
        except QueryError as ex:
            return {"status": "error", "message": str(ex)}
        _, want = db.q(want_sql)
    finally:
        db.close()
    mine_n, want_n = norm(mine), norm(want)
    ok = mine_n == want_n if ordered else sorted(mine_n, key=repr) == sorted(want_n, key=repr)
    if ok:
        return {"status": "pass", "rows": len(mine)}
    wc = len(want[0]) if want else None
    mc = len(mine[0]) if mine else None
    msg = f"{len(mine)} rows, expected {len(want)}."
    if wc and mc and wc != mc:
        msg = f"Your query returns {mc} column(s); expected {wc}."
    elif ordered and sorted(mine_n, key=repr) == sorted(want_n, key=repr):
        msg = "Right rows, wrong order - this exercise checks ORDER BY."
    else:
        ms, ws = {repr(r) for r in mine_n}, {repr(r) for r in want_n}
        msg += f" {len(ws - ms)} expected row(s) missing, {len(ms - ws)} unexpected row(s)."
    return {"status": "fail", "message": msg, "rows": len(mine)}


def check_answer(n, sql):
    e = gym.BYID[n]
    return check_sql(sql, e[4], e[5])


def check_bug(bid, sql):
    b = games.BUG_BY_ID[bid]
    r = check_sql(sql, b["solution"], b["ordered"])
    if r["status"] == "pass":
        r["lesson"] = b["lesson"]
    return r


def match_path(mid):
    return os.path.join(gym.ANS, f"match_{mid}.sql")


def check_match(mid, sql):
    m = games.MATCH_BY_ID[mid]
    r = check_sql(sql, m["solution"], m["ordered"])
    if r["status"] == "pass":
        r["lesson"] = m["lesson"]
    return r


def match_status(mid):
    p = match_path(mid)
    if not os.path.exists(p) or not open(p).read().strip():
        return "none"
    return check_match(mid, open(p).read())["status"]


def match_target(mid):
    db = DB(gym.DB)
    try:
        cols, rows = db.q(games.MATCH_BY_ID[mid]["solution"])
    finally:
        db.close()
    return {"cols": cols, "rows": [list(r) for r in rows[:30]], "total": len(rows)}


def bug_path(bid):
    return os.path.join(gym.ANS, f"bug_{bid}.sql")


def bug_status(bid):
    p = bug_path(bid)
    if not os.path.exists(p) or not open(p).read().strip():
        return "none"
    return check_bug(bid, open(p).read())["status"]


def answer_path(n):
    return os.path.join(gym.ANS, f"{n}.sql")


def exercise_status(n):
    p = answer_path(n)
    if not os.path.exists(p):
        return "none"
    sql = open(p).read().strip()
    if not sql:
        return "none"
    return check_answer(n, sql)["status"]


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "SQLGym"

    def log_message(self, *a):
        pass

    def _host_ok(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost", "[::1]")

    def _json(self, obj, code=200):
        body = json.dumps(clean(obj)).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 200_000:
            raise ValueError("too large")
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        if not self._host_ok():
            return self._json({"error": "bad host"}, 403)
        path = urlparse(self.path).path
        try:
            if path == "/api/exercises":
                return self._json([{"id": e[0], "stage": e[1], "prompt": e[2], "status": exercise_status(e[0])} for e in gym.EX])
            if path.startswith("/api/exercise/"):
                n = int(path.rsplit("/", 1)[1]); e = gym.BYID[n]
                p = answer_path(n)
                return self._json({"id": n, "stage": e[1], "prompt": e[2], "hint": e[3], "ordered": e[5],
                                   "answer": open(p).read() if os.path.exists(p) else ""})
            if path.startswith("/api/solution/"):
                return self._json({"sql": gym.BYID[int(path.rsplit("/", 1)[1])][4]})
            if path == "/api/bugs":
                return self._json([{"id": b["id"], "title": b["title"], "topic": b["topic"], "status": bug_status(b["id"])} for b in games.BUGS])
            if path.startswith("/api/bug/"):
                b = games.BUG_BY_ID[path.rsplit("/", 1)[1]]
                p = bug_path(b["id"])
                return self._json({"id": b["id"], "title": b["title"], "topic": b["topic"], "story": b["story"], "broken": b["broken"],
                                   "ordered": b["ordered"], "answer": open(p).read() if os.path.exists(p) else ""})
            if path == "/api/matches":
                return self._json([{"id": m["id"], "title": m["title"], "topic": m["topic"], "status": match_status(m["id"])} for m in games.MATCH])
            if path.startswith("/api/match/"):
                m = games.MATCH_BY_ID[path.rsplit("/", 1)[1]]
                p = match_path(m["id"])
                return self._json({"id": m["id"], "title": m["title"], "topic": m["topic"], "ordered": m["ordered"], "target": match_target(m["id"]),
                                   "answer": open(p).read() if os.path.exists(p) else ""})
            if path.startswith("/api/matchhint/"):
                return self._json({"hint": games.MATCH_BY_ID[path.rsplit("/", 1)[1]]["hint"]})
            if path.startswith("/api/matchsolution/"):
                m = games.MATCH_BY_ID[path.rsplit("/", 1)[1]]
                return self._json({"sql": m["solution"], "lesson": m["lesson"]})
            if path.startswith("/api/bughint/"):
                return self._json({"hint": games.BUG_BY_ID[path.rsplit("/", 1)[1]]["hint"]})
            if path.startswith("/api/bugsolution/"):
                b = games.BUG_BY_ID[path.rsplit("/", 1)[1]]
                return self._json({"sql": b["solution"], "lesson": b["lesson"]})
            if path == "/api/predict/new":
                qs = parse_qs(urlparse(self.path).query)
                seed = int((qs.get("seed") or ["1"])[0])
                excl = [x for x in (qs.get("exclude") or [""])[0].split(",") if x]
                db = DB(gym.DB)
                try:
                    return self._json(games.new_question(db, seed, excl))
                finally:
                    db.close()
            if path == "/api/schema":
                db = DB(gym.DB, sandbox=False)
                try:
                    sch = schema_info(db)
                    for t in sch:
                        t["data"] = table_rows(db, t["name"], 300)
                    return self._json(sch)
                finally:
                    db.close()
        except (KeyError, ValueError):
            return self._json({"error": "not found"}, 404)
        return self._static(path)

    def do_POST(self):
        if not self._host_ok() or "application/json" not in (self.headers.get("Content-Type") or ""):
            return self._json({"error": "forbidden"}, 403)
        path = urlparse(self.path).path
        try:
            data = self._body()
            if path == "/api/trace":
                r = run_trace(gym.DB, data.get("sql", ""))
                ex, bug, mt = data.get("exercise"), data.get("bug"), data.get("match")
                if ex and r.get("ok"):
                    r["check"] = check_answer(int(ex), data["sql"])
                elif bug and r.get("ok"):
                    r["check"] = check_bug(bug, data["sql"])
                elif mt and r.get("ok"):
                    r["check"] = check_match(mt, data["sql"])
                return self._json(r)
            if path == "/api/predict/answer":
                db = DB(gym.DB)
                try:
                    return self._json(games.answer_question(db, data["id"], data["seed"], str(data["choice"])))
                finally:
                    db.close()
            if path == "/api/matchcheck":
                return self._json(check_match(data["id"], data["sql"]))
            if path == "/api/bugcheck":
                return self._json(check_bug(data["id"], data["sql"]))
            if path == "/api/check":
                return self._json(check_answer(int(data["id"]), data["sql"]))
        except (KeyError, ValueError, TypeError):
            return self._json({"error": "bad request"}, 400)
        return self._json({"error": "not found"}, 404)

    def do_PUT(self):
        if not self._host_ok() or "application/json" not in (self.headers.get("Content-Type") or ""):
            return self._json({"error": "forbidden"}, 403)
        path = urlparse(self.path).path
        try:
            if path.startswith("/api/matchanswer/"):
                mid = path.rsplit("/", 1)[1]
                games.MATCH_BY_ID[mid]
                sql = self._body().get("sql", "")
                p = match_path(mid)
                if sql.strip():
                    os.makedirs(gym.ANS, exist_ok=True)
                    open(p, "w").write(sql)
                elif os.path.exists(p):
                    os.remove(p)
                return self._json({"ok": True})
            if path.startswith("/api/bunganswer/"):
                bid = path.rsplit("/", 1)[1]
                b = games.BUG_BY_ID[bid]
                sql = self._body().get("sql", "")
                p = bug_path(bid)
                if sql.strip() and sql.strip() != b["broken"].strip():
                    os.makedirs(gym.ANS, exist_ok=True)
                    open(p, "w").write(sql)
                elif os.path.exists(p):
                    os.remove(p)
                return self._json({"ok": True})
            if path.startswith("/api/answer/"):
                n = int(path.rsplit("/", 1)[1]); gym.BYID[n]
                sql = self._body().get("sql", "")
                p = answer_path(n)
                if sql.strip():
                    os.makedirs(gym.ANS, exist_ok=True)
                    open(p, "w").write(sql)
                elif os.path.exists(p):
                    os.remove(p)
                return self._json({"ok": True})
        except (KeyError, ValueError, TypeError):
            return self._json({"error": "bad request"}, 400)
        return self._json({"error": "not found"}, 404)

    def _static(self, path):
        if path == "/":
            path = "/index.html"
        fp = os.path.realpath(os.path.join(STATIC, path.lstrip("/")))
        if not fp.startswith(STATIC + os.sep) or not os.path.isfile(fp):
            return self._json({"error": "not found"}, 404)
        data = open(fp, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", (mimetypes.guess_type(fp)[0] or "application/octet-stream") + ("; charset=utf-8" if fp.endswith((".html", ".js", ".css")) else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def main():
    port = 8765
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    ThreadingHTTPServer.allow_reuse_address = True
    ThreadingHTTPServer.daemon_threads = True
    srv = None
    for p in range(port, port + 20):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", p), H); port = p; break
        except OSError:
            continue
    if srv is None:
        sys.exit("no free port")
    url = f"http://127.0.0.1:{port}/"
    print(f"SQL Gym running at {url}  (Ctrl+C to stop)")
    if "--no-open" not in sys.argv:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
