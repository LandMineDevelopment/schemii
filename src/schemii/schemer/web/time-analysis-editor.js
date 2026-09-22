import { element } from '#common/dom.js';
import { modelSelect } from '#model/select.js';
import { fieldLabel } from '#model/model-columns.js';
import { availableTimeDimensions, selectTimeDimension, timeDimensions } from './time-analysis.js';

export function renderTimeAnalysis(tile, model, catalog, onChange) {
  const host = element('section', { className: 'time-analysis-settings' });
  const selectedDimensions = timeDimensions(tile, model, catalog);
  const dimensions = availableTimeDimensions(model, catalog);
  const enabled = element('input', { attrs: { type: 'checkbox', 'aria-label': 'Group by time' } });
  enabled.checked = Boolean(tile.timeAnalysis);
  enabled.disabled = !dimensions.length && !tile.timeAnalysis;
  enabled.onchange = () => {
    if (enabled.checked) {
      const field = selectedDimensions[0] || dimensions[0];
      tile.timeAnalysis = { table: field.table, column: field.column, granularity: 'month', timezone: 'UTC', weekStart: 'monday', comparison: 'none', runningTotal: false };
      selectTimeDimension(tile, field);
    } else tile.timeAnalysis = null;
    onChange();
  };
  host.append(element('label', { className: 'time-toggle' }, [enabled, 'Group by time']));
  if (!dimensions.length) host.append(element('p', { className: 'hint', text: 'This model does not expose a date or timestamp field for time analysis.' }));
  const time = tile.timeAnalysis;
  if (!time) return host;
  const controls = element('div', { className: 'time-analysis-controls' });
  const selectedIndex = dimensions.findIndex(field => field.table === time.table && field.column === time.column);
  controls.append(modelSelect('Time dimension', dimensions.map((field, index) => [String(index), fieldLabel(model.definition, catalog, field)]), selectedIndex < 0 ? '' : String(selectedIndex), value => {
    const field = dimensions[Number(value)]; if (field) { selectTimeDimension(tile, field); onChange(); }
  }));
  controls.append(modelSelect('Time grouping', [['day', 'Day'], ['week', 'Week'], ['month', 'Month']], time.granularity, value => { time.granularity = value; onChange(); }));
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
