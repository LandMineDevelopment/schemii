import { element } from '#common/dom.js';
import { modelSelect } from '#model/select.js';
import { fieldLabel } from '#model/model-columns.js';
import { timeDimensions } from './time-analysis.js';

export function renderTimeAnalysis(tile, model, catalog, onChange) {
  const host = element('section', { className: 'time-analysis-settings' });
  const dimensions = timeDimensions(tile, model, catalog);
  const enabled = element('input', { attrs: { type: 'checkbox', 'aria-label': 'Group by time' } });
  enabled.checked = Boolean(tile.timeAnalysis);
  enabled.disabled = !dimensions.length && !tile.timeAnalysis;
  enabled.onchange = () => {
    tile.timeAnalysis = enabled.checked ? { table: dimensions[0].table, column: dimensions[0].column, granularity: 'month', timezone: 'UTC', weekStart: 'monday', comparison: 'none', runningTotal: false } : null;
    onChange();
  };
  host.append(element('label', { className: 'time-toggle' }, [enabled, 'Group by time']));
  if (!dimensions.length) host.append(element('p', { className: 'hint', text: 'Add a date or timestamp dimension in View & fields to enable time analysis.' }));
  const time = tile.timeAnalysis;
  if (!time) return host;
  const controls = element('div', { className: 'time-analysis-controls' });
  const selectedIndex = dimensions.findIndex(field => field.table === time.table && field.column === time.column);
  controls.append(modelSelect('Time dimension', dimensions.map((field, index) => [String(index), fieldLabel(model.definition, catalog, field)]), selectedIndex < 0 ? '' : String(selectedIndex), value => {
    const field = dimensions[Number(value)]; if (field) { time.table = field.table; time.column = field.column; }
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

