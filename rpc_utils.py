import asyncio

from collections.abc import AsyncGenerator, AsyncIterator
from types import TracebackType
from typing import Self


class StoppableStream[T]:
    """
    Read responses from an RPC stream or a local async generator.

    Iteration ends when the source is exhausted or shutdown is requested.
    Use inside `async with` to cancel pending reads and close the generator,
    including when the source is waiting without producing a response.
    """

    def __init__(self, responses: AsyncIterator[T], stopping: asyncio.Event):
        self._responses = responses
        self._stopping = stopping
        self._stopped: asyncio.Task[bool] | None = None
        self._next_response: asyncio.Future[T] | None = None
        self._finished = False

    async def __aenter__(self) -> Self:
        if self._stopped is not None or self._finished:
            raise RuntimeError("A StoppableStream context can only be entered once")
        self._stopped = asyncio.create_task(self._stopping.wait())
        return self

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> T:
        if self._stopped is None:
            raise RuntimeError("Use StoppableStream inside 'async with'")
        if self._finished or self._stopping.is_set():
            raise StopAsyncIteration

        # A quiet upstream must not prevent shutdown.
        self._next_response = asyncio.ensure_future(anext(self._responses))
        await asyncio.wait(
            (self._next_response, self._stopped),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if self._stopping.is_set():
            self._finished = True
            raise StopAsyncIteration
        try:
            return self._next_response.result()
        except StopAsyncIteration:
            self._finished = True
            raise

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._finished = True
        if self._stopped is not None:
            self._stopped.cancel()
        if self._next_response is not None:
            self._next_response.cancel()
            # ConnectRPC can wrap cancellation in an exception. Retrieve it and
            # finish the read before closing the upstream generator.
            await asyncio.gather(self._next_response, return_exceptions=True)
        if self._stopped is not None:
            await asyncio.gather(self._stopped, return_exceptions=True)
        if isinstance(self._responses, AsyncGenerator):
            await self._responses.aclose()
