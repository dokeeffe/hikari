export function element<T extends HTMLElement = HTMLElement>(id: string): T {
  const found = document.getElementById(id);
  if (!found) throw new Error(`Missing dashboard element: ${id}`);
  return found as T;
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function toast(msg: string, err = false): void {
  const el = element('toast');
  el.textContent = msg;
  el.style.display = 'block';
  el.style.borderColor = err ? 'var(--fail)' : 'var(--success)';
  setTimeout(() => { el.style.display = 'none'; }, 3500);
}


export function esc(s: unknown): string {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}
