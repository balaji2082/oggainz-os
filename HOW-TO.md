# OG Gainz Command Center — How to Use

Your whole business — customers, subscriptions, daily meals, trials and revenue —
in one simple app that runs on **your PC** and your **phone**. No more copying meal
rows in Excel by hand.

> **Eat Like It Matters.**

---

## ▶ Starting the app

1. Open the **`OG-Gainz-OS`** folder.
2. Double-click **`Start-OG-Gainz.bat`**.
3. A black window opens and your browser opens the app automatically.
   **Keep the black window open** while you use the app. Close it to stop the app.

**On your phone** (same WiFi as the PC): the black window shows a line like
`On phone: http://192.168.x.x:5000`. Type that address in your phone's browser.
Tip: on the phone, use "Add to Home Screen" to get an app icon.

---

## 📅 Today (home screen) — your daily routine

This is where you live every day.

- It shows **every meal to deliver today**, auto-built from your active subscriptions
  (name, plan, slot, timing, locality, and any customisation notes).
- Tap the green **✓** when a meal is delivered, or the orange **✕** if it's skipped.
  Tapping again undoes it. Everything updates instantly — counts, the customer's
  remaining meals, and all dashboards.
- **Cook list** at the top tells you how many of each plan to prep.
- Alert strips warn you about **subscriptions ending soon** and **unpaid** ones.
- Tap **💬** to open WhatsApp with that customer.

Skipped meals are **carried forward** automatically — the subscription just needs
that many more delivery days, exactly like before.

---

## 📦 Subscriptions

- See everyone, filter by **Active / Ending Soon / Completed**, or search.
- Each shows a progress bar: delivered / total, and how many meals are left.
- Open one to: **edit**, **mark paid**, **pause/resume**, **mark completed**, or **renew**.
- **Renew** creates the next subscription for that customer in one tap.
- **+ New** adds a subscription (pick the customer, plan, price, etc.). Monthly = 20
  meals, Weekly = 5 by default — you can change it.

Status is automatic: when the last meal is delivered it becomes **Completed**; when
only a couple of meals are left it becomes **⚠ Ending Soon** (you can change that
number in Settings).

---

## 👥 Customers

Your full database — name, phone, locality, address, notes. Tap **💬 WhatsApp** to
message them, see their subscription history and lifetime spend. **+ New** to add one.

---

## 🧪 Trials & 💰 Revenue

- **Trials** (under *More*): every trial pack, amount, and whether they converted.
- **Revenue**: monthly sales, growth %, sales by plan, money received vs pending —
  the same numbers your old Finance and Monthly sheets showed, calculated for you.

---

## 💾 Your data is safe

- Everything is saved in **one file** (`oggainz.db`) inside OneDrive, so it's backed
  up to the cloud automatically.
- A timestamped backup is made **every time you start the app** (kept in `backups/`).
- In **Settings** you can: **Backup now**, **Export to Excel** (a familiar workbook you
  can open anytime), or **Re-import** from your `NEW TRACKER.xlsx` (a backup is taken first).

**Never lose data:** if anything ever looks wrong, your backups and Excel exports are
right there in the folder.

---

## 🆘 Quick fixes

- **Browser didn't open?** Type `http://localhost:5000` in your browser.
- **"Python is not installed"?** Install Python 3 from python.org and tick
  *"Add python.exe to PATH"*, then run the `.bat` again.
- **Phone can't connect?** Make sure the phone and PC are on the **same WiFi**, and the
  black window is still open on the PC.

---

*Built for OG Gainz · your data stays on your machine.*
