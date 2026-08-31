"""
Money — the finance dashboard, on the phone.

The dashboard used to exist only on the laptop: `Edit-Dashboard.bat` starts a
local server, and a phone could reach it over home wifi while the laptop stayed
awake. This module puts the same dashboard in the Command Center so it works
from anywhere, which is the whole point.

It reuses `finance/actions.py` — the same code the laptop server runs — so the
category rules, the option-list safety checks and the Notion writes behave
identically in both places.

WHAT THIS PUTS ON THE SERVER
    Business, personal AND appa's expenses. That is a deliberate choice, made
    knowingly: it is more sensitive than the customer data already here. So:

    * every route is ADMIN-ONLY. The team password (OG_TEAM_PASSWORD), which
      editor friends use for the Content tab, cannot reach any of it — the gate
      below refuses before a single Notion call is made, and the nav link is
      hidden from them too.
    * the whole tab is OFF unless OG_NOTION_TOKEN is set on the server. A
      deployment that never sets it serves nothing and links nothing.
    * nothing is written to oggainz.db. Notion stays the source of truth, and
      the only local artefacts are the same cache files the laptop already
      writes, which are gitignored.

WHY THE NOTION CALLS ARE CACHED
    A refresh pages through every row of three databases 100 at a time — several
    seconds, and slow enough to feel broken on mobile data. So a built dashboard
    is held in memory for CACHE_TTL and re-used. "Refresh" forces a rebuild, and
    every successful write clears the cache so an edit is never shown stale.
"""

import os
import sys
import threading
import time

from flask import abort, jsonify, redirect, render_template, request, session, url_for

APP_DIR = os.path.dirname(os.path.abspath(__file__))
FIN_DIR = os.path.join(APP_DIR, "finance")
sys.path.insert(0, FIN_DIR)

# How long a built dashboard stays fresh. Money moves in days, not seconds, and
# the Refresh button is always there — so this is about not re-paginating Notion
# on every tap, not about hiding changes.
CACHE_TTL = 600          # seconds

_CACHE = {"html": None, "at": 0.0, "error": None}
_LOCK = threading.Lock()


def enabled():
    """The tab exists only where a Notion token is configured."""
    try:
        import build_finance as bf
        return bool(bf.get_token())
    except Exception:                                        # noqa: BLE001
        return False


def _is_admin():
    # role() is only set once a password is configured; local runs have no gate
    # at all and are the owner's own laptop, so they count as admin.
    return session.get("role", "admin") == "admin"


def register(app):

    @app.context_processor
    def _money_globals():
        # Drives the More-menu link. Hidden from the team role, and hidden
        # entirely where no token is configured.
        return {"g_money_on": enabled() and _is_admin()}

    def _gate():
        """Refuse anyone who is not the owner, before touching Notion.

        Returns a response to send, or None to continue. 404 rather than 403 on
        the team role: an editor friend has no business knowing the tab is here.
        """
        if not _is_admin():
            abort(404)
        if not enabled():
            return render_template(
                "money_off.html",
                reason=("No Notion token on this server. Set OG_NOTION_TOKEN in the "
                        "Web tab's environment variables and Reload."))
        return None

    def _connect():
        import actions
        if actions.connected():
            return True, None
        ok, why = actions.connect()
        return ok, (None if ok else why)

    # ---------------------------------------------------------------- the page

    @app.route("/money")
    def money():
        stop = _gate()
        if stop is not None:
            return stop
        ok, why = _connect()
        if not ok:
            return render_template("money_off.html", reason=why)

        force = request.args.get("refresh") == "1"
        html, err = _dashboard_html(force=force)
        if err:
            return render_template("money_off.html", reason=err)
        if force:
            # Redirect after the rebuild so a phone refresh doesn't re-pull Notion.
            return redirect(url_for("money"))
        return html

    @app.route("/money/refresh", methods=["POST"])
    def money_refresh():
        stop = _gate()
        if stop is not None:
            return stop
        _invalidate()
        return jsonify({"ok": True})

    # ---------------------------------------------------------------- the API
    #
    # Mirrors the laptop server's endpoints exactly, so the dashboard's own
    # JavaScript works unchanged against either one.

    @app.route("/money/api/ping")
    def money_ping():
        if not _is_admin():
            abort(404)
        return jsonify({"ok": True, "editable": True})

    @app.route("/money/api/schema")
    def money_schema():
        return _read(lambda a: a.schema())

    @app.route("/money/api/lists")
    def money_lists():
        db = request.args.get("db", "business")
        return _read(lambda a: a.lists(db))

    @app.route("/money/api/rows")
    def money_rows():
        return _read(lambda a: a.rows())

    @app.route("/money/api/<path:action>", methods=["POST"])
    def money_action(action):
        stop = _gate()
        if stop is not None:
            abort(404)
        ok, why = _connect()
        if not ok:
            return jsonify({"error": why}), 502
        import actions as A
        if action == "rebuild":
            payload, code = A.rebuild()
            _invalidate()
            return jsonify(payload), code
        fn = A.POST_ACTIONS.get(action)
        if not fn:
            return jsonify({"error": "not found"}), 404
        payload, code = fn(request.get_json(silent=True) or {})
        # Any successful write makes the cached dashboard stale by definition.
        if code == 200 and payload.get("ok"):
            _invalidate()
        return jsonify(payload), code

    def _read(fn):
        stop = _gate()
        if stop is not None:
            abort(404)
        ok, why = _connect()
        if not ok:
            return jsonify({"error": why}), 502
        import actions as A
        payload, code = fn(A)
        return jsonify(payload), code


# ---------------------------------------------------------------- dashboard build

def _invalidate():
    with _LOCK:
        _CACHE["html"] = None
        _CACHE["at"] = 0.0


def _dashboard_html(force=False):
    """The built dashboard, from cache when it is still fresh.

    Returns (html, error). The lock means two phones tapping at once produce one
    Notion pull, not two.
    """
    with _LOCK:
        fresh = _CACHE["html"] and (time.time() - _CACHE["at"] < CACHE_TTL)
        if fresh and not force:
            return _CACHE["html"], None
        try:
            html = _build()
        except SystemExit as e:                 # notion_query exits on a bad token
            return None, str(e)
        except Exception as e:                  # noqa: BLE001
            return None, f"{type(e).__name__}: {e}"
        _CACHE["html"] = html
        _CACHE["at"] = time.time()
        return html, None


def _build():
    """Pull Notion + revenue and render the dashboard, in memory.

    Deliberately does NOT call build_finance.main(): that writes dashboard.html,
    finance.json and the xlsx to disk. On the server those are unnecessary and
    would leave personal figures lying in the filesystem. Everything here stays
    in memory, and the revenue half reads the server's own oggainz.db — which is
    the live one, so the Money tab is if anything more current than the laptop.

    Mirrors build_finance.main()'s shaping step exactly; when that changes, this
    has to change with it, which the test suite checks by comparing arities.
    """
    import build_finance as bf
    import dashboard_render as dr
    import actions as A

    token = A.STATE.get("token") or bf.get_token()
    biz_rows = bf.flatten(bf.notion_query(bf.BUSINESS_DB, token))
    per_rows = bf.flatten(bf.notion_query(bf.PERSONAL_DB, token))
    dad_rows, dad_status = [], None
    if "dad" in A.STATE.get("db_ids", {}):
        dad_rows = bf.flatten(bf.notion_query(A.STATE["db_ids"]["dad"], token))
    else:
        dad_status = "not shared with the integration yet"

    rev, _plans = bf.load_revenue()
    expenses, cash, _cash_cat, true_cost, delivery, _vendors = bf.shape_expenses(biz_rows)
    personal, personal_m, _personal_cats = bf.shape_personal(per_rows)
    dad = bf.shape_shared(dad_rows)
    monthly = bf.build_monthly(rev, cash, true_cost, delivery, personal_m)
    if not monthly:
        raise ValueError("No finance data to build from yet.")

    # Same money model the laptop build runs. Without it the page renders with
    # empty summaries: no P&L, no payer split, no health panel — and rows with no
    # payer or cost type, so those filters would be dead. See model.py.
    import model
    model.enrich(expenses)
    appa_extra, appa_mirrored = model.merge_appa(expenses, dad)
    all_rows = expenses + appa_extra

    # Feature the latest month with REAL activity — same rule as the laptop build,
    # so both front doors open on the same month.
    real = [m for m in monthly if m["total_revenue"] or m["cash_out"] or m["delivered"]]
    cur = real[-1]["month"] if real else monthly[-1]["month"]

    rev_by_month = {m["month"]: m for m in monthly}
    months = model.all_months(all_rows) or [m["month"] for m in monthly]
    if cur not in months:
        months = months + [cur]
    summaries = {m: model.month_summary(all_rows, m, rev_by_month.get(m)) for m in months}
    health = {m: model.health(expenses, dad, appa_extra, appa_mirrored, m) for m in months}

    html = dr.render_dashboard(monthly, all_rows, personal, dad, cur, dad_status,
                               summaries=summaries, health=health, months=months)
    return _mobile_shell(html)


def _mobile_shell(html):
    """Make the generated page behave inside the Command Center on a phone.

    The dashboard was built desktop-first for a browser tab on the laptop. Rather
    than fork its markup, we inject: a viewport tag if it lacks one, a back link
    to the app, a Refresh control, and a small stylesheet that makes its tables
    scroll instead of forcing the page sideways.
    """
    extra = """
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<style>
  /* Command Center bar, so the dashboard never becomes a dead end on a phone. */
  .og-bar{position:sticky; top:0; z-index:999; display:flex; gap:10px; align-items:center;
    padding:10px 14px; background:#004030; color:#fff; font:600 .9rem/1.2 system-ui,sans-serif}
  .og-bar a,.og-bar button{color:#fff; background:rgba(255,255,255,.14); border:0;
    border-radius:8px; padding:7px 11px; font:inherit; text-decoration:none; cursor:pointer}
  .og-bar .sp{flex:1}
  /* Wide content scrolls in its own box; the page itself must never scroll sideways. */
  body{overflow-x:hidden}
  table{display:block; overflow-x:auto; max-width:100%}
  @media (max-width:640px){
    .wrap,.container,main{padding-left:10px !important; padding-right:10px !important}
    svg{max-width:100%; height:auto}
  }
</style>"""
    bar = """
<div class="og-bar">
  <a href="/more">&lsaquo; Command Center</a>
  <span class="sp"></span>
  <a href="/money?refresh=1" id="ogRefresh">&#8635; Refresh</a>
</div>
<script>
  // The dashboard's own JS calls its API with RELATIVE paths ('api/schema'), which
  // the laptop server resolves correctly because it serves the page at /. Here the
  // page is at /money, so the browser would resolve them to /api/... at the app root
  // and 404 — which silently left the whole tab read-only. Rewrite both spellings.
  (function () {
    var f = window.fetch;
    window.fetch = function (u, o) {
      if (typeof u === 'string') {
        if (u.indexOf('api/') === 0) u = '/money/' + u;
        else if (u.indexOf('/api/') === 0) u = '/money' + u;
      }
      return f(u, o);
    };
  })();
</script>"""
    if "</head>" in html:
        html = html.replace("</head>", extra + "\n</head>", 1)
    else:
        html = extra + html
    if "<body" in html:
        i = html.index("<body")
        j = html.index(">", i) + 1
        html = html[:j] + bar + html[j:]
    else:
        html = bar + html
    return html
