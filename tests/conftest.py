"""Explicit runtime selection for tests that import the module ASGI app."""

import os


os.environ.setdefault("SCHEMII_DEPLOYMENT_MODE", "local-development")
os.environ.setdefault("SCHEMII_TARGET_EGRESS_MODE", "internal-only")
os.environ.setdefault("SCHEMII_STORAGE_MODE", "memory")
