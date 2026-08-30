from __future__ import annotations

from typing import Any, Callable

from .config import MetadataConfig
from .errors import MetadataStoreError
from ..secret_file import read_secret_file


class MetadataConnectionFactory:
    def __init__(self, config: MetadataConfig, connect: Callable[..., Any] | None = None):
        self.config = config
        self._connect = connect

    def __call__(self):
        try:
            settings = {
                "connect_timeout": self.config.connect_timeout,
                "application_name": self.config.application_name,
            }
            if self.config.password_file:
                settings["password"] = read_secret_file(
                    self.config.password_file, "SCHEMII_METADATA_PASSWORD_FILE",
                )
            if self._connect is not None:
                return self._connect(self.config.dsn, **settings)
            import psycopg
            from psycopg.rows import dict_row

            return psycopg.connect(
                self.config.dsn,
                **settings,
                row_factory=dict_row,
            )
        except Exception as exc:
            raise MetadataStoreError(
                "metadata_unavailable",
                "Server metadata PostgreSQL is unavailable",
                status=503,
                retryable=True,
            ) from exc
