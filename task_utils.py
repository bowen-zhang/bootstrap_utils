import abc
import asyncio
import logging

from typing import Optional


class BackgroundTask:
    _name: str
    _logger: logging.Logger
    _task: asyncio.Task | None
    _stopping: asyncio.Event

    def __init__(self, name: Optional[str] = None):
        self._name = name or self.__class__.__name__
        self._logger = logging.getLogger(self._name)
        self._task = None
        self._stopping = asyncio.Event()

    @property
    def name(self) -> str:
        return self._name

    @property
    def stopping_event(self) -> asyncio.Event:
        return self._stopping

    @property
    def is_stopping(self) -> bool:
        return self._stopping.is_set()

    @property
    def logger(self) -> logging.Logger:
        return self._logger

    def start(self):
        if self._task:
            self.logger.debug(f"{self.name} is already running.")
            return

        self._stopping.clear()
        self._task = asyncio.create_task(self._execute())

    async def stop(self):
        self.logger.info(f"Stopping {self.name}...")
        self._stopping.set()
        # Give an opportunity for task to finish gracefully before cancelling
        if self._task and not self._task.done():
            try:
                await asyncio.wait_for(self._task, timeout=2)
            except asyncio.TimeoutError:
                pass
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def sleep(self, interval_sec: float):
        try:
            await asyncio.wait_for(self._stopping.wait(), timeout=interval_sec)
            raise asyncio.CancelledError()
        except asyncio.TimeoutError:
            pass

    async def _execute(self):
        try:
            await self._before_start()
        except Exception as ex:
            self.logger.exception(f"{self.name} had unexpected error before start.")
            raise

        try:
            await self._run()
        except Exception as ex:
            self.logger.exception(f"{self.name} had unexpected error during run.")
            raise

        try:
            await self._before_exit()
        except Exception as ex:
            self.logger.exception(f"{self.name} had unexpected error before exit.")
            # Continue with cleanup even if there was an error before exit.

    @abc.abstractmethod
    async def _before_start(self):
        pass

    @abc.abstractmethod
    async def _run(self):
        raise NotImplementedError

    @abc.abstractmethod
    async def _before_exit(self):
        pass


class RecurringBackgroundTask(BackgroundTask):
    _loop_interval_sec: float

    def __init__(self, name: Optional[str] = None, loop_interval_sec: float = 1.0):
        super().__init__(name)
        self._loop_interval_sec = loop_interval_sec

    async def _run(self):
        while not self.is_stopping:
            try:
                await self._run_once()
            except asyncio.CancelledError:
                break
            except Exception as ex:
                self.logger.exception(f"{self.name} had unexpected error.")

            try:
                await self.sleep(self._loop_interval_sec)
            except asyncio.CancelledError:
                break


    @abc.abstractmethod
    async def _run_once(self):
        raise NotImplementedError

