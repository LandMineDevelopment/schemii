"""Typed report arithmetic over complete semantic aggregates, never cached rows."""
from decimal import Decimal
from sqlglot import exp, parse_one
from schemii.common.api.errors import ApiProblem
from schemii.schemoo.derived import normalized_type, output_type, NUMERIC_ORDER
from schemii.schemoo.service import document
from .dashboard_models import ReportOutput, base_measures
from .time_analysis import source_type

REPORT_NAMESPACE = "@report"


def key(output):
    return output.table, output.column, output.aggregate, output.kind


def reference(calculation):
    return ReportOutput(table=REPORT_NAMESPACE,column=calculation.id,kind="calculated")


def fail(message):
    raise ApiProblem(422,"invalid_calculated_measure",message)


def output_definitions(tile):
    from .dashboard_models import tile_dimensions
    fields = tile.detail_fields if tile.kind == "detail" else [*tile_dimensions(tile),*base_measures(tile)]
    result = [ReportOutput(**document(field)) for field in fields]
    time = getattr(tile,"time_analysis",None)
    if time:
        for field in base_measures(tile):
            kinds = ["comparison","change","percent_change"] if time.comparison != "none" else []
            if time.running_total:
                kinds.append("running_total")
            result.extend(ReportOutput(**document(field),kind=kind) for kind in kinds)
    return [*result,*(reference(item) for item in getattr(tile,"calculations",[]))]


class MeasureGraph:
    def __init__(self,tile,catalog,definition):
        self.tile,self.catalog,self.definition = tile,catalog,definition
        self.outputs = output_definitions(tile)
        self.positions = {key(item):index for index,item in enumerate(self.outputs)}
        self.calculations = {item.id:item for item in getattr(tile,"calculations",[])}
        self.types,self.scopes,self.constituents,self.order = {},{},{},[]
        self.population = {}
        path=[]
        def infer(identifier):
            if identifier in self.types:
                return self.types[identifier]
            if identifier in path:
                fail(f"Calculated measure cycle: {' → '.join([*path,identifier])}. Rebind an operand to break the cycle.")
            item=self.calculations.get(identifier)
            if item is None:
                fail(f"Missing calculation on dependency path {' → '.join([*path,identifier])}.")
            path.append(identifier)
            scopes=set();constituents=[]
            for operand in [item.left,item.right]:
                if operand.constant is not None:
                    continue
                output=operand.output
                if key(output) not in self.positions:
                    fail(f"{item.label}: referenced output was removed. Restore its base input or choose a replacement.")
                if output.kind == "calculated":
                    if operand.scope != "group":
                        fail(f"{item.label}: population operands must reference a base aggregate, not another calculation.")
                    infer(output.column);scopes.update(self.scopes[output.column]);constituents.extend(self.constituents[output.column])
                else:
                    self.validate_numeric(output,item.label)
                    if operand.scope != "group":
                        if output.kind != "value" or output.aggregate == "none":
                            fail(f"{item.label}: population scopes require a raw/semantic base aggregate. Choose SUM, AVG, COUNT, DISTINCT, MIN or MAX.")
                        time=getattr(tile,"time_analysis",None)
                        if time and (time.comparison != "none" or time.running_total):
                            fail(f"{item.label}: population denominators cannot combine calendar windows. Use period values or group-scope operands.")
                        self.population[(key(output),operand.scope)]=output
                    scopes.add(operand.scope);constituents.append(operand.model_dump(exclude_none=True))
            path.pop();self.types[identifier]="numeric";self.scopes[identifier]=scopes
            self.constituents[identifier]=list({str(value):value for value in constituents}.values());self.order.append(identifier)
            return "numeric"
        for identifier in self.calculations:
            infer(identifier)
        for predicate in getattr(tile,"aggregate_filters",[]):
            if predicate.kind == "calculated" and "qualifying_groups" in self.scopes.get(predicate.column,set()):
                fail(f"Result filter on {predicate.column} would define its own qualifying-groups denominator. Filter an independent measure or use the source population.")
        comparison=getattr(tile,"kpi_comparison",None)
        if comparison and comparison.baseline.output:
            operand=comparison.baseline
            if key(operand.output) not in self.positions:
                fail("The KPI baseline output was removed. Restore it or choose a replacement.")
            if operand.output.kind == "calculated":
                infer(operand.output.column)
            else:
                self.validate_numeric(operand.output,"KPI baseline")
            if operand.scope != "group":
                if operand.output.kind != "value" or operand.output.aggregate == "none":
                    fail("KPI population baselines require a base aggregate.")
                self.population[(key(operand.output),operand.scope)] = operand.output

    def validate_numeric(self,output,label):
        from .dashboard_models import tile_dimensions
        if any((field.table,field.column,field.aggregate)==(output.table,output.column,output.aggregate) for field in tile_dimensions(self.tile)):
            fail(f"{label}: choose an aggregate measure, not a grouping dimension.")
        kind=output_type(output.aggregate,source_type(self.catalog,self.definition,output.table,output.column)) if output.aggregate != "none" else source_type(self.catalog,self.definition,output.table,output.column)
        if output.kind in {"change","percent_change","running_total"}:
            kind="numeric"
        if normalized_type(kind) not in NUMERIC_ORDER:
            fail(f"{label}: arithmetic requires numeric measures; found {kind or 'unknown type'}.")

    def visible_indices(self):
        hidden={key(ReportOutput(**document(field))) for field in getattr(self.tile,"hidden_measures",[])}
        hidden_base={(field.table,field.column,field.aggregate) for field in getattr(self.tile,"hidden_measures",[])}
        return [index for index,output in enumerate(self.outputs) if (
            self.calculations[output.column].visible if output.kind == "calculated" else
            key(output) not in hidden and (output.table,output.column,output.aggregate) not in hidden_base)]

    def metadata(self):
        visible=self.visible_indices()
        return [{"id":identifier,"label":self.calculations[identifier].label,"unit":"percent" if self.calculations[identifier].operation=="percent_of" else "number",
            "scale":100 if self.calculations[identifier].operation=="percent_of" else 1,
            "index":visible.index(self.positions[key(reference(self.calculations[identifier]))]) if self.positions[key(reference(self.calculations[identifier]))] in visible else None,
            "constituents":self.constituents[identifier]} for identifier in self.calculations]


def q(name):
    return '"'+name.replace('"','""')+'"'


def predicate_sql(index,predicate,alias="groups"):
    value=f'{alias}.{q("measure_"+str(index))}'
    if predicate.operator in {"is_null","not_null"}:
        return value+(" IS NULL" if predicate.operator=="is_null" else " IS NOT NULL")
    from schemii.schemoo.prototype import _literal
    return value+" "+{"eq":"=","ne":"<>","gt":">","gte":">=","lt":"<","lte":"<="}[predicate.operator]+" "+_literal(predicate.value)


def arithmetic(item,left,right):
    if item.operation in {"divide","percent_of"}:
        ratio=f'(CAST({left} AS NUMERIC) / NULLIF({right},0))'
        return f'({ratio} * 100)' if item.operation=="percent_of" else ratio
    return f'({left} '+{"add":"+","subtract":"-","multiply":"*"}[item.operation]+f' {right})'


def raw_aggregate(output,alias):
    return f'COUNT(DISTINCT {alias})' if output.aggregate=="count_distinct" else f'{output.aggregate.upper()}({alias})'


def compile_measures(graph,statement,*,populations=None,qualification_filters=()):
    """Append each DAG node in a separate SQL stage, preserving all base outputs."""
    populations=populations or {}
    labels=[output.alias_or_name for output in statement.expressions]
    base_count=len(graph.outputs)-len(graph.calculations)
    statement=statement.copy()
    statement.set("expressions",[(output.this if isinstance(output,exp.Alias) else output).as_(f'measure_{index}',quoted=True) for index,output in enumerate(statement.expressions)])
    ctes=[f'measure_base AS ({statement.sql(dialect="postgres")})']
    previous="measure_base";completed=set()
    pop_names={scope:f'population_{scope}' for _,scope in graph.population}
    for scope,sql in populations.items():
        if scope!="qualifying_groups":
            ctes.append(f'{pop_names[scope]} AS ({sql})')
    def operand_sql(operand,alias="groups"):
        if operand.constant is not None:
            return f'CAST({str(Decimal(str(operand.constant)))} AS NUMERIC)'
        if operand.scope != "group":
            position=graph.positions[key(operand.output)]
            return f'(SELECT {q("measure_"+str(position))} FROM {pop_names[operand.scope]})'
        return f'{alias}.{q("measure_"+str(graph.positions[key(operand.output)]))}'
    def stage(identifier):
        nonlocal previous
        item=graph.calculations[identifier];position=graph.positions[key(reference(item))]
        name=f'measure_stage_{len(completed)}'
        ctes.append(f'{name} AS (SELECT groups.*, {arithmetic(item,operand_sql(item.left),operand_sql(item.right))} AS {q("measure_"+str(position))} FROM {previous} groups)')
        previous=name;completed.add(identifier)
    for identifier in graph.order:
        if "qualifying_groups" not in graph.scopes[identifier]:
            stage(identifier)
    # Independent filters define the population before ranking/top N. A filter
    # depending on this population was rejected by MeasureGraph as circular.
    qualification=" AND ".join(predicate_sql(index,predicate) for index,predicate in qualification_filters) or "TRUE"
    ctes.append(f'measure_qualifying AS (SELECT groups.* FROM {previous} groups WHERE {qualification})')
    if "qualifying_groups" in populations:
        ctes.append(f'{pop_names["qualifying_groups"]} AS ({populations["qualifying_groups"]})')
    for identifier in graph.order:
        if identifier not in completed:
            stage(identifier)
    output_labels=[*labels,*[item.label for item in graph.calculations.values()]]
    expressions=[f'groups.{q("measure_"+str(index))} AS {q(label)}' for index,label in enumerate(output_labels)]
    kpi=getattr(graph.tile,"kpi_comparison",None)
    kpi_metadata=None
    if kpi:
        current=f'groups.{q("measure_"+str(graph.visible_indices()[0]))}'
        baseline=operand_sql(kpi.baseline)
        values=[baseline,f'({current} - {baseline})',f'(CAST(({current} - {baseline}) AS NUMERIC) / NULLIF(ABS({baseline}),0) * 100)']
        kpi_metadata={"label":kpi.label,"favorable":kpi.favorable,"indices":[len(graph.visible_indices())+index for index in range(3)],"percentScale":100}
        expressions.extend(f'{value} AS {q(label)}' for value,label in zip(values,[kpi.label,"Absolute variance","Variance (%)"]))
        output_labels += [kpi.label,"Absolute variance","Variance (%)"]
    sql='WITH '+', '.join(ctes)+' SELECT '+', '.join(expressions)+f' FROM {previous} groups'
    return parse_one(sql,read="postgres"),{"calculatedMeasures":graph.metadata(),"kpiComparison":kpi_metadata,"calculationPopulation":"complete query before Top N; no cached rows"}
