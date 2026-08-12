"""
Tests for reading a map pin out of whatever can be copied on a phone.

    python test_location.py

Runs OFFLINE by default: the two network paths (following a share link,
looking a place name up on Nominatim) are stubbed, so the suite is
deterministic and never depends on OpenStreetMap being up or on
PythonAnywhere's outgoing allowlist.

    python test_location.py --live      also hits the real Nominatim

The thing being protected here is that a wrong pin never gets saved. Every
distance, every zone and every rupee of the ₹3/km petrol allowance is measured
from these coordinates, so "couldn't read it" has to beat "close enough".
"""

import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

APP_DIR = os.path.dirname(os.path.abspath(__file__))
os.environ["OG_DB_PATH"] = os.path.join(tempfile.gettempdir(), "og_loc_test.db")
os.environ.setdefault("OG_SECRET_KEY", "test-secret")
sys.path.insert(0, APP_DIR)

import ops                                                       # noqa: E402

LIVE = "--live" in sys.argv
FAILS, N = [], 0


def check(label, cond, extra=""):
    global N
    N += 1
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} {extra}")
        FAILS.append(label)


def near(got, lat, lng, tol=0.002):
    return got[0] is not None and abs(got[0] - lat) < tol and abs(got[1] - lng) < tol


KLAT, KLNG = 12.892438, 80.204187

print("== 1. what you can copy on a phone, read offline ==")
cases = [
    ("bare coordinates",        f"{KLAT}, {KLNG}"),
    ("no space after comma",    f"{KLAT},{KLNG}"),
    ("with degree signs",       f"{KLAT}° N, {KLNG}° E"),
    ("a ?q= Maps link",         f"https://www.google.com/maps?q={KLAT},{KLNG}"),
    ("an ?ll= Maps link",       f"https://maps.google.com/?ll={KLAT},{KLNG}&z=17"),
    ("a geo: URI",              f"geo:{KLAT},{KLNG}"),
    ("a directions link",       f"https://www.google.com/maps/dir/?api=1&destination={KLAT},{KLNG}"),
]
for label, text in cases:
    check(label, near(ops.parse_location(text), KLAT, KLNG), str(ops.parse_location(text)))

check("a plus code", near(ops.parse_location("V6R3+XM Chennai"), KLAT, KLNG),
      str(ops.parse_location("V6R3+XM Chennai")))
check("...and a bare one", near(ops.parse_location("V6R3+XM"), KLAT, KLNG))

# In a Maps place URL the @ is only where the map was CENTRED; !3d/!4d is the
# place itself. They can sit a street apart, so the place has to win.
place = ("https://www.google.com/maps/place/Somewhere/@12.8924,80.2041,17z/"
         "data=!3m1!4b1!4m6!3m5!1s0x0:0x0!8m2!3d12.8931!4d80.2055")
check("a place link prefers the place over the map centre",
      near(ops.parse_location(place), 12.8931, 80.2055), str(ops.parse_location(place)))

print("\n== 2. a bad pin is refused, not guessed ==")
for label, text, want in [
    ("empty",                    "",                      "empty"),
    ("a point in London",        "51.5074, -0.1278",      "not in the Chennai"),
    ("swapped lat/lng",          "80.204187, 12.892438",  "swapped"),
    ("a plus code in London",    "9C3XGV4C+2V",           "not near Chennai"),
    ("free text with no clues",  "somewhere near the temple", "couldn't find"),
]:
    got = ops.parse_location(text)
    check(label + " is refused", got[0] is None, str(got))
    check(f"...and says why ({want})", want in (got[2] or ""), got[2])

print("\n== 3. a share link carries no coordinates ==")
short = "https://maps.app.goo.gl/aBcDeF123456"
got = ops.parse_location(short)
check("offline, a share link cannot be read", got[0] is None)
check("...and it says so specifically", "share link" in (got[2] or ""), got[2])
check("_short_link spots the new form", ops._short_link(short) == short)
check("...and the old goo.gl/maps form",
      ops._short_link("see https://goo.gl/maps/xyz789 thanks") == "https://goo.gl/maps/xyz789")
check("...and is not fooled by an ordinary Maps URL",
      ops._short_link("https://www.google.com/maps/place/X") is None)

print("\n== 4. following a share link ==")
TARGET = ("https://www.google.com/maps/place/Test/@12.8000,80.2000,17z/"
          "data=!4m6!3m5!8m2!3d12.893100!4d80.205500")


class _R(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(302)
        self.send_header("Location", TARGET)
        self.end_headers()

    def log_message(self, *a):
        pass


srv = HTTPServer(("127.0.0.1", 0), _R)
threading.Thread(target=srv.serve_forever, daemon=True).start()
try:
    expanded = ops.expand_short_link(f"http://127.0.0.1:{srv.server_address[1]}/abc")
    check("the redirect is followed to the real URL", expanded == TARGET, str(expanded))
    check("...and the place comes out of it",
          near(ops.parse_location(expanded), 12.8931, 80.2055))
finally:
    srv.shutdown()

# The live server cannot reach goo.gl at all (PythonAnywhere's free plan
# allowlists outgoing requests). That must be a clean "no", never a traceback
# and never a hang that takes the page down with it.
check("an unreachable link fails closed",
      ops.expand_short_link("http://127.0.0.1:9/nope", timeout=3) is None)

print("\n== 5. looking a place up by name ==")
_real_geocode, _real_expand = ops.geocode_place, ops.expand_short_link
ops.geocode_place = lambda t, timeout=7: (12.9174, 80.2164, "Sholinganallur, Chennai")
ops.expand_short_link = lambda u, timeout=6: None
try:
    got = ops.resolve_location("Sholinganallur")
    check("a place name resolves", near(got, 12.9174, 80.2164), str(got))
    check("...and reports that it was a name lookup",
          (got[2] or "").startswith("found by name"), got[2])

    # Coordinates in the text are exact and free -- they must never be thrown
    # away in favour of a lookup.
    ops.geocode_place = lambda t, timeout=7: (1.0, 1.0, "WRONG")
    check("real coordinates beat a name lookup",
          near(ops.resolve_location(f"{KLAT}, {KLNG}"), KLAT, KLNG))
    check("a plus code beats a name lookup",
          near(ops.resolve_location("V6R3+XM Chennai"), KLAT, KLNG))

    # A share link that cannot be followed, with nothing else to go on, must not
    # fall back to geocoding the URL string itself.
    got = ops.resolve_location(short)
    check("an unfollowable share link resolves to nothing", got[0] is None, str(got))
    check("...and explains what to copy instead",
          "place NAME" in (got[2] or "") or "Use my location" in (got[2] or ""), got[2])

    ops.geocode_place = lambda t, timeout=7: None
    got = ops.resolve_location("Qwerty Xyzzy Nowhere")
    check("an unknown place is refused", got[0] is None, str(got))

    check("allow_network=False never goes out",
          ops.resolve_location("Sholinganallur", allow_network=False)[0] is None)
finally:
    ops.geocode_place, ops.expand_short_link = _real_geocode, _real_expand

if LIVE:
    print("\n== 6. against the real Nominatim ==")
    got = ops.geocode_place("Sholinganallur")
    check("a real Chennai locality is found", got is not None and near(got, 12.917, 80.216, 0.05),
          str(got))
    check("nonsense is not", ops.geocode_place("Qwerty Xyzzy Nowhere Zzz") is None)

print(f"\n{N - len(FAILS)}/{N} checks passed")
print("ALL GREEN" if not FAILS else "FAILURES: " + ", ".join(FAILS))
sys.exit(1 if FAILS else 0)
