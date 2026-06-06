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
  try {
    const res = await fetch(`/api/delivery/${deliveryId}/mark`, {
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
