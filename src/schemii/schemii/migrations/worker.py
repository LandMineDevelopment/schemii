"""Bounded lifespan worker for durable migration execution work."""

from __future__ import annotations

import asyncio
import logging
import secrets

from .execution import MigrationExecutionCoordinator
from .repository import ExecutionWork


LOGGER = logging.getLogger(__name__)


class MigrationExecutionWorker:
    """Process one migration at a time and renew its durable lease while active."""

    def __init__(
        self,
        coordinator: MigrationExecutionCoordinator,
        *,
        poll_interval_seconds: float = 1.0,
        heartbeat_interval_seconds: float | None = None,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("migration worker poll interval must be positive")
        lease_seconds = coordinator.lease_ttl.total_seconds()
        heartbeat = heartbeat_interval_seconds or min(30.0, lease_seconds / 3)
        if heartbeat <= 0 or heartbeat >= lease_seconds:
            raise ValueError("migration worker heartbeat must be shorter than its lease")
        self._coordinator = coordinator
        self._poll_interval_seconds = poll_interval_seconds
        self._heartbeat_interval_seconds = heartbeat
        self._worker_id = f"mls_{secrets.token_hex(16)}"
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def worker_id(self) -> str:
        return self._worker_id

    async def start(self) -> None:
        if self._task is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._stop.clear()
        self._wake.set()
        self._task = asyncio.create_task(
            self._run(), name="schemii-migration-worker"
        )

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        self._stop.set()
        self.notify()
        await task
        self._task = None
        self._loop = None

    def notify(self) -> None:
        """Wake the worker after a reservation; safe from request threads."""

        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._wake.set)

    async def run_once(self) -> bool:
        """Claim and finish at most one durable unit; useful for deterministic tests."""

        work = await asyncio.to_thread(
            self._coordinator.claim_next, self._worker_id
        )
        if work is None:
            return False
        await self._process_with_heartbeat(work)
        return True

    async def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            try:
                if await self.run_once():
                    continue
            except Exception:
                LOGGER.exception("Migration worker could not claim or process durable work")
            if self._stop.is_set():
                break
            try:
                await asyncio.wait_for(
                    self._wake.wait(), timeout=self._poll_interval_seconds
                )
            except asyncio.TimeoutError:
                pass

    async def _process_with_heartbeat(self, work: ExecutionWork) -> None:
        processing = asyncio.create_task(
            asyncio.to_thread(self._coordinator.process, work)
        )
        while True:
            done, _ = await asyncio.wait(
                {processing}, timeout=self._heartbeat_interval_seconds
            )
            if done:
                await processing
                return
            try:
                renewed = await asyncio.to_thread(self._coordinator.renew, work)
            except Exception:
                LOGGER.exception(
                    "Migration worker could not renew its lease",
                    extra={"execution_id": work.record.execution.id},
                )
                continue
            if not renewed:
                LOGGER.warning(
                    "Migration worker no longer owns its lease",
                    extra={"execution_id": work.record.execution.id},
                )
