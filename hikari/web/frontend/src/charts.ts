import Chart from 'chart.js/auto';
import { element } from './helpers';
import type { Observation } from './types';

let ensembleChart: Chart | null = null;

export function renderEnsembleChart(o: Observation) {
  if (ensembleChart) { ensembleChart.destroy(); ensembleChart = null; }
  const canvas = element<HTMLCanvasElement>('ensemble-chart');
  const extra  = o.extra || {};
  const points = extra.ensemble_points || [];
  const targetInstMag = extra.target_instrumental_mag;

  if (!points.length) {
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('Unable to access the ensemble chart canvas.');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = '#8b949e'; ctx.font = '12px sans-serif'; ctx.textAlign = 'center';
    ctx.fillText('No ensemble data', canvas.width / 2, canvas.height / 2);
    return;
  }

  const compData = points.map(p => ({ x: p.instrumental_mag, y: p.published_mag }));
  const xVals = points.map(p => p.instrumental_mag);
  const xMin = Math.min(...xVals) - 0.2;
  const xMax = Math.max(...xVals) + 0.2;
  const fitData = [];
  if (o.fit_slope != null && o.fit_intercept != null) {
    fitData.push({ x: xMin, y: o.fit_slope * xMin + o.fit_intercept });
    fitData.push({ x: xMax, y: o.fit_slope * xMax + o.fit_intercept });
  }
  const targetData = [];
  if (targetInstMag != null && o.magnitude != null) {
    targetData.push({ x: targetInstMag, y: o.magnitude });
  }

  ensembleChart = new Chart(canvas, {
    type: 'scatter',
    data: {
      datasets: [
        { label: 'Comp stars', data: compData, backgroundColor: '#58a6ff', pointRadius: 5 },
        { label: 'Target',     data: targetData, backgroundColor: '#f85149', pointStyle: 'triangle', pointRadius: 8 },
        { label: 'Fit', data: fitData, type: 'line', borderColor: '#8b949e', borderDash: [5,3],
          borderWidth: 1.5, pointRadius: 0, showLine: true },
      ],
    },
    options: {
      animation: false,
      plugins: { legend: { labels: { color: '#c9d1d9', font: { size: 11 } } } },
      scales: {
        x: { title: { display:true, text:'Instrumental mag', color:'#8b949e', font:{size:11} },
             ticks:{color:'#8b949e'}, grid:{color:'#30363d'} },
        y: { title: { display:true, text:'Published mag',    color:'#8b949e', font:{size:11} },
             ticks:{color:'#8b949e'}, grid:{color:'#30363d'}, reverse:true },
      },
    },
  });
}

export function renderCutout(o: Observation) {
  const img = element<HTMLImageElement>('cutout-img');
  const ph  = element('cutout-placeholder');
  img.style.display = 'none'; ph.textContent = 'Loading…'; ph.style.display = 'block';
  img.onload  = () => { img.style.display = 'block'; ph.style.display = 'none'; };
  img.onerror = () => { ph.textContent = 'No cutout available'; };
  img.src = `/api/observations/${o.id}/cutout`;
}



export function destroyEnsembleChart(): void {
  ensembleChart?.destroy();
  ensembleChart = null;
}
