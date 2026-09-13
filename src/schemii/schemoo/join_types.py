"""Conservative equality compatibility for authored connections, without casts."""

import re

_NUMERIC = {'smallint', 'integer', 'bigint', 'numeric', 'real', 'double precision'}
_ALIASES = {'int2': 'smallint', 'int4': 'integer', 'int': 'integer', 'int8': 'bigint',
            'decimal': 'numeric', 'float4': 'real', 'float8': 'double precision',
            'varchar': 'character varying', 'bool': 'boolean',
            'timestamp': 'timestamp without time zone', 'timestamptz': 'timestamp with time zone',
            'time': 'time without time zone', 'timetz': 'time with time zone'}
_EQUALITY = _NUMERIC | {'text', 'character varying', 'character', 'boolean', 'uuid', 'date',
    'timestamp without time zone', 'timestamp with time zone', 'time without time zone',
    'time with time zone', 'interval', 'bytea', 'jsonb', 'inet', 'cidr', 'macaddr', 'macaddr8',
    'bit', 'bit varying', 'money', 'oid'}


def normalized_type(value):
    value = re.sub(r'\s+', ' ', (value or '').strip().lower())
    value = re.sub(r'\(\s*\d+(?:\s*,\s*\d+)?\s*\)', '', value).strip()
    return _ALIASES.get(value, value)


def comparable_types(left, right):
    left, right = normalized_type(left), normalized_type(right)
    if left not in _EQUALITY or right not in _EQUALITY:
        return False
    return (left == right or {left, right} <= _NUMERIC
            or {left, right} <= {'text', 'character varying'})


def validate_join_types(left, right):
    if not comparable_types(left, right):
        raise ValueError(f'Connection columns must have comparable types without casts ({left or "unknown"} and {right or "unknown"}).')
