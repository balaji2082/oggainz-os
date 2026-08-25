"""
OG Gainz — the skip/pause deadline.

The one place in the system that knows what time the cutoff is. Everything else
asks it: the API, the countdown on the customer's meal card, the calendar's
shading of past-deadline days, and the generated terms page.

[LAW] (og-gainz-app/docs/02 §2.0) The cutoff is a SETTING, never a constant.
`6:00 PM` is today's value, not a law of nature — it changes with a second shift
or a bigger team, and when it does, one edit in Settings must change everything.
There is no second copy of "18:00" anywhere in this codebase.

[LAW] (docs/02 §2.4) Evaluated in Asia/Kolkata, always. Never in UTC and never in
the browser's timezone. A customer travelling abroad gets the same deadline as
one in Perumbakkam.

[LAW] (docs/03 §5.3) The browser shows a countdown; the SERVER decides whether a
request was in time. A client whose clock is 20 minutes fast must not be able to
skip a meal that is already being cooked.

Pure functions over a connection and a date — no Flask, no request context — so
the boundary can be tested directly on both sides, including the case that
matters most: that CHANGING the setting moves it.
"""

from datetime import date, datetime, time, timedelta

import core
import db

# Python 3.9+ ships zoneinfo, but PythonAnywhere's older images can be missing
# the tzdata package behind it. A fixed offset is correct here and needs no
# database: India has one timezone and has never observed daylight saving.
IST_OFFSET = timedelta(hours=5, minutes=30)

DEFAULT_CUTOFF = "18:00"


def now_ist():
    """The current time in Asia/Kolkata, as a naive datetime.

    Naive on purpose: every date in this database is a naive IST calendar date,
    and mixing aware and naive datetimes is how a comparison silently starts
    throwing. The conversion happens here, once.
    """
    return datetime.utcnow() + IST_OFFSET


def _setting(conn, key, default=None):
    """A setting, read at most once per connection.

    `/deadlines` describes up to 120 days in one call, and each day was asking
    the settings table for the cutoff time and the mode independently — 285
    queries for a single request. The values cannot change midway through one
    request, so reading them once is not a cache with a staleness problem; it is
    just not asking the same question ninety times.

    Keyed on the connection object, so it dies with the request.
    """
    store = getattr(conn, "_og_setting_cache", None)
    if store is None:
        store = {}
        try:
            conn._og_setting_cache = store
        except AttributeError:
            return db.get_setting(conn, key, default)
    if key not in store:
        store[key] = db.get_setting(conn, key, default)
    return store[key]


def cutoff_time(conn):
    """The configured deadline as a `time`. Falls back to 18:00 if unreadable.

    A malformed setting must not take skipping offline for everyone, and it must
    not silently become midnight — which would make every request 'late' and is
    the worst possible failure direction. It falls back to the documented
    default and carries on.
    """
    raw = (_setting(conn, "skip_cutoff_time") or DEFAULT_CUTOFF).strip()
    try:
        hh, mm = raw.split(":")[:2]
        return time(int(hh), int(mm))
    except (ValueError, AttributeError):
        hh, mm = DEFAULT_CUTOFF.split(":")
        return time(int(hh), int(mm))


def _previous_operating_day(conn, day):
    """The last day the kitchen runs before `day`, searching back at most a fortnight."""
    probe = day - timedelta(days=1)
    for _ in range(14):
        if core.is_operating_day(conn, probe.strftime("%Y-%m-%d")):
            return probe
        probe -= timedelta(days=1)
    return day - timedelta(days=1)


def deadline_for(conn, day_iso):
    """The exact IST moment after which `day_iso` can no longer be changed freely.

    `skip_cutoff_applies_to` picks which day the clock hangs off:

        calendar_day    (default)  the previous calendar day — Monday's meal cuts
                                   off at 6 PM Sunday even though the kitchen is
                                   shut on Sunday. Simpler to explain, and errs
                                   toward the kitchen having MORE notice.
        operating_day              the previous day the kitchen actually runs —
                                   Monday's meal cuts off at 6 PM Friday.
    """
    day = datetime.strptime(day_iso, "%Y-%m-%d").date()
    mode = (_setting(conn, "skip_cutoff_applies_to") or "calendar_day").strip()
    base = _previous_operating_day(conn, day) if mode == "operating_day" else day - timedelta(days=1)
    return datetime.combine(base, cutoff_time(conn))


def is_late(conn, day_iso, at=None):
    """True if it is already too late to change `day_iso` without asking."""
    return (at or now_ist()) > deadline_for(conn, day_iso)


def minutes_late(conn, day_iso, at=None):
    """How far past the deadline we are, in whole minutes. 0 when still in time.

    [LAW] (docs/03 §2.2) This is STORED on the request, never recomputed. The
    cutoff is a setting and will change; history must keep saying how late a
    request was against the rule that was in force when it was made.
    """
    delta = (at or now_ist()) - deadline_for(conn, day_iso)
    return max(int(delta.total_seconds() // 60), 0)


def describe(conn, day_iso, at=None):
    """Everything the app needs to render the deadline for one day, in one call.

    Returned with every meal so the countdown is driven by a server-supplied
    absolute deadline rather than a timer the browser started at page load —
    docs/02 §7.5: it has to survive the screen being locked and reopened.
    """
    at = at or now_ist()
    deadline = deadline_for(conn, day_iso)
    late = at > deadline
    return {
        "deadline": deadline.strftime("%Y-%m-%d %H:%M:%S"),
        "cutoff_time": cutoff_time(conn).strftime("%H:%M"),
        "server_now": at.strftime("%Y-%m-%d %H:%M:%S"),
        "late": late,
        "minutes_late": max(int((at - deadline).total_seconds() // 60), 0) if late else 0,
        "seconds_left": max(int((deadline - at).total_seconds()), 0) if not late else 0,
    }


def late_requests_allowed(conn):
    """Whether a past-deadline request may be raised at all (docs/02 §9)."""
    return (_setting(conn, "late_requests_allowed") or "1").strip() not in ("0", "", "false", "no")


def pause_max_days(conn):
    """Optional cap on a single pause window. None = unlimited."""
    raw = (_setting(conn, "pause_max_days") or "").strip()
    try:
        n = int(raw)
        return n if n > 0 else None
    except ValueError:
        return None


def freeze_eligible(conn, plan_name):
    """RETIRED, 25 Aug 2026. Superseded by flexibility.effective_enabled().

    Kept only so an old call site fails loudly rather than quietly allowing
    something, and so `freeze_eligible_plans` still reads correctly for anyone
    inspecting the database. NOTHING in the request path calls this any more:
    whether a pack may skip or pause is a decision about a PACK, and this
    function could only ever answer about a PLAN.

    Do not reintroduce it as a gate. If a plan-level default is ever wanted
    again, it belongs inside flexibility.py where the per-pack override can
    still win over it.
    """
    raw = (_setting(conn, "freeze_eligible_plans") or "").strip()
    if not raw:
        return True
    allowed = [p.strip().lower() for p in raw.split(",") if p.strip()]
    return (plan_name or "").strip().lower() in allowed


def operating_dates(conn, start_iso, end_iso):
    """Every day the kitchen runs in [start, end] inclusive.

    Used to answer "how many meals does this pause actually cost you?" — a
    Saturday inside the window is not a meal and must not be counted as one.
    """
    start = datetime.strptime(start_iso, "%Y-%m-%d").date()
    end = datetime.strptime(end_iso, "%Y-%m-%d").date()
    out = []
    day = start
    while day <= end:
        iso = day.strftime("%Y-%m-%d")
        if core.is_operating_day(conn, iso):
            out.append(iso)
        day += timedelta(days=1)
    return out


def today_iso():
    """Today in IST — not in the server's timezone."""
    return now_ist().date().strftime("%Y-%m-%d")


def split_by_deadline(conn, dates, at=None):
    """Split a list of dates into the ones still in time and the ones past it.

    This is what makes docs/02 §4 possible: a customer asking at 9 PM on Thursday
    to pause Friday→Wednesday gets Monday, Tuesday and Wednesday applied straight
    away, and only Friday waits. Rejecting all five because one was late is the
    kind of thing that makes people stop using an app.
    """
    at = at or now_ist()
    in_time, late = [], []
    for d in dates:
        (late if at > deadline_for(conn, d) else in_time).append(d)
    return in_time, late


__all__ = [
    "now_ist", "today_iso", "cutoff_time", "deadline_for", "is_late",
    "minutes_late", "describe", "late_requests_allowed", "pause_max_days",
    "freeze_eligible", "operating_dates", "split_by_deadline",
]
