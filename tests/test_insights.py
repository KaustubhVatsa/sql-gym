"""Each new insight must fire on the query that deserves it and stay silent on the corrected one."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gym
from engine.api import run_trace

def texts(sql):
    r = run_trace(gym.DB, sql, strict=True)
    return [t for b in r["blocks"] for s in b["stages"] for k, t in s["insights"] if k in ("warn", "tip")]

CASES = [  # (name, sql, substring expected in some warn/tip or None for "no such insight")
 ("limit tie", "SELECT name, salary FROM employees ORDER BY salary DESC LIMIT 2", "LIMIT cut falls inside a tie"),
 ("limit clean", "SELECT name, salary FROM employees ORDER BY salary DESC LIMIT 1", None),
 ("limit tiebreak", "SELECT name, salary FROM employees ORDER BY salary DESC, emp_id LIMIT 2", None),
 ("bare col", "SELECT name, COUNT(*) FROM employees GROUP BY dept_id", "neither in GROUP BY"),
 ("bare col pk ok", "SELECT c.name, COUNT(o.order_id) FROM customers c LEFT JOIN orders o ON o.customer_id=c.customer_id GROUP BY c.customer_id", None),
 ("bare col minmax ok", "SELECT name, MAX(salary) FROM employees GROUP BY dept_id", None),
 ("int division", "SELECT SUM(qty) / COUNT(*) AS a FROM order_items", "Integer division"),
 ("float division ok", "SELECT SUM(qty) * 1.0 / COUNT(*) AS a FROM order_items", None),
 ("text date", "SELECT order_id FROM orders WHERE order_date > '2024-1-15'", "zero-padded ISO date"),
 ("iso date ok", "SELECT order_id FROM orders WHERE order_date > '2024-01-15'", None),
 ("not in null", "SELECT dept_name FROM departments WHERE dept_id NOT IN (SELECT dept_id FROM employees)", "can never be TRUE"),
 ("not in ok", "SELECT dept_name FROM departments WHERE dept_id NOT IN (SELECT dept_id FROM employees WHERE dept_id IS NOT NULL)", None),
 ("like", "SELECT name FROM employees WHERE name LIKE 'a%'", "LIKE ignores case"),
]
WATCH = ("LIMIT cut falls", "neither in GROUP BY", "Integer division", "zero-padded ISO", "can never be TRUE")
bad = []
for name, sql, want in CASES:
    t = texts(sql)
    if want and not any(want in x for x in t): bad.append(f"{name}: expected '{want}' but got {t}")
    if want is None and any(w in x for x in t for w in WATCH): bad.append(f"{name}: unexpected insight {t}")
# false-positive sweep: the 25 reference solutions must not trigger the new warnings spuriously
for e in gym.EX:
    t = texts(e[4])
    hits = [x for x in t if any(w in x for w in WATCH)]
    if hits: bad.append(f"exercise {e[0]} solution flagged: {hits}")
print(f"{len(CASES)} insight cases + 25 solution sweeps; problems: {len(bad)}")
for b in bad: print(" -", b)
sys.exit(1 if bad else 0)
