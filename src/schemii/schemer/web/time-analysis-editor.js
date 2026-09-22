import { element } from '#common/dom.js';
import { modelSelect } from '#model/select.js';
export function renderTimeAnalysis(tile) {
  const host = element('details', { className: 'time-analysis-settings' });
  host.append(element('summary', { text: 'Date grouping options' }));
  const time = tile.timeAnalysis;
  const controls = element('div', { className: 'time-analysis-controls' });
  const timezone = element('input', { attrs: { 'aria-label': 'Time zone', placeholder: 'America/New_York', maxlength: 100, spellcheck: 'false' } });
  timezone.value = time.timezone; timezone.oninput = () => { time.timezone = timezone.value.trim(); };
  controls.append(element('label', { className: 'stack' }, ['Time zone', timezone]));
  if (time.granularity === 'week') controls.append(modelSelect('Week starts on', [['monday', 'Monday'], ['sunday', 'Sunday']], time.weekStart, value => { time.weekStart = value; }));
  controls.append(modelSelect('Compare with', [['none', 'No comparison'], ['previous_period', 'Previous period'], ['prior_year', 'Prior year']], time.comparison, value => { time.comparison = value; }));
  const running = element('input', { attrs: { type: 'checkbox', 'aria-label': 'Running totals' } });
  running.checked = time.runningTotal; running.onchange = () => { time.runningTotal = running.checked; };
  controls.append(element('label', { className: 'time-toggle' }, [running, 'Running totals']));
  host.append(controls,
    element('p', { className: 'hint', text: 'Time zones apply to timestamps with a zone. Dates and timestamps without a zone keep their calendar values. Rows without a date are excluded.' }),
    element('p', { className: 'hint', text: 'Comparisons use the same filters and exact calendar periods. Missing periods stay absent; missing comparisons and percentage changes from zero are shown as NULL. Partial periods use available data without prorating.' }),
    element('p', { className: 'hint', text: 'Running totals add each period’s measure, oldest first, separately for each combination of other dimensions. All calculations use the full filtered result, including periods outside the preview.' }));
  return host;
}
