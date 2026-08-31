"""
Tests for the Money tab (finance_hub.py) and the shared finance action layer.

    python test_money.py

Copies oggainz.db to a throwaway file first and points the app at the copy.
Deliberately makes NO network calls: everything here is about the gate, the
off-switch, and the contract between finance_hub and build_finance. Live Notion
behaviour is exercised by actually opening the tab, not by a test that would
need a token and a working connection to pass.

The checks that matter:
  * the team password cannot reach ANY money route, or even see the link;
  * with no Notion token the whole tab is off rather than half-broken;
  * finance_hub._build() unpacks build_finance exactly as build_finance.main()
    does — that is the thing most likely to silently break later, because the
    two live in different files and nothing else couples them.
"""

import inspect
import os
import shutil
import sys
import tempfile

APP_DIR = os.path.dirname(os.path.abspath(__file__))
LIVE_DB = os.path.join(APP_DIR, "oggainz.db")

_tmp = os.path.join(tempfile.gettempdir(), "oggainz_moneytest.db")
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(_tmp + _s)
    except OSError:
        pass
if os.path.exists(LIVE_DB):
    shutil.copyfile(LIVE_DB, _tmp)
os.environ["OG_DB_PATH"] = _tmp
os.environ["OG_PASSWORD"] = "test-owner-pw"
os.environ["OG_TEAM_PASSWORD"] = "test-team-pw"
os.environ.setdefault("OG_SECRET_KEY", "test-secret")

sys.path.insert(0, APP_DIR)
import db                                                      # noqa: E402

if os.path.abspath(db.DB_PATH) == os.path.abspath(LIVE_DB):
    sys.exit("REFUSING TO RUN: the app is pointed at the live database.")

import app as A                                                # noqa: E402
import finance_hub                                             # noqa: E402

sys.path.insert(0, os.path.join(APP_DIR, "finance"))
import actions                                                 # noqa: E402
import build_finance as bf                                     # noqa: E402
import dashboard_render as dr                                  # noqa: E402

FAILS, N = [], 0


def check(label, cond, extra=""):
    global N
    N += 1
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} {extra}")
        FAILS.append(label)


A.app.config["TESTING"] = True

READ_ROUTES = ["/money", "/money/api/schema", "/money/api/rows",
               "/money/api/lists", "/money/api/ping"]
WRITE_ROUTES = ["/money/api/update", "/money/api/bulk", "/money/api/archive",
                "/money/api/option/add", "/money/api/option/rename",
                "/money/api/option/delete", "/money/api/pairs", "/money/api/rebuild"]

print("== 1. logged out reaches nothing ==")
out = A.app.test_client()
for p in READ_ROUTES:
    check(f"GET {p} -> login", out.get(p).status_code == 302)
for p in WRITE_ROUTES:
    check(f"POST {p} -> login", out.post(p, json={}).status_code == 302)

print("\n== 2. the team password reaches nothing ==")
team = A.app.test_client()
team.post("/login", data={"password": "test-team-pw"})
check("team is logged in at all", team.get("/content").status_code == 200)
for p in READ_ROUTES:
    r = team.get(p)
    check(f"GET {p} blocked", r.status_code in (302, 403, 404),
          f"got {r.status_code}")
for p in WRITE_ROUTES:
    r = team.post(p, json={})
    check(f"POST {p} blocked", r.status_code in (302, 403, 404),
          f"got {r.status_code}")
body = team.get("/money", follow_redirects=True).get_data(as_text=True)
check("team never sees finance figures",
      not any(w in body for w in ["COGS ·", "OPEX ·", "Appa", "settle"]))
check("team never sees the Money link", "/money" not in team.get("/content").get_data(as_text=True))

print("\n== 3. the owner can reach it ==")
admin = A.app.test_client()
admin.post("/login", data={"password": "test-owner-pw"})
check("ping -> 200", admin.get("/money/api/ping").status_code == 200)
check("the More menu offers Money",
      ("/money" in admin.get("/more").get_data(as_text=True)) == finance_hub.enabled())

print("\n== 4. no token = the tab is off, not broken ==")
_tok = os.environ.pop("OG_NOTION_TOKEN", None)
_real = bf.get_token
bf.get_token = lambda: None
try:
    check("enabled() is False", finance_hub.enabled() is False)
    r = admin.get("/money")
    check("/money explains itself instead of erroring", r.status_code == 200)
    check("...and says what to set",
          "OG_NOTION_TOKEN" in r.get_data(as_text=True))
    check("the More menu hides Money",
          "/money" not in admin.get("/more").get_data(as_text=True))
finally:
    bf.get_token = _real
    if _tok is not None:
        os.environ["OG_NOTION_TOKEN"] = _tok

print("\n== 5. finance_hub unpacks build_finance the way main() does ==")
# These arities are the coupling between the two front doors. If someone adds a
# column to shape_expenses, this fails here rather than 500ing on the phone.
src = inspect.getsource(bf.main)
for fn_name, call in [("shape_expenses", "expenses, cash, _cash_cat, true_cost, delivery, _vendors"),
                      ("shape_personal", "personal, personal_m, _personal_cats"),
                      ("load_revenue", "rev, _plans")]:
    hub = inspect.getsource(finance_hub._build)
    n_hub = len([p for p in call.split(",")])
    main_line = next((l for l in src.split("\n") if f"= {fn_name}(" in l), "")
    n_main = len(main_line.split("=")[0].split(",")) if main_line else -1
    check(f"{fn_name}: hub unpacks {n_hub}, main unpacks {n_main}", n_hub == n_main,
          f"main says: {main_line.strip()}")
    check(f"{fn_name} is actually called in _build", f"{fn_name}(" in hub)

sig = inspect.signature(dr.render_dashboard)
check("render_dashboard takes the 6 positionals plus the 3 model kwargs",
      len(sig.parameters) == 9, str(sig))
check("the model kwargs all default, so an old caller still works",
      all(sig.parameters[p].default is not inspect.Parameter.empty
          for p in ("summaries", "health", "months")))

hub_src = inspect.getsource(finance_hub._build)
check("_build passes the model results, not bare rows",
      "render_dashboard(monthly, all_rows, personal, dad, cur, dad_status," in hub_src
      and "summaries=summaries" in hub_src and "health=health" in hub_src
      and "months=months" in hub_src)

# The Money tab on the server is only as good as the model behind it. Without
# these three the page renders with empty panels and dead filters, which is worse
# than the old dashboard because it looks like it works.
for call in ("model.enrich(expenses)", "model.merge_appa(expenses, dad)",
             "model.month_summary(", "model.health("):
    check("_build runs " + call.rstrip("("), call in hub_src)

main_src = inspect.getsource(bf.main)
for call in ("model.enrich(expenses)", "model.merge_appa(expenses, dad)",
             "model.month_summary(", "model.health("):
    check("build_finance.main runs " + call.rstrip("("), call in main_src)

print("\n== 6. the shared action layer is what both front doors use ==")
import serve_dashboard as sd                                   # noqa: E402
check("the laptop server delegates to actions", sd.actions is actions)
check("...and shares one STATE", sd.STATE is actions.STATE)
check("every POST action is registered",
      sorted(actions.POST_ACTIONS) == sorted(
          ["update", "bulk", "archive", "option/add", "option/rename",
           "option/delete", "pairs"]))
check("no duplicated action code left in the server",
      not any(hasattr(sd.Handler, n) for n in
              ["do_update", "do_bulk", "do_archive", "do_option_add"]))
for name, fn in actions.POST_ACTIONS.items():
    res = fn({})
    check(f"{name}: a bad request is refused cleanly, not raised",
          isinstance(res, tuple) and len(res) == 2 and res[1] in (400, 502))

print("\n== 7. the rest of the app is untouched ==")
for p in ["/today", "/subscriptions", "/revenue", "/customers", "/more", "/planner"]:
    check(f"GET {p} -> 200", admin.get(p).status_code == 200)

print(f"\n{N - len(FAILS)}/{N} checks passed")
if FAILS:
    print("FAILURES:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ALL GREEN")
