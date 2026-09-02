"""Explicit runtime selection for tests that import the module ASGI app."""

import os


os.environ.setdefault("SCHEMII_DEPLOYMENT_MODE", "local-development")
os.environ.setdefault("SCHEMII_TARGET_EGRESS_MODE", "internal-only")
os.environ.setdefault(
    "SCHEMII_ALLOWED_TARGET_HOSTS",
    "localhost,127.0.0.1,postgres,demo-postgres,application-postgres,postgres.internal,runtime-only-host-9f70.internal",
)
os.environ.setdefault("SCHEMII_STORAGE_MODE", "memory")
