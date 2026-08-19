"""
OG Gainz Command Center — evening delivery ops.

Everything geographic lives here: turning a day's orders into zones, a riding
order, a Google Maps multi-stop link, a total-km figure and the ₹3/km petrol
allowance that gets paid on it.

The design follows three rules that came out of running the route by hand:

1.  **The route must end at the kitchen.** Distance is what the allowance is paid
    on, and the rider genuinely does ride home. A route that ends at the last
    customer under-pays him every single day.

2.  **Order by zone band, not by nearest-neighbour.** Clearing the 2-3 hyperlocal
    drops first closes half the orders in ten minutes and lightens the bag before
    the long legs. A naive shortest-path ignores that and interleaves them.

3.  **Never silently drop an order.** An order with no map pin still has to be
    cooked, packed and delivered. It comes back in `unpinned` and is printed with
    a warning rather than vanishing from the sheet.
"""

import hashlib
import json
import math
import urllib.error
import urllib.request
from datetime import datetime, timedelta

import db

# ---------------------------------------------------------------------------
# Settings + defaults
# ---------------------------------------------------------------------------
# Perumbakkam centroid. This is a PLACEHOLDER so the system is usable before the
# real pin is set — it is not the kitchen. `kitchen_pin_confirmed` stays 0 until
# Balaji drops the actual pin, and the UI nags until he does, because every
# distance and therefore every rupee of allowance is measured from this point.
KITCHEN_FALLBACK = (12.9010, 80.2279)

DEFAULTS = {
    "kitchen_lat":          str(KITCHEN_FALLBACK[0]),
    "kitchen_lng":          str(KITCHEN_FALLBACK[1]),
    "kitchen_pin_confirmed": "0",
    "petrol_rate":          "3",      # ₹ per km, per the signed terms letter
    # Straight-line → road fallback, used ONLY when no routing provider is
    # configured, and always labelled as an estimate. Measured against real
    # routes out of this kitchen the true ratio runs 1.5x–2.1x depending on
    # direction (the Pallikaranai marsh forces long detours to the west and
    # north), so no single number is right — 1.7 is the least-wrong average.
    "road_factor":          "1.7",
    # Bands are ROAD kilometres from the kitchen, not straight-line.
    "zone_hyperlocal_km":   "2",
    "zone_near_km":         "6",
    "zone_mid_km":          "12",
    "porter_min_km":        "12",     # beyond this, consider a Porter handoff
    "rider_max_km":         "10",     # at or under this and on-corridor, rider delivers
    # Porter is OPT-IN, and off by default. The automatic rule in assign_modes()
    # used to send every far order to a Porter on its own, which quietly emptied
    # the rider's route: on 10 Aug the entire Tambaram run — two in Tambaram,
    # Chrompet, Nanganallur — was classed 'porter_direct', so printing Route 1
    # gave one Perumbakkam stop, 1.3 km and a Rs 4 allowance for a ~50 km ride.
    # No Porter has ever actually been booked. A far order is now ridden unless
    # a handoff point is chosen for it by hand.
    "porter_auto":          "0",
    # Which run a corridor rides on, by default. Assigning routes by hand, order
    # by order, every single day does not survive contact with a real week: on
    # 19 Aug only 7 of 32 addresses had ever been given one, so Kumaran in
    # Nanganallur -- squarely on the Tambaram run -- appeared on NOBODY's route
    # sheet, while the whole-slot sheet mixed Semmancheri in with Chrompet.
    # A locality already knows its corridor, so a corridor can know its route,
    # and every customer lands on the right run from the day they are added.
    # Route 1 then MEANS the Tambaram side, every day, without being re-decided.
    "corridor_routes":      '{"Local": 1, "West": 1, "OMR-N": 2, "OMR-S": 2}',
    "avg_speed_kmph":       "20",     # Chennai evening traffic, two-wheeler
    "dwell_min":            "2.5",    # minutes parked per drop
    "depart_time":          "18:40",  # 6:40 PM, per the run-of-show
    "shift_end":            "21:00",  # 9:00 PM (extended from 8:00 on 31 Jul 2026)
    "prep_start_time":      "4:00 PM",
    "pack_done_time":       "6:15 PM",
    "kitchen_address":      "",
    "kitchen_pluscode":     "",
    "staff_name":           "",
    # --- road distance provider -------------------------------------------
    # An ordered chain, tried left to right until one answers, e.g. "google,ors".
    # That way Google can be primary (it matches what Balaji sees in Maps) while
    # ORS silently covers a quota cap, an outage or a revoked key — the sheet is
    # never blocked on one service being up.
    "distance_provider":    "",
    # Which route ORS should measure. 'recommended' is the default on purpose:
    # the sheet hands the rider a GOOGLE MAPS link, so he rides Google's
    # recommended route — main roads, not the theoretical shortest path. Paying
    # a per-km allowance on a route he never takes underpays him by ~8%.
    # 'shortest' is kept available for comparison.
    "ors_preference":       "recommended",
    "routes_api_key":       "",       # Google Routes API key
    "ors_api_key":          "",       # openrouteservice.org key
    "osrm_base_url":        "",       # a self-hosted OSRM, e.g. http://host:5000
}

ZONES = ["Hyperlocal", "Near", "Mid", "Long"]

# Corridors out of Perumbakkam (Cowork §4.1). A stop is "on corridor" when the
# rider already has another stop on the same corridor that evening.
CORRIDORS = {
    "OMR-N": "North OMR — Sholinganallur, Karapakkam, Thoraipakkam, Perungudi, Adyar, Guindy",
    "West":  "West — Medavakkam, Pallikaranai, Velachery, Tambaram, Chromepet",
    "OMR-S": "South OMR — Navalur, Siruseri, Padur, Kelambakkam, Thalambur",
    "Local": "Within Perumbakkam",
}

# Locality → corridor. Keys are normalised (lowercase, no spaces/punctuation) so
# the many spellings already in the database all land correctly.
_LOCALITY_CORRIDOR = {
    "perumbakkam": "Local",
    "sholinganallur": "OMR-N", "shollinganallur": "OMR-N", "solinganallur": "OMR-N",
    "karapakkam": "OMR-N", "thoraipakkam": "OMR-N", "perungudi": "OMR-N",
    "adyar": "OMR-N", "guindy": "OMR-N", "neelankarai": "OMR-N",
    "okkiyampet": "OMR-N", "palavakkam": "OMR-N", "thiruvanmiyur": "OMR-N",
    "medavakkam": "West", "pallikaranai": "West", "velachery": "West",
    "tambaram": "West", "chromepet": "West", "chrompet": "West",
    "nanganallur": "West", "kovilambakkam": "West", "ottiambakkam": "West",
    "sithalapakkam": "West", "pallavaram": "West", "madipakkam": "West",
    "navalur": "OMR-S", "navallur": "OMR-S", "siruseri": "OMR-S",
    "padur": "OMR-S", "kelambakkam": "OMR-S", "thalambur": "OMR-S",
    "semmancheri": "OMR-S", "semmencheri": "OMR-S", "egattur": "OMR-S",
}


def _norm(s):
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


# ---------------------------------------------------------------------------
# Open Location Code (Google "plus code") support
# ---------------------------------------------------------------------------
# Plus codes are what Google Maps actually shows when you long-press a spot, and
# they are far easier to read off a phone than a pair of decimals — so the app
# accepts them anywhere a pin can be entered. A short code ('V6R3+XM Chennai')
# omits its first four characters; those are recovered from a reference point,
# which for us is always Chennai.
_OLC_ALPHABET = "23456789CFGHJMPQRVWX"
_OLC_RES = [20.0, 1.0, 0.05, 0.0025, 0.000125]
CHENNAI_REF = (13.0827, 80.2707)


def olc_decode(code):
    """Full plus code → (lat, lng) at the centre of its cell. None if invalid."""
    c = (code or "").replace("+", "").upper()
    if len(c) < 8 or any(ch not in _OLC_ALPHABET for ch in c.rstrip("0")):
        return None
    c = c.rstrip("0")
    lat = lng = 0.0
    for i in range(0, len(c) - len(c) % 2, 2):
        r = _OLC_RES[i // 2]
        lat += _OLC_ALPHABET.index(c[i]) * r
        lng += _OLC_ALPHABET.index(c[i + 1]) * r
    cell = _OLC_RES[(len(c) // 2) - 1]
    return (lat - 90 + cell / 2, lng - 180 + cell / 2)


def olc_recover(short_code, ref=CHENNAI_REF):
    """Short plus code ('V6R3+XM') → (lat, lng), resolved near `ref`."""
    s = (short_code or "").strip().upper()
    if "+" not in s:
        return None
    body = s.split()[0]                      # drop any trailing ' Chennai'
    if len(body.replace("+", "")) >= 8:      # already a full code
        return olc_decode(body)

    ref_lat, ref_lng = ref
    la, ln = ref_lat + 90, ref_lng + 180
    prefix = ""
    for i in range(2):
        r = _OLC_RES[i]
        di, dj = int(la // r), int(ln // r)
        prefix += _OLC_ALPHABET[di] + _OLC_ALPHABET[dj]
        la -= di * r
        ln -= dj * r

    # The recovered point must be the candidate nearest the reference. Stepping
    # the second pair by ±1 covers the wrap cases; without this a code just south
    # of a 1° boundary resolves a whole degree away (~111 km).
    best = None
    for dlat in (-1, 0, 1):
        for dlng in (-1, 0, 1):
            p = list(prefix)
            i2 = _OLC_ALPHABET.index(p[2]) + dlat
            j2 = _OLC_ALPHABET.index(p[3]) + dlng
            if not (0 <= i2 < 20 and 0 <= j2 < 20):
                continue
            p[2], p[3] = _OLC_ALPHABET[i2], _OLC_ALPHABET[j2]
            got = olc_decode("".join(p) + body)
            if not got:
                continue
            d = abs(got[0] - ref_lat) + abs(got[1] - ref_lng)
            if best is None or d < best[0]:
                best = (d, got)
    return best[1] if best else None


def setting(conn, key):
    """A setting with its documented default applied."""
    return db.get_setting(conn, key, DEFAULTS.get(key, ""))


def fnum(conn, key):
    try:
        return float(setting(conn, key))
    except (TypeError, ValueError):
        return float(DEFAULTS.get(key) or 0)


def kitchen(conn):
    """(lat, lng, confirmed) for the kitchen. Origin AND destination of every route."""
    return (fnum(conn, "kitchen_lat"), fnum(conn, "kitchen_lng"),
            setting(conn, "kitchen_pin_confirmed") == "1")


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------
def haversine(lat1, lng1, lat2, lng2):
    """Great-circle km between two pins."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def zone_for_km(conn, km):
    if km <= fnum(conn, "zone_hyperlocal_km"):
        return "Hyperlocal"
    if km <= fnum(conn, "zone_near_km"):
        return "Near"
    if km <= fnum(conn, "zone_mid_km"):
        return "Mid"
    return "Long"


def corridor_routes(conn):
    """{corridor: batch_no} — the standing answer to "whose run is this?"."""
    raw = setting(conn, "corridor_routes")
    try:
        m = json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        return {}
    out = {}
    for k, v in (m or {}).items():
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if k in CORRIDORS and 1 <= n <= 6:
            out[k] = n
    return out


def route_label(conn, batch_no):
    """'West · Tambaram side' — a route named after the ground it covers.

    'Route 1' says nothing, and a number that means a different direction on
    different days is worse than no name at all.
    """
    carried = [c for c, b in corridor_routes(conn).items() if b == batch_no]
    if not carried:
        return f"Route {batch_no}"
    places = {"West": "Tambaram side", "OMR-N": "Sholinganallur side",
              "OMR-S": "Navalur side", "Local": "Perumbakkam"}
    named = [places[c] for c in ("West", "OMR-N", "OMR-S", "Local") if c in carried]
    return " + ".join(named) if named else f"Route {batch_no}"


def corridor_for(locality, address=""):
    """Best-guess corridor from the locality text. Overridable per customer.

    Localities in the database are messy free text ('Perumbakkam & Shollinganallur',
    'Semmancheri/ Perumbakkam'), so every known name is searched for anywhere in
    the string and the first non-Local match wins — a customer written up as
    'Perumbakkam & Shollinganallur' is really an OMR-N stop.
    """
    blob = _norm(f"{locality} {address}")
    hits = [c for name, c in _LOCALITY_CORRIDOR.items() if name in blob]
    for c in hits:
        if c != "Local":
            return c
    return hits[0] if hits else None


# ---------------------------------------------------------------------------
# Addresses — a customer can have several, and which one is used varies by day
# ---------------------------------------------------------------------------
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def customer_addresses(conn, cid, active_only=True):
    q = "SELECT * FROM customer_addresses WHERE customer_id=?"
    if active_only:
        q += " AND active=1"
    return conn.execute(q + " ORDER BY is_default DESC, sort, id", (cid,)).fetchall()


def weekday_list(spec):
    """'0,2,4' → [0, 2, 4]. Blank or malformed → []."""
    out = []
    for x in str(spec or "").split(","):
        x = x.strip()
        if x.isdigit() and 0 <= int(x) <= 6:
            out.append(int(x))
    return out


def weekday_label(spec):
    d = weekday_list(spec)
    return ", ".join(WEEKDAYS[i] for i in d) if d else ""


def resolve_address(conn, cid, day_iso, address_id=None, cache=None, slot=None):
    """Which address a given day's delivery actually goes to.

    Most specific wins:
      1. `address_id` — an explicit choice made for that single delivery, whether
         set the night before or on the morning itself
      2. a schedule for THIS slot on this weekday   (lunch → home on Mon & Fri)
      3. a schedule for ANY slot on this weekday     (office Mon–Wed, both meals)
      4. the customer's default address
      5. the first address on file

    Returns None when the customer has no address rows at all, so the caller can
    fall back to the legacy columns on `customers`.
    """
    if cache is None:
        cache = {}
    if cid not in cache:
        cache[cid] = customer_addresses(conn, cid)
    addrs = cache[cid]
    if not addrs:
        return None

    if address_id:
        for a in addrs:
            if a["id"] == address_id:
                return a                     # an explicit choice beats everything

    try:
        wd = datetime.strptime(day_iso, "%Y-%m-%d").weekday()      # Mon = 0
    except (ValueError, TypeError):
        wd = None

    if wd is not None:
        def scheduled(match_slot):
            for a in addrs:
                if wd not in weekday_list(a["weekdays"]):
                    continue
                aslot = (a["slot"] if "slot" in a.keys() else None) or ""
                if match_slot is None and not aslot:
                    return a
                if match_slot is not None and aslot == match_slot:
                    return a
            return None

        if slot:
            hit = scheduled(slot)            # a rule written for this meal
            if hit:
                return hit
        hit = scheduled(None)                # a rule that covers both meals
        if hit:
            return hit

    for a in addrs:
        if a["is_default"]:
            return a
    return addrs[0]


def set_default_address(conn, cid, address_id):
    """Exactly one default per customer, and mirror it onto the customer row so
    older screens and the legacy fallback stay correct."""
    conn.execute("UPDATE customer_addresses SET is_default=0 WHERE customer_id=?", (cid,))
    conn.execute("UPDATE customer_addresses SET is_default=1 WHERE id=? AND customer_id=?",
                 (address_id, cid))
    a = conn.execute("SELECT * FROM customer_addresses WHERE id=?", (address_id,)).fetchone()
    if a:
        conn.execute("UPDATE customers SET address=?, lat=?, lng=?, corridor=?, "
                     "drop_type=?, map_note=? WHERE id=?",
                     (a["address"], a["lat"], a["lng"], a["corridor"],
                      a["drop_type"], a["map_note"], cid))
    conn.commit()


# ---------------------------------------------------------------------------
# Route building
# ---------------------------------------------------------------------------
def _stop_from_row(conn, r, klat, klng, day_iso=None, acache=None):
    """Turn one delivery row into a stop dict, or flag it as unpinned.

    The location comes from the address resolved for THIS day — a customer may
    have an office address on weekdays and a home address otherwise, or a one-off
    override for tonight only.
    """
    keys = r.keys()
    addr = None
    if r["customer_id"] and day_iso:
        addr = resolve_address(conn, r["customer_id"], day_iso,
                               r["address_id"] if "address_id" in keys else None,
                               cache=acache, slot=(r["meal_type"] or "Lunch"))

    def pick(field, fallback=None):
        if addr is not None and field in addr.keys() and addr[field] is not None:
            return addr[field]
        return r[field] if field in keys else fallback

    lat = addr["lat"] if addr is not None else (r["lat"] if "lat" in keys else None)
    lng = addr["lng"] if addr is not None else (r["lng"] if "lng" in keys else None)
    if addr is not None and lat is None:          # address on file but not pinned
        lat = lng = None

    # Where this order is handed over, if it has been decided by hand.
    # Most specific wins: this one order tonight, then the standing rule on the
    # address. NULL at both levels means "let the automatic rule decide".
    assigned = r["porter_point_id"] if "porter_point_id" in keys else None
    assigned_from = "order" if assigned is not None else None
    if assigned is None and addr is not None and "porter_point_id" in addr.keys():
        assigned = addr["porter_point_id"]
        assigned_from = "address" if assigned is not None else None

    # Which route this order rides on. Most specific wins: this order tonight,
    # then the standing preference saved on the address ("Nanganallur always
    # rides West"), then the corridor's own default. The corridor fallback is
    # what stops a new customer being invisible on every route sheet until
    # somebody remembers to tick a box for them.
    batch_no = r["batch_no"] if "batch_no" in keys else None
    standing = (addr["batch_no"]
                if addr is not None and "batch_no" in addr.keys() else None)
    batch_from = "order" if batch_no is not None else None
    if batch_no is None and standing is not None:
        batch_no, batch_from = standing, "address"

    name = r["name"] or "—"
    base = {
        "assigned_point": assigned,
        "assigned_from": assigned_from,
        "batch_no": batch_no,
        "batch_from": batch_from,
        "standing_batch": standing,
        "delivery_id": r["id"],
        "name": name,
        "phone": r["phone"],
        "address": (addr["address"] if addr is not None else r["address"]) or "",
        "address_label": addr["label"] if addr is not None else None,
        "address_id": addr["id"] if addr is not None else None,
        "locality": r["locality"] or "",
        "map_note": pick("map_note"),
        "drop_type": pick("drop_type"),
        "plan_name": r["plan_name"] or "Meal",
        "day_note": r["day_note"],
        "sub_notes": r["sub_notes"] if "sub_notes" in keys else "",
        "status": r["status"],
    }
    if lat is None or lng is None:
        # No pin, but the locality text still names a corridor, and an order with
        # no pin still has to be carried by somebody.
        corr = pick("corridor") or corridor_for(r["locality"], base["address"])
        if base["batch_no"] is None and corr:
            fallback = corridor_routes(conn).get(corr)
            if fallback:
                base["batch_no"], base["batch_from"] = fallback, "corridor"
        base.update({"km": None, "zone": None, "corridor": corr, "pinned": False})
        return base
    # ROAD distance from the kitchen — this is the number printed on the sheet
    # and the number zones are banded on. Straight-line is kept only for the
    # ordering heuristics, where relative position is all that matters.
    km, src = road_km_from_kitchen(conn, lat, lng)
    zone = pick("zone") or zone_for_km(conn, km)
    corr = pick("corridor") or corridor_for(r["locality"], base["address"]) or "Local"
    if base["batch_no"] is None:
        fallback = corridor_routes(conn).get(corr)
        if fallback:
            base["batch_no"], base["batch_from"] = fallback, "corridor"
    base.update({"lat": lat, "lng": lng, "km": round(km, 2), "km_source": src,
                 "straight_km": round(haversine(klat, klng, lat, lng), 2),
                 "zone": zone, "corridor": corr, "pinned": True})
    return base


def _nearest_neighbour(stops, start_lat, start_lng):
    """Order a small group of stops greedily. Fine *within* a zone band."""
    out, pool = [], list(stops)
    clat, clng = start_lat, start_lng
    while pool:
        nxt = min(pool, key=lambda s: haversine(clat, clng, s["lat"], s["lng"]))
        pool.remove(nxt)
        out.append(nxt)
        clat, clng = nxt["lat"], nxt["lng"]
    return out


def assign_modes(conn, stops):
    """Decide rider / porter_handoff / porter_direct for each stop (Cowork §5.3).

    'On corridor' means the rider already has another stop on that corridor
    tonight — so carrying this one costs him nothing extra. A handoff is only
    ever offered at a porter point on a corridor he is genuinely riding; a
    detour to reach one costs more rider time than the Porter fare it saves.

    A MANUAL ASSIGNMENT ALWAYS WINS. Balaji knows things the rule cannot: that
    tonight the rider is passing Medavakkam anyway, or that a particular Porter
    driver is reliable at that junction. `assigned_point` on a stop (from the
    delivery row, or standing on the address) overrides every heuristic below.

    PUTTING AN ORDER ON A ROUTE ALSO WINS. Deciding that an order rides on
    someone's run IS the decision to ride it, whatever the distance says. Before
    this, a Tambaram order placed on the rider's own route was still classed
    'porter_direct' at 14 km and dropped out of both the printed route and the
    km the allowance is paid on.

    And the automatic rule only runs at all when `porter_auto` is switched on.
    Off — the default — a pinned order is ridden unless a handoff point was
    chosen for it by hand.
    """
    rider_max = fnum(conn, "rider_max_km")
    porter_min = fnum(conn, "porter_min_km")
    auto = str(setting(conn, "porter_auto") or "0") == "1"
    points = {p["id"]: dict(p) for p in conn.execute(
        "SELECT * FROM porter_points ORDER BY sort, id")}
    active = [p for p in points.values() if p["active"]]

    # Corridors the rider is definitely riding: those with a stop he must deliver
    # himself (short enough to be unambiguous), plus every corridor on a route he
    # has been given. Manually-assigned handoffs do not count — he is going to the
    # porter point, not to the customer.
    ridden = {s["corridor"] for s in stops
              if s.get("pinned") and not s.get("assigned_point")
              and (s.get("batch_no") is not None
                   or (s["km"] is not None and s["km"] <= rider_max))}

    for s in stops:
        # --- manual assignment, set on the order or standing on the address ---
        # Checked BEFORE the pin, deliberately: handing an order over at a porter
        # point does not need the customer's own pin at all. That is much of the
        # point of a porter point, and most customers are still unpinned.
        forced = s.get("assigned_point")
        if forced is not None:
            if forced == 0:                                  # "rider does this one"
                s["mode"], s["porter_point"] = "rider", None
                s["assigned"] = True
                continue
            pp = points.get(forced)
            if pp:
                s["mode"], s["porter_point"] = "porter_handoff", pp
                s["assigned"] = True
                continue
            # Assigned to a point that has since been deleted — fall through to
            # the automatic rule rather than silently dropping the instruction.
            s["assign_missing"] = True

        if not s.get("pinned"):
            s["mode"], s["porter_point"] = "rider", None
            continue

        # --- on somebody's route, or Porter suggestions switched off ----------
        # Either way the answer is the same: he rides it. This is the branch that
        # keeps a 14 km Tambaram stop on the run it was assigned to instead of
        # quietly turning it into a Porter booking nobody made.
        if s.get("batch_no") is not None or not auto:
            s["mode"], s["porter_point"] = "rider", None
            continue

        km, corr = s["km"], s["corridor"]
        on_corridor = corr in ridden

        if km <= rider_max and on_corridor:
            s["mode"], s["porter_point"] = "rider", None      # close, and on his way
            continue
        if km < porter_min:
            s["mode"], s["porter_point"] = "rider", None      # too near to be worth a Porter
            continue

        # Far. A handoff only pays off at a point he is already passing, so it
        # needs a pinned porter point on a corridor he is genuinely riding.
        pp = next((p for p in active
                   if p["lat"] is not None
                   and corr in [x.strip() for x in (p["serves"] or "").split(",")]),
                  None)
        if pp and on_corridor:
            s["mode"], s["porter_point"] = "porter_handoff", pp
        else:
            # Off every corridor he rides — sending him there costs more in his
            # time than the Porter fare it would save. Book direct and accept it.
            s["mode"], s["porter_point"] = "porter_direct", None
    return stops


# ---------------------------------------------------------------------------
# Splitting a slot into several routes, one per rider
# ---------------------------------------------------------------------------
# Ten lunch orders are not one person's run. Tambaram and Sholinganallur are
# opposite directions out of Perumbakkam, and riding both is an hour of wasted
# road. Today Balaji takes one direction and his father the other; a hired rider
# takes one whole batch. So a slot splits into batches, and each batch is its own
# route with its own km and its own allowance.

# Which corridors sit together naturally. West and Local share the same roads out
# of Perumbakkam; OMR-N and OMR-S share the other. Keeping these pairs whole is
# what stops the suggestion cutting a run in half down the middle of one road.
CORRIDOR_SIDE = {"West": "West", "Local": "West",
                 "OMR-N": "OMR", "OMR-S": "OMR"}


def suggest_batches(conn, day_iso, slot, n_batches=2):
    """Propose which order rides on which route. Returns {delivery_id: batch_no}.

    Groups by direction first, then balances. A customer with a standing batch
    on their address keeps it — those are decisions already made, and the
    suggestion must not quietly undo them.
    """
    n_batches = max(1, min(int(n_batches or 2), 6))
    route = build_route(conn, day_iso, slot, split=False)
    stops = [s for s in route["stops"] if s.get("delivery_id")]
    stops += route["handoffs"] + route["porter_direct"] + route["unpinned"]

    # How finely to cut. Two routes → the two sides out of Perumbakkam, which is
    # the split Balaji and his father already ride. Three or more → individual
    # corridors, otherwise everything past the second route comes out empty.
    def key_of(s):
        corr = s.get("corridor") or "Local"
        return CORRIDOR_SIDE.get(corr, "West") if n_batches <= 2 else corr

    out, groups = {}, {}
    for s in stops:
        did = s.get("delivery_id")
        if not did:
            continue
        if s.get("standing_batch"):
            out[did] = min(int(s["standing_batch"]), n_batches)
            continue
        groups.setdefault(key_of(s), []).append(s)

    # Still fewer groups than routes? Cut the biggest one by distance — the far
    # half and the near half of a corridor are a sensible pair of runs.
    while len(groups) < n_batches and any(len(v) > 1 for v in groups.values()):
        big = max(groups, key=lambda k: len(groups[k]))
        members = sorted(groups[big], key=lambda s: -(s.get("km") or 0))
        half = max(1, len(members) // 2)
        groups[big], groups[f"{big} (near)"] = members[:half], members[half:]

    # Load already carried by the standing assignments, so the rest balances
    # around them rather than ignoring them.
    load = {i: 0.0 for i in range(1, n_batches + 1)}
    for b in out.values():
        load[b] = load.get(b, 0) + 1

    # Biggest direction first into the emptiest route.
    for _key, members in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        target = min(load, key=lambda b: (load[b], b))
        for s in members:
            out[s["delivery_id"]] = target
            load[target] += 1
    return out


def batch_rows(conn, day_iso, slot):
    """The saved batches for a slot, always at least one."""
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM route_batches WHERE date=? AND slot=? ORDER BY batch_no",
        (day_iso, slot))]
    return rows


def batches_on_day(conn, day_iso, slot, rows=None):
    """The runs this slot actually splits into, worked out from the orders.

    Read from `route_batches` alone this came back empty for any day nobody had
    opened the Routes screen for -- so the sheet offered no way to print one
    rider's run, and the only sheet available was the whole slot with Chrompet
    and Semmancheri interleaved. A route exists because orders ride on it.
    """
    route = build_route(conn, day_iso, slot, rows=rows, split=False)
    seen = {}
    for group in (route["stops"], route["porter_direct"], route["unpinned"]):
        for st in group:
            if not st.get("delivery_id"):
                continue
            b = st.get("batch_no")
            if b is None:
                continue
            seen.setdefault(int(b), 0)
            seen[int(b)] += 1
    saved = {r["batch_no"]: r for r in batch_rows(conn, day_iso, slot)}
    out = []
    for b in sorted(seen):
        row = saved.get(b) or {}
        name = (row.get("name") or "").strip()
        # "Route 1" is the name ensure_batches writes when it has nothing better;
        # it carries no information, so the corridor name replaces it.
        if not name or name == f"Route {b}":
            name = route_label(conn, b)
        out.append({"batch_no": b, "name": name, "rider": row.get("rider"),
                    "n": seen[b]})
    return out


def ensure_batches(conn, day_iso, slot, n):
    """Make sure batch rows 1..n exist; never deletes one that has orders on it."""
    have = {r["batch_no"] for r in batch_rows(conn, day_iso, slot)}
    for i in range(1, n + 1):
        if i not in have:
            conn.execute(
                "INSERT INTO route_batches(date,slot,batch_no,name,created_at) "
                "VALUES(?,?,?,?,?)",
                (day_iso, slot, i, f"Route {i}", db.now_iso()))
    conn.commit()


def record_estimate(conn, day_iso, slot, batch_no, route):
    """Save what a route measured, so the week's conveyance can be added up later.

    The allowance is paid weekly, in cash, on kilometres nobody writes down. The
    columns to hold it have existed since the table was created and were never
    written, so a week's pay had no source but memory. Every time a route is
    built for a real batch, its figures are stamped here.

    `actual_km` is never touched — that is the rider-corrected number and it wins
    over anything measured.
    """
    if batch_no is None:
        return
    rate = fnum(conn, "petrol_rate")
    est = route.get("est_km") or 0
    conn.execute(
        "INSERT INTO route_batches(date, slot, batch_no, name, est_km, allowance, created_at) "
        "VALUES(?,?,?,?,?,?,?) "
        "ON CONFLICT(date, slot, batch_no) DO UPDATE SET est_km=excluded.est_km, "
        "allowance=excluded.allowance",
        (day_iso, slot, int(batch_no), f"Route {batch_no}", est,
         round(est * rate), db.now_iso()))
    conn.commit()


def payout_week(conn, start_iso, end_iso):
    """Every route ridden between two dates, and what it owes, grouped by rider.

    This is the sheet the weekly cash handover is counted from. Kilometres come
    from the rider's own corrected figure when he gave one, and from the measured
    route when he did not — the source is named on every line, because paying an
    estimate as though it were measured is how trust goes.
    """
    rate = fnum(conn, "petrol_rate")
    rows = conn.execute(
        "SELECT * FROM route_batches WHERE date>=? AND date<=? "
        "ORDER BY date, slot DESC, batch_no", (start_iso, end_iso)).fetchall()

    riders, days = {}, []
    for r in rows:
        n = conn.execute(
            "SELECT COUNT(*) n FROM deliveries WHERE date=? AND meal_type=? "
            "AND batch_no=? AND status IN ('Pending','Delivered')",
            (r["date"], r["slot"], r["batch_no"])).fetchone()["n"]
        if not n:
            continue              # a route nobody ended up riding owes nothing

        km, src = r["actual_km"], "rider"
        if km is None:
            km, src = r["est_km"], "measured"
        if km is None:
            # Never measured and never opened. Measure it now rather than pay zero.
            route = build_route(conn, r["date"], r["slot"], batch=r["batch_no"])
            km, src = route["est_km"], "measured"
            record_estimate(conn, r["date"], r["slot"], r["batch_no"], route)
        km = float(km or 0)

        rider = (r["rider"] or "").strip() or "— no rider named —"
        line = {"date": r["date"], "slot": r["slot"], "batch_no": r["batch_no"],
                "name": r["name"] or f"Route {r['batch_no']}", "rider": rider,
                "orders": n, "km": round(km, 1), "km_source": src,
                "amount": round(km * rate)}
        days.append(line)
        b = riders.setdefault(rider, {"rider": rider, "km": 0.0, "amount": 0,
                                      "runs": 0, "orders": 0, "lines": []})
        b["km"] += km
        b["amount"] += line["amount"]
        b["runs"] += 1
        b["orders"] += n
        b["lines"].append(line)

    for b in riders.values():
        b["km"] = round(b["km"], 1)
    out = sorted(riders.values(), key=lambda b: -b["amount"])
    return {"rate": rate, "riders": out, "lines": days,
            "total_km": round(sum(b["km"] for b in out), 1),
            "total_amount": sum(b["amount"] for b in out)}


def _group_by_point(handoffs):
    """[(porter_point_id, [stops])] — several orders to one point are one visit."""
    groups = {}
    for s in handoffs:
        pp = s.get("porter_point")
        if pp is None:
            continue
        groups.setdefault(pp["id"], []).append(s)
    return sorted(groups.items())


def build_route(conn, day_iso, slot, rows=None, batch=None, split=True):
    """The evening's route: ordered stops, modes, ETAs, Maps URL, km, allowance.

    Ordering (Cowork §5.4): hyperlocal cluster → near band → porter handoff →
    mid band → back to the kitchen. Long stops the rider actually carries are
    slotted after mid; long stops going by Porter never enter the riding order.

    `batch` limits the route to one of the slot's runs — each rider's own route,
    with its own km and its own allowance. None means the whole slot, which is
    also exactly what happens on a day nobody has split.
    `split=False` ignores batching entirely; used when proposing a split.
    """
    import core
    if rows is None:
        rows = core.deliveries_for_day(conn, day_iso)

    klat, klng, confirmed = kitchen(conn)
    want = "Dinner" if slot == "Dinner" else "Lunch"

    stops, unpinned = [], []
    acache = {}
    for r in rows:
        if (r["meal_type"] or "Lunch") != want:
            continue
        if r["status"] not in ("Pending", "Delivered"):
            continue          # Skipped/Off orders are not cooked and not ridden
        s = _stop_from_row(conn, r, klat, klng, day_iso, acache)
        if split and batch is not None and s["batch_no"] != int(batch):
            continue
        (stops if s["pinned"] else unpinned).append(s)

    # Unpinned stops are included so an explicit porter assignment still applies
    # to them — a handoff needs the porter point's pin, not the customer's.
    assign_modes(conn, stops + unpinned)
    # An unpinned order that has been assigned a handoff is no longer a problem
    # to solve: it has somewhere to go. Move it out of the warning list.
    handed = [s for s in unpinned if s["mode"] == "porter_handoff"]
    unpinned = [s for s in unpinned if s["mode"] != "porter_handoff"]

    porter_direct = [s for s in stops if s["mode"] == "porter_direct"]
    handoffs = [s for s in stops + handed if s["mode"] == "porter_handoff"]
    riding = [s for s in stops
              if s["mode"] != "porter_direct" and s["mode"] != "porter_handoff"]

    # A handoff stop is NOT ridden to the customer — the rider goes to the porter
    # point and hands the bag over there. So the porter point is the waypoint,
    # and several orders going to the same point are ONE visit, not several.
    # Routing to the customer instead would send him 13 km to Nanganallur for a
    # bag that only ever needed to reach Medavakkam, 3.8 km away, and would
    # overpay the km allowance by the same margin.
    for pp_id, group in _group_by_point(handoffs):
        pp = group[0]["porter_point"]
        if pp is None or pp["lat"] is None:
            continue                      # unpinned porter point: cannot be ridden to
        km, _src = road_km_from_kitchen(conn, pp["lat"], pp["lng"])
        riding.append({
            "is_handoff_point": True,
            "porter_point": pp,
            "orders": group,
            "name": pp["name"],
            "locality": pp["note"] or "",
            "lat": pp["lat"], "lng": pp["lng"],
            "km": round(km, 2),
            "zone": zone_for_km(conn, km),
            "corridor": (pp["serves"] or "").split(",")[0].strip() or "Local",
            "mode": "porter_handoff", "pinned": True,
            "delivery_id": None, "phone": None, "address": "",
            "plan_name": f"{len(group)} order{'s' if len(group) != 1 else ''} to hand over",
            "day_note": None, "sub_notes": "", "status": "Pending",
            "map_note": pp["note"], "drop_type": "Porter point",
        })
        for o in group:
            o["handoff_km"] = round(km, 2)

    bands = {z: [s for s in riding if s["zone"] == z] for z in ZONES}
    ordered = []
    ordered += _nearest_neighbour(bands["Hyperlocal"], klat, klng)
    anchor = (ordered[-1]["lat"], ordered[-1]["lng"]) if ordered else (klat, klng)
    ordered += _nearest_neighbour(bands["Near"], *anchor)
    anchor = (ordered[-1]["lat"], ordered[-1]["lng"]) if ordered else (klat, klng)
    ordered += _nearest_neighbour(bands["Mid"], *anchor)
    anchor = (ordered[-1]["lat"], ordered[-1]["lng"]) if ordered else (klat, klng)
    ordered += _nearest_neighbour(bands["Long"], *anchor)

    # One routing call for the whole loop: kitchen → each stop → kitchen. That
    # gives the billable total AND the per-hop distances used for the ETAs.
    loop = [(klat, klng)] + [(s["lat"], s["lng"]) for s in ordered] + [(klat, klng)]
    path = road_path(conn, loop) if ordered else {"total_km": 0.0, "legs": [], "source": "none"}
    legs = path["legs"] or [
        haversine(loop[i][0], loop[i][1], loop[i + 1][0], loop[i + 1][1])
        * fnum(conn, "road_factor") for i in range(len(loop) - 1)]

    speed = fnum(conn, "avg_speed_kmph") or 20
    dwell = fnum(conn, "dwell_min")
    t = _parse_hhmm(setting(conn, "depart_time"))
    prev_pin = None
    for i, s in enumerate(ordered, 1):
        leg = legs[i - 1] if i - 1 < len(legs) else 0.0
        # Handing several bags over at one point takes a little longer than a
        # single drop, but not once per order — one conversation, several bags.
        n_here = len(s.get("orders") or []) or 1
        # Same idea one door down: a customer taking an extra box that day is two
        # order rows at one address. The rider knocks once, so the second row is
        # the same visit — it must not book a second full dwell.
        pin = (s["lat"], s["lng"])
        same_visit = (pin == prev_pin)
        s["same_stop_as_prev"] = same_visit
        prev_pin = pin
        t += timedelta(minutes=(leg / speed) * 60
                       + dwell * ((0.4 if same_visit else 1) + 0.4 * (n_here - 1)))
        s["seq"] = i
        s["km_from_prev"] = round(leg, 2)
        s["eta"] = _clock(t)
        for o in (s.get("orders") or []):
            o["seq"] = i
            o["eta"] = s["eta"]
    # The ride home. The loop has always been measured kitchen -> stops -> kitchen,
    # so this leg is already inside est_km and already paid for — but it was never
    # shown, so on a 50 km Tambaram round trip there was no way to see that the
    # return was counted. It is printed on the sheet now, as its own line.
    back = legs[-1] if legs else 0.0
    t += timedelta(minutes=(back / speed) * 60)

    straight_km = _straight_path_km(loop) if ordered else 0.0
    est_km = round(path["total_km"], 1)
    km_source = path["source"]
    rate = fnum(conn, "petrol_rate")

    # Will this route actually close by the end of the shift? Worth knowing at
    # print time, while there is still a chance to hand something to a Porter —
    # not at 8:30 when he is still out on OMR.
    end = _parse_hhmm(setting(conn, "shift_end"))
    late_by = int((t - end).total_seconds() // 60) if ordered else 0

    return {
        "day": day_iso, "slot": slot, "batch": batch,
        "stops": ordered,
        "porter_direct": porter_direct,
        "handoffs": handoffs,          # the ORDERS handed over, not the points
        "handoff_points": [s for s in ordered if s.get("is_handoff_point")],
        "unpinned": unpinned,
        "kitchen": {"lat": klat, "lng": klng, "confirmed": confirmed},
        "maps_url": maps_url(klat, klng, ordered),
        "straight_km": round(straight_km, 2),
        "est_km": est_km,
        "km_source": km_source,
        "measured": km_source not in ("estimate", "none"),
        "rate": rate,
        "allowance": round(est_km * rate),
        "return_km": round(back, 2) if ordered else 0.0,
        "outbound_km": round(max(est_km - back, 0), 1) if ordered else 0.0,
        "return_eta": _clock(t) if ordered else None,
        "late_by": max(0, late_by),
        "shift_end": _clock(end),
        "over_waypoint_limit": len(ordered) > 9,
        "n_riding": len(ordered),
        "n_porter": len(porter_direct) + len(handoffs),
    }


def _parse_hhmm(s):
    try:
        h, m = str(s).split(":")
        return datetime(2000, 1, 1, int(h), int(m))
    except Exception:
        return datetime(2000, 1, 1, 18, 40)


def _clock(t):
    """'6:52 PM'. Written out rather than using %-I/%#I, which differ per platform."""
    h = t.hour % 12 or 12
    return f"{h}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


# ---------------------------------------------------------------------------
# Google Maps deep link
# ---------------------------------------------------------------------------
def maps_url(klat, klng, stops):
    """Multi-stop two-wheeler route that starts and ends at the kitchen.

    Verified against Google's Maps URLs spec:
      * api=1 is mandatory — without it every other parameter is silently dropped
      * waypoints are followed in the order given; there is no auto-optimise flag,
        which suits us because zone order beats shortest-path here
      * 9 waypoints max in the Maps app, only 3 if it opens in a mobile browser,
        so the app must be installed on the rider's phone
      * hard limit of 2,048 characters
    Coordinates are used rather than the free-text addresses because Chennai
    addresses like 'Campus K school' geocode to the wrong place or not at all.
    """
    origin = f"{klat},{klng}"
    parts = [
        "https://www.google.com/maps/dir/?api=1",
        f"origin={origin}",
        f"destination={origin}",          # ends where it started — allowance depends on it
        "travelmode=two-wheeler",
    ]
    pts = [s for s in stops if s.get("lat") is not None][:9]
    if pts:
        way = "%7C".join(f"{s['lat']},{s['lng']}" for s in pts)
        parts.append(f"waypoints={way}")
    url = "&".join(parts)
    return url[:2048]


def qr_data_uri(url):
    """Inline PNG QR of the route link, so it can be scanned off the printout."""
    try:
        import base64
        import io

        import qrcode
        img = qrcode.make(url, box_size=4, border=2)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None          # never let a missing QR break the sheet


# ---------------------------------------------------------------------------
# Road distance
# ---------------------------------------------------------------------------
# Straight-line distance is NOT usable here and must never be shown as if it
# were a delivery distance. Measured against this kitchen:
#
#     Rajkiran   straight 1.18 km   road 1.82 km   (1.53x)
#     Kaviya     straight 3.24 km   road 7.15 km   (2.13x)
#
# Sholinganallur is only 3.2 km away as the crow flies but 7 km by road, because
# the Pallikaranai marsh forces a long detour. A single multiplier is therefore
# wrong for every stop in a different way, so real road distance is fetched from
# a routing provider and cached. Without a provider the app still works, but the
# figure is labelled an estimate everywhere it appears — it is never presented as
# measured, because the petrol allowance is paid on it.

def _sig(points, flavour=""):
    """Cache key. `flavour` folds in the routing preference, so changing from
    shortest to recommended re-measures instead of serving the old answer."""
    raw = "|".join(f"{lat:.5f},{lng:.5f}" for lat, lng in points)
    if flavour:
        raw += "#" + flavour
    return hashlib.sha1(raw.encode()).hexdigest()


def _straight_path_km(points):
    return sum(haversine(points[i][0], points[i][1], points[i + 1][0], points[i + 1][1])
               for i in range(len(points) - 1))


def road_path(conn, points, use_cache=True):
    """Road distance along an ordered list of (lat, lng).

    Returns {total_km, legs: [km per leg], source}. `source` is the provider
    name when measured, or 'estimate' when it is haversine x road_factor.

    Cached by the exact ordered point list. The same customers repeat most
    evenings, so in practice a provider is called once per *new* route shape —
    a handful of calls a month, not one per delivery.
    """
    points = [(float(a), float(b)) for a, b in points]
    if len(points) < 2:
        return {"total_km": 0.0, "legs": [], "source": "none"}

    sig = _sig(points, setting(conn, "ors_preference"))
    if use_cache:
        hit = conn.execute(
            "SELECT km, source FROM route_km_cache WHERE signature=?", (sig,)).fetchone()
        if hit and hit["source"] != "estimate":
            legs = _cached_legs(conn, sig)
            return {"total_km": hit["km"], "legs": legs, "source": hit["source"]}

    # Try each configured provider in turn. The first that answers wins, and the
    # answer is tagged with which one it came from so the sheet can say so.
    for provider in provider_chain(conn):
        if provider == "google":
            got = _google_path(setting(conn, "routes_api_key"), points)
        elif provider == "ors":
            got = _ors_path(setting(conn, "ors_api_key"), points,
                            preference=setting(conn, "ors_preference"))
        elif provider == "osrm":
            got = _osrm_path(setting(conn, "osrm_base_url"), points)
        else:
            continue
        if got:
            total, legs = got
            conn.execute("INSERT OR REPLACE INTO route_km_cache(signature,km,source,at,legs) "
                         "VALUES(?,?,?,?,?)",
                         (sig, round(total, 2), provider, db.now_iso(),
                          json.dumps([round(x, 3) for x in legs])))
            conn.commit()
            return {"total_km": round(total, 2), "legs": legs, "source": provider}

    # No provider, or the provider failed. Fall back — clearly labelled.
    f = fnum(conn, "road_factor")
    legs = [haversine(points[i][0], points[i][1], points[i + 1][0], points[i + 1][1]) * f
            for i in range(len(points) - 1)]
    return {"total_km": round(sum(legs), 2), "legs": legs, "source": "estimate"}


def _cached_legs(conn, sig):
    row = conn.execute("SELECT legs FROM route_km_cache WHERE signature=?", (sig,)).fetchone()
    if row and row["legs"]:
        try:
            return json.loads(row["legs"])
        except ValueError:
            pass
    return []


def road_km_from_kitchen(conn, lat, lng):
    """Cached road distance kitchen → one point. (km, source)."""
    klat, klng, _ = kitchen(conn)
    r = road_path(conn, [(klat, klng), (lat, lng)])
    return r["total_km"], r["source"]


# --- providers. Each returns (total_km, [leg_km, ...]) or None on any failure --
def _http_json(req, timeout=15):
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def _google_path(api_key, points):
    """Google Routes API computeRoutes, with per-leg distances."""
    if not api_key:
        return None

    def wp(p):
        return {"location": {"latLng": {"latitude": p[0], "longitude": p[1]}}}

    body = {
        "origin": wp(points[0]),
        "destination": wp(points[-1]),
        "travelMode": "TWO_WHEELER",
        "routingPreference": "TRAFFIC_UNAWARE",   # distance is what we bill on
    }
    if len(points) > 2:
        body["intermediates"] = [wp(p) for p in points[1:-1]]
    req = urllib.request.Request(
        "https://routes.googleapis.com/directions/v2:computeRoutes",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "X-Goog-Api-Key": api_key,
                 "X-Goog-FieldMask": "routes.distanceMeters,routes.legs.distanceMeters"})
    out = _http_json(req)
    try:
        r = out["routes"][0]
        legs = [lg["distanceMeters"] / 1000.0 for lg in r.get("legs", [])]
        return r["distanceMeters"] / 1000.0, legs
    except (TypeError, KeyError, IndexError):
        return None


def _ors_path(api_key, points, preference="recommended"):
    """openrouteservice.org directions. Free tier, no card — see the ops doc.

    `preference` decides WHICH route is measured, and that is the whole story
    behind "the app says 44 km but Google Maps says 48":

      shortest    — the least-distance path. Reads ~8% under Google Maps because
                    Google shows its *recommended* route, not the shortest one.
      recommended — main roads, what Google Maps actually draws. The default,
                    because the printed sheet gives the rider a Google Maps link,
                    so this is the route he genuinely rides.

    They are two different routes, not two measurements of one route. Paying a
    per-kilometre allowance on a shortest path nobody rides underpays the rider.

    ORS has no motorcycle profile; driving-car is the closest match.
    """
    if not api_key:
        return None
    if preference not in ("shortest", "recommended", "fastest"):
        preference = "recommended"
    body = {"coordinates": [[p[1], p[0]] for p in points],   # ORS wants lng,lat
            "preference": preference}
    req = urllib.request.Request(
        "https://api.openrouteservice.org/v2/directions/driving-car",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": api_key,
                 "Accept": "application/json"})
    out = _http_json(req)
    try:
        r = out["routes"][0]
        segs = [sg["distance"] / 1000.0 for sg in r.get("segments", [])]
        return r["summary"]["distance"] / 1000.0, segs
    except (TypeError, KeyError, IndexError):
        return None


def _osrm_path(base_url, points):
    """A self-hosted OSRM instance. The public demo server is non-commercial
    only, so it is deliberately not a default — set this to your own host."""
    base = (base_url or "").rstrip("/")
    if not base:
        return None
    coords = ";".join(f"{p[1]},{p[0]}" for p in points)
    req = urllib.request.Request(
        f"{base}/route/v1/driving/{coords}?overview=false&alternatives=false",
        headers={"User-Agent": "og-gainz-command-center"})
    out = _http_json(req)
    try:
        r = out["routes"][0]
        legs = [lg["distance"] / 1000.0 for lg in r.get("legs", [])]
        return r["distance"] / 1000.0, legs
    except (TypeError, KeyError, IndexError):
        return None


PROVIDER_KEYS = {"google": "routes_api_key", "ors": "ors_api_key", "osrm": "osrm_base_url"}


def provider_chain(conn):
    """The configured providers, in priority order, keeping only usable ones."""
    raw = (setting(conn, "distance_provider") or "").strip().lower()
    out = []
    for p in raw.replace(" ", "").split(","):
        if p in PROVIDER_KEYS and setting(conn, PROVIDER_KEYS[p]) and p not in out:
            out.append(p)
    return out


def provider_status(conn):
    """What the app will use for distance, and whether it is actually usable."""
    raw = (setting(conn, "distance_provider") or "").strip().lower()
    chain = provider_chain(conn)
    named = [p for p in raw.replace(" ", "").split(",") if p]
    if not named:
        return {"provider": None, "chain": [], "ready": False,
                "why": "No routing provider configured — all distances are estimates."}
    unusable = [p for p in named if p not in chain]
    if not chain:
        miss = ", ".join(f"{p} (no {PROVIDER_KEYS.get(p, 'key')})" for p in unusable)
        return {"provider": None, "chain": [], "ready": False,
                "why": f"Nothing usable: {miss}. Distances are estimates."}
    return {"provider": chain[0], "chain": chain, "ready": True,
            "backup": chain[1:],
            "why": (f"Unusable and skipped: {', '.join(unusable)}." if unusable else "")}


def check_provider(conn):
    """Live round-trip against every configured provider, bypassing the cache."""
    st = provider_status(conn)
    if not st["ready"]:
        return {"ok": False, "msg": st["why"], "results": []}
    klat, klng, _ = kitchen(conn)
    probe = [(klat, klng), (klat + 0.02, klng + 0.02)]
    straight = _straight_path_km(probe)

    results = []
    for p in st["chain"]:
        fn = {"google": lambda: _google_path(setting(conn, "routes_api_key"), probe),
              "ors": lambda: _ors_path(setting(conn, "ors_api_key"), probe),
              "osrm": lambda: _osrm_path(setting(conn, "osrm_base_url"), probe)}[p]
        got = fn()
        results.append({"provider": p, "ok": bool(got),
                        "km": round(got[0], 2) if got else None})

    live = [r for r in results if r["ok"]]
    if not live:
        return {"ok": False, "results": results,
                "msg": "No provider answered. Check the keys — distances will be estimates."}
    parts = [f"{r['provider']} {r['km']} km" if r["ok"] else f"{r['provider']} FAILED"
             for r in results]
    primary = live[0]["provider"]
    extra = ""
    if primary != st["chain"][0]:
        extra = (f" Your first choice ({st['chain'][0]}) did not answer, so "
                 f"{primary} would be used instead.")
    return {"ok": True, "results": results,
            "msg": f"Test leg ({straight:.2f} km straight-line) → " + ", ".join(parts) +
                   f". Using {primary}.{extra}"}


# ---------------------------------------------------------------------------
# Pin coverage — what still needs a map pin
# ---------------------------------------------------------------------------
# How far back a delivered trial stays on the pin page. Long enough that a trial
# from this week or last can still be given a location — that is when it matters,
# because a trial that converts brings its pin with it — and short enough that the
# work list stays a work list instead of a year of history.
TRIAL_PIN_WINDOW_DAYS = 14


def pin_status(conn):
    """Which delivery stops still need a pin, the ones riding soonest first.

    Keyed on addresses rather than customers, because a customer can have a
    pinned home and an unpinned office — and the unpinned one silently drops out
    of the route on the days it is used.

    Trials are listed alongside them. A trial box is carried to a real door by
    the same rider on the same run, so it needs a pin for exactly the same
    reasons; leaving trials out meant a trial customer could never be given a
    location and rode every time as "no pin", with no distance and no share of
    the km allowance. Only recent and upcoming trials are listed — an old
    delivered trial is history, not work — but one that already has a pin is
    always shown, so a location that was set never looks lost.

    Every row is a plain dict with the same shape, `kind` saying which table it
    came from and `pin_id` its key in that table.
    """
    out = []
    for r in conn.execute(
            "SELECT a.*, c.name, c.locality, c.phone, "
            "  (SELECT COUNT(*) FROM subscriptions s "
            "    WHERE s.customer_id=c.id AND s.status IN ('Active','Ending Soon')) AS active_subs "
            "FROM customer_addresses a JOIN customers c ON c.id=a.customer_id "
            "WHERE a.active=1 ORDER BY c.id, a.is_default DESC, a.id").fetchall():
        d = dict(r)
        d.update({"kind": "address", "pin_id": r["id"], "sort_key": r["customer_id"],
                  "blocking": (r["active_subs"] or 0) > 0,
                  "label": r["label"] or "Home"})
        out.append(d)

    cutoff = (datetime.now() - timedelta(days=TRIAL_PIN_WINDOW_DAYS)).strftime("%Y-%m-%d")
    for r in conn.execute(
            "SELECT * FROM trial_orders "
            "WHERE lat IS NOT NULL "                       # never hide a pin already set
            "   OR COALESCE(delivery_status,'Pending') <> 'Delivered' "   # still to ride
            "   OR COALESCE(delivery_date, date) >= ? "                   # recent enough
            "ORDER BY COALESCE(delivery_date, date) DESC, id DESC", (cutoff,)).fetchall():
        d = dict(r)
        # A trial's own address is all it has; there is no customer row behind it.
        d.update({"kind": "trial", "pin_id": r["id"], "sort_key": r["id"],
                  "customer_id": r["id"], "name": (r["name"] or "—").strip(),
                  "label": "Trial", "locality": "", "active_subs": 0,
                  "blocking": (r["delivery_status"] or "Pending") == "Pending"})
        out.append(d)

    missing = [r for r in out if r["lat"] is None or r["lng"] is None]
    pinned = [r for r in out if r["lat"] is not None and r["lng"] is not None]
    missing.sort(key=lambda r: (not r["blocking"], r["sort_key"]))
    return {
        "missing": missing,
        "pinned": pinned,
        "blocking": [r for r in missing if r["blocking"]],
        "total": len(out),
    }


def parse_location(text):
    """Anything you can copy out of Google Maps → (lat, lng, how) or (None, None, reason).

    Offline and exact — it only ever reads coordinates that are already IN the
    text. Accepts, in this order:
      * a plus code, short or full   — 'V6R3+XM Chennai', '7M42V6R3+XM'
      * a Maps URL carrying a point  — !3d12.89!4d80.20, @12.89,80.20,
                                       ?q=/ll=/daddr=/center=, geo:12.89,80.20
      * a bare coordinate pair       — '12.892438, 80.204187', '12.89° N, 80.20° E'

    Deliberately strict: a pin that is merely *plausible* is worse than no pin,
    because a wrong one silently mis-pays the petrol allowance every day. Anything
    it cannot read with confidence comes back as None with a reason to show.

    A share link (maps.app.goo.gl/...) carries no coordinates at all, so it fails
    here by design — resolve_location() is the one that can go and fetch them.
    """
    import re
    s = (text or "").strip()
    if not s:
        return None, None, "empty"

    # Plus code — checked first because it also contains digits and commas.
    m = re.search(r"\b([23456789CFGHJMPQRVWX]{4,8}\+[23456789CFGHJMPQRVWX]{2,3})\b", s.upper())
    if m:
        got = olc_recover(m.group(1))
        if got and _in_chennai(*got):
            return got[0], got[1], "plus code"
        if got:
            return None, None, "that plus code is not near Chennai"

    # !3d<lat>!4d<lng> comes first: in a Maps place URL it is the PLACE, whereas
    # the @ that precedes it is only where the map happened to be centred. They
    # can sit a street apart, and the place is the one being pinned.
    pats = [
        r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)",
        r"@(-?\d+\.\d+),(-?\d+\.\d+)",                       # .../@12.89,80.20,17z
        r"[?&](?:q|ll|sll|daddr|saddr|center|destination|query)="
        r"(-?\d+\.\d+),\s*(-?\d+\.\d+)",
        r"\bgeo:(-?\d+\.\d+),(-?\d+\.\d+)",                  # geo: URI from a share sheet
        r"(-?\d{1,2}\.\d{3,})\s*°?\s*[NS]?\s*,\s*(-?\d{1,3}\.\d{3,})\s*°?\s*[EW]?",
    ]
    m = None
    for p in pats:
        m = re.search(p, s, re.I)
        if m:
            break
    if not m:
        if _short_link(s):
            return None, None, "that share link doesn't contain any coordinates"
        return None, None, "couldn't find a plus code or coordinates in that"

    lat, lng = float(m.group(1)), float(m.group(2))
    if not _in_chennai(lat, lng):
        if _in_chennai(lng, lat):
            return None, None, "those look swapped — latitude comes first"
        return None, None, "that point is not in the Chennai area"
    return lat, lng, "coordinates"


# --- the two network paths, for what cannot be read out of the text itself ----
# Both are best-effort and fail closed: the caller still gets (None, None, reason)
# and the pin is simply not saved.
def _short_link(s):
    """Is this one of Google's share links, which carry no coordinates?"""
    import re
    m = re.search(r"https?://(?:maps\.app\.goo\.gl|goo\.gl/maps|g\.co/kgs)/\S+", s, re.I)
    return m.group(0).rstrip(".,;)") if m else None


def expand_short_link(url, timeout=6):
    """Follow a Maps share link to the real URL, which does carry coordinates.

    NOTE: on PythonAnywhere's free plan outgoing requests are allowlisted and
    goo.gl is NOT on the list, so this fails there ("Tunnel connection failed")
    and the place-name lookup below is what actually answers. It works on the
    laptop, and would start working on a paid plan, so it is tried first.
    """
    class _Keep(urllib.request.HTTPRedirectHandler):
        # Google answers the share link with a redirect whose Location IS the
        # answer; there is no need to fetch the (large) Maps page behind it.
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise _Redirected(newurl)

    try:
        opener = urllib.request.build_opener(_Keep)
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with opener.open(req, timeout=timeout) as resp:
            return resp.geturl()
    except _Redirected as r:
        return r.url
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None


class _Redirected(Exception):
    def __init__(self, url):
        super().__init__(url)
        self.url = url


_UA = "OG-Gainz-CommandCenter/1.0 (+https://oggainz.pythonanywhere.com)"


def geocode_place(text, timeout=7):
    """A place name or address → (lat, lng, label) via OpenStreetMap's Nominatim.

    Chosen because nominatim.openstreetmap.org IS on PythonAnywhere's allowlist
    and needs no API key, so it works on the live server exactly as it does on
    the laptop. Biased hard to Chennai: bounded viewbox, India only, so a
    half-typed apartment name cannot return a match in another state.

    Known-weak on the addresses this business actually gets ("Campus K school") —
    that is why the result is reported back with the address it matched, so a
    wrong match is visible instead of silently becoming a delivery point.
    """
    from urllib.parse import urlencode
    q = (text or "").strip()
    if len(q) < 3:
        return None
    params = urlencode({
        "q": q if "chennai" in q.lower() else q + ", Chennai",
        "format": "jsonv2", "limit": "1", "countrycodes": "in",
        # left,top,right,bottom — greater Chennai, with bounded=1 to enforce it
        "viewbox": "79.9,13.35,80.45,12.6", "bounded": "1",
    })
    req = urllib.request.Request(
        "https://nominatim.openstreetmap.org/search?" + params,
        headers={"User-Agent": _UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            got = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    if not got:
        return None
    try:
        lat, lng = float(got[0]["lat"]), float(got[0]["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    if not _in_chennai(lat, lng):
        return None
    return lat, lng, (got[0].get("display_name") or q)


def resolve_location(text, allow_network=True):
    """parse_location, then the network paths it cannot cover. Same 3-tuple.

    Order matters: coordinates already in the text are exact and free, so they
    always win. Only when there are none do we go out — first expanding a share
    link, then falling back to looking the words up as a place. The place lookup
    is last because it is the only one that can be confidently WRONG.
    """
    lat, lng, how = parse_location(text)
    if lat is not None or not allow_network:
        return lat, lng, how

    s = (text or "").strip()
    if not s:
        return None, None, "empty"

    link = _short_link(s)
    if link:
        full = expand_short_link(link)
        if full:
            lat, lng, how2 = parse_location(full)
            if lat is not None:
                return lat, lng, "share link"
        # The link could not be followed (or led nowhere useful). Any words typed
        # alongside it are still worth a look-up; a bare link leaves nothing to go on.
        rest = s.replace(link, " ").strip(" -–—·,")
        if len(rest) < 3:
            return None, None, ("that share link can't be opened from the server. "
                                "Open it in Maps and copy the place NAME, or tap "
                                "'Use my location' while you are standing there")
        s = rest

    if s.lower().startswith("http"):
        return None, None, how
    got = geocode_place(s)
    if got:
        return got[0], got[1], f"found by name - {got[2][:80]}"
    return None, None, ("couldn't find that place near Chennai - try the plus code "
                        "from the Maps place card, or tap 'Use my location'")


def _in_chennai(lat, lng):
    """Generous box around greater Chennai — catches swaps and typos, not fine errors."""
    return 12.0 <= lat <= 14.0 and 79.0 <= lng <= 81.0


def maps_search_url(cust):
    """A Maps search seeded with the customer's address, to find and drop the pin."""
    from urllib.parse import quote
    q = ", ".join(x for x in [(cust["address"] or "").strip(),
                              (cust["locality"] or "").strip(), "Chennai"] if x)
    return "https://www.google.com/maps/search/?api=1&query=" + quote(q)
