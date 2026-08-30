from __future__ import annotations

import json
import sys

from .metadata import MetadataConfig, MetadataConnectionFactory, MetadataMigrator, MetadataStoreError


def main() -> int:
    try:
        config = MetadataConfig.from_env()
        version = MetadataMigrator(MetadataConnectionFactory(config)).migrate()
    except (MetadataStoreError, ValueError) as exc:
        if isinstance(exc, MetadataStoreError):
            payload = exc.to_dict()
        else:
            payload = {"error": {"code": "metadata_configuration_invalid", "message": str(exc), "retryable": False}}
        print(json.dumps(payload, separators=(",", ":")), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "version": version}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
