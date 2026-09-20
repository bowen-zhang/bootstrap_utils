import asyncio
import collections
import datetime
import typing

from . import metric_utils


_T = typing.TypeVar('_T')


class EventQueue(typing.Generic[_T]):
    def __init__(self, maxsize: int):
        self._maxsize = maxsize
        self._queue = collections.deque[_T](maxlen=maxsize)
        
        # Public async primitives
        self._has_data = asyncio.Event()
        self._finished = asyncio.Event()
        self._finished.set()
        
        self._unfinished_tasks = 0

    def put_nowait(self, item: _T):
        """Synchronous put. Safely triggers events without async syntax."""
        was_full = self.full()
        self._queue.append(item)
        
        if not was_full:
            self._unfinished_tasks += 1
            self._finished.clear()
            
        # Unconditionally signal that data is available.
        # This is safe to call inside a normal synchronous function.
        self._has_data.set()

    async def get(self, timeout_sec: float | None = None) -> _T:
        """Async get. Suspends the coroutine if the queue is empty."""
        while not self._queue:
            self._has_data.clear()
            if timeout_sec:
                await asyncio.wait_for(self._has_data.wait(), timeout=timeout_sec)
            else:
                await self._has_data.wait()
            
        # Pop the item
        item = self._queue.popleft()
        
        # If we just emptied the queue, make sure the next getter waits
        if not self._queue:
            self._has_data.clear()
            
        return item

    def task_done(self):
        """Public task tracking decrement."""
        if self._unfinished_tasks <= 0:
            raise ValueError('task_done() called too many times')
        self._unfinished_tasks -= 1
        if self._unfinished_tasks == 0:
            self._finished.set()

    async def join(self):
        """Block until all items have been processed via task_done()."""
        await self._finished.wait()

    def qsize(self) -> int:
        return len(self._queue)

    def empty(self) -> bool:
        return len(self._queue) == 0

    def full(self) -> bool:
        return len(self._queue) >= self._maxsize


class _UserContext(typing.Generic[_T]):
    """Broadcast event to multiple subscribers.

    This is a synchronous class and must remain so.
    """

    _DEFAULT_QUEUE_MAX_SIZE = 10

    _user_id: str
    _subscribers: list[EventQueue[_T]]
    _last_published_at: datetime.datetime | None

    def __init__(self, user_id: str) -> None:
        self._user_id = user_id
        self._subscribers = []
        self._last_published_at = None

    @property
    def user_id(self) -> str:
        return self._user_id

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @property
    def is_active(self) -> bool:
        if not self._last_published_at:
            return False
        
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        return (now - self._last_published_at) < datetime.timedelta(minutes=30)

    def subscribe(
        self, max_size: int | None = None
    ) -> EventQueue[_T]:
        capacity = max_size if max_size is not None else self._DEFAULT_QUEUE_MAX_SIZE
        queue = EventQueue[_T](maxsize=capacity)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: EventQueue[_T]) -> None:
        self._subscribers.remove(queue)

    def publish(self, event: _T) -> None:
        self._last_published_at = datetime.datetime.now(tz=datetime.timezone.utc)
        for subscriber in self._subscribers:
            subscriber.put_nowait(event)


class EventQueueContext(typing.Generic[_T]):
    def __init__(self, event_manager: EventManager, user_id: str, max_size: int | None):
        self._event_manager = event_manager
        self._user_id = user_id
        self._max_size = max_size
        self._queue = None

    def __enter__(self) -> EventQueue[_T]:
        self._queue = self._event_manager.subscribe(self._user_id, self._max_size)
        return self._queue

    def __exit__(self, exc_type, exc, tb):
        if self._queue:
            self._event_manager.unsubscribe(self._user_id, self._queue)
            self._queue = None


class EventManager(typing.Generic[_T]):
    """Manage event delivery and broadcasting for multiple users.

    This is a synchronous class and must remain so.
    """

    _user_contexts: dict[str, _UserContext[_T]]

    def __init__(self, metric_builder: metric_utils.MetricBuilder):
        self._user_contexts = {}
        self._total_subscribed_users = metric_builder.gauge("total_subscribed_users")
        self._active_subscribed_users = metric_builder.gauge("active_subscribed_users")

    def new(self, user_id: str, max_size: int | None = None) -> EventQueueContext[_T]:
        return EventQueueContext(self, user_id, max_size)

    def subscribe(self, user_id: str, max_size: int | None = None) -> EventQueue[_T]:
        if user_id not in self._user_contexts:
            self._user_contexts[user_id] = _UserContext[_T](user_id)

        self._refresh_metrics()
        user_context = self._user_contexts[user_id]
        return user_context.subscribe(max_size)

    def unsubscribe(self, user_id: str, queue: EventQueue[_T]) -> None:
        if user_id not in self._user_contexts:
            return
        user_context = self._user_contexts[user_id]

        user_context.unsubscribe(queue)
        if user_context.subscriber_count == 0:
            del self._user_contexts[user_id]
        self._refresh_metrics()

    def publish(self, user_id: str, event: _T) -> None:
        if user_id not in self._user_contexts:
            return
        user_context = self._user_contexts[user_id]
        user_context.publish(event)

    def _refresh_metrics(self) -> None:
        total_users = len(self._user_contexts)
        active_users = len([x for x in self._user_contexts.values() if x.is_active])
        self._total_subscribed_users.update(total_users)
        self._active_subscribed_users.update(active_users)