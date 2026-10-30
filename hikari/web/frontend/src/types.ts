export interface RunProgress {
  total?: number;
  completed?: number;
  current_group?: string | null;
}

export interface Run {
  id: number;
  source_path: string;
  status: string;
  created_at: string;
  completed_at?: string | null;
  progress?: RunProgress | null;
  n_jobs: number;
  n_observations: number;
  notes?: string | null;
}

export interface Job {
  id: number;
  object_name: string;
  filter: string;
  status: string;
  n_observations: number;
  n_failed: number;
  n_rejected?: number;
}

export interface EnsemblePoint {
  instrumental_mag: number;
  published_mag: number;
}

export interface ObservationExtra {
  ensemble_points?: EnsemblePoint[];
  target_instrumental_mag?: number | null;
}

export interface Observation {
  id: number;
  job_id: number;
  file_name: string;
  star_name: string;
  julian_date: number;
  magnitude: number | null;
  magnitude_error: number | null;
  filter: string;
  airmass: number | null;
  fit_rms: number | null;
  fit_slope?: number | null;
  fit_intercept?: number | null;
  n_ensemble_stars: number | null;
  check_star_auid: string | null;
  check_star_magnitude: number | null;
  check_star_published_magnitude: number | null;
  status: string;
  rejected: boolean;
  rejection_reason: string | null;
  extra?: ObservationExtra | null;
}

export interface HikariSettings {
  obs_code: string;
  observer_notes: string;
  aperture_radius: number;
  sky_inner_radius: number;
  sky_outer_radius: number;
  fwhm: number;
  source_snr: number;
  ensemble_size: number;
  linear_limit: number;
  brightest_comp_star: number;
  dimmest_comp_star: number;
  field_of_view: number;
  excluded_auids: string[];
  output_dir: string;
  database_url: string;
  web_host: string;
  web_port: number;
  cache_dir: string | null;
}

export interface SettingsResponse {
  config_path: string;
  settings: HikariSettings;
}

export interface BrowseEntry {
  name: string;
  path: string;
}

export interface BrowseResponse {
  path: string;
  parent: string | null;
  entries: BrowseEntry[];
}

export interface ClearResponse {
  entries_deleted?: number;
  jobs_deleted?: number;
}

export interface ApiErrorResponse {
  detail?: string | { msg?: string }[];
}
