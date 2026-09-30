"""Read output aliases from the real saved-model SQL plan; never execute SQL."""

import json
import sys

from sqlglot import exp, parse


def compiled_columns(sql: str) -> list[str]:
    if not isinstance(sql, str) or len(sql.encode()) > 1024 * 1024:
        raise ValueError("invalid_compiled_plan")
    statements = parse(sql, read="postgres")
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise ValueError("invalid_compiled_plan")
    columns = [output.alias_or_name for output in statements[0].expressions]
    if not columns or len(columns) > 64 or any(
        not name or len(name.encode()) > 63 for name in columns
    ) or len(set(columns)) != len(columns):
        raise ValueError("invalid_compiled_columns")
    return columns


if __name__ == "__main__":
    try:
        print(json.dumps(compiled_columns(json.loads(sys.stdin.read(1024 * 1024 + 1)))))
    except Exception:
        # SQL and parser diagnostics stay out of reports and stderr.
        print("invalid_compiled_plan", file=sys.stderr)
        sys.exit(1)
