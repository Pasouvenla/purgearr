// Purgearr — app.js

// ── Mobile nav ────────────────────────────────────────────────────────────────

function toggleMobileNav() {
  const menu = document.getElementById('mobile-nav');
  const hamburger = document.getElementById('hamburger-icon');
  const closeIco = document.getElementById('close-icon');
  if (!menu) return;
  const isHidden = menu.classList.toggle('hidden');
  if (hamburger) hamburger.classList.toggle('hidden', !isHidden);
  if (closeIco) closeIco.classList.toggle('hidden', isHidden);
}

function closeMobileNav() {
  const menu = document.getElementById('mobile-nav');
  const hamburger = document.getElementById('hamburger-icon');
  const closeIco = document.getElementById('close-icon');
  if (menu) menu.classList.add('hidden');
  if (hamburger) hamburger.classList.remove('hidden');
  if (closeIco) closeIco.classList.add('hidden');
}

// ── Library filters sidebar (mobile drawer) ───────────────────────────────────

function toggleFiltersSidebar() {
  const sidebar = document.getElementById('filters-sidebar');
  const overlay = document.getElementById('sidebar-overlay');
  if (!sidebar) return;
  const isOpen = sidebar.classList.contains('translate-x-0');
  if (isOpen) {
    sidebar.classList.remove('translate-x-0');
    sidebar.classList.add('-translate-x-full');
    if (overlay) overlay.classList.add('hidden');
  } else {
    sidebar.classList.add('translate-x-0');
    sidebar.classList.remove('-translate-x-full');
    if (overlay) overlay.classList.remove('hidden');
  }
}

function closeFiltersSidebar() {
  const sidebar = document.getElementById('filters-sidebar');
  const overlay = document.getElementById('sidebar-overlay');
  if (sidebar) {
    sidebar.classList.remove('translate-x-0');
    sidebar.classList.add('-translate-x-full');
  }
  if (overlay) overlay.classList.add('hidden');
}

// ── Saved filters ─────────────────────────────────────────────────────────────

function loadSavedFilter(queryString) {
  if (!queryString) return;
  window.location.href = '/library?' + queryString;
}

function toggleSaveFilterForm() {
  const form = document.getElementById('save-filter-form');
  if (!form) return;
  form.classList.toggle('hidden');
  if (!form.classList.contains('hidden')) {
    document.getElementById('save-filter-name')?.focus();
  }
}

function saveCurrentFilter() {
  const nameEl = document.getElementById('save-filter-name');
  const name = nameEl ? nameEl.value.trim() : '';
  if (!name) { showToast('Enter a preset name.', true); return; }
  const params = new URLSearchParams(window.location.search);
  params.delete('page');
  fetch('/library/filters/save', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, query_string: params.toString() }),
  })
    .then(r => r.json())
    .then(() => { showToast(`Preset "${name}" saved.`); location.reload(); })
    .catch(() => showToast('Save failed.', true));
}

function deleteSavedFilter() {
  const sel = document.getElementById('saved-filter-select');
  if (!sel || !sel.value) return;
  const name = sel.options[sel.selectedIndex]?.text;
  if (!name || !confirm(`Delete preset "${name}"?`)) return;
  fetch(`/library/filters/${encodeURIComponent(name)}`, { method: 'DELETE' })
    .then(r => r.json())
    .then(() => { showToast(`Preset "${name}" deleted.`); location.reload(); })
    .catch(() => showToast('Delete failed.', true));
}

// ── XSS helper ───────────────────────────────────────────────────────────────

function _esc(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ── Selection state ───────────────────────────────────────────────────────────

const selected = new Set();

function toggleSelect(id) {
  const cb = document.getElementById(`cb-${id}`);
  const wrap = document.getElementById(`cb-wrap-${id}`);
  if (selected.has(id)) {
    selected.delete(id);
    cb.checked = false;
    wrap.classList.remove('checked');
  } else {
    selected.add(id);
    cb.checked = true;
    wrap.classList.add('checked');
  }
  updateBulkBar();
}

function selectAll() {
  document.querySelectorAll('[id^="cb-"]').forEach(cb => {
    if (!cb.type) return;
    const id = parseInt(cb.id.replace('cb-', ''));
    if (!isNaN(id)) {
      selected.add(id);
      cb.checked = true;
      const wrap = document.getElementById(`cb-wrap-${id}`);
      if (wrap) wrap.classList.add('checked');
    }
  });
  updateBulkBar();
}

function clearSelection() {
  selected.forEach(id => {
    const cb = document.getElementById(`cb-${id}`);
    if (cb) cb.checked = false;
    const wrap = document.getElementById(`cb-wrap-${id}`);
    if (wrap) wrap.classList.remove('checked');
  });
  selected.clear();
  updateBulkBar();
}

function updateBulkBar() {
  const bar = document.getElementById('bulk-bar');
  const countEl = document.getElementById('bulk-count');
  if (!bar) return;
  if (selected.size > 0) {
    bar.classList.remove('translate-y-full', 'opacity-0');
    bar.classList.add('translate-y-0', 'opacity-100');
  } else {
    bar.classList.add('translate-y-full', 'opacity-0');
    bar.classList.remove('translate-y-0', 'opacity-100');
  }
  if (countEl) countEl.textContent = `${selected.size} selected`;
}

// ── Detail panel ──────────────────────────────────────────────────────────────

function openDetail(id, event) {
  if (event && event.target.type === 'checkbox') return;

  const panel = document.getElementById('detail-panel');
  const overlay = document.getElementById('detail-overlay');
  const content = document.getElementById('detail-content');

  if (!panel || !content) return;

  content.innerHTML = '<div class="p-8 text-center text-slate-400">Loading…</div>';
  panel.classList.remove('translate-x-full');
  if (overlay) overlay.classList.remove('hidden');

  fetch(`/library/item/${id}`)
    .then(r => r.text())
    .then(html => { content.innerHTML = html; })
    .catch(() => {
      content.innerHTML = '<div class="p-8 text-red-400">Failed to load item details.</div>';
    });
}

function closeDetail() {
  const panel = document.getElementById('detail-panel');
  const overlay = document.getElementById('detail-overlay');
  if (panel) panel.classList.add('translate-x-full');
  if (overlay) overlay.classList.add('hidden');
}

// ── Delete modal ──────────────────────────────────────────────────────────────

let _deleteSingleId = null;

function openDeleteModal() {
  _deleteSingleId = null;
  const modal = document.getElementById('delete-modal');
  const desc = document.getElementById('delete-modal-desc');
  if (!modal) return;
  if (desc) desc.textContent = `This will remove ${selected.size} selected item(s).`;
  _resetDeleteSteps();
  modal.classList.remove('hidden');
  modal.classList.add('flex');
}

function openDeleteModalSingle(id, title) {
  _deleteSingleId = id;
  const modal = document.getElementById('delete-modal');
  const desc = document.getElementById('delete-modal-desc');
  if (!modal) return;
  if (desc) desc.textContent = `This will remove "${title}".`;
  _resetDeleteSteps();
  modal.classList.remove('hidden');
  modal.classList.add('flex');
}

function closeDeleteModal() {
  const modal = document.getElementById('delete-modal');
  if (modal) {
    modal.classList.add('hidden');
    modal.classList.remove('flex');
  }
  _deleteSingleId = null;
}

function _resetDeleteSteps() {
  backToOptions();
  _showReviewAgain();
  const result = document.getElementById('delete-result');
  if (result) { result.innerHTML = ''; result.classList.add('hidden'); }
}

function _hideReviewForResult() {
  document.getElementById('delete-review-list')?.classList.add('hidden');
  document.getElementById('delete-review-counts')?.classList.add('hidden');
  document.getElementById('delete-confirm-btn')?.classList.add('hidden');
  document.getElementById('delete-review-footnote')?.classList.add('hidden');
}

function _showReviewAgain() {
  document.getElementById('delete-review-list')?.classList.remove('hidden');
  document.getElementById('delete-review-counts')?.classList.remove('hidden');
  document.getElementById('delete-confirm-btn')?.classList.remove('hidden');
  document.getElementById('delete-review-footnote')?.classList.remove('hidden');
}

function backToOptions() {
  document.getElementById('delete-step-options')?.classList.remove('hidden');
  document.getElementById('delete-step-review')?.classList.add('hidden');
}

function _deleteOptions() {
  return {
    delete_from_arr: document.getElementById('opt-arr')?.checked ?? true,
    delete_torrent: document.getElementById('opt-torrent')?.checked ?? false,
    delete_files: document.getElementById('opt-files')?.checked ?? false,
    delete_seerr_request: document.getElementById('opt-seerr')?.checked ?? false,
    delete_from_media_server: document.getElementById('opt-media-server')?.checked ?? false,
  };
}

function _humanSize(bytes) {
  if (!bytes) return '';
  const gb = bytes / 1073741824;
  return gb >= 1 ? `${gb.toFixed(1)} GB` : `${Math.round(bytes / 1048576)} MB`;
}

// Step 1 → 2. Asks the server what it would do, with nothing deleted, and
// lays the answer out item by item. The purge that cost us was a bulk one:
// its warnings only showed up once the items were already gone.
function goToReview() {
  const ids = _deleteSingleId ? [_deleteSingleId] : Array.from(selected);
  if (ids.length === 0) return;

  const btn = document.querySelector('#delete-step-options button');
  if (btn) { btn.disabled = true; btn.textContent = 'Checking…'; }

  fetch('/actions/delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ item_ids: ids, dry_run: true, ..._deleteOptions() }),
  })
    .then(r => r.json())
    .then(data => _renderReview(data))
    .catch(err => {
      const result = document.getElementById('delete-result');
      if (result) {
        result.classList.remove('hidden');
        const div = document.createElement('div');
        div.className = 'px-3 py-2 rounded bg-red-900/40 border border-red-700/40 text-red-300 text-sm';
        div.textContent = `Preview failed: ${err.message}`;
        result.appendChild(div);
      }
    })
    .finally(() => { if (btn) { btn.disabled = false; btn.textContent = 'Review'; } });
}

// Each group gets ONE literal class string: the Tailwind CDN builds what it
// sees, so a class name assembled by concatenation is a coin toss.
const REVIEW_GROUPS = {
  warned: {
    label: 'Check these first',
    heading: 'text-[11px] font-semibold uppercase tracking-wider text-amber-400',
    button: 'border border-amber-400/50 bg-amber-400/10',
    figure: 'text-base font-bold text-amber-400',
  },
  clean: {
    label: 'No reservations',
    heading: 'text-[11px] font-semibold uppercase tracking-wider text-emerald-400',
    button: 'border border-emerald-400/40 bg-emerald-400/10',
    figure: 'text-base font-bold text-emerald-400',
  },
  all: {
    label: 'Tick all',
    heading: '',
    button: 'border border-slate-500/45 bg-transparent',
    figure: 'text-base font-bold text-white',
  },
};

function _reviewCountButton(which, n, caption) {
  const g = REVIEW_GROUPS[which];
  const off = n === 0 ? ' opacity-40 cursor-not-allowed' : '';
  return `
    <button type="button" onclick="tickReview('${which}')" ${n === 0 ? 'disabled' : ''}
            class="rounded-md px-1.5 py-2 text-center transition ${g.button}${off}">
      <span class="block ${g.figure}">${n}</span>
      <span class="block text-[10px] text-slate-400 mt-0.5">${caption}</span>
    </button>`;
}

function _reviewRow(p) {
  const warnings = p.warnings || [];
  // Anything carrying a warning starts unticked: it has to be looked at
  // before it goes, which is exactly what nobody could do before.
  const checked = warnings.length === 0 ? 'checked' : '';
  const edge = warnings.length ? ' border-l-[3px] border-amber-400' : '';
  const lines = (p.actions || []).map(a => _esc(a)).join('<br>');
  const warns = warnings.map(w =>
    `<span class="flex items-start gap-1.5 text-xs text-amber-400 leading-relaxed mt-1">
       <svg class="w-3.5 h-3.5 shrink-0 mt-0.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v4m0 4h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/></svg>
       <span>${_esc(w)}</span>
     </span>`).join('');
  return `
    <div class="bg-[#16213e] rounded-lg px-3 py-2.5${edge}">
      <label for="rev-${p.id}" class="flex items-start gap-2.5 cursor-pointer">
        <input type="checkbox" id="rev-${p.id}" data-review-id="${p.id}"
               data-review-group="${warnings.length ? 'warned' : 'clean'}" ${checked}
               onchange="_updateReviewCount()" class="w-4 h-4 mt-0.5 shrink-0 accent-[#e5a00d]" />
        <span class="flex-1 min-w-0">
          <span class="flex items-baseline justify-between gap-2">
            <span class="text-[13px] font-semibold text-white">${_esc(p.title)}</span>
            <span class="text-[11px] text-slate-400 shrink-0">${_humanSize(p.would_free_bytes)}</span>
          </span>
          ${lines ? `<span class="block text-xs text-slate-400 leading-relaxed mt-0.5">${lines}</span>` : ''}
          ${warns}
        </span>
      </label>
    </div>`;
}

function tickReview(which) {
  document.querySelectorAll('#delete-review-list input[data-review-id]').forEach(cb => {
    if (which === 'all') cb.checked = true;
    else cb.checked = cb.dataset.reviewGroup === which;
  });
  _updateReviewCount();
}

let _reviewPlans = [];

function _renderReview(data) {
  const plans = Array.isArray(data) ? data : (data.plans || []);
  _reviewPlans = plans;
  const notes = Array.isArray(data) ? [] : (data.notes || []);
  const list = document.getElementById('delete-review-list');
  if (!list) return;

  const warned = plans.filter(p => (p.warnings || []).length);
  const clean = plans.filter(p => !(p.warnings || []).length);

  const counts = document.getElementById('delete-review-counts');
  if (counts) {
    counts.innerHTML =
      _reviewCountButton('clean', clean.length, 'no reservations') +
      _reviewCountButton('warned', warned.length, 'to check') +
      _reviewCountButton('all', plans.length, 'tick all');
  }

  // A note that holds for the whole batch is said ONCE here. Repeating the same
  // media-server refusal on eight rows is how a readable result became a wall.
  const noteBox = document.getElementById('delete-review-notes');
  if (noteBox) {
    noteBox.innerHTML = notes.map(n =>
      `<p class="flex items-start gap-1.5 text-[11px] text-amber-400 leading-relaxed">
         <svg class="w-3 h-3 shrink-0 mt-0.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v4m0 4h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/></svg>
         <span>${_esc(n)}</span>
       </p>`).join('');
  }

  const bloc = (which, items) => items.length
    ? `<p class="${REVIEW_GROUPS[which].heading} mb-2">${REVIEW_GROUPS[which].label}</p>`
      + items.map(_reviewRow).join('')
    : '';
  // Warned first, deliberately: the group that needs a decision is the one you
  // land on, not the one you scroll past.
  list.innerHTML = bloc('warned', warned)
    + (warned.length && clean.length ? '<div class="h-2"></div>' : '')
    + bloc('clean', clean);

  document.getElementById('delete-step-options')?.classList.add('hidden');
  document.getElementById('delete-step-review')?.classList.remove('hidden');
  _updateReviewCount();
}

function _reviewSelectedIds() {
  return Array.from(document.querySelectorAll('#delete-review-list input[data-review-id]'))
    .filter(cb => cb.checked)
    .map(cb => parseInt(cb.dataset.reviewId, 10));
}

function _updateReviewCount() {
  const btn = document.getElementById('delete-confirm-btn');
  if (!btn) return;
  const n = _reviewSelectedIds().length;
  btn.textContent = n === 1 ? 'Delete 1 item' : `Delete ${n} items`;
  btn.disabled = n === 0;
  btn.classList.toggle('opacity-50', n === 0);
  btn.classList.toggle('cursor-not-allowed', n === 0);

  const note = document.getElementById('delete-review-footnote');
  if (note) {
    const ticked = new Set(_reviewSelectedIds());
    const bytes = _reviewPlans
      .filter(p => ticked.has(p.id))
      .reduce((sum, p) => sum + (p.would_free_bytes || 0), 0);
    note.textContent = n === 0
      ? 'Nothing ticked'
      : (bytes ? `frees up to ${_humanSize(bytes)}` : 'no files will be removed');
  }
}

function confirmDelete() {
  const ids = _reviewSelectedIds();
  if (ids.length === 0) return;

  const resultEl = document.getElementById('delete-result');

  fetch('/actions/delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ item_ids: ids, ..._deleteOptions() }),
  })
    .then(r => r.json())
    .then(data => {
      const succeeded = data.results.filter(r => r.success).length;
      const partial = data.results.filter(r => r.status === 'partial').length;
      const failed = data.results.filter(r => r.status === 'failed').length;

      if (resultEl) {
        resultEl.classList.remove('hidden');
        // The reviewed list is stale the moment a result exists: its rows have
        // already gone from the page behind. Hiding it hands its height to the
        // only text worth reading now, instead of squeezing both.
        _hideReviewForResult();
        if (failed === 0 && partial === 0) {
          resultEl.innerHTML = `<div class="px-3 py-2 rounded bg-green-900/40 border border-green-700/40 text-green-300 text-sm">${succeeded} item(s) deleted successfully.</div>`;
        } else {
          // A partial outcome is not a failure: the media is gone, something
          // around it is not. Saying so beats one red block for both.
          const msgs = data.results
            .filter(r => (r.errors || []).length)
            .map(r => `${_esc(r.title)}: ${(r.errors || []).map(_esc).join(', ')}`)
            .join('<br>');
          const tone = failed ? 'red' : 'amber';
          resultEl.innerHTML = `<div class="px-3 py-2 rounded bg-${tone}-900/40 border border-${tone}-700/40 text-${tone}-300 text-sm">${succeeded} done, ${partial} partial, ${failed} failed.<br><span class="text-xs opacity-80">${msgs}</span></div>`;
        }
      }

      data.results.filter(r => r.media_removed).forEach(r => {
        const card = document.querySelector(`[onclick*="openDetail(${r.id},"]`);
        if (card) card.remove();
        const row = document.querySelector(`tr[data-item-id="${r.id}"]`);
        if (row) row.remove();
        selected.delete(r.id);
      });

      updateBulkBar();
      closeDetail();
      if (failed === 0 && partial === 0) setTimeout(() => closeDeleteModal(), 2500);
    })
    .catch(err => {
      if (resultEl) {
        resultEl.classList.remove('hidden');
        const div = document.createElement('div');
        div.className = 'px-3 py-2 rounded bg-red-900/40 border border-red-700/40 text-red-300 text-sm';
        div.textContent = `Request failed: ${err.message}`;
        resultEl.appendChild(div);
      }
    });
}

// ── Bulk actions ──────────────────────────────────────────────────────────────

function bulkUnmonitor() {
  const ids = Array.from(selected);
  if (ids.length === 0) return;

  fetch('/actions/unmonitor', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ item_ids: ids }),
  })
    .then(r => r.json())
    .then(data => {
      const succeeded = data.results.filter(r => r.success).length;
      const failed = data.results.filter(r => !r.success).length;
      showToast(
        failed > 0 ? `Unmonitored ${succeeded}, failed ${failed}.` : `Unmonitored ${succeeded} item(s).`,
        failed > 0
      );
    })
    .catch(() => showToast('Unmonitor request failed.', true));
}

function bulkSearch() {
  const ids = Array.from(selected);
  if (ids.length === 0) return;
  bulkSearchIds(ids);
}

function bulkSearchSingle(id) {
  bulkSearchIds([id]);
}

function bulkSearchIds(ids) {
  fetch('/actions/search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ item_ids: ids }),
  })
    .then(r => r.json())
    .then(data => {
      const succeeded = data.results.filter(r => r.success).length;
      showToast(`Search triggered for ${succeeded}/${ids.length} item(s).`);
    })
    .catch(() => showToast('Search request failed.', true));
}

// ── Season actions ────────────────────────────────────────────────────────────

function loadSeasons(itemId) {
  const btn = document.getElementById(`seasons-load-btn-${itemId}`);
  const container = document.getElementById(`seasons-list-${itemId}`);
  if (btn) btn.textContent = 'Loading…';

  fetch(`/actions/seasons/${itemId}`)
    .then(r => r.json())
    .then(data => {
      if (btn) btn.style.display = 'none';
      if (!data.seasons || data.seasons.length === 0) {
        if (container) container.innerHTML = '<p class="text-xs text-slate-500">No season data available.</p>';
        return;
      }
      const rows = data.seasons.map(s => {
        const sizeGb = s.size_on_disk ? (s.size_on_disk / 1073741824).toFixed(1) : '—';
        return `
          <div class="flex items-center justify-between py-2 border-b border-slate-700/30 last:border-0">
            <div class="flex items-center gap-3">
              <span class="text-sm text-white font-medium">Season ${s.season_number}</span>
              <span class="text-xs text-slate-400">${s.episode_count} eps · ${sizeGb} GB</span>
              <span class="px-1.5 py-0.5 rounded text-xs ${s.monitored ? 'bg-green-900/30 text-green-400' : 'bg-slate-700 text-slate-400'}">${s.monitored ? 'Monitored' : 'Unmonitored'}</span>
            </div>
            <div class="flex items-center gap-2">
              <button onclick="unmonitorSeason(${itemId}, ${s.season_number})"
                class="px-2 py-1 rounded text-xs bg-slate-700 text-slate-300 hover:bg-slate-600 transition">
                Unmonitor
              </button>
              <button onclick="deleteSeasonFiles(${itemId}, ${s.season_number})"
                class="px-2 py-1 rounded text-xs bg-red-900/30 text-red-300 border border-red-700/30 hover:bg-red-900/50 transition">
                Del Files
              </button>
            </div>
          </div>`;
      }).join('');
      if (container) container.innerHTML = rows;
    })
    .catch(() => {
      if (container) container.innerHTML = '<p class="text-xs text-red-400">Failed to load seasons.</p>';
      if (btn) btn.textContent = 'Retry';
    });
}

function deleteSeasonFiles(itemId, seasonNumber) {
  if (!confirm(`Delete all files for Season ${seasonNumber}? This cannot be undone.`)) return;
  fetch('/actions/delete-season', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ item_id: itemId, season_number: seasonNumber, delete_files: true }),
  })
    .then(r => r.json())
    .then(data => {
      if (data.success) {
        showToast(`Season ${seasonNumber} files deleted.`);
        loadSeasons(itemId);
      } else {
        showToast(`Failed: ${(data.errors || []).join(', ')}`, true);
      }
    })
    .catch(() => showToast('Request failed.', true));
}

function unmonitorSeason(itemId, seasonNumber) {
  fetch('/actions/delete-season', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ item_id: itemId, season_number: seasonNumber, delete_files: false }),
  })
    .then(r => r.json())
    .then(() => {
      showToast(`Season ${seasonNumber} unmonitored.`);
      loadSeasons(itemId);
    })
    .catch(() => showToast('Request failed.', true));
}

function unmonitorSingle(itemId, title) {
  fetch('/actions/unmonitor', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ item_ids: [itemId] }),
  })
    .then(r => r.json())
    .then(data => {
      const ok = data.results && data.results[0] && data.results[0].success;
      showToast(ok ? `"${title}" unmonitored.` : `Unmonitor failed for "${title}".`, !ok);
    })
    .catch(() => showToast('Request failed.', true));
}

// ── Seerr ─────────────────────────────────────────────────────────────────────

function seerrDeleteRequest(itemId) {
  fetch(`/actions/seerr-delete/${itemId}`, { method: 'POST' })
    .then(r => r.json())
    .then(data => showToast(data.message || (data.success ? 'Done.' : 'Failed.'), !data.success))
    .catch(() => showToast('Request failed.', true));
}

// ── Rules ─────────────────────────────────────────────────────────────────────

function previewRule(id) {
  const modal = document.getElementById('preview-modal');
  const content = document.getElementById('preview-content');
  const summary = document.getElementById('preview-summary');
  if (!modal) return;
  modal.classList.remove('hidden');
  modal.classList.add('flex');
  content.innerHTML = '<div class="text-center text-slate-400 py-8">Loading…</div>';
  summary.textContent = '';

  fetch(`/rules/${id}/preview`, { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      if (data.error) {
        content.innerHTML = '';
        const div = document.createElement('div');
        div.className = 'text-red-400 text-sm p-4';
        div.textContent = data.error;
        content.appendChild(div);
        return;
      }
      summary.textContent = `${data.matched} item(s) would be affected by this rule.`;
      if (!data.items || data.items.length === 0) {
        content.innerHTML = '<div class="text-slate-400 text-sm p-4">No items match this rule currently.</div>';
        return;
      }
      const rows = data.items.map(item => {
        const sizeGb = item.file_size_bytes ? (item.file_size_bytes / 1073741824).toFixed(1) + ' GB' : '—';
        const lw = item.last_watched ? item.last_watched.split('T')[0] : 'Never';
        return `<tr class="border-b border-slate-700/30 hover:bg-[#16213e]/40">
          <td class="py-2 pr-4 text-white font-medium">${_esc(item.title)}</td>
          <td class="py-2 pr-4">
            <span class="px-1.5 py-0.5 rounded text-xs ${item.media_type === 'movie' ? 'bg-[#e5a00d]/20 text-[#e5a00d]' : 'bg-sky-900/40 text-sky-300'}">
              ${item.media_type === 'movie' ? 'Movie' : 'Series'}
            </span>
          </td>
          <td class="py-2 pr-4 text-slate-400">${_esc(sizeGb)}</td>
          <td class="py-2 text-slate-400">${_esc(lw)}</td>
        </tr>`;
      }).join('');
      content.innerHTML = `<table class="w-full text-sm">
        <thead><tr class="text-left border-b border-slate-700/50">
          <th class="pb-2 text-xs text-slate-500 uppercase tracking-wider">Title</th>
          <th class="pb-2 text-xs text-slate-500 uppercase tracking-wider">Type</th>
          <th class="pb-2 text-xs text-slate-500 uppercase tracking-wider">Size</th>
          <th class="pb-2 text-xs text-slate-500 uppercase tracking-wider">Last Watched</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
    })
    .catch(err => {
      content.innerHTML = '';
      const div = document.createElement('div');
      div.className = 'text-red-400 text-sm p-4';
      div.textContent = `Request failed: ${err.message}`;
      content.appendChild(div);
    });
}

function closePreviewModal() {
  const modal = document.getElementById('preview-modal');
  if (modal) { modal.classList.add('hidden'); modal.classList.remove('flex'); }
}

function runRule(id) {
  if (!confirm('Run this rule now? All matching items will be processed immediately.')) return;
  fetch(`/rules/${id}/run`, { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      const isErr = !!data.error;
      const msg = isErr
        ? `Error: ${data.error}`
        : `Rule executed: ${data.matched} matched, ${data.deleted} deleted${data.errors && data.errors.length ? ` (${data.errors.length} errors)` : ''}`;
      showToast(msg, isErr);
    })
    .catch(() => showToast('Rule execution failed.', true));
}

function toggleRule(id, enabled) {
  const formData = new FormData();
  formData.append('enabled', enabled ? 'true' : 'false');
  fetch(`/rules/${id}`, { method: 'PUT', body: formData })
    .then(r => r.text())
    .then(html => {
      const el = document.getElementById('rules-list');
      if (el) el.innerHTML = html;
    });
}

function deleteRule(id) {
  if (!confirm('Delete this rule? This cannot be undone.')) return;
  fetch(`/rules/${id}`, { method: 'DELETE' })
    .then(r => r.text())
    .then(html => {
      const el = document.getElementById('rules-list');
      if (el) el.innerHTML = html;
    });
}

function toggleRuleForm() {
  const container = document.getElementById('rule-form-container');
  const btn = document.getElementById('toggle-rule-form-btn');
  if (!container) return;
  const isHidden = container.classList.toggle('hidden');
  if (btn) btn.textContent = isHidden ? '+ New Rule' : '✕ Cancel';
}

// ── Toast ─────────────────────────────────────────────────────────────────────

function showToast(msg, isError) {
  const t = document.createElement('div');
  t.className = `fixed top-4 right-4 z-[70] bg-[#1a1a2e] border ${isError ? 'border-red-700/40 text-red-300' : 'border-[#e5a00d]/40 text-white'} px-4 py-3 rounded-lg shadow-xl text-sm max-w-sm transition-opacity duration-300`;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => {
    t.style.opacity = '0';
    setTimeout(() => t.remove(), 300);
  }, 4000);
}

// ── Keyboard shortcuts ────────────────────────────────────────────────────────

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    closeDetail();
    closeDeleteModal();
    closePreviewModal();
    closeMobileNav();
    closeFiltersSidebar();
  }
});

// ── HTMX: re-sync checkboxes + export button after grid swap ─────────────────

document.addEventListener('htmx:afterSwap', e => {
  if (e.detail.target && e.detail.target.id === 'media-grid') {
    selected.forEach(id => {
      const cb = document.getElementById(`cb-${id}`);
      if (cb) {
        cb.checked = true;
        const wrap = document.getElementById(`cb-wrap-${id}`);
        if (wrap) wrap.classList.add('checked');
      }
    });
    // Keep export CSV URL in sync with current filters
    const exportBtn = document.getElementById('export-btn');
    if (exportBtn) {
      const qs = window.location.search.slice(1);
      exportBtn.href = '/library/export' + (qs ? '?' + qs : '');
    }
  }
});

// ── Analytics charts ──────────────────────────────────────────────────────────

function initAnalyticsCharts() {
  fetch('/analytics/snapshots')
    .then(r => r.json())
    .then(snaps => {
      if (!snaps || snaps.length === 0) {
        document.getElementById('chart-no-data')?.classList.remove('hidden');
        return;
      }
      const labels = snaps.map(s => s.date);
      const primary = '#e5a00d';
      const sky = '#38bdf8';
      const slate = '#475569';
      const chartDefaults = {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { labels: { color: '#94a3b8', font: { size: 12 } } } },
        scales: {
          x: { ticks: { color: '#64748b', maxTicksLimit: 10 }, grid: { color: '#1e293b' } },
          y: { ticks: { color: '#64748b' }, grid: { color: '#1e293b' } },
        },
      };

      const sizeCtx = document.getElementById('chart-size');
      if (sizeCtx) {
        new Chart(sizeCtx, {
          type: 'line',
          data: {
            labels,
            datasets: [
              { label: 'Total (GB)', data: snaps.map(s => s.size_gb), borderColor: primary, backgroundColor: primary + '20', tension: 0.3, fill: true },
              { label: 'Movies (GB)', data: snaps.map(s => s.movies_size_gb), borderColor: sky, backgroundColor: sky + '10', tension: 0.3 },
              { label: 'Series (GB)', data: snaps.map(s => s.series_size_gb), borderColor: slate, backgroundColor: slate + '10', tension: 0.3 },
            ],
          },
          options: { ...chartDefaults, plugins: { ...chartDefaults.plugins, title: { display: false } } },
        });
      }

      const itemsCtx = document.getElementById('chart-items');
      if (itemsCtx) {
        new Chart(itemsCtx, {
          type: 'line',
          data: {
            labels,
            datasets: [
              { label: 'Total', data: snaps.map(s => s.total), borderColor: primary, tension: 0.3 },
              { label: 'Watched', data: snaps.map(s => s.watched), borderColor: '#4ade80', tension: 0.3 },
              { label: 'Unwatched', data: snaps.map(s => s.unwatched), borderColor: '#f87171', tension: 0.3 },
            ],
          },
          options: chartDefaults,
        });
      }
    })
    .catch(err => console.error('Analytics data failed:', err));
}

// History: expand a clamped failure message. The reason a deletion failed is
// often the only actionable output of the run, so it must be fully readable.
function toggleErrorDetail(el) {
  const expanded = el.style.webkitLineClamp === 'unset';
  el.style.webkitLineClamp = expanded ? '2' : 'unset';
}
