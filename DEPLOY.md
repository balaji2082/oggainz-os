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
