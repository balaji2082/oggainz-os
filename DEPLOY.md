# Put OG Gainz Command Center on your phone (anywhere, no laptop)

This puts your app on a free always-on server (**PythonAnywhere**) so you can open it from
your phone on mobile data, with the laptop off. Your values are already filled in below.

- **Your web address (after setup):** `https://oggainz.pythonanywhere.com`
- **GitHub repo (app code):** `https://github.com/balaji2082/oggainz-os`
- **PythonAnywhere user:** `oggainz`

You do this **once**. After that, any change I make reaches your phone with one command.

---

## Step 1 — GitHub ✅ (done — I push the code for you)

Account made and repo created. I add the upload address and push the app code from your laptop.
Your customer data (`oggainz.db`) and personal/Finance files are **not** included.

---

## Step 2 — PythonAnywhere account ✅ (done)

You're signed in as `oggainz`.

---

## Step 3 — Download the app onto the server

1. Top menu → **Consoles** → under "Start a new console" click **Bash**.
   (A black command window opens — this is normal.)
2. Type this and press **Enter**:

   ```
   git clone https://github.com/balaji2082/oggainz-os.git
   ```

   Because the repo is **private**, it asks for your GitHub **username** (`balaji2082`) and a
   **password** — but GitHub needs a **"token"** here, not your real password. Make one:
   GitHub → click your avatar → **Settings** → (left, very bottom) **Developer settings** →
   **Personal access tokens** → **Tokens (classic)** → **Generate new token (classic)** →
   give it a name, tick the **`repo`** box → **Generate** → **copy** it.
   Paste that token when the console asks for the password. *(Nothing appears while you paste —
   that's normal — then press Enter.)*  I'll be right here for this.

3. You now have a folder called `oggainz-os`. ✅

---

## Step 4 — Install the two libraries the app needs

In the same Bash window, run these two lines (one at a time, Enter after each):

```
mkvirtualenv ogenv --python=/usr/bin/python3.10
pip install -r oggainz-os/requirements.txt
```

The first makes a clean little workspace; the second installs Flask + openpyxl. Wait for each
to finish. ✅

---

## Step 5 — Create the web app + set your password

1. Top menu → **Web** → **Add a new web app** → **Next**.
2. Choose **Manual configuration** (NOT "Flask") → choose **Python 3.10** → **Next** → done.
3. You're now on the Web page. Two things to set:

   **(a) Virtualenv** — find the "Virtualenv" section, click the red text and enter:
   ```
   ogenv
   ```
   then click the tick to save.

   **(b) WSGI file** — find "WSGI configuration file" and click the blue link
   (`/var/www/oggainz_pythonanywhere_com_wsgi.py`). It opens an editor.
   **Delete everything** in it and paste exactly this:

   ```python
   import os
   import sys

   # ---- OG Gainz settings ----
   # Change ONLY the password below to whatever you want to type on your phone.
   os.environ["OG_PASSWORD"]   = "CHANGE-ME-pick-a-password"
   os.environ["OG_SECRET_KEY"] = "ee853b58130af877e2141a140f134d0fa4f394ff32ea437277b9fc09bee03ea7"

   project_home = "/home/oggainz/oggainz-os"
   if project_home not in sys.path:
       sys.path.insert(0, project_home)

   from app import app as application   # this runs your Command Center
   ```

   - Change `CHANGE-ME-pick-a-password` to your real password (keep the quotes).
   - Leave everything else exactly as-is.
   - Click the green **Save** button (top right of the editor).

---

## Step 6 — Move your real data onto the server

Your customers/subscriptions live in a file called **`oggainz.db`** on your laptop. We copy it up.

1. Top menu → **Files**.
2. Open the **`oggainz-os`** folder.
3. Click **Upload a file** and choose `oggainz.db` from
   `…\Desktop\AI-Workspace\OG-Gainz-OS\oggainz.db` on your laptop.
   *(If it asks to overwrite, say yes.)*

✅ All your real data is now on the server.

---

## Step 7 — Go live & add to your phone

1. Back to **Web** → click the big green **Reload** button.
2. On your phone open: **`https://oggainz.pythonanywhere.com`**
   → password screen → type your password → you're in. 🎉
3. **Add to home screen** so it feels like a real app:
   - **iPhone (Safari):** Share button → *Add to Home Screen*.
   - **Android (Chrome):** ⋮ menu → *Add to Home screen* / *Install app*.

**Final test:** turn **WiFi off** on your phone (so you're on mobile data), open the app, mark
a delivery, refresh — the change sticks. That proves it works anywhere and saves correctly.

---

## Updating the app later (the easy part)

When you want a change, tell me. I push it to GitHub. Then you:

1. **Consoles** → open a **Bash** console.
2. Run:
   ```
   cd oggainz-os && git pull
   ```
3. **Web** → **Reload**. Done — the change is live on your phone.

---

## If something looks wrong

- **"Something went wrong" / error page:** Web tab → scroll to **Error log** → click it →
  copy the last few red lines and send them to me. (Usually a small typo in the WSGI file.)
- **Password not asked / wrong password:** re-open the WSGI file, check `OG_PASSWORD`, Save, Reload.
- **Page won't load at all:** make sure you clicked the green **Reload** after every change.

> Free-tier note: PythonAnywhere emails you every ~3 months to click one link to keep the
> account active. The first load after a quiet spell can take a few seconds. Both are normal.

---

## July 2026 upgrade — new features & how to deploy them

The upgrade (detailed prep list, menu engine, invoices + T&C, backlog clearer, OG Agent) is
all additive: push the code and it sets itself up on first load (new tables, brochure menu,
updated pack prices, Royal retired). Your data is untouched; a backup is still taken locally.

**Deploy as always:**
1. Laptop: `git add -A && git commit -m "upgrade" && git push`
2. PythonAnywhere Bash console: `cd oggainz-os && git pull`
3. Web tab → **Reload**.

**Optional — OG Agent extras (both configured inside the app, Settings → OG Agent):**
- **Chat brain:** paste an Anthropic API key (console.anthropic.com → API keys). Without it,
  the rules brain (morning brief, note reading, reminders) still works fully.
- **Telegram alerts:** create a bot with @BotFather, paste the bot token + your chat id,
  then hit "Send Telegram test".
- **Morning brief without opening the app:** PythonAnywhere → Tasks → add a daily task:
  `python3.10 /home/oggainz/oggainz-os/agent_daily.py` (free tier includes one daily task).

**Customer-facing links (no password needed, safe to share):**
- Invoices: each invoice gets its own secret link (`/i/<code>`) — send it via the WhatsApp button.
- Terms & Conditions: `https://oggainz.pythonanywhere.com/terms`
- Brochure: `https://oggainz.pythonanywhere.com/brochure` — plans, menus & pricing.

---

## 14 July 2026 update — what's new (same 2-step deploy: `git pull` + Reload)

All additive again — new tables create themselves on first load; **live customer data and
subscriptions are untouched.**

- **Prep list tick-offs:** every ingredient on Today → Prep list now has a checkbox — tick
  items off as you prep them. Ticks are saved per day (and tidy themselves up after 2 weeks).
- **Terms & Conditions:** new clause on agreed delivery slots (best effort; occasional delays
  from traffic/weather/logistics) and a new **Delivery fee** section (free zone, ₹30–75 by
  distance, fixed once agreed). Check the wording once at `/terms`.
- **Invoices:** the totals now show *Food subscription / Delivery fee / Total / Amount paid /
  Balance due* separately, and every invoice carries a prominent **Terms & Conditions** box
  with a button to the full terms. The WhatsApp message includes the terms link too.
- **OG Agent everywhere:** the round green 🤖 button floats on every page — tap it to chat
  from wherever you are. It now answers from the live brochure + terms (a full FAQ), and it
  **learns**: tell it something new and confirm the 🧠 suggestion — the fact is remembered
  forever (see/teach/forget at the bottom of the OG Agent page). You confirm every action;
  it never changes anything on its own.
- **Digital brochure:** More → *Brochure*. Public shareable link (`/brochure`), editable any
  time (`/brochure/edit` — bullets, tables, bold), **⬇ PDF** to download, **💬 Send on
  WhatsApp** to share, and a one-tap **📘 Send brochure** button on every CRM lead.
  It seeded itself from the app's live pack prices — give it one read and tweak any wording
  you want.

---

## 9 August 2026 update — delivery schedules, extra meals, proper renewals

Same 2-step deploy (`git pull` + **Reload**). All additive: the new tables create themselves
on first load and every existing pack keeps its current behaviour — **nothing changes for
anyone until you change it yourself.** Verified against a copy of the live database: all 74
subscriptions came through the migration with identical counts and statuses.

- **Delivery schedule, per subscription** (subscription page → *📅 Weekly schedule*, and now
  on the create/edit form too). Three choices:
  - **Every kitchen day** — Mon–Fri, exactly as today. This is what every existing pack is set
    to, so nothing moves until you change it.
  - **Chosen weekdays** — tick only the days they eat. *Razia* on Mon/Wed/Fri, *Raja Sankar*
    on Mon–Thu. No order is generated on the other days, so their meals last longer instead of
    being burnt on days they don't eat.
  - **On demand** — nothing is planned automatically; you tick their days in the week planner.
    For the people who take 1–3 meals a week and confirm the day before.
- **Alternative boxes** (same card). Give a subscription a second, named box — "Protein Salad",
  "Veg Thursday" — with its own contents, then point individual weekdays at it.
  *Vaithi* is now: Mon Signature · Tue Salad · Wed Signature · **Thu veg (paneer + legumes)** ·
  Fri Salad, and the prep list, rider sheet and customer app all follow it on their own.
  Standing rules ("no yogurt, ever") still apply on top of whichever box the day picks.
- **Next 2 weeks** preview on the subscription page — check a schedule does what you meant
  before the kitchen finds out.
- **Week planner** (More → *Week planner*). Every on-demand subscriber as a Mon–Fri tick grid
  with meals-left shown. Tick a day → the order appears. Untick → it's gone. Delivered and
  skipped days are locked so re-saving a part-done week is safe.
- **Extra meals.** A customer taking a second box in a day — a different slot, or just one
  more — is now a real order row instead of a note. Add it from the ➕ on the day's order, or
  from *➕ Extra meal* on the subscription page. It comes out of the pack they already bought,
  so the count stays honest: **Raja's 30 July dinner box now reads 12/20 instead of 11/20.**
  There's a "sold separately" option for a meal that shouldn't use a pack meal.
  Automatic order-generation never creates or deletes an extra — only you do.
- **Renewals actually carry the pack over.** *🔄 Renew this pack* now opens a **new
  subscription form already filled in** — contents, add-ons, alternative boxes and the weekly
  schedule — for you to check, adjust anything different this month, and create. Previously a
  renewal copied only the subscription row, so the new pack arrived **with no contents at all**
  and the prep list couldn't see it until you re-entered everything by hand.
- **Regression tests:** `python test_schedule.py` (51 checks) covers all of the above,
  including that a delivered meal is never rewritten by a schedule change and that a renewal's
  boxes belong to the renewal. `test_customer_api.py` (51) and `test_invite_button.py` (27)
  still pass unchanged.

Nothing here touches validity/expiry — packs still never expire on their own.

---

## 10 August 2026 — Money on your phone

Adds a **Money** tab to More: your finance dashboard (revenue, expenses, profit,
business / personal / appa) served from the Command Center, so it works on mobile data
away from home instead of only on the laptop's wifi.

**This one needs an environment variable — without it the tab stays switched off.**

1. PythonAnywhere → **Web** tab → scroll to **Environment variables**
2. Add: name `OG_NOTION_TOKEN`, value = the same token that's in `.notion_token` on the
   laptop (open that file, copy the whole line)
3. **Reload**

Then More → **Money**. If you skip the variable nothing breaks — the tab explains what
to set and the laptop dashboard keeps working exactly as before.

### What to know

- **Admin only.** The team password (`OG_TEAM_PASSWORD`) that your editor friends use
  cannot open any part of it, and the Money link doesn't appear for them at all. Two
  independent checks enforce this, and `test_money.py` proves it every run.
- **Personal and appa's figures are on the server now.** That was a deliberate choice.
  If you ever want to pull back to business-only, it's a small change — say the word.
- **It reads the server's own `oggainz.db`** for the revenue half, so the numbers are
  the live ones, not your laptop's copy.
- **Nothing is written to disk on the server** — no `dashboard.html`, no xlsx, no
  `finance.json`. It's all built in memory per request.
- **Editing works from the phone**, exactly as on the laptop: same code, same safety
  rules (a category still can't be created without its COGS/OPEX/CAPEX/DRAW class, and
  deleting an option in use still refuses until you say where those rows should go).
- **The page is cached for 10 minutes** because a full Notion pull pages through three
  databases 100 rows at a time (~3 seconds). Hit **↻ Refresh** in the green bar for the
  current picture; any edit you make clears the cache on its own.

### Under the hood

`finance/serve_dashboard.py` went from 683 lines to 184: everything it can *do* moved to
`finance/actions.py`, which the laptop server and the Money tab both call. There is now
one copy of the category rules and the Notion writes, not two that drift. `Edit-Dashboard.bat`
is unchanged and works as before.

Tests: `python test_money.py` (60 checks — the gate, the off-switch, and the coupling
between the two front doors).
