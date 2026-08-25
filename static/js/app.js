// OG Gainz Command Center — minimal client JS (no external libraries).

// --- Toasts auto-dismiss ---
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.toast').forEach(t => {
    setTimeout(() => { t.style.transition = 'opacity .4s'; t.style.opacity = '0';
      setTimeout(() => t.remove(), 400); }, 2600);
  });
});

function showToast(msg, ok = true) {
  let wrap = document.querySelector('.toasts');
  if (!wrap) { wrap = document.createElement('div'); wrap.className = 'toasts'; document.body.appendChild(wrap); }
  const t = document.createElement('div');
  t.className = 'toast ' + (ok ? 'ok' : '');
  t.textContent = msg;
  wrap.appendChild(t);
  setTimeout(() => { t.style.transition = 'opacity .4s'; t.style.opacity = '0';
    setTimeout(() => t.remove(), 400); }, 2200);
}

// --- Mark a delivery (Delivered / Skipped / Pending toggle) ---
async function mark(btn, deliveryId, status) {
  const row = btn.closest('.drow');
  // toggle off if already in that status -> set Pending
  let target = status;
  if (row.dataset.status === status) target = 'Pending';
  // Tell the server which day this page is showing, so the live counters recalculate
  // that exact day (not the server's own "today", which may be a different date).
  const dayEl = btn.closest('[data-day]');
  const dayQs = dayEl && dayEl.dataset.day ? ('?d=' + encodeURIComponent(dayEl.dataset.day)) : '';
  try {
    const res = await fetch(`/api/delivery/${deliveryId}/mark` + dayQs, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: 'status=' + encodeURIComponent(target)
    });
    const data = await res.json();
    // A meal the customer skipped in the app is theirs to undo, not ours
    // (app.py api_mark). Say exactly that rather than "could not save", which
    // reads like a bug and invites a second tap.
    if (!data.ok && data.locked) { showToast(data.error, false); return; }
    if (!data.ok) throw new Error();
    applyRowStatus(row, target);
    updateCounters(data);
    if (target === 'Delivered') showToast('Marked delivered ✓');
    else if (target === 'Skipped') showToast('Marked skipped', false);
    else showToast('Reset to pending');
  } catch (e) {
    showToast('Could not save — try again', false);
  }
}

function applyRowStatus(row, status) {
  row.dataset.status = status;
  row.classList.toggle('is-delivered', status === 'Delivered');
  row.classList.toggle('is-skipped', status === 'Skipped');
  const d = row.querySelector('.iconbtn.deliver');
  const s = row.querySelector('.iconbtn.skip');
  if (d) d.classList.toggle('on', status === 'Delivered');
  if (s) s.classList.toggle('on', status === 'Skipped');
}

function updateCounters(d) {
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  set('cnt-done', d.done);
  set('cnt-pending', d.pending);
  set('cnt-skipped', d.skipped);
}

// --- Extra meals (another box, taken from the customer's own pack) ---
// The ➕ on an order opens a sheet rather than adding immediately. An extra is
// usually being RECORDED, not planned — a box that went out yesterday, in the
// other slot, already handed over — so the date, the slot and whether it has
// been delivered all have to be answerable. Guessing them from the row that was
// clicked is what puts a wrong number in the meal count.
function openExtra(btn) {
  const sheet = document.getElementById('extrasheet');
  if (!sheet) return;
  const sid = btn.dataset.sid;
  document.getElementById('extraform').action = '/subscriptions/' + sid + '/extra';
  document.getElementById('ex_for').textContent = '· ' + btn.dataset.name;
  // Seed from the row that was clicked, then let it all be changed.
  document.getElementById('ex_date').value = btn.dataset.date || '';
  document.getElementById('ex_slot').value = btn.dataset.slot || 'Lunch';
  document.getElementById('ex_note').value = '';
  const pending = document.querySelector('#extraform input[value=Pending]');
  if (pending) pending.checked = true;

  // Only offer a box picker to customers who actually have alternative boxes.
  const boxes = (window.OG_VARIANTS || {})[sid] || [];
  const wrap = document.getElementById('ex_boxwrap');
  const sel = document.getElementById('ex_box');
  sel.innerHTML = '<option value="">usual box</option>';
  boxes.forEach(v => {
    const o = document.createElement('option');
    o.value = v.id; o.textContent = v.name;
    sel.appendChild(o);
  });
  wrap.hidden = boxes.length === 0;

  sheet.hidden = false;
  document.getElementById('ex_date').focus();
}

function closeExtra() {
  const sheet = document.getElementById('extrasheet');
  if (sheet) sheet.hidden = true;
}

document.addEventListener('click', e => {
  if (e.target && e.target.id === 'extrasheet') closeExtra();
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeExtra();
});

// Removing one is a fetch: the row disappears and the page reloads behind it.
async function removeExtra(id) {
  if (!confirm('Remove this extra meal? The pack gets the serving back.')) return;
  try {
    const res = await fetch(`/api/delivery/${id}/delete`, { method: 'POST' });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || '');
    showToast('Extra meal removed');
    location.reload();
  } catch (e) {
    showToast('Could not remove it — try again', false);
  }
}

// --- Per-order note (one-off note for a single day's delivery) ---
function toggleNote(btn) {
  const row = btn.closest('.drow');
  const ed = row.querySelector('.note-editor');
  if (!ed) return;
  if (ed.hasAttribute('hidden')) {
    ed.removeAttribute('hidden');
    const t = ed.querySelector('.note-input');
    if (t) { t.focus(); t.setSelectionRange(t.value.length, t.value.length); }
  } else {
    ed.setAttribute('hidden', '');
  }
}

async function saveNote(btn) {
  const row = btn.closest('.drow');
  const id = row.dataset.id;
  const input = row.querySelector('.note-input');
  const text = input.value.trim();
  btn.disabled = true;
  try {
    const res = await fetch(`/api/delivery/${id}/note`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: 'note=' + encodeURIComponent(text)
    });
    const data = await res.json();
    if (!data.ok) throw new Error();
    const disp = row.querySelector('[data-note]');
    const span = disp.querySelector('.daynote-text');
    if (data.note) { span.textContent = data.note; disp.removeAttribute('hidden'); }
    else { span.textContent = ''; disp.setAttribute('hidden', ''); }
    const nb = row.querySelector('.iconbtn.note');
    if (nb) nb.classList.toggle('on', !!data.note);
    row.querySelector('.note-editor').setAttribute('hidden', '');
    showToast(data.note ? 'Note saved 📝' : 'Note cleared');
  } catch (e) {
    showToast('Could not save note — try again', false);
  } finally {
    btn.disabled = false;
  }
}

// --- Prep list: tick an ingredient off as "prepped" (kitchen self-check) ---
// A shut drawer shows "3/7 done", so the count has to follow the tick — otherwise
// closing the drawer reports a number that stopped being true when he ticked.
function prepCount(cb) {
  const drawer = cb.closest('.drawer');
  if (!drawer) return;
  const boxes = drawer.querySelectorAll('.ing-row input[type=checkbox]');
  const done = drawer.querySelectorAll('.ing-row input[type=checkbox]:checked').length;
  const amt = drawer.querySelector('.d-amt');
  if (amt) amt.textContent = done + '/' + boxes.length;
  drawer.classList.toggle('drawer-done', boxes.length > 0 && done === boxes.length);
}

async function prepTick(cb) {
  const row = cb.closest('.ing-row');
  const dayEl = cb.closest('[data-day]');
  const day = dayEl ? dayEl.dataset.day : '';
  const on = cb.checked;
  row.classList.toggle('ticked', on);
  prepCount(cb);
  try {
    const res = await fetch('/api/prep/tick', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: 'd=' + encodeURIComponent(day) +
            '&key=' + encodeURIComponent(cb.dataset.key) +
            '&on=' + (on ? '1' : '0')
    });
    const data = await res.json();
    if (!data.ok) throw new Error();
  } catch (e) {
    cb.checked = !on;                        // roll back the optimistic tick
    row.classList.toggle('ticked', cb.checked);
    prepCount(cb);
    showToast('Could not save the tick — try again', false);
  }
}

// --- Backlog: clear an old order that was never marked (Today → Unmarked past orders) ---
async function markPast(btn, deliveryId, status) {
  const row = btn.closest('.bl-row');
  btn.disabled = true;
  try {
    const res = await fetch(`/api/delivery/${deliveryId}/mark`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: 'status=' + encodeURIComponent(status)
    });
    const data = await res.json();
    if (!data.ok && data.locked) { btn.disabled = false; showToast(data.error, false); return; }
    if (!data.ok) throw new Error();
    row.style.transition = 'opacity .3s'; row.style.opacity = '.35';
    row.querySelectorAll('button').forEach(b => b.disabled = true);
    row.classList.add(status === 'Delivered' ? 'is-delivered' : 'is-skipped');
    const badge = document.getElementById('backlog-count');
    if (badge) badge.textContent = Math.max(0, parseInt(badge.textContent || '1') - 1);
    showToast(status === 'Delivered' ? 'Marked delivered ✓' : 'Marked skipped — carries forward');
  } catch (e) {
    btn.disabled = false;
    showToast('Could not save — try again', false);
  }
}

// --- Trials: one-tap Paid / Delivered toggles (progressive enhancement) ---
// Each toggle is a real <form> so it still works with JS off; here we intercept
// it, POST in the background and flip the button in place — no page reload.
document.addEventListener('submit', async (ev) => {
  const form = ev.target.closest('.qtoggle');
  if (!form) return;
  ev.preventDefault();
  const btn = form.querySelector('button');
  const kind = form.dataset.kind;
  btn.disabled = true;
  try {
    const res = await fetch(form.action, {
      method: 'POST',
      headers: { 'X-Requested-With': 'fetch' }
    });
    const data = await res.json();
    if (!data.ok) throw new Error();
    if (kind === 'pay') {
      const paid = data.payment_status === 'Paid';
      btn.classList.toggle('on-paid', paid);
      btn.textContent = paid ? '💰 Paid' : 'Mark paid';
      showToast(paid ? 'Marked paid 💰' : 'Marked unpaid');
    } else {
      const done = data.delivery_status === 'Delivered';
      btn.classList.toggle('on-deliver', done);
      btn.textContent = done ? '✓ Delivered' : 'Mark delivered';
      showToast(done ? 'Marked delivered ✓' : 'Reset to pending');
    }
  } catch (e) {
    showToast('Could not save — try again', false);
  } finally {
    btn.disabled = false;
  }
});

/* ---------------------------------------------------------------------------
 * "Use my location" on every map-pin box.
 *
 * The Google Maps app on a phone gives you no way to copy a latitude and
 * longitude -- long-pressing shows them on desktop, but on Android the share
 * sheet only offers a maps.app.goo.gl link, which carries no coordinates at all.
 * The phone itself knows exactly where it is, so when you are standing at the
 * customer's door this is both the easiest and the most accurate way to pin it.
 *
 * Attaches to any input named "pin" and to the pins-page rows, so a new form
 * with a pin field gets the button without anyone remembering to add it.
 * Requires HTTPS (the live site) -- on plain http the browser blocks the API,
 * so we say so rather than showing a button that silently never works.
 * ------------------------------------------------------------------------- */
(function () {
  var boxes = document.querySelectorAll('input[name="pin"], input.pin-in');
  if (!boxes.length) return;

  boxes.forEach(function (inp) {
    var note = document.createElement('div');
    note.className = 'muted';
    note.style.cssText = 'font-size:.85em;margin-top:4px;flex-basis:100%';

    if (!navigator.geolocation || !window.isSecureContext) {
      // Don't offer it where it cannot work (http:// on the laptop).
      note.textContent = 'Tip: paste a plus code, a Maps link, or just the place name.';
      inp.insertAdjacentElement('afterend', note);
      return;
    }

    var btn = document.createElement('button');
    btn.type = 'button';                    // never submit the form it sits in
    btn.className = 'btn btn-ghost btn-sm';
    btn.textContent = '⊙ Use my location';
    btn.style.cssText = 'margin-top:6px;flex:0 0 auto';

    btn.addEventListener('click', function () {
      btn.disabled = true;
      btn.textContent = 'Locating…';
      note.style.color = '';
      note.textContent = 'Asking the phone for a GPS fix — this can take a few seconds.';
      navigator.geolocation.getCurrentPosition(
        function (pos) {
          var la = pos.coords.latitude, ln = pos.coords.longitude;
          var acc = Math.round(pos.coords.accuracy || 0);
          inp.value = la.toFixed(6) + ', ' + ln.toFixed(6);
          btn.disabled = false;
          btn.textContent = '⊙ Use my location';
          // Accuracy is shown, not hidden: a 500 m fix means the phone used the
          // mobile tower, not GPS, and that pin would put the zone and the
          // petrol allowance wrong every single day it is used.
          if (acc > 100) {
            note.style.color = '#c0392b';
            note.textContent = 'Rough fix, accurate to about ' + acc + ' m — step outside '
                             + 'and tap again before saving.';
          } else {
            note.textContent = 'Located to about ' + acc + ' m. Save to keep it.';
          }
        },
        function (err) {
          btn.disabled = false;
          btn.textContent = '⊙ Use my location';
          note.style.color = '#c0392b';
          note.textContent =
            err.code === 1 ? 'Location permission was refused — allow it for this site in the browser settings.'
          : err.code === 3 ? 'The phone took too long to find a fix. Try again outdoors.'
          :                  'Could not get a location from the phone.';
        },
        { enableHighAccuracy: true, timeout: 20000, maximumAge: 0 }
      );
    });

    inp.insertAdjacentElement('afterend', btn);
    btn.insertAdjacentElement('afterend', note);
  });
})();

// --- CRM: send a written message and record it in the same tap ---------------
// The form posts normally (so the lead page reloads showing the new timeline
// entry, the moved stage and the next follow-up date), while WhatsApp opens
// alongside it in a new tab. Two separate things had to happen for a chase to be
// both sent and recorded, and asking a busy person to do the second one by hand
// is how a CRM ends up full of stale stages.
//
// The URL lives in a data attribute rather than inline JS: the message body is
// his own text and will eventually contain quotes, and a broken onsubmit would
// fail silently at exactly the wrong moment.
document.addEventListener('submit', (ev) => {
  const form = ev.target.closest('form.sendwa');
  if (!form) return;
  const url = form.dataset.wa;
  if (url) window.open(url, '_blank', 'noopener');
});
