from __future__ import annotations

import re


_CREDENTIAL = re.compile(r"[A-Za-z0-9_-]{16,256}")


def read_secret_file(path: str, setting: str) -> str:
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8", newline="") as secret_file:
            value = secret_file.read(1025)
    except OSError as exc:
        raise ValueError(f"{setting} is unreadable") from exc
    if value.endswith("\n"):
        value = value[:-1]
    if not _CREDENTIAL.fullmatch(value):
        raise ValueError(
            f"{setting} must contain one line of 16-256 characters from [A-Za-z0-9_-]"
        )
    return value
