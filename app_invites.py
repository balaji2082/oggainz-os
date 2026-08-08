"""
OG Gainz Command Center — the "Invite to app" button.

One button on a customer's page that produces the private link Balaji sends on
WhatsApp, so the customer can sign in to the OG Gainz app.

Additive: this module registers one route and touches no existing view.

WHY IT ASKS SOMEONE ELSE
------------------------
The invite itself is NOT stored here. Login accounts and invites live in the
app's own Postgres database (og-gainz-app/docs/03 §3), which this app has no
access to — deliberately, so each database has exactly one owner. So the button
asks the app to mint a link and shows the answer.

    Command Center  ──X-OG-Admin-Token──>  the app  ──>  Postgres
       (this file)                          /api/admin/invite

Nothing about a customer, a subscription or a meal is sent or changed. The
request carries one thing: which customer id the link is for.

SWITCHED OFF UNLESS CONFIGURED
------------------------------
With OG_APP_URL / OG_ADMIN_TOKEN unset, the button does not appear at all. So
this is inert until Balaji deliberately turns it on.
"""

import json
import os
import urllib.error
import urllib.request

from flask import flash, redirect, request, url_for

import db

APP_URL = os.environ.get("OG_APP_URL")          # e.g. https://oggainz.com
ADMIN_TOKEN = os.environ.get("OG_ADMIN_TOKEN")


def enabled():
    return bool(APP_URL and ADMIN_TOKEN)


def register(app):

    @app.context_processor
    def _invite_globals():
        # The customer page links straight to the app's own invite screen.
        # It cannot POST here: PythonAnywhere's free plan permits outgoing
        # requests only to an allowlist, so this server is not allowed to call
        # the app at all -- its proxy answers "Tunnel connection failed".
        # The connection works one way only: app -> Command Center.
        return {"g_invites_on": enabled(), "g_app_url": (APP_URL or "").rstrip("/")}

    @app.route("/customers/<cid>/invite", methods=["POST"])
    def customer_invite(cid):
        """Ask the app for a fresh invite link for this customer."""
        if not enabled():
            flash("The app link is not set up yet — OG_APP_URL and OG_ADMIN_TOKEN "
                  "need to be set on the server.", "")
            return redirect(url_for("customer_detail", cid=cid))

        c = db.get_db()
        cust = c.execute("SELECT id, name, phone FROM customers WHERE id=?", (cid,)).fetchone()
        c.close()
        if not cust:
            flash("No such customer.", "")
            return redirect(url_for("customers"))

        payload = json.dumps({"customer_id": cid}).encode()
        req = urllib.request.Request(
            APP_URL.rstrip("/") + "/api/admin/invite",
            data=payload,
            headers={"Content-Type": "application/json",
                     "X-OG-Admin-Token": ADMIN_TOKEN},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            # The app answers 409 when this customer already has an account —
            # a real answer, not a failure, so say what it means.
            try:
                body = json.loads(e.read().decode())
            except Exception:
                body = {}
            if e.code == 409:
                flash(body.get("message") or f"{cust['name']} already has an app account.", "")
            else:
                flash(f"The app refused the request ({e.code}). "
                      f"{body.get('error') or ''}".strip(), "")
            return redirect(url_for("customer_detail", cid=cid))
        except Exception as e:
            # Vercel unreachable, DNS, timeout. Never leave Balaji guessing.
            flash(f"Could not reach the app to make a link: {e}", "")
            return redirect(url_for("customer_detail", cid=cid))

        link = data.get("url")
        if not link:
            flash("The app did not return a link.", "")
            return redirect(url_for("customer_detail", cid=cid))

        # Stash it for one render so the page can show it with a copy + WhatsApp
        # button. Not stored: a link is single-use and expires in a week, and
        # keeping copies of live invite links around is exactly what you would
        # not want if the database were ever seen by someone else.
        days = data.get("expires_days", 7)
        flash(f"INVITE|{link}|{cust['name']}|{cust['phone'] or ''}|{days}", "invite")
        return redirect(url_for("customer_detail", cid=cid))
