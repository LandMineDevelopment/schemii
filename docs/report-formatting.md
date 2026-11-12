# Report number formats

A tile's **Number formats** tab binds each format to its saved semantic output
(table, column, aggregation and output kind). Reordering measures or changing a
label does not move its format. Base fields, time outputs, calculated measures,
KPI targets/variances and pivot measures have independent identities. Pivot totals
use the same measure format at every grouping grain. A removed, hidden or changed
numeric source stays in the repair draft until the author rebinds or removes it.

Numbers use the viewer's browser locale by default. An author can override it for
this tile with one of the supported report number locales: en-US, en-GB, de-DE,
fr-FR, es-ES, pt-BR, ja-JP, zh-CN, hi-IN or ar-EG. The override applies to numeric
presentation; it does not change source calendar grouping or database collation.
Currency codes identify the unit, without conversion or exchange-rate lookup.
Decimal places range from 0 to 12. Blank uses the currency's normal precision,
or at most three fraction digits for other numbers. Unit suffixes are plain text.
Existing tiles need no configuration migration and keep their raw query values.

Percentage input is explicit. **Fraction** treats 0.25 as 25%; **already 0–100**
treats 25 as 25%. **Automatic** uses complete-query output semantics: calculated
Percent of, time percentage changes and KPI percentage variance already use
0–100 values. Ordinary numeric fields use fractions when displayed as percent.
A plain ratio is a number until an author chooses percentage presentation.
KPI absolute variance in a percentage measure is percentage points, not percent
change. Targets and absolute variances inherit the primary display style unless
configured separately; conditional rules do not silently inherit.

Rules are ordered, with the first matching rule winning. They compare exact raw
numeric values before percentage display scaling or rounding. Between is
inclusive. NULL is distinct from zero; NULL rules have no thresholds. Every rule
has a readable label and a symbol in addition to its fixed tone. Authors can move
rules using keyboard-operable controls. Thresholds use a dot decimal separator without locale grouping, and remain exact decimal text,
including large integers, negative values and small exponents; invalid drafts are
retained for correction. There are at most 64 formats and eight rules per output.

Formatting uses modern browser Intl decimal-string formatting, without converting
exact SQL numeric text through a JavaScript Number. Tiny nonzero values that round
to zero show an approximation marker, with an accessible explanation. Extreme
values use bounded scientific text; invalid or unsupported magnitude values have
an explicit display warning and a bounded raw excerpt. The full raw value remains
in the existing result cache and cell viewer. Only visible grid windows are
decorated; no duplicate formatted-row cache is kept. Intl instances have a 64-entry
LRU bound. Chart geometry necessarily uses finite floating-point coordinates;
labels and thresholds still use original raw values. Charts reject values outside
finite geometry, while tables and raw CSV can retain larger exact SQL numerics.

Both cached and full CSV controls are labelled **RAW CSV**. They exclude display
formats, locale separators, currency/percent symbols and conditional indicators.
Cached CSV contains the rows already received. Full CSV runs the complete saved
query at a fresh snapshot, retaining report limits and explicit pivot/contributor
scope. SQL bigint/integer/numeric scalar values outside JavaScript's safe integer
range are transported as exact text; Decimal values already use exact text. Safe
integers remain JSON numbers. JSON/JSONB scalars, JSON objects, SQL arrays and
unknown loaders retain their existing type/number semantics; this change does not
promise arbitrary-precision nested JSON numbers or reinterpret custom loaders.
Text, JSON and array CSV cells retain spreadsheet-formula escaping: an apostrophe is added to formula-like text. Only complete finite numeric tokens under actual SQL numeric column metadata bypass that escaping, so raw numeric precision is retained without claiming byte-identical escaped text.
Raw rows remain the inputs to sorting, calculations, grouping and drill selection.
