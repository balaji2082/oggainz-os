# Put OG Gainz Command Center on your phone (anywhere, no laptop)

This puts your app on a free always-on server (**PythonAnywhere**) so you can open it from
your phone on mobile data, with the laptop off. Follow it top to bottom — I'll be with you
for every step. You only type/click what's shown in **bold** or in `code boxes`.

**What you'll end up with:** a private web address like `https://oggainz.pythonanywhere.com`
that asks for a password, then shows your Command Center — added to your phone home screen
like a normal app.

> You do this **once**. After that, any change I make reaches your phone with one command.

---

## Before we start — the 2 free accounts

1. **GitHub** — a private online folder for the app *code only* (never your customer data).
2. **PythonAnywhere** — the always-on computer that runs the app.

Neither needs a credit card. I'll tell you exactly when to make each.

---

## Step 1 — GitHub (I do most of this)

1. Go to **https://github.com/signup** and make an account (use your email).
2. Tell me your GitHub **username**. I'll create a **private** repository called
   `og-gainz-os` and push the app code into it from your laptop.
3. When I'm done I'll give you the repo's web address (looks like
   `https://github.com/YOURNAME/og-gainz-os`).

✅ Your `Finance/`, business, and personal files are **not** in this repo — only the app.
✅ Your customer database is **not** uploaded here either (we move it separately in Step 6).

---

## Step 2 — Make your PythonAnywhere account

1. Go to **https://www.pythonanywhere.com/registration/register/beginner/**
2. Sign up (free "Beginner" plan, no card). Pick a username — this becomes part of your web
   address, so pick something clean like **`oggainz`** → `oggainz.pythonanywhere.com`.
3. Confirm your email and log in. You'll land on the **Dashboard**.

---

## Step 3 — Download the app onto the server

1. On the top menu click **Consoles** → under "Start a new console" click **Bash**.
   (A black command window opens — this is normal.)
2. Type this line and press **Enter** (replace `YOURNAME` with your GitHub username):

   ```
   git clone https://github.com/YOURNAME/og-gainz-os.git
   ```

   Because the repo is private, it will ask for your GitHub **username** and a **password**.
   GitHub no longer accepts your normal password here — it needs a **"token"**. I'll walk you
   through making one (GitHub → Settings → Developer settings → Personal access tokens →
   *Generate new token (classic)* → tick **repo** → copy it). Paste the token when it asks for
   the password. *(Nothing shows while you paste — that's normal. Press Enter.)*

3. You now have a folder called `og-gainz-os`. ✅

---

## Step 4 — Install the two libraries the app needs

In the same Bash window, run these two lines (one at a time, Enter after each):

```
mkvirtualenv ogenv --python=/usr/bin/python3.10
pip install -r og-gainz-os/requirements.txt
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
   then the tick to save.

   **(b) WSGI file** — find "WSGI configuration file" and click the blue link
   (looks like `/var/www/oggainz_pythonanywhere_com_wsgi.py`). It opens an editor.
   **Delete everything** in it and paste the block below.

   ```python
   import os
   import sys

   # ---- OG Gainz settings ----
   # Change ONLY the password below to whatever you want to type on your phone.
   os.environ["OG_PASSWORD"]   = "CHANGE-ME-pick-a-password"
   os.environ["OG_SECRET_KEY"] = "ee853b58130af877e2141a140f134d0fa4f394ff32ea437277b9fc09bee03ea7"

   # ---- make the app importable (replace YOURUSER with your PythonAnywhere username) ----
   project_home = "/home/YOURUSER/og-gainz-os"
   if project_home not in sys.path:
       sys.path.insert(0, project_home)

   from app import app as application   # this runs your Command Center
   ```

   - Change `CHANGE-ME-pick-a-password` to your real password (keep the quotes).
   - Change `YOURUSER` to your PythonAnywhere username (e.g. `oggainz`).
   - Leave the long `OG_SECRET_KEY` exactly as-is.
   - Click the green **Save** button (top right of the editor).

---

## Step 6 — Move your real data onto the server

Your customers/subscriptions live in a file called **`oggainz.db`** on your laptop. We copy it up.

1. Top menu → **Files**.
2. Open the **`og-gainz-os`** folder.
3. Click **Upload a file** and choose `oggainz.db` from
   `…\Desktop\AI-Workspace\OG-Gainz-OS\oggainz.db` on your laptop.
   *(I'll point you to the exact file. If it asks to overwrite, say yes.)*

✅ All your real data is now on the server.

---

## Step 7 — Go live & add to your phone

1. Back to **Web** → click the big green **Reload** button.
2. Open your address on your phone: **`https://YOURUSER.pythonanywhere.com`**
   → you should see the **password screen** → type your password → you're in. 🎉
3. **Add to home screen** so it feels like a real app:
   - **iPhone (Safari):** Share button → *Add to Home Screen*.
   - **Android (Chrome):** ⋮ menu → *Add to Home screen* / *Install app*.

**Final test:** turn **WiFi off** on your phone (so you're on mobile data), open the app, mark
a delivery, refresh — the change sticks. That proves it works anywhere and is saving correctly.

---

## Updating the app later (the easy part)

When you want a change, tell me. I edit and push it to GitHub. Then you:

1. **Consoles** → open a **Bash** console.
2. Run:
   ```
   cd og-gainz-os && git pull
   ```
3. **Web** → **Reload**. Done — the change is live on your phone.

---

## If something looks wrong

- **"Something went wrong" / error page:** Web tab → scroll to **Error log** → click it →
  copy the last few red lines and send them to me. (Usually a small typo in the WSGI file —
  e.g. `YOURUSER` not replaced, or a missing quote.)
- **Password not asked / wrong password:** re-open the WSGI file, check `OG_PASSWORD`, Save,
  Reload.
- **Page won't load at all:** make sure you clicked the green **Reload** after every change.

> Free-tier note: PythonAnywhere emails you every ~3 months to click one link to keep the
> account active. The first load after a quiet spell can take a few seconds. Both are normal.
