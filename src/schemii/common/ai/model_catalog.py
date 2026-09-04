"""Public, credential-free discovery of currently advertised free Zen models."""

import asyncio
import copy
import json
import logging
from datetime import datetime, timezone
from threading import Lock
from urllib.request import HTTPRedirectHandler, Request, build_opener


ZEN_MODELS_URL = "https://opencode.ai/zen/v1/models"
MODEL_METADATA_URL = "https://models.dev/api.json"
MAX_CATALOG_BYTES = 16 * 1024 * 1024
logger = logging.getLogger(__name__)


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _reject_constant(value):
    raise ValueError("Invalid JSON number")


def fetch_public_catalog(url):
    """Fetch only the two public catalog URLs, never a provider's arbitrary URL."""
    if url not in (ZEN_MODELS_URL, MODEL_METADATA_URL):
        raise ValueError("Unsupported catalog URL")
    request = Request(url, headers={"Accept": "application/json",
                                   "User-Agent": "Schemii/0.1 (public-model-discovery)"})
    with build_opener(_NoRedirects()).open(request, timeout=10) as response:
        raw = response.read(MAX_CATALOG_BYTES + 1)
    if len(raw) > MAX_CATALOG_BYTES:
        raise ValueError("Catalog exceeds response limit")
    return json.loads(raw, parse_constant=_reject_constant)


def _zero_cost(value):
    return type(value) in (int, float) and value == 0


def _positive_limit(value):
    return value if type(value) is int and value > 0 else None


def _models(availability, metadata):
    if not isinstance(availability, dict) or not isinstance(availability.get("data"), list):
        raise ValueError("Invalid availability catalog")
    provider = metadata.get("opencode") if isinstance(metadata, dict) else None
    if not isinstance(provider, dict) or not isinstance(provider.get("models"), dict):
        raise ValueError("Invalid price catalog")
    active = {entry["id"] for entry in availability["data"]
              if isinstance(entry, dict) and isinstance(entry.get("id"), str)}
    result = []
    for model_id, model in provider["models"].items():
        if model_id not in active or not isinstance(model, dict):
            continue
        if model.get("status") in ("deprecated", "retired", "disabled"):
            continue
        costs = model.get("cost")
        if not isinstance(costs, dict) or not all(_zero_cost(costs.get(key)) for key in ("input", "output")):
            continue
        # A cache charge or tiered price would no longer be an unambiguously free model.
        if any(not _zero_cost(value) for value in costs.values()):
            continue
        if model.get("id", model_id) != model_id:
            continue
        name = model.get("name")
        limits = model.get("limit")
        limits = limits if isinstance(limits, dict) else {}
        result.append({
            "id": model_id, "providerId": "opencode",
            "name": name if isinstance(name, str) and name else model_id,
            "toolCall": model.get("tool_call") is True,
            "reasoning": model.get("reasoning") is True,
            "contextWindow": _positive_limit(limits.get("context")),
            "maxOutputTokens": _positive_limit(limits.get("output")),
        })
    return sorted(result, key=lambda model: (model["name"].casefold(), model["id"]))


class ZenModelCatalog:
    """One shared in-memory snapshot; discovery contains no account or query data."""

    def __init__(self, *, refresh_seconds=3600, max_stale_seconds=86400,
                 fetch_json=fetch_public_catalog, clock=None):
        if refresh_seconds <= 0 or max_stale_seconds < refresh_seconds:
            raise ValueError("Invalid model catalog refresh policy")
        self.refresh_seconds = refresh_seconds
        self.max_stale_seconds = max_stale_seconds
        self._fetch = fetch_json
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._refresh_lock = Lock()
        self._state_lock = Lock()
        self._models = []
        self._checked_at = None
        self._success_at = None
        self._error = None

    def snapshot(self):
        now = self._clock()
        with self._state_lock:
            age = (now - self._success_at).total_seconds() if self._success_at else None
            expired = age is None or age >= self.max_stale_seconds
            return {
                "models": copy.deepcopy(self._models) if not expired else [],
                "checkedAt": self._checked_at.isoformat() if self._checked_at else None,
                "lastSuccessAt": self._success_at.isoformat() if self._success_at else None,
                "stale": expired or self._error is not None or age >= self.refresh_seconds,
                "error": self._error,
            }

    def refresh(self, *, force=False):
        if not self._refresh_lock.acquire(blocking=False):
            return self.snapshot()
        try:
            now = self._clock()
            with self._state_lock:
                due = self._checked_at is None or (now - self._checked_at).total_seconds() >= self.refresh_seconds
            if force or due:
                try:
                    models = _models(self._fetch(ZEN_MODELS_URL), self._fetch(MODEL_METADATA_URL))
                except Exception:
                    with self._state_lock:
                        self._checked_at = self._clock()
                        self._error = "catalog_unavailable"
                    logger.warning("Free-model catalog refresh failed; retaining bounded previous snapshot")
                else:
                    with self._state_lock:
                        self._checked_at = self._success_at = self._clock()
                        self._models = models
                        self._error = None
            return self.snapshot()
        finally:
            self._refresh_lock.release()


class ModelCatalogWorker:
    """Refresh outside request handling; stop waits for the bounded active fetch."""

    def __init__(self, catalog):
        self.catalog = catalog
        self._stopped = asyncio.Event()
        self._task = None

    async def start(self):
        if self._task is None:
            self._stopped.clear()
            self._task = asyncio.create_task(self._run(), name="free-model-catalog")

    async def _run(self):
        while not self._stopped.is_set():
            await asyncio.to_thread(self.catalog.refresh)
            try:
                await asyncio.wait_for(self._stopped.wait(), self.catalog.refresh_seconds)
            except TimeoutError:
                pass

    async def stop(self):
        self._stopped.set()
        if self._task is not None:
            await self._task
            self._task = None
