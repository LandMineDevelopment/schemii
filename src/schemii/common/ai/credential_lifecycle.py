"""Expire inactive owners' encrypted Pi credentials without recording chat activity.

This boundary does not manage the separate legacy OpenCode credential store.
Only explicit application activity renews retention; polling and token refresh do not.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, Request, Response

from schemii.common.metadata.models import Principal, get_current_principal


router = APIRouter(tags=["activity"])
logger = logging.getLogger(__name__)


@router.post("/api/v1/activity", status_code=204)
def record_activity(request: Request, principal: Principal = Depends(get_current_principal)):
    """Record foreground application use for the authenticated owner only."""
    request.app.state.services.metadata.ai_credentials.touch_activity(principal.user_id)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


class CredentialExpiryWorker:
    """One bounded periodic sweep, including startup and graceful shutdown."""

    def __init__(self, store, *, enabled: bool = True, interval_seconds: float = 60):
        self.store = store
        self.enabled = enabled
        self.interval_seconds = interval_seconds
        self._stopped = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def _sweep(self):
        try:
            await asyncio.to_thread(self.store.expire_inactive)
        except Exception:
            # Provider/DB exceptions can contain secrets. Never log their text or trace.
            logger.error("AI credential expiration sweep failed; retrying at next scheduled sweep")

    async def start(self):
        if not self.enabled or self._task is not None:
            return
        self._stopped.clear()
        await self._sweep()
        self._task = asyncio.create_task(self._run(), name="ai-credential-expiry")

    async def _run(self):
        while not self._stopped.is_set():
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                await self._sweep()

    async def stop(self):
        self._stopped.set()
        if self._task is not None:
            await self._task
            self._task = None
