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
async function prepTick(cb) {
  const row = cb.closest('.ing-row');
  const dayEl = cb.closest('[data-day]');
  const day = dayEl ? dayEl.dataset.day : '';
  const on = cb.checked;
  row.classList.toggle('ticked', on);
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
