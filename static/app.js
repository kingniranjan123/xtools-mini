// ─────────────────────────────────────────────────────────────────
//  Nikethan Reels Toolkit — Client-side JS
// ─────────────────────────────────────────────────────────────────

/* ── Toast system ───────────────────────────────────────────── */
function showToast(msg, type = 'info', duration = 4000) {
  const icons = { success: '✅', error: '❌', warn: '⚠️', info: 'ℹ️' };
  const colors = {
    success: '#00d4a8',
    error:   '#ff4545',
    warn:    '#ffaa00',
    info:    '#4e9eff'
  };
  const container = document.getElementById('toast-container');
  if (!container) return;

  const t = document.createElement('div');
  t.className = 'toast';
  t.style.borderLeft = `3px solid ${colors[type] || colors.info}`;
  t.innerHTML = `<span>${icons[type] || icons.info}</span><span>${msg}</span>`;
  container.appendChild(t);

  setTimeout(() => {
    t.style.animation = 'toastOut 0.25s ease forwards';
    setTimeout(() => t.remove(), 250);
  }, duration);
}

/* ── SSE Job Progress ───────────────────────────────────────── */
function startSSEProgress({ url, logId, barId, statusId, onComplete }) {
  const log    = document.getElementById(logId);
  const bar    = document.getElementById(barId);
  const status = document.getElementById(statusId);
  const evtSrc = new EventSource(url);

  evtSrc.addEventListener('progress', e => {
    const d = JSON.parse(e.data);
    if (log) log.textContent += d.line + '\n';
    if (bar && d.pct !== undefined) bar.style.width = d.pct + '%';
    if (status && d.status) status.textContent = d.status;
  });

  evtSrc.addEventListener('done', e => {
    evtSrc.close();
    if (bar) { bar.style.width = '100%'; bar.classList.remove('animated'); }
    if (status) status.textContent = 'Complete';
    const d = JSON.parse(e.data);
    showToast(d.message || 'Job complete!', 'success');
    if (onComplete) onComplete(d);
  });

  evtSrc.addEventListener('error', () => {
    evtSrc.close();
    showToast('Connection lost — job may still be running', 'warn');
  });

  return evtSrc;
}

/* ── Drop zone setup ────────────────────────────────────────── */
function initDropZone(zoneId, fileInputId, labelId, onFile) {
  const zone  = document.getElementById(zoneId);
  const input = document.getElementById(fileInputId);
  const label = document.getElementById(labelId);
  if (!zone || !input) return;

  zone.addEventListener('click', () => input.click());

  zone.addEventListener('dragover', e => {
    e.preventDefault();
    zone.classList.add('drag-over');
  });

  zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));

  zone.addEventListener('drop', e => {
    e.preventDefault();
    zone.classList.remove('drag-over');
    const file = e.dataTransfer.files[0];
    if (file) {
      if (label) label.textContent = file.name;
      if (onFile) onFile(file);
    }
  });

  input.addEventListener('change', () => {
    const file = input.files[0];
    if (file) {
      if (label) label.textContent = file.name;
      if (onFile) onFile(file);
    }
  });
}

/* ── Clip rows (trailer page) ───────────────────────────────── */
let clipCount = 1;

function addClipRow() {
  clipCount++;
  const table = document.getElementById('clip-tbody');
  if (!table) return;
  const row = document.createElement('tr');
  row.className = 'clip-tr';
  row.dataset.id = clipCount;
  row.innerHTML = `
    <td style="width:32px; color:var(--text-dim); cursor:grab; text-align:center">⣿</td>
    <td><input class="form-input mono form-input-sm" type="text" placeholder="00:00:00" name="from_${clipCount}" id="from_${clipCount}" /></td>
    <td><input class="form-input mono form-input-sm" type="text" placeholder="00:00:30" name="to_${clipCount}" id="to_${clipCount}" /></td>
    <td><input class="form-input form-input-sm" type="text" placeholder="Optional label" name="label_${clipCount}" /></td>
    <td style="text-align:center">
      <button type="button" class="btn btn-ghost btn-sm" onclick="removeClipRow(this)" title="Remove">✕</button>
    </td>
  `;
  table.appendChild(row);
}

function removeClipRow(btn) {
  const row = btn.closest('tr');
  if (row) row.remove();
}

/* ── Watermark position picker ──────────────────────────────── */
function selectPosition(btn, value) {
  document.querySelectorAll('.pos-btn').forEach(b => b.classList.remove('selected'));
  btn.classList.add('selected');
  const inp = document.getElementById('wm-position');
  if (inp) inp.value = value;
}

/* ── CUDA toggle ────────────────────────────────────────────── */
function toggleCudaHint(enabled) {
  const hint = document.getElementById('cuda-hint');
  if (!hint) return;
  hint.textContent = enabled
    ? '🚀 CUDA (h264_nvenc) will be used for hardware-accelerated encoding.'
    : '🖥️ CPU mode (libx264) — slower but always available.';
}

/* ── Bulk URL counter ───────────────────────────────────────── */
function countURLs(textarea, counterId) {
  const counter = document.getElementById(counterId);
  if (!counter || !textarea) return;
  const lines = textarea.value.split('\n').filter(l => l.trim().startsWith('http'));
  counter.textContent = lines.length + ' URL' + (lines.length !== 1 ? 's' : '') + ' detected';
}

/* ── Seconds preview ────────────────────────────────────────── */
function updateSplitPreview(durationSec, nSec, previewId) {
  const el = document.getElementById(previewId);
  if (!el || !durationSec || !nSec) return;
  const parts = Math.ceil(durationSec / nSec);
  el.textContent = `→ ${parts} segment${parts !== 1 ? 's' : ''} of ~${nSec}s each`;
}

/* ── Generic fetch POST helper ──────────────────────────────── */
async function postJSON(url, data) {
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  });
  return resp.json();
}

/* ── DOM ready shim ─────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  // Highlight active nav item
  const path = window.location.pathname;
  document.querySelectorAll('.nav-item').forEach(a => {
    if (a.getAttribute('href') && path.startsWith(a.getAttribute('href')) && a.getAttribute('href') !== '/') {
      a.classList.add('active');
    }
  });
});
