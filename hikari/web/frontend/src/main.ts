import {
  acceptObs,
  clearAllData,
  clearCache,
  deleteRun,
  downloadJobCsv,
  downloadRunCsv,
  initializeDashboard,
  lastFolderKey,
  loadRuns,
  navigateObservation,
  rejectObs,
  saveRunNotes,
  selectRun,
  showRuns,
  showSettings,
  startRun,
  toggleJob,
} from './dashboard';
import { closeDirPicker, confirmDirPicker, openDirPicker } from './directory-picker';
import { saveSettings } from './settings';

Object.assign(window, {
  acceptObs,
  clearAllData,
  clearCache,
  closeDirPicker,
  confirmDirPicker,
  deleteRun,
  downloadJobCsv,
  downloadRunCsv,
  lastFolderKey,
  loadRuns,
  navigateObservation,
  openDirPicker,
  rejectObs,
  saveRunNotes,
  saveSettings,
  selectRun,
  showRuns,
  showSettings,
  startRun,
  toggleJob,
});

initializeDashboard();
