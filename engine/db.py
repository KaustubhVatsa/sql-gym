"""Read-only, sandboxed SQLite access."""
import math
import sqlite3
import time


class QueryError(Exception):
    pass


# sqlite3 authorizer action codes we allow: SELECT, READ, FUNCTION, RECURSIVE
_ALLOWED = {21, 20, 31, 33}


def _authorizer(action, a1, a2, db_name, source):
    return sqlite3.SQLITE_OK if action in _ALLOWED else sqlite3.SQLITE_DENY


class DB:
    def __init__(self, path, timeout=3.0, max_rows=20000, sandbox=True):
        self.path, self.timeout, self.max_rows = path, timeout, max_rows
        self.con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        if sandbox:
            self.con.set_authorizer(_authorizer)
        self._deadline = 0
        self.con.set_progress_handler(self._tick, 20000)
        self.queries = 0

    def _tick(self):
        return 1 if time.monotonic() > self._deadline else 0

    def q(self, sql):
        """Run one statement -> (column_names, rows). Raises QueryError with SQLite's message."""
        self._deadline = time.monotonic() + self.timeout
        self.queries += 1
        try:
            cur = self.con.execute(sql)
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchmany(self.max_rows + 1)
            if len(rows) > self.max_rows:
                raise QueryError(f"Result has more than {self.max_rows} rows")
            return cols, rows
        except sqlite3.Error as e:
            msg = str(e)
            if "interrupted" in msg:
                msg = f"Query took longer than {self.timeout:g}s and was stopped (infinite recursion?)"
            if "not authorized" in msg:
                msg = "This playground is read-only: only SELECT queries are allowed"
            raise QueryError(msg) from None
        except OverflowError as e:
            raise QueryError(str(e)) from None

    def close(self):
        try:
            self.con.close()
        except Exception:
            pass


def clean(v):
    """Make a value JSON-safe."""
    if isinstance(v, float):
        if math.isnan(v):
            return "NaN"
        if math.isinf(v):
            return "Infinity" if v > 0 else "-Infinity"
        return v
    if isinstance(v, (bytes, bytearray, memoryview)):
        return "x'" + bytes(v).hex() + "'"
    if isinstance(v, tuple):
        return [clean(x) for x in v]
    if isinstance(v, list):
        return [clean(x) for x in v]
    if isinstance(v, dict):
        return {k: clean(x) for k, x in v.items()}
    return v


def literal(v):
    """SQL literal for a Python value (used to feed recursive-CTE iterations back in as VALUES)."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return "NULL" if (math.isnan(v) or math.isinf(v)) else repr(v)
    if isinstance(v, (bytes, bytearray)):
        return "x'" + bytes(v).hex() + "'"
    return "'" + str(v).replace("'", "''") + "'"
