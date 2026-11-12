"""HTTP ownership of closeable synchronous PostgreSQL exports."""

import anyio
from fastapi.responses import StreamingResponse


class QueryExportStreamingResponse(StreamingResponse):
    """Cancel blocking reads and release their lease on every response exit."""

    def __init__(self, export, **kwargs):
        self.export = export
        super().__init__(export, **kwargs)

    async def __call__(self, scope, receive, send):
        try:
            async with anyio.create_task_group() as group:

                async def stream():
                    try:
                        await self.stream_response(send)
                    finally:
                        group.cancel_scope.cancel()

                async def disconnect():
                    try:
                        await self.listen_for_disconnect(receive)
                    finally:
                        # This also runs on outer request cancellation, while a
                        # shielded worker thread may still be inside next().
                        with anyio.CancelScope(shield=True):
                            await anyio.to_thread.run_sync(self.export.cancel)
                        group.cancel_scope.cancel()

                group.start_soon(stream)
                group.start_soon(disconnect)
        finally:
            # Task-group exit has joined next(); never close an executing
            # generator or its database session from a competing thread.
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(self.export.close)
        if self.background is not None:
            await self.background()
