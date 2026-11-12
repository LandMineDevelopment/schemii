"""Compile dashboard tiles and their contributing rows from saved model rules.

Drill projections retain the original participating paths. Added detail branches
are LEFT joins; they cannot activate previously inactive conditional scopes or
change a required scope's correlation. They can repeat contributors, which is
reported explicitly rather than implying a drill row count equals a measure.
"""

import re

from sqlglot import exp, parse_one

from schemii.common.api.errors import ApiProblem
from schemii.schemoo.domain import source_identity
from schemii.schemoo.models import ExploreState
from schemii.schemoo.service import document, load_model, model_catalog, plan_query
from schemii.schemoo.prototype import _literal
from .models import ReportQuery
from .time_analysis import bucket_expression, compile_time_analysis, validate_time_analysis, source_type
from .dashboard_models import ReportOutput, tile_dimensions, base_measures
from .measures import MeasureGraph, output_definitions, compile_measures, population_queries, source_population_eligibility


def _problem(message):
    raise ApiProblem(422, "invalid_tile_drill", message)


def _key(field):
    return field.table, field.column


def _output_key(field):
    return field.table, field.column, field.aggregate, getattr(field, "kind", "value")


def report_outputs(tile):
    return output_definitions(tile)


def validate_report_controls(tile, catalog, definition):
    """Resolve selected semantic outputs and typed values, without slicer values."""
    from datetime import date, datetime, time
    from uuid import UUID

    outputs = report_outputs(tile)
    positions = {_output_key(output): index for index, output in enumerate(outputs)}
    measure_keys = {_key(field) + (field.aggregate,) for field in base_measures(tile)}
    filters, ordering = [], []
    for sort in getattr(tile, "sort", []):
        if _output_key(sort) not in positions:
            raise ApiProblem(422, "invalid_report_output", "A sort output is no longer selected. Repair it in the tile editor.")
        ordering.append((positions[_output_key(sort)], sort.direction, sort.nulls))
    for predicate in getattr(tile, "aggregate_filters", []):
        if _output_key(predicate) not in positions or (predicate.kind != "calculated" and (*_key(predicate), predicate.aggregate) not in measure_keys):
            raise ApiProblem(422, "invalid_report_output", "Aggregate filters must reference selected measures or their enabled time-analysis outputs.")
        if predicate.kind in {"change", "percent_change", "running_total", "calculated"} or predicate.aggregate in {"count", "count_distinct", "sum", "avg"}:
            kind = "numeric"
        else:
            kind = source_type(catalog, definition, predicate.table, predicate.column)
        kind = re.sub(r"\(\d+\)", "", kind).strip().lower()
        value = predicate.value
        if predicate.operator not in {"is_null", "not_null"}:
            numeric = kind.split("(")[0].strip() in {"numeric", "decimal", "integer", "bigint", "smallint", "real", "double precision", "int2", "int4", "int8", "float4", "float8"}
            valid = False
            if numeric:
                valid = type(value) in {int, float}
            elif kind == "boolean":
                valid = type(value) is bool and predicate.operator in {"eq", "ne"}
            elif kind in {"text", "varchar", "character varying", "char", "character"}:
                valid = isinstance(value, str)
            elif isinstance(value, str):
                try:
                    if kind == "date":
                        date.fromisoformat(value)
                    elif kind.startswith("timestamp"):
                        datetime.fromisoformat(value)
                    elif kind in {"time", "time without time zone", "time with time zone", "timetz"}:
                        match = re.fullmatch(r"([0-9]{2})(:?)([0-9]{2})(?:\2([0-9]{2})(?:[.,]([0-9]{1,6}))?)?([Zz]|[+-](?:[0-9]{2}(?::[0-9]{2}(?::[0-9]{2})?)?|[0-9]{4}(?:[0-9]{2})?))?", value)
                        if match is None:
                            raise ValueError
                        hour, _, minute, second, fraction, zone = match.groups()
                        # PostgreSQL also represents midnight as 24:00:00.
                        if hour == "24" and any(int(part or "0") for part in (minute, second, fraction)):
                            raise ValueError
                        if zone and zone.lower() != "z":
                            parts = zone[1:].replace(":", "")
                            if int(parts[:2]) > 15 or int(parts[2:4] or "0") > 59 or int(parts[4:6] or "0") > 59:
                                raise ValueError
                        parsed = time.fromisoformat(("00" + value[2:] if hour == "24" else value).replace("z", "Z"))
                        offset = parsed.utcoffset()
                        if (offset is not None) != (kind in {"time with time zone", "timetz"}):
                            raise ValueError
                        if offset is not None and abs(offset.total_seconds()) >= 16 * 3600:
                            raise ValueError
                    elif kind == "uuid" and predicate.operator in {"eq", "ne"}:
                        UUID(value)
                    else:
                        raise ValueError
                    valid = True
                except ValueError:
                    pass
            if not valid:
                raise ApiProblem(422, "invalid_aggregate_filter", f"Choose a {kind} value and compatible operator for this aggregate output.")
        filters.append((positions[_output_key(predicate)], predicate))
    return outputs, ordering, filters


class DashboardPlanning:
    """One lazy model/catalog projection, owned only by a dashboard request.

    Managed privileges stay uncached across requests. Execution and delivery
    retain their independent current-authority checks through report services.
    """

    def __init__(self, services, owner, dashboard):
        self.services, self.owner, self.dashboard = services, owner, dashboard
        self.model = self.catalog = self.catalog_error = None

    def load_model(self):
        if self.model is None:
            self.model = load_model(self.services, self.owner, self.dashboard.model_id,
                                    self.dashboard.model_revision)
        return self.model

    def model_catalog(self):
        if self.catalog_error is not None:
            raise self.catalog_error
        if self.catalog is None:
            try:
                self.catalog = model_catalog(self.services, self.owner, self.load_model(), fresh=True)
            except ApiProblem as error:
                # A shared projection failure belongs to each affected tile;
                # keep the bounded partial-error behavior without reacquisition.
                if error.status_code == 422:
                    self.catalog_error = error
                raise
        return self.catalog

    def check_current(self):
        if self.model is None:
            return
        current = load_model(self.services, self.owner, self.dashboard.model_id,
                             self.dashboard.model_revision)
        identity = lambda model: (model.connection_id, model.connection_owner_id,
                                  model.database, model.namespace)
        if identity(current) != identity(self.model):
            raise ApiProblem(409, "model_source_changed", "The model's source changed. Reload the report.")
        if self.catalog is not None:
            profile = self.services.connections.get(self.owner, current.connection_id)
            if (profile.revision != self.catalog["connectionRevision"]
                    or profile.database != self.catalog["database"]):
                raise ApiProblem(409, "report_connection_changed", "The report connection changed. Reload the report.")


def tile_report_query(dashboard, tile, selections=None):
    fields = tile.detail_fields if tile.kind == "detail" else [*tile_dimensions(tile), *base_measures(tile)]
    return ReportQuery(model_id=dashboard.model_id, expected_revision=dashboard.model_revision,
                       explore=ExploreState(fields=fields, selections=dashboard.selections if selections is None else selections,
                                            reportFilters=tile.report_filters, limit=tile.limit))


def _text_order_fields(catalog, definition, fields):
    """Only coerce known PostgreSQL types lacking ordinary ordering operators."""
    nodes = {node.id: node for node in definition.nodes}
    tables = {source_identity(catalog, table): table for table in catalog["tables"]}

    def needs_text(table, column, aggregate="none"):
        if aggregate not in {"none", "min", "max"}:
            return False
        node = nodes[table]
        if node.derivation:
            output = next((item for item in node.derivation.outputs if item.id == column), None)
            if output:
                return output.operation in {"min", "max"} and needs_text(output.nodeId or node.derivation.source, output.column)
            return needs_text(node.derivation.source, column)
        details = next((item for item in tables[source_identity(catalog, node.model_dump(exclude_none=True))]["columns"] if item["name"] == column), {})
        kind = details.get("dataType", details.get("data_type", "")).lower().strip()
        return kind.removesuffix("[]") in {"json", "xml", "point", "line", "lseg", "box", "path", "polygon", "circle"}

    return {index for index, field in enumerate(fields) if needs_text(field.table, field.column, field.aggregate)}


def _ordered(plan, sql, page_size, text_order_fields=(), *, ordering=(), filters=(), top_n=None, visible_indices=None):
    # One retained execution supplies forward-only batches. Sorting happens
    # once; subsequent result requests advance its cursor without rerunning SQL.
    # Keep numeric/date dimensions in their native order (not 1, 10, 2).
    # JSON and geometric values without ordering operators use their text form.
    output_labels = [output.alias_or_name for output in sql.expressions]
    # PostgreSQL silently truncates identifiers to 63 bytes. Author-facing
    # labels can therefore collide after truncation (especially when a model
    # has a descriptive source name and several similarly named fields). Use
    # compact, deterministic SQL aliases for the nested result and retain the
    # complete labels as response metadata for the dashboard UI.
    aliases = [f"output_{index + 1}" for index in range(len(output_labels))]
    sql.set("expressions", [
        (output.this if isinstance(output, exp.Alias) else output).as_(alias, quoted=True)
        for output, alias in zip(sql.expressions, aliases)
    ])
    sql = exp.select(*(exp.column(alias, table="page_rows", quoted=True) for alias in aliases)).from_(sql.subquery("page_rows"))
    predicates = []
    operators = {"eq": exp.EQ, "ne": exp.NEQ, "gt": exp.GT, "gte": exp.GTE, "lt": exp.LT, "lte": exp.LTE}
    for index, predicate in filters:
        column = exp.column(aliases[index], table="page_rows", quoted=True)
        if predicate.operator in {"is_null", "not_null"}:
            condition = exp.Is(this=column, expression=exp.Null())
            if predicate.operator == "not_null":
                condition = exp.Not(this=condition)
        else:
            condition = operators[predicate.operator](this=column, expression=parse_one(_literal(predicate.value), read="postgres"))
        predicates.append(condition)
    if predicates:
        sql = sql.where(*predicates)
    specified = {index for index, _, _ in ordering}
    complete_order = [*ordering, *((index, "asc", "last") for index in range(len(aliases)) if index not in specified)]
    ordered = []
    for index, direction, nulls in complete_order:
        alias = aliases[index]
        expression = exp.column(alias, table="page_rows", quoted=True)
        if index in text_order_fields:
            expression = exp.Cast(this=expression, to=exp.DataType.build("text"))
        ordered.append(exp.Ordered(this=expression, desc=direction == "desc", nulls_first=nulls == "first"))
    sql = sql.order_by(*ordered)
    if top_n is not None:
        sql = sql.limit(top_n)
    projected_metadata={}
    if visible_indices is not None:
        if "computedOutputs" in plan:
            projected_metadata["computedOutputs"]=[{**item,"outputIndex":visible_indices.index(item["outputIndex"])} for item in plan["computedOutputs"] if item["outputIndex"] in visible_indices]
        if "reportOutputIdentities" in plan:
            projected_metadata["reportOutputIdentities"]=[plan["reportOutputIdentities"][index] for index in visible_indices if index<len(plan["reportOutputIdentities"])]
        sql = exp.select(*(exp.column(aliases[index],table="visible_rows",quoted=True) for index in visible_indices)).from_(sql.subquery("visible_rows"))
        output_labels = [output_labels[index] for index in visible_indices]
    return {**plan, **projected_metadata, "sql": sql.sql(dialect="postgres") + ";", "rowLimit": page_size,
            "outputLabels": output_labels}


def tile_plan(services, owner, dashboard, tile_id, *, selections=None, selection=None, fresh=False,
              planning=None, exporting=False):
    """Return (saved model, unbounded SQL plan) for one retained execution.

    selection is {dimensions: [{table, column, value}], measureIndex: int}.
    No SQL, root overrides, filter operators or arbitrary fields are accepted.
    """
    tile = next((item for item in dashboard.tiles if item.id == tile_id), None)
    if tile is None:
        raise ApiProblem(404, "tile_not_found", "This dashboard tile no longer exists.")
    query = tile_report_query(dashboard, tile, selections)
    if planning is not None and (planning.services is not services or planning.owner != owner
                                 or planning.dashboard is not dashboard):
        raise ValueError("Dashboard planning must belong to this request")
    model = planning.load_model() if planning is not None else load_model(
        services, owner, query.model_id, query.expected_revision)
    query.explore.root = model.definition.root
    optional_selections = getattr(tile, "selections", {})
    required = {scope.id for scope in model.definition.scopes if scope.kind == "required"}
    if required & optional_selections.keys():
        _problem("Required model slicers belong to the whole dashboard and cannot be overridden by a tile.")
    configured = set(getattr(dashboard, "optional_filters", []))
    unauthorized = {scope.id for scope in model.definition.scopes
                    if scope.requirement == "optional" and scope.id not in configured}
    if any(query.explore.selections.get(scope_id, None) and query.explore.selections[scope_id].active
           for scope_id in unauthorized):
        _problem("This optional model filter is not available on the dashboard.")
    query.explore.selections = {**query.explore.selections, **optional_selections}
    catalog = planning.model_catalog() if planning is not None else model_catalog(services, owner, model, fresh=fresh)
    graph = MeasureGraph(tile,catalog,model.definition) if (getattr(tile,"calculations",[]) or getattr(tile,"hidden_measures",[]) or getattr(tile,"kpi_comparison",None)) else None
    original = plan_query(catalog, model.definition, query.explore, model.catalog_fingerprint, _bounded=False)
    if graph:
        graph.validate_grain(original)
    if graph and any(field.table in {node.id for node in model.definition.nodes if node.derivation and node.derivation.kind=="aggregate"} for field in base_measures(tile)):
        original["warnings"]=[*original.get("warnings",[]),"Population calculations over model summaries aggregate per-owner semantic values, not original raw child records."]
    time_analysis = getattr(tile, "time_analysis", None)
    dimensions = tile_dimensions(tile)
    time_tile = tile.model_copy(update={"dimensions": dimensions,"measures":base_measures(tile)}) if tile.kind == "pivot" or graph else tile
    time_kind = validate_time_analysis(time_tile, catalog, model.definition)
    _, ordering, filters = validate_report_controls(tile, catalog, model.definition)
    parent_statement = parse_one(original["sql"], read="postgres")
    if time_analysis:
        parent_statement, metadata = compile_time_analysis(time_tile, parent_statement, catalog, model.definition)
        metadata["warnings"] = [*original.get("warnings", []), *metadata["warnings"]]
        original.update(metadata)
    visible_indices = None
    if graph:
        populations=population_queries(graph,catalog,model,tile,query,original)
        parent_statement,metadata=compile_measures(graph,parent_statement,populations=populations,qualification_filters=filters)
        original.update(metadata)
        original["reportOutputIdentities"]=[document(output) for output in graph.outputs]
        visible_indices=graph.visible_indices()
        if tile.kpi_comparison:
            visible_indices += list(range(len(graph.outputs),len(graph.outputs)+4))
    parent_plan = _ordered(original, parent_statement, tile.limit,
                          _text_order_fields(catalog, model.definition, query.explore.fields),
                          ordering=ordering, filters=filters, top_n=getattr(tile, "top_n", None),
                          visible_indices=None if tile.kind=="pivot" else visible_indices)
    if hasattr(selection,"model_dump"):
        selection=selection.model_dump(mode="json",by_alias=True,exclude_none=True)
    constituent = selection.get("constituent") if isinstance(selection,dict) else None
    if constituent is not None:
        selection={key:value for key,value in selection.items() if key!="constituent"}
    if tile.kind == "pivot":
        from .pivot import compile_pivot, pivot_selection
        qualifying = _ordered(original, parent_statement.copy(), tile.limit,
            _text_order_fields(catalog, model.definition, query.explore.fields),
            ordering=ordering, filters=filters)
        if selection is None:
            if not graph:
                return model, compile_pivot(catalog, model, tile, query, original, qualifying, parent_plan, exporting=exporting)
            from .pivot import META_LABELS
            internal_tile=tile.model_copy(update={"measures":base_measures(tile)})
            count=len(dimensions)+len(base_measures(tile))
            pivot_plan=compile_pivot(catalog,model,internal_tile,query,original,
                {**qualifying,"outputLabels":qualifying["outputLabels"][:count]},
                {**parent_plan,"outputLabels":parent_plan["outputLabels"][:count]},exporting=exporting)
            source=parse_one(pivot_plan["sql"],read="postgres").subquery("pivot_values")
            names=[*(f"output_{index+1}" for index in range(count)),"pivot_row_depth","pivot_column_depth","pivot_status","pivot_column_count","pivot_rows_omitted","pivot_qualifying_groups","pivot_scope"]
            labels=[*pivot_plan["outputLabels"][:count],*META_LABELS]
            statement=exp.select(*(exp.column(name,table="pivot_values",quoted=True).as_(label,quoted=True) for name,label in zip(names,labels))).from_(source)
            populations=population_queries(graph,catalog,model,tile,query,original,qualifying=qualifying)
            statement,metadata=compile_measures(graph,statement,populations=populations)
            pivot_plan.update(metadata)
            pivot_plan["pivot"].update(measureCount=len(graph.visible_indices())-len(dimensions),metadataOffset=len(graph.visible_indices()))
            projection=[*graph.visible_indices(),*range(len(graph.outputs),len(graph.outputs)+len(META_LABELS))]
            return model,_ordered(pivot_plan,statement,tile.limit,visible_indices=projection)
        selection, scope = pivot_selection(tile, selection)
        if scope != "cell":
            parent_plan = qualifying
    if selection is None:
        return model, parent_plan
    if hasattr(selection, "model_dump"):
        selection = selection.model_dump(mode="json", by_alias=True)
    if tile.kind == "detail" or not tile.detail_fields:
        _problem("Choose drill-through detail columns in the tile editor first.")
    pivot_depths = {key: selection.pop(key) for key in ("rowDepth", "columnDepth")} if tile.kind == "pivot" else {}
    if not isinstance(selection, dict) or set(selection) != {"dimensions", "measureIndex"}:
        _problem("Select the dimensions and measure of a tile result.")
    measure_index = selection["measureIndex"]
    visible_calculations=[item for item in getattr(tile,"calculations",[]) if item.visible]
    if type(measure_index) is not int or not 0 <= measure_index < len(tile.measures)+len(visible_calculations):
        _problem("The selected measure does not belong to this tile.")
    calculation_scope=None
    if measure_index >= len(tile.measures):
        from .dashboard_models import MeasureOperand
        from .measures import key as measure_key
        item=visible_calculations[measure_index-len(tile.measures)]
        if constituent is None:
            _problem("Choose which calculation constituent and population to inspect; a composite measure has no single detail set.")
        chosen=MeasureOperand.model_validate(constituent)
        allowed=graph.constituents[item.id]
        if chosen.output is None or not any(measure_key(MeasureOperand.model_validate(value).output)==measure_key(chosen.output) and value["scope"]==chosen.scope for value in allowed):
            _problem("That constituent does not belong to the saved calculation. Reopen its records chooser.")
        if chosen.output.kind != "value":
            _problem("A calendar window constituent combines periods. Inspect its base period measure instead.")
        measure=chosen.output
        population_scope=chosen.scope
        calculation_scope={"id":item.id,"label":item.label,"constituent":chosen.model_dump(exclude_none=True),"population":{
            "group":"selected qualifying group","source_population":"source-filtered report population before result filters and Top N",
            "qualifying_groups":"complete qualifying groups before Top N"}[population_scope]}
    else:
        if constituent is not None:
            _problem("Constituent selections belong only to calculated measures.")
        measure=tile.measures[measure_index]
        population_scope="group"
    values = selection["dimensions"]
    expected_dimensions = dimensions if tile.kind != "pivot" else [
        *tile.row_dimensions[:pivot_depths["rowDepth"]],
        *tile.column_dimensions[:pivot_depths["columnDepth"]]]
    if not isinstance(values, list) or len(values) != len(expected_dimensions):
        _problem("Supply exactly the dimensions displayed by this tile.")
    selected = {}
    for item in values:
        if not isinstance(item, dict) or set(item) != {"table", "column", "value"}:
            _problem("A dimension selection needs its table, column and value.")
        if not isinstance(item["table"], str) or not isinstance(item["column"], str):
            _problem("A selected dimension must name a saved table and column.")
        key = item["table"], item["column"]
        if key in selected or key not in {_key(field) for field in expected_dimensions}:
            _problem("The selected dimensions do not match this tile.")
        value = item["value"]
        if value is not None and not isinstance(value, (str, int, float, bool)):
            _problem("Select one scalar value for each dimension.")
        selected[key] = value
    if calculation_scope:
        calculation_scope["selectedDimensions"]=values
        calculation_scope["measureIndex"]=measure_index
        if tile.kind=="pivot":
            calculation_scope["pivot"]={"kind":scope,**pivot_depths}
    raw_fields = []
    for field in [*query.explore.fields, *tile.detail_fields]:
        if _key(field) not in {_key(existing) for existing in raw_fields}:
            raw_fields.append(field.model_copy(update={"aggregate": "none"}))
    if len(raw_fields) > 64:
        _problem("Tile and detail columns together must use at most 64 source fields.")
    raw = query.explore.model_copy(update={"fields": raw_fields})
    plan = plan_query(catalog, model.definition, raw, model.catalog_fingerprint, _bounded=False,
                      _participation_fields=[document(field) for field in query.explore.fields],
                      _scope_outer_nodes=original["outerNodes"])
    statement = parse_one(plan["sql"], read="postgres")
    labels = {_key(field): output.alias for field, output in zip(raw_fields, statement.expressions)}
    # The contributor relation is another nesting boundary: display labels may
    # exceed PostgreSQL's 63-byte identifier limit or collide after truncation.
    # Keep them as presentation metadata and reference compact SQL aliases.
    aliases = {_key(field): f"field_{index + 1}" for index, field in enumerate(raw_fields)}
    statement.set("expressions", [
        output.this.as_(aliases[_key(field)], quoted=True)
        for field, output in zip(raw_fields, statement.expressions)
    ])
    column = lambda key: exp.column(aliases[key], table="contributors", quoted=True)
    conditions, parent_conditions = [], []
    for key, value in selected.items():
        if value is None:
            if population_scope=="group":
                conditions.append(exp.Is(this=column(key), expression=exp.Null()))
            literal = exp.Null()
        else:
            try:
                literal = parse_one(_literal(value), read="postgres")
            except (ValueError, TypeError):
                _problem("The selected dimension has an invalid value.")
            expression = column(key)
            if time_analysis and key == (time_analysis.table, time_analysis.column):
                expression = bucket_expression(expression, time_analysis, time_kind)
                # Only canonical bucket dates from the displayed results are accepted.
                from datetime import date
                try:
                    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
                        raise ValueError
                except ValueError:
                    _problem("Select a valid calendar bucket date.")
                literal = exp.Cast(this=literal, to=exp.DataType.build("date"))
            if population_scope=="group":
                conditions.append(exp.EQ(this=expression, expression=literal))
        index = next(index for index, field in enumerate(dimensions) if _key(field) == key)
        parent_conditions.append(exp.NullSafeEQ(this=exp.column(f"output_{index + 1}", table="qualifying_parent", quoted=True), expression=literal.copy()))
    if tile.kind == "pivot" and population_scope=="group":
        # Every contributing leaf group must qualify, even within a subtotal.
        # Correlating only the clicked prefix would admit filtered-out cells.
        parent_conditions = []
        for index, field in enumerate(dimensions):
            expression = column(_key(field))
            if time_analysis and _key(field) == (time_analysis.table, time_analysis.column):
                expression = bucket_expression(expression, time_analysis, time_kind)
            parent_conditions.append(exp.NullSafeEQ(
                this=exp.column(f"output_{index + 1}", table="qualifying_parent", quoted=True),
                expression=expression))
    # Qualify against the complete final parent relation. Never push a selected
    # group into that relation before its windows, filters, ranking and top-N.
    parent = exp.select(exp.Literal.number(1)).from_(parse_one(parent_plan["sql"], read="postgres").subquery("qualifying_parent"))
    if parent_conditions:
        parent = parent.where(*parent_conditions)
    conditions.append(exp.Exists(this=parent))
    if population_scope=="source_population" and time_analysis:
        eligibility=source_population_eligibility(tile,bucket_expression(
            column((time_analysis.table,time_analysis.column)),time_analysis,time_kind))
        conditions.append(eligibility)
    if population_scope=="qualifying_groups":
        qualifying_plan=_ordered(original,parent_statement.copy(),tile.limit,filters=filters)
        qualifying_parent=exp.select(exp.Literal.number(1)).from_(parse_one(qualifying_plan["sql"],read="postgres").subquery("population_groups"))
        correlations=[]
        for index,field in enumerate(dimensions):
            expression=column(_key(field))
            if time_analysis and _key(field)==(time_analysis.table,time_analysis.column):
                expression=bucket_expression(expression,time_analysis,time_kind)
            correlations.append(exp.NullSafeEQ(this=exp.column(f"output_{index+1}",table="population_groups",quoted=True),expression=expression))
        if correlations:
            qualifying_parent=qualifying_parent.where(*correlations)
        conditions.append(exp.Exists(this=qualifying_parent))
    # SQL aggregates ignore NULL inputs, including count(column) and DISTINCT.
    # MIN/MAX drill shows all inputs used to compute the extremum, not just ties.
    conditions.append(exp.Not(this=exp.Is(this=column(_key(measure)), expression=exp.Null())))
    statement = exp.select(*(column(_key(field)).as_(labels[_key(field)], quoted=True)
                             for field in tile.detail_fields)).from_(statement.subquery("contributors")).where(*conditions)
    warnings = list(plan.get("warnings", []))
    if set(plan["outerNodes"]) - set(original["outerNodes"]):
        warnings.append("Related detail branches can repeat a contributing row. Detail row counts are not the chart measure.")
    if measure.aggregate == "count_distinct":
        warnings.append("These are raw contributing rows; repeated values count once in COUNT DISTINCT.")
    plan.update(warnings=warnings, grain="Raw rows contributing to the selected measure", drill=True)
    if calculation_scope:
        plan["calculationScope"]=calculation_scope
        warnings.append("These rows describe only the explicitly selected calculation constituent and population; they are not one detail set for the composite value.")
        if exporting:
            import json
            statement=statement.select(exp.Literal.string(calculation_scope["label"]).as_("Calculated measure",quoted=True),
                exp.Literal.string(calculation_scope["population"]).as_("Constituent population",quoted=True),
                exp.Literal.string(json.dumps(calculation_scope,ensure_ascii=False,allow_nan=False)).as_("Calculation selection",quoted=True))
    if tile.kind == "pivot":
        plan["pivotScope"] = {"kind": scope, **pivot_depths, "dimensions": values,
                              "measureIndex": measure_index, "population": "qualifying groups before Top N" if scope != "cell" else "selected qualifying cell"}
        warnings.append("Pivot totals drill into the complete qualifying population before Top N; contributors are never top-N limited.")
    if tile.kind == "pivot" and exporting:
        import json
        # A raw CSV is independently interpretable after it leaves the report.
        # Fixed scope columns retain the exact typed selection without changing
        # contributor membership, grouping or row limits.
        scope_columns = [exp.Literal.string(scope).as_("Pivot scope", quoted=True),
            exp.Literal.number(pivot_depths["rowDepth"]).as_("Row grouping depth", quoted=True),
            exp.Literal.number(pivot_depths["columnDepth"]).as_("Column grouping depth", quoted=True),
            exp.Literal.string(json.dumps(plan["pivotScope"], ensure_ascii=False, allow_nan=False)).as_("Pivot selection", quoted=True)]
        statement = statement.select(*scope_columns)
    return model, _ordered(plan, statement, tile.limit,
                        _text_order_fields(catalog, model.definition, tile.detail_fields))
