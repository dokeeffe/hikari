import { api } from './api';
import { element, errorMessage, esc, toast } from './helpers';
import type { HikariSettings, SettingsResponse } from './types';

type SettingField = {
  key: keyof HikariSettings;
  label: string;
  help: string;
  type?: 'text' | 'textarea' | 'number' | 'list';
  step?: string;
};

const settingsSections: { title: string; fields: SettingField[] }[] = [
  { title: 'Observer', fields: [
    { key: 'obs_code', label: 'AAVSO observer code', help: 'Your AAVSO observer code.' },
    { key: 'observer_notes', label: 'Observer notes', help: 'Notes included with AAVSO observations.', type: 'textarea' },
  ] },
  { title: 'Photometry', fields: [
    { key: 'aperture_radius', label: 'Aperture radius', help: 'Photometry aperture in pixels.', type: 'number', step: 'any' },
    { key: 'sky_inner_radius', label: 'Sky inner radius', help: 'Sky annulus inner radius in pixels.', type: 'number', step: 'any' },
    { key: 'sky_outer_radius', label: 'Sky outer radius', help: 'Sky annulus outer radius in pixels.', type: 'number', step: 'any' },
    { key: 'fwhm', label: 'FWHM', help: 'Expected stellar full width at half maximum in pixels.', type: 'number', step: 'any' },
    { key: 'source_snr', label: 'Source SNR', help: 'Source detection threshold in sigma.', type: 'number', step: 'any' },
    { key: 'ensemble_size', label: 'Ensemble size', help: 'Number of comparison stars in the ensemble.', type: 'number', step: '1' },
    { key: 'linear_limit', label: 'Linearity limit', help: 'ADU threshold used to flag saturation.', type: 'number', step: 'any' },
  ] },
  { title: 'Comparison stars', fields: [
    { key: 'brightest_comp_star', label: 'Brightest comparison star', help: 'Brightest permitted catalog magnitude.', type: 'number', step: 'any' },
    { key: 'dimmest_comp_star', label: 'Dimmest comparison star', help: 'Dimmest permitted catalog magnitude.', type: 'number', step: 'any' },
    { key: 'field_of_view', label: 'Field of view', help: 'AAVSO VSP query radius in arcminutes.', type: 'number', step: 'any' },
    { key: 'excluded_auids', label: 'Excluded AUIDs', help: 'One comparison-star AUID per line.', type: 'list' },
  ] },
  { title: 'File and database', fields: [
    { key: 'output_dir', label: 'Output directory', help: 'Directory for generated files.' },
    { key: 'database_url', label: 'Database URL', help: 'SQLAlchemy database URL. Changing this requires a dashboard restart.' },
    { key: 'cache_dir', label: 'Cache directory', help: 'Optional; leave blank to use ~/.cache/hikari.' },
  ] },
  { title: 'Web dashboard', fields: [
    { key: 'web_host', label: 'Web host', help: 'Listening address. Changing this requires a dashboard restart.' },
    { key: 'web_port', label: 'Web port', help: 'Listening port. Changing this requires a dashboard restart.', type: 'number', step: '1' },
  ] },
];

export async function loadSettingsForm(): Promise<void> {
  const root = element('settings-content');
  root.innerHTML = '<div class="empty"><span class="spinner"></span></div>';
  try {
    const data = await api<SettingsResponse>('/api/settings');
    const sections = settingsSections.map(section => `
      <section class="settings-section">
        <h3>${esc(section.title)}</h3>
        <div class="settings-grid">${section.fields.map(field => settingField(field, data.settings[field.key])).join('')}</div>
      </section>`).join('');
    root.innerHTML = `
      <div class="settings-intro">
        Editing <code>${esc(data.config_path)}</code>. Changes are saved to this file.
        Restart the dashboard for the database URL or web host/port changes to take effect.
      </div>
      <form class="settings-form" onsubmit="saveSettings(event)">
        ${sections}
        <div class="settings-actions">
          <button class="primary" id="settings-save" type="submit">Save settings</button>
          <span class="muted">Other changes are used by the next processing run.</span>
        </div>
      </form>`;
  } catch (error) {
    root.innerHTML = `<div class="empty" style="color:var(--fail)">${esc(errorMessage(error))}</div>`;
  }
}

function settingField(field: SettingField, value: HikariSettings[SettingField['key']]): string {
  const id = `setting-${field.key}`;
  const content = field.type === 'textarea' || field.type === 'list'
    ? `<textarea id="${id}" ${field.type === 'list' ? 'data-setting-list="true"' : ''}>${esc(field.type === 'list' ? (value as string[]).join('\n') : value)}</textarea>`
    : `<input id="${id}" type="${field.type || 'text'}" value="${esc(value)}"${field.step ? ` step="${field.step}"` : ''}>`;
  return `<div class="setting-field">
    <label for="${id}">${esc(field.label)}</label>
    ${content}
    <small>${esc(field.help)}</small>
  </div>`;
}

export async function saveSettings(event: SubmitEvent): Promise<void> {
  event.preventDefault();
  const button = element<HTMLButtonElement>('settings-save');
  const settings: Record<string, unknown> = {};
  for (const section of settingsSections) {
    for (const field of section.fields) {
      const input = element<HTMLInputElement | HTMLTextAreaElement>(`setting-${field.key}`);
      if (field.type === 'number') {
        settings[field.key] = Number(input.value);
      } else if (field.type === 'list') {
        settings[field.key] = input.value.split('\n').map(s => s.trim()).filter(Boolean);
      } else if (field.key === 'cache_dir') {
        settings[field.key] = input.value.trim() || null;
      } else {
        settings[field.key] = input.value;
      }
    }
  }
  button.disabled = true;
  try {
    const result = await api<{ config_path: string }>('/api/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    });
    toast(`Settings saved to ${result.config_path}`);
  } catch (error) {
    toast(errorMessage(error), true);
  } finally {
    button.disabled = false;
  }
}
