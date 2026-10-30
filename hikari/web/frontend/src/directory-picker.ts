import { api } from './api';
import { element, errorMessage, esc } from './helpers';
import type { BrowseResponse } from './types';

const lastFolderKey = 'hikari.lastFitsFolder';

let pickerCurrentPath: string | null = null;

export async function openDirPicker() {
  const current = element<HTMLInputElement>('path-input').value.trim();
  element('dir-modal-backdrop').classList.add('open');
  await browseTo(current || localStorage.getItem(lastFolderKey) || null);
}

export function closeDirPicker() {
  element('dir-modal-backdrop').classList.remove('open');
}

export function confirmDirPicker() {
  if (pickerCurrentPath) {
    element<HTMLInputElement>('path-input').value = pickerCurrentPath;
    localStorage.setItem(lastFolderKey, pickerCurrentPath);
  }
  closeDirPicker();
}

async function browseTo(path: string | null) {
  const entries = element<HTMLDivElement>('dir-entries');
  entries.innerHTML = '<div class="empty"><span class="spinner"></span></div>';
  try {
    const params = path ? `?path=${encodeURIComponent(path)}` : '';
    const data = await api<BrowseResponse>(`/api/browse${params}`);
    pickerCurrentPath = data.path;
    element('dir-modal-path').textContent = data.path;

    let html = '';
    if (data.parent) {
      html += `<div class="dir-entry" data-path="${esc(data.parent)}">
        <span class="dir-icon">↑</span><span style="color:var(--muted)">.. (up)</span>
      </div>`;
    }
    if (!data.entries.length) {
      html += '<div class="empty" style="padding:20px">No subdirectories</div>';
    } else {
      html += data.entries.map(e =>
        `<div class="dir-entry" data-path="${esc(e.path)}">
          <span class="dir-icon">📁</span><span>${esc(e.name)}</span>
        </div>`
      ).join('');
    }
    entries.innerHTML = html;
    entries.querySelectorAll<HTMLDivElement>('.dir-entry[data-path]').forEach(el => {
      el.addEventListener('click', () => browseTo(el.dataset.path ?? ''));
    });
  } catch(error) {
    entries.innerHTML = `<div class="empty" style="color:var(--fail)">${esc(errorMessage(error))}</div>`;
  }
}



export function initializeDirectoryPicker(): void {
  // Close on backdrop click
  element('dir-modal-backdrop').addEventListener('click', (e: MouseEvent) => {
    if (e.target === element('dir-modal-backdrop')) closeDirPicker();
  });
}
