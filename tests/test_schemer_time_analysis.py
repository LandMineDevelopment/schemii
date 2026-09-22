import pytest
from pydantic import ValidationError
from sqlglot import parse_one

from schemii.common.api.errors import ApiProblem
from schemii.schemer.dashboard_models import DashboardTile, TimeAnalysis
from schemii.schemer import tile_queries as queries
from test_schemer_tile_queries import context, field


def timed(context, **options):
    dashboard, tile, model = context
    catalog = queries.model_catalog(None, None, model)
    catalog['tables'][0]['columns'].append({'name': 'created_at', 'dataType': 'timestamp with time zone'})
    catalog['tables'][0]['columns'][0]['dataType'] = 'integer'
    tile.dimensions = [field('people', 'created_at'), field('org', 'name')]
    tile.time_analysis = TimeAnalysis(table='people', column='created_at', **options)
    return dashboard, tile, model


def test_time_plan_is_unbounded_and_partitions_other_dimensions(context):
    dashboard, tile, _ = timed(context, comparison='previous_period', runningTotal=True, timezone='America/New_York')
    tile.limit = 1
    _, plan = queries.tile_plan(None, None, dashboard, tile.id)
    sql = plan['sql']
    assert "AT TIME ZONE 'America/New_York'" in sql
    assert "INTERVAL '1 MONTH'" in sql
    assert 'IS NOT DISTINCT FROM' in sql
    assert 'PARTITION BY current."field_1" ORDER BY current."field_0"' in sql
    assert 'UNBOUNDED PRECEDING' in sql and 'CURRENT ROW' in sql
    assert 'NULLIF' in sql
    assert 'LIMIT' not in sql and 'OFFSET' not in sql
    assert plan['rowLimit'] == 1
    assert [c['kind'] for c in plan['computedOutputs']] == ['comparison', 'change', 'percent_change', 'running_total']
    assert len(plan['outputLabels']) == 7
    assert parse_one(sql, read='postgres')


def test_week_sunday_and_prior_year_use_calendar_lookup(context):
    dashboard, tile, _ = timed(context, granularity='week', weekStart='sunday', comparison='prior_year')
    _, plan = queries.tile_plan(None, None, dashboard, tile.id)
    assert "INTERVAL '1 YEAR'" in plan['sql']
    assert "INTERVAL '1 DAY'" in plan['sql']
    assert 'LEFT JOIN buckets AS previous' in plan['sql']
    assert 'LAG(' not in plan['sql']


@pytest.mark.parametrize('comparison', ['previous_period', 'prior_year'])
def test_year_grouping_uses_calendar_year_comparison(context, comparison):
    dashboard, tile, _ = timed(context, granularity='year', comparison=comparison)
    _, plan = queries.tile_plan(None, None, dashboard, tile.id)
    assert "DATE_TRUNC('YEAR'," in plan['sql']
    assert "INTERVAL '1 YEAR'" in plan['sql']
    assert plan['timeAnalysis']['granularity'] == 'year'


def test_year_bucket_drill_uses_the_same_calendar_group(context):
    dashboard, tile, _ = timed(context, granularity='year', timezone='America/New_York')
    _, plan = queries.tile_plan(None, None, dashboard, tile.id, selection={
        'dimensions': [{'table': 'people', 'column': 'created_at', 'value': '2024-01-01'},
                       {'table': 'org', 'column': 'name', 'value': 'Engineering'}], 'measureIndex': 0})
    assert "DATE_TRUNC('YEAR'," in plan['sql']
    assert "AT TIME ZONE 'America/New_York'" in plan['sql']
    assert "CAST(e'2024-01-01' AS DATE)" in plan['sql']
    assert 'GROUP BY' not in plan['sql']


def test_time_bucket_drill_preserves_zone_and_filters(context):
    dashboard, tile, _ = timed(context, granularity='day', timezone='America/New_York')
    _, plan = queries.tile_plan(None, None, dashboard, tile.id, selection={
        'dimensions': [{'table': 'people', 'column': 'created_at', 'value': '2024-03-10'},
                       {'table': 'org', 'column': 'name', 'value': 'Engineering'}], 'measureIndex': 0})
    assert "AT TIME ZONE 'America/New_York'" in plan['sql']
    assert "CAST(e'2024-03-10' AS DATE)" in plan['sql']
    assert 'Engineering' in plan['sql']
    assert 'GROUP BY' not in plan['sql']
    with pytest.raises(ApiProblem, match='calendar bucket'):
        queries.tile_plan(None, None, dashboard, tile.id, selection={
            'dimensions': [{'table': 'people', 'column': 'created_at', 'value': '2024-03-10T12:00:00Z'},
                           {'table': 'org', 'column': 'name', 'value': 'Engineering'}], 'measureIndex': 0})


def test_time_fields_and_arithmetic_types_are_validated(context):
    dashboard, tile, _ = timed(context, comparison='previous_period')
    tile.measures = [field('people', 'name', 'max')]
    with pytest.raises(ApiProblem, match='numeric measures'):
        queries.tile_plan(None, None, dashboard, tile.id)
    tile.time_analysis.column = 'name'
    with pytest.raises(ApiProblem, match='date or timestamp'):
        queries.tile_plan(None, None, dashboard, tile.id)


@pytest.mark.parametrize('granularity', ['day', 'week', 'month', 'year'])
def test_time_configuration_round_trips_and_validates(granularity):
    body = {'id': 'time', 'title': 'Monthly', 'kind': 'line',
            'dimensions': [{'table': 'events', 'column': 'created_at'}],
            'measures': [{'table': 'events', 'column': 'id', 'aggregate': 'count'}],
            'timeAnalysis': {'table': 'events', 'column': 'created_at', 'granularity': granularity,
                             'weekStart': 'sunday', 'timezone': 'America/New_York', 'runningTotal': True}}
    tile = DashboardTile.model_validate(body)
    assert DashboardTile.model_validate(tile.model_dump(by_alias=True)) == tile
    for changes in [{'timezone': 'Invalid/Zone'}, {'weekStart': 'friday'}, {'column': 'other'}]:
        with pytest.raises(ValidationError):
            DashboardTile.model_validate({**body, 'timeAnalysis': {**body['timeAnalysis'], **changes}})
    with pytest.raises(ValidationError, match='explicit aggregation'):
        DashboardTile.model_validate({**body, 'measures': [{'table': 'events', 'column': 'id'}]})


@pytest.mark.parametrize("kind,zoned", [("timestamp(3) with time zone", True), ("timestamp(6) without time zone", False)])
def test_timestamp_precision_preserves_timezone_semantics(context, kind, zoned):
    dashboard, tile, model = timed(context, timezone='America/New_York')
    catalog = queries.model_catalog(None, None, model)
    catalog['tables'][0]['columns'][-1]['dataType'] = kind
    _, plan = queries.tile_plan(None, None, dashboard, tile.id)
    assert ("AT TIME ZONE 'America/New_York'" in plan['sql']) is zoned
