from __future__ import annotations

import os
from pathlib import Path

from .examples import initialize_postgres_example_profile, postgres_example_profile_from_environment
from .postgres_service import PostgresService


def main() -> None:
    config_dir = Path(os.environ["SCHEMII_CONFIG_DIR"]).expanduser().resolve()
    service = PostgresService(config_dir, application_name="profile_initializer")
    try:
        initialize_postgres_example_profile(service, postgres_example_profile_from_environment())
    finally:
        service.close()


if __name__ == "__main__":
    main()
