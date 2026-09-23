import asyncio
import datetime
import enum
import typing


_T = typing.TypeVar('_T')

class EventException(Exception):
    pass


class EventQueue(typing.Generic[_T]):
    def __init__(self, maxsize: int):
        self._queue = asyncio.Queue(maxsize=maxsize)

    def put_nowait(self, item: _T):
        """Synchronous put. Safely triggers events without async syntax."""
        if self._queue.full():
            self._queue.get_nowait()
        self._queue.put_nowait(item)

    async def get(self, timeout_sec: float | None = None) -> _T:
        """Async get. Suspends the coroutine if the queue is empty."""
        if timeout_sec is not None:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout_sec)
        return await self._queue.get()

    def empty(self) -> bool:
        return self._queue.empty()

    def full(self) -> bool:
        return self._queue.full()

    def close(self):
        self._queue.shutdown(immediate=True)


class EventQueueContext(typing.Generic[_T]):
    def __init__(self, topic: Topic[_T], subscriber_id: str, max_size: int | None):
        self._topic = topic
        self._subscriber_id = subscriber_id
        self._max_size = max_size
        self._queue = None

    def __enter__(self) -> EventQueue[_T]:
        self._queue = self._topic.subscribe(self._subscriber_id, self._max_size)
        return self._queue

    def __exit__(self, exc_type, exc, tb):
        if self._queue:
            self._topic.unsubscribe(self._queue)
            self._queue = None


class SubscribingBehavior(enum.Enum):
    DISCARD_OLD_DUPLICATE = 1
    REJECT_NEW_DUPLICATE = 2


class Topic(typing.Generic[_T]):
    """Publish events of a specific topic to multiple subscribers.

    This is a synchronous class and must remain so.
    """

    _DEFAULT_QUEUE_MAX_SIZE = 10

    _name: str
    _subscribing_behavior: SubscribingBehavior
    _subscribers: dict[str, EventQueue[_T]]
    _last_published_at: datetime.datetime | None

    def __init__(
            self,
            name: str,
            subscribing_behavior: SubscribingBehavior = SubscribingBehavior.REJECT_NEW_DUPLICATE,
    ) -> None:
        self._name = name
        self._subscribing_behavior = subscribing_behavior
        self._subscribers = {}
        self._last_published_at = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @property
    def last_published_at(self) -> datetime.datetime | None:
        return self._last_published_at

    def new(self, subscriber_id: str, max_size: int | None = None) -> EventQueueContext[_T]:
        return EventQueueContext(self, subscriber_id, max_size)

    def subscribe(
        self, subscriber_id: str, max_size: int | None = None
    ) -> EventQueue[_T]:
        if subscriber_id in self._subscribers:
            if self._subscribing_behavior == SubscribingBehavior.DISCARD_OLD_DUPLICATE:
                old_queue = self._subscribers[subscriber_id]
                self.unsubscribe(old_queue)
            else:
                raise EventException(f"Subscriber with ID '{subscriber_id}' already exists.")
        
        capacity = max_size if max_size is not None else self._DEFAULT_QUEUE_MAX_SIZE
        queue = EventQueue[_T](maxsize=capacity)
        self._subscribers[subscriber_id] = queue
        return queue

    def unsubscribe(self, queue: EventQueue[_T]) -> None:
        subscriber_id = None
        for key, value in self._subscribers.items():
            if value == queue:
                subscriber_id = key
                break
        if subscriber_id is None:
            raise EventException("Subscriber not found for the given queue.")
        
        del self._subscribers[subscriber_id]
        queue.close()

    def unsubscribe_all(self) -> None:
        subscribers = list(self._subscribers.values())
        self._subscribers.clear()
        for queue in subscribers:
            queue.close()

    def publish(self, event: _T) -> None:
        self._last_published_at = datetime.datetime.now(tz=datetime.timezone.utc)
        for subscriber in self._subscribers.values():
            subscriber.put_nowait(event)
