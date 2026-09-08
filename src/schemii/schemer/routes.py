"""API routes for reports, dashboards, filters, and visualizations."""

from fastapi import APIRouter


router = APIRouter(prefix="/api/v1/schemer", tags=["schemer"])

# TODO(schemer-query-execution): Do not make Schemii/Schemoo's retained
# PostgreSQL cursor-per-result strategy the default for interactive reports.
# Schemer must support many concurrent users and queries, so holding one
# connection and REPEATABLE READ transaction open while each user scrolls would
# exhaust the pool and retain old snapshots. Design Schemer's execution boundary
# around bounded admission, cancellation, stable deterministic pagination, and
# a scalable stateless or short-lived result strategy. Any cache/materialization
# must remain explicitly bounded and must not persist raw result rows in the
# metadata database. Keep the existing shared cursor path available only where
# its consistency/streaming tradeoff is intentionally selected.
