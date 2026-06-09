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
