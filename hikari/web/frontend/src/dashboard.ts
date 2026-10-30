import { api } from './api';
import { element, errorMessage, esc, toast } from './helpers';
import { loadSettingsForm } from './settings';
import { destroyEnsembleChart, renderCutout, renderEnsembleChart } from './charts';
import { closeDirPicker, confirmDirPicker, initializeDirectoryPicker, openDirPicker } from './directory-picker';
import type { Job, Observation, Run } from './types';

// ── State ──────────────────────────────────────────────────────────────────
let currentRunId: number | null = null;
let expandedJobId: number | null = null;
let currentJobObs: Record<number, Observation[]> = {};
let pollTimer: number | null = null;
let selectedObsId: number | null = null;
let activeRun: Run | null = null;
export const lastFolderKey = 'hikari.lastFitsFolder';

  // ── API helpers ─────────────────────────────────────────────────────────────
export function showRuns(): void {
    element('dashboard-main').style.display = '';
    element('settings-page').style.display = 'none';
    element('runs-nav').classList.add('primary');
    element('settings-nav').classList.remove('primary');
    loadRuns();
  }

export async function showSettings(): Promise<void> {
    stopPolling();
    element('dashboard-main').style.display = 'none';
    element('settings-page').style.display = 'block';
    element('runs-nav').classList.remove('primary');
    element('settings-nav').classList.add('primary');
    await loadSettingsForm();
  }


// ── Run list ───────────────────────────────────────────────────────────────
export async function loadRuns(): Promise<void> {
    const el = element('run-list');
    try {
      const runs = await api<Run[]>('/api/runs?limit=50');
      if (!runs.length) { el.innerHTML = '<div class="empty">No runs yet. Process a directory above.</div>'; return; }
      el.innerHTML = runs.map(runItem).join('');
      if (currentRunId) {
        document.querySelector(`.run-item[data-id="${currentRunId}"]`)?.classList.add('active');
      }
    } catch(error) {
      el.innerHTML = `<div class="empty" style="color:var(--fail)">${esc(errorMessage(error))}</div>`;
    }
  }

  function badgeClass(status: string): string {
    return `badge-${status}`;
  }

  function runItem(r: Run): string {
    const dt = new Date(r.created_at).toLocaleString();
    const pathShort = r.source_path.split('/').slice(-2).join('/');
    return `<div class="run-item" data-id="${r.id}" onclick="selectRun(${r.id})">
      <div class="run-path" title="${esc(r.source_path)}">${esc(pathShort)}</div>
      <div class="run-meta">
        <span>${dt}</span>
        <span class="badge ${badgeClass(r.status)}">${r.status}</span>
        <span>${r.n_jobs} job${r.n_jobs !== 1 ? 's' : ''}</span>
        <span>${r.n_observations} obs</span>
        ${r.notes ? `<span title="${esc(r.notes)}" style="color:var(--accent)">✎</span>` : ''}
      </div>
    </div>`;
  }

  // ── New run ────────────────────────────────────────────────────────────────
export async function startRun(): Promise<void> {
    const input = element<HTMLInputElement>('path-input');
    const path = input.value.trim();
    if (!path) { toast('Enter a directory path first', true); return; }
    localStorage.setItem(lastFolderKey, path);

    const btn = element<HTMLButtonElement>('start-btn');
    btn.disabled = true;
    btn.textContent = 'Starting…';

    try {
      const run = await api<Run>('/api/runs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source_path: path }),
      });
      toast(`Run #${run.id} started`);
      await loadRuns();
      selectRun(run.id);
    } catch(error) {
      toast(errorMessage(error), true);
    } finally {
      btn.disabled = false;
      btn.textContent = 'Process';
    }
  }

  // ── Run detail ─────────────────────────────────────────────────────────────
export async function selectRun(runId: number): Promise<void> {
    stopPolling();
    currentRunId = runId;
    expandedJobId = null;
    currentJobObs = {};
    selectedObsId = null;
    activeRun = null;

    document.querySelectorAll('.run-item').forEach(el => el.classList.remove('active'));
    document.querySelector(`.run-item[data-id="${runId}"]`)?.classList.add('active');

    const panel = element('detail-panel');
    panel.innerHTML = '<div class="empty"><span class="spinner"></span></div>';
    resetVizPanel();

    await refreshRunDetail(runId);

    // Poll if still running
    const r = await api<Run>(`/api/runs/${runId}`).catch(() => null);
    if (r && r.status === 'running') {
      pollTimer = setInterval(() => pollRun(runId), 2000);
    }
  }

  async function refreshRunDetail(runId: number): Promise<void> {
    try {
      const [run, jobs] = await Promise.all([
        api<Run>(`/api/runs/${runId}`),
        api<Job[]>(`/api/runs/${runId}/jobs`),
      ]);
      renderRunDetail(run, jobs);
    } catch(error) {
      element('detail-panel').innerHTML =
        `<div class="empty" style="color:var(--fail)">${esc(errorMessage(error))}</div>`;
    }
  }

  async function pollRun(runId: number): Promise<void> {
    try {
      const run = await api<Run>(`/api/runs/${runId}`);
      if (run.status !== 'running') {
        stopPolling();
        await loadRuns();
      }
      // Re-render detail (preserves expanded state via expandedJobId)
      const jobs = await api<Job[]>(`/api/runs/${runId}/jobs`);
      renderRunDetail(run, jobs);
    } catch(e) { /* silently ignore poll errors */ }
  }

  function stopPolling(): void {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  function renderRunDetail(run: Run, jobs: Job[]): void {
    const panel = element('detail-panel');
    activeRun = run;
    const dt = new Date(run.created_at).toLocaleString();
    const totalObs = jobs.reduce((s, j) => s + j.n_observations, 0);
    const totalFailed = jobs.reduce((s, j) => s + j.n_failed, 0);
    const totalRejected = jobs.reduce((s, j) => s + (j.n_rejected || 0), 0);
    const isRunning = run.status === 'running';
    const exportReady = !isRunning && totalObs > 0;

    // Progress section (only while running)
    let progressHtml = '';
    if (run.status === 'running') {
      const prog = run.progress || {};
      const total = prog.total || 1;
      const completed = prog.completed || 0;
      const pct = Math.round((completed / total) * 100);
      const label = prog.current_group ? `Processing: ${esc(prog.current_group)}` : 'Starting…';
      progressHtml = `
        <div class="progress-wrap">
          <div class="progress-label"><span class="spinner"></span> &nbsp;${label} (${completed}/${total})</div>
          <div class="progress-bar-track"><div class="progress-bar-fill" style="width:${pct}%"></div></div>
        </div>`;
    }

    // Job sections
    const jobSections = jobs.map(j => jobSection(j)).join('');

    panel.innerHTML = `
      <div id="detail-header">
        <h2>${esc(run.source_path)}</h2>
        <div class="sub">
          <span>${dt}</span>
          <span class="badge ${badgeClass(run.status)}">${run.status}</span>
          <span>${jobs.length} job${jobs.length !== 1 ? 's' : ''}</span>
          <span>${totalObs} obs accepted${totalFailed ? ` · <span style="color:var(--fail)">${totalFailed} failed</span>` : ''}</span>
        </div>
      </div>
      ${progressHtml}
      <div class="workflow-steps" aria-label="Workflow progress">
        <div class="workflow-step ${isRunning ? 'active' : 'done'}">
          <strong>1 · Process</strong>${isRunning ? 'Processing FITS files' : run.status === 'failed' ? 'Finished with errors' : 'Processing finished'}
        </div>
        <div class="workflow-step ${isRunning ? '' : 'active'}">
          <strong>2 · Review</strong>${isRunning ? 'Available when processing finishes' : 'Select observations to inspect'}
        </div>
        <div class="workflow-step ${exportReady ? 'ready' : ''}">
          <strong>3 · Export</strong>${exportReady ? `${totalObs} accepted · ${totalRejected} rejected` : 'Available after processing'}
        </div>
      </div>
      <div class="toolbar" style="flex-shrink:0">
        <button class="primary" onclick="downloadRunCsv(${run.id})" ${exportReady ? '' : 'disabled'}>Download AAVSO CSV (${totalObs})</button>
        <span class="export-summary">${exportReady
          ? `${totalObs} accepted observation${totalObs === 1 ? '' : 's'} will be included; ${totalRejected} rejected excluded.`
          : isRunning ? 'Wait for processing to finish before exporting.' : 'No accepted observations to export yet.'}</span>
        <button class="danger" onclick="deleteRun(${run.id})" style="margin-left:auto">Delete Run</button>
      </div>
      <div style="padding:10px 16px; border-bottom:1px solid var(--border); flex-shrink:0">
        <textarea id="run-notes" rows="2"
          placeholder="Notes (e.g. uploaded to AAVSO, poor seeing…)"
          onblur="saveRunNotes(${run.id})"
          style="width:100%; background:var(--bg); border:1px solid var(--border); border-radius:5px;
                 color:var(--text); font-family:var(--font); font-size:0.82rem; padding:6px 10px;
                 resize:vertical; outline:none;"
          onfocus="this.style.borderColor='var(--accent)'"
          onblur="this.style.borderColor='var(--border)'; saveRunNotes(${run.id})"
        >${esc(run.notes || '')}</textarea>
      </div>
      <div class="jobs-scroll">
        <div id="job-accordion">${jobSections || '<div class="empty">No jobs yet.</div>'}</div>
      </div>`;

    // Re-attach obs table click handlers for already-expanded job
    if (expandedJobId && currentJobObs[expandedJobId]) {
      const wrap = element(`job-obs-${expandedJobId}`);
      if (wrap) {
        const obsDiv = wrap.querySelector('.obs-table-wrap');
        if (obsDiv) attachObsClicks(obsDiv, currentJobObs[expandedJobId]);
      }
    }
  }

  function jobSection(j: Job): string {
    const isOpen = j.id === expandedJobId;
    const nOk = j.n_observations;
    const nFail = j.n_failed;
    const failStr = nFail ? ` · <span style="color:var(--fail)">${nFail} failed</span>` : '';
    const icon = isOpen ? '▼' : '▶';

    let obsContent = '';
    if (isOpen && currentJobObs[j.id]) {
      obsContent = renderObsTable(j.id, currentJobObs[j.id]);
    }

    return `<div class="job-section" id="job-section-${j.id}">
      <div class="job-header" onclick="toggleJob(${j.id})">
        <span class="expand-icon" id="icon-${j.id}">${icon}</span>
        <span class="job-title">${esc(j.object_name)} / <span style="color:var(--muted)">${esc(j.filter)}</span></span>
        <span class="job-counts">${nOk} obs${failStr}</span>
      </div>
      <div class="job-obs${isOpen ? ' open' : ''}" id="job-obs-${j.id}">
        ${isOpen ? obsContent : ''}
      </div>
    </div>`;
  }

export async function toggleJob(jobId: number): Promise<void> {
    if (expandedJobId === jobId) {
      // Collapse
      expandedJobId = null;
      selectedObsId = null;
      resetVizPanel();
      const obs = element(`job-obs-${jobId}`);
      if (obs) { obs.classList.remove('open'); obs.innerHTML = ''; }
      const icon = element(`icon-${jobId}`);
      if (icon) icon.textContent = '▶';
      return;
    }

    // Collapse previous
    selectedObsId = null;
    resetVizPanel();
    if (expandedJobId) {
      const prev = element(`job-obs-${expandedJobId}`);
      if (prev) { prev.classList.remove('open'); prev.innerHTML = ''; }
      const prevIcon = element(`icon-${expandedJobId}`);
      if (prevIcon) prevIcon.textContent = '▶';
    }

    expandedJobId = jobId;
    const obsDiv = element(`job-obs-${jobId}`);
    const icon = element(`icon-${jobId}`);
    if (icon) icon.textContent = '▼';
    if (obsDiv) {
      obsDiv.classList.add('open');
      obsDiv.innerHTML = '<div class="empty"><span class="spinner"></span></div>';

      try {
        const obs = await api<Observation[]>(`/api/jobs/${jobId}/observations`);
        currentJobObs[jobId] = obs;
        obsDiv.innerHTML = renderObsTable(jobId, obs);
        attachObsClicks(obsDiv, obs);
      } catch(error) {
        obsDiv.innerHTML = `<div class="empty" style="color:var(--fail)">${esc(errorMessage(error))}</div>`;
      }
    }
  }

  function renderObsTable(jobId: number, obs: Observation[]): string {
    const rows = obs.map(o => obsRow(o)).join('');
    return `
      <div class="toolbar" style="border-top:1px solid var(--border); background:var(--bg)">
        <div class="review-nav">
          <span class="review-position" id="review-position-${jobId}">${obs.length} observations · select a row to review</span>
          <button id="review-previous-${jobId}" onclick="navigateObservation(-1)" disabled>Previous</button>
          <button id="review-next-${jobId}" onclick="navigateObservation(1)" disabled>Next</button>
        </div>
        <span class="info">${obs.length} observations · ${obs.filter(o=>o.rejected).length} rejected</span>
      </div>
      <div class="quality-legend">Fit RMS: <span class="rms-good">&lt;0.03</span> good · <span class="rms-warn">0.03–0.07</span> review · <span class="rms-bad">&gt;0.07</span> poor (lower is better). Check Δ is the absolute magnitude difference.</div>
      <div class="obs-table-wrap">
        <table>
          <thead>
            <tr>
              <th>File</th><th>JD</th><th>Mag</th><th>±Err</th>
              <th title="Fit residual RMS in magnitudes. Lower values indicate a tighter ensemble fit.">RMS fit</th><th>N ens</th><th title="Check star and absolute difference between calculated and published magnitudes.">Check Δ</th>
              <th>Airmass</th><th>Status</th><th></th>
            </tr>
          </thead>
          <tbody>${rows || `<tr><td colspan="10" class="empty">No observations</td></tr>`}</tbody>
        </table>
      </div>`;
  }

  function attachObsClicks(container: Element, obs: Observation[]): void {
    container.querySelectorAll<HTMLTableRowElement>('tr.obs-clickable').forEach(tr => {
      tr.addEventListener('click', (event: MouseEvent) => {
        if (event.target instanceof Element && event.target.closest('button')) return;
        const o = obs.find(x => x.id === Number(tr.dataset.obsId));
        if (o) selectObsRow(o);
      });
      tr.addEventListener('keydown', (event: KeyboardEvent) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        const o = obs.find(x => x.id === Number(tr.dataset.obsId));
        if (o) selectObsRow(o);
      });
    });
    updateReviewNavigation();
  }

  // ── Observation row ─────────────────────────────────────────────────────────
  function rmsClass(rms: number | null): string {
    if (rms == null) return '';
    if (rms < 0.03) return 'rms-good';
    if (rms < 0.07) return 'rms-warn';
    return 'rms-bad';
  }

  function obsRow(o: Observation): string {
    const rejected = o.rejected;
    const mag = o.magnitude  != null ? o.magnitude.toFixed(3)       : '—';
    const err = o.magnitude_error != null ? o.magnitude_error.toFixed(3) : '—';
    const rms = o.fit_rms    != null ? o.fit_rms.toFixed(4)         : '—';
    const am  = o.airmass    != null ? o.airmass.toFixed(2)         : '—';
    const jd  = o.julian_date       ? o.julian_date.toFixed(5)      : '—';
    const badge = o.status === 'success'
      ? `<span class="badge badge-success">ok</span>`
      : `<span class="badge badge-fail">${esc(o.status)}</span>`;
    const rejectBtn = rejected
      ? `<button class="sm primary" onclick="acceptObs(${o.id},event)">Accept</button>`
      : `<button class="sm danger"  onclick="rejectObs(${o.id},event)">Reject</button>`;

    let chkCell;
    if (!o.check_star_auid) {
      chkCell = '<td>—</td>';
    } else {
      const shortAuid = o.check_star_auid.split('-').pop();
      const pub   = o.check_star_published_magnitude != null ? o.check_star_published_magnitude.toFixed(3) : '?';
      const cal   = o.check_star_magnitude != null ? o.check_star_magnitude.toFixed(3) : '?';
      const delta = (o.check_star_published_magnitude != null && o.check_star_magnitude != null)
        ? Math.abs(o.check_star_magnitude - o.check_star_published_magnitude).toFixed(3) : '?';
      chkCell = `<td title="${esc(o.check_star_auid)} | published=${pub} calculated=${cal}">` +
        `${esc(shortAuid)}${delta !== '?' ? ` <span class="mag" style="color:var(--muted)">Δ${delta}</span>` : ''}</td>`;
    }

    return `<tr class="${rejected ? 'rejected ' : ''}${o.id === selectedObsId ? 'obs-selected ' : ''}obs-clickable" id="obs-${o.id}" data-obs-id="${o.id}" tabindex="0" aria-label="Review ${esc(shortName(o.file_name))}">
      <td title="${esc(o.file_name)}">${esc(shortName(o.file_name))}</td>
      <td class="mag">${jd}</td>
      <td class="mag">${mag}</td>
      <td class="mag">${err}</td>
      <td class="mag ${rmsClass(o.fit_rms)}">${rms}</td>
      <td>${o.n_ensemble_stars ?? '—'}</td>
      ${chkCell}
      <td>${am}</td>
      <td>${badge}</td>
      <td>${rejectBtn}</td>
    </tr>`;
  }

  function shortName(path: string): string {
    return path.split(/[\\/]/).at(-1) ?? '';
  }

  // ── Actions ─────────────────────────────────────────────────────────────────
export async function rejectObs(id: number, event: Event): Promise<void> {
    event.stopPropagation();
    const reason = prompt('Rejection reason (optional):');
    if (reason === null) return;
    try {
      await api(`/api/observations/${id}/reject`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason }),
      });
      toast('Observation rejected');
      if (expandedJobId) await reloadExpandedJob();
    } catch(error) { toast(errorMessage(error), true); }
  }

export async function acceptObs(id: number, event: Event): Promise<void> {
    event.stopPropagation();
    try {
      await api(`/api/observations/${id}/accept`, { method: 'POST' });
      toast('Observation accepted');
      if (expandedJobId) await reloadExpandedJob();
    } catch(error) { toast(errorMessage(error), true); }
  }

  async function reloadExpandedJob(): Promise<void> {
    if (!expandedJobId) return;
    const obsDiv = element(`job-obs-${expandedJobId}`);
    if (!obsDiv) return;
    const obs = await api<Observation[]>(`/api/jobs/${expandedJobId}/observations`).catch(() => null);
    if (!obs) return;
    currentJobObs[expandedJobId] = obs;
    obsDiv.innerHTML = renderObsTable(expandedJobId, obs);
    attachObsClicks(obsDiv, obs);
    if (activeRun) await refreshRunDetail(activeRun.id);
  }

export async function saveRunNotes(runId: number): Promise<void> {
    const text = element<HTMLTextAreaElement>('run-notes').value;
    try {
      await api(`/api/runs/${runId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ notes: text }),
      });
    } catch { toast('Failed to save notes', true); }
  }

export async function deleteRun(runId: number): Promise<void> {
    if (!confirm('Delete this run and all its observations? This cannot be undone.')) return;
    try {
      await api(`/api/runs/${runId}`, { method: 'DELETE' });
      toast('Run deleted');
      stopPolling();
      currentRunId = null;
      expandedJobId = null;
      currentJobObs = {};
      selectedObsId = null;
      activeRun = null;
      element('detail-panel').innerHTML =
        '<div class="empty" style="padding:32px"><h2 style="font-size:1.1rem; color:var(--text); margin-bottom:12px">Your photometry workflow</h2><p>Choose a FITS folder to get started, then process, review, and export accepted observations.</p></div>';
      resetVizPanel();
      loadRuns();
    } catch(error) { toast(errorMessage(error), true); }
  }

export async function downloadRunCsv(runId: number): Promise<void> {
    const r = await fetch(`/api/runs/${runId}/aavso-csv`);
    if (!r.ok) {
      const error = await r.json().catch(() => null);
      toast(typeof error?.detail === 'string' ? error.detail : 'Failed to generate CSV', true);
      return;
    }
    const text = await r.text();
    const blob = new Blob([text], { type: 'text/csv' });
    const a = document.createElement('a');
    const url = URL.createObjectURL(blob);
    a.href = url;
    a.download = `AAVSO-run-${runId}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast('AAVSO CSV downloaded');
  }

export async function downloadJobCsv(jobId: number): Promise<void> {
    const r = await fetch(`/api/jobs/${jobId}/aavso-csv`);
    if (!r.ok) {
      const error = await r.json().catch(() => null);
      toast(typeof error?.detail === 'string' ? error.detail : 'Failed to generate CSV', true);
      return;
    }
    const text = await r.text();
    const blob = new Blob([text], { type: 'text/csv' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `AAVSO-job-${jobId}.csv`;
    a.click();
  }

export async function clearCache(): Promise<void> {
    if (!confirm('Delete all cached API responses? The next run will re-fetch from AAVSO.')) return;
    try {
      const result = await api<{ entries_deleted: number }>('/api/cache', { method: 'DELETE' });
      toast(`Cache cleared (${result.entries_deleted} entries)`);
    } catch(error) { toast(errorMessage(error), true); }
  }

export async function clearAllData(): Promise<void> {
    if (!confirm('Delete ALL runs, jobs and observations? This cannot be undone.')) return;
    try {
      const result = await api<{ jobs_deleted: number }>('/api/data', { method: 'DELETE' });
      toast(`Cleared ${result.jobs_deleted} job(s)`);
      stopPolling();
      currentRunId = null;
      expandedJobId = null;
      currentJobObs = {};
      selectedObsId = null;
      activeRun = null;
      element('detail-panel').innerHTML =
        '<div class="empty" style="padding:32px"><h2 style="font-size:1.1rem; color:var(--text); margin-bottom:12px">Your photometry workflow</h2><p>Choose a FITS folder to get started, then process, review, and export accepted observations.</p></div>';
      resetVizPanel();
      loadRuns();
    } catch(error) { toast(errorMessage(error), true); }
  }

  // ── Viz panel ───────────────────────────────────────────────────────────────
  function resetVizPanel(): void {
    destroyEnsembleChart();
    element('viz-filename').textContent = '';
    element('viz-empty').style.display = 'block';
    element('viz-cards').style.display = 'none';
  }

  function selectObsRow(o: Observation): void {
    selectedObsId = o.id;
    document.querySelectorAll('tr.obs-clickable').forEach(tr => tr.classList.remove('obs-selected'));
    const tr = element(`obs-${o.id}`);
    if (tr) tr.classList.add('obs-selected');

    element('viz-empty').style.display = 'none';
    element('viz-cards').style.display = 'flex';
    element('viz-filename').textContent = shortName(o.file_name);
    updateReviewNavigation();

    renderEnsembleChart(o);
    renderCutout(o);
  }

  function updateReviewNavigation(): void {
    if (expandedJobId === null) return;
    const obs = currentJobObs[expandedJobId] || [];
    const index = obs.findIndex(o => o.id === selectedObsId);
    const position = element(`review-position-${expandedJobId}`);
    const previous = element<HTMLButtonElement>(`review-previous-${expandedJobId}`);
    const next = element<HTMLButtonElement>(`review-next-${expandedJobId}`);
    if (!position || !previous || !next) return;
    position.textContent = index < 0 ? `${obs.length} observations · select a row to review`
      : `Observation ${index + 1} of ${obs.length}${obs[index].rejected ? ' · rejected' : ''}`;
    previous.disabled = index <= 0;
    next.disabled = index < 0 || index >= obs.length - 1;
  }

export function navigateObservation(offset: number): void {
    if (expandedJobId === null) return;
    const obs = currentJobObs[expandedJobId] || [];
    const index = obs.findIndex(o => o.id === selectedObsId);
    const next = obs[index + offset];
    if (!next) return;
    selectObsRow(next);
    element(`obs-${next.id}`)?.scrollIntoView({ block: 'nearest' });
  }


  // ── Utility ──────────────────────────────────────────────────────────────────
  // ── Directory picker ─────────────────────────────────────────────────────────

export function initializeDashboard(): void {
  const rememberedFolder = localStorage.getItem(lastFolderKey);
  if (rememberedFolder) element<HTMLInputElement>('path-input').value = rememberedFolder;
  initializeDirectoryPicker();
  document.addEventListener('keydown', event => {
    if (!expandedJobId || !selectedObsId || event.altKey || event.ctrlKey || event.metaKey) return;
    if ((event.target as HTMLElement).closest('input, textarea, select, button, [contenteditable="true"]')) return;
    if (event.key === 'ArrowLeft') {
      event.preventDefault();
      navigateObservation(-1);
    } else if (event.key === 'ArrowRight') {
      event.preventDefault();
      navigateObservation(1);
    }
  });
  loadRuns();
  window.setInterval(loadRuns, 30000);
}
