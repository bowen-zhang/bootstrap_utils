import asyncio
import logging
import protobuf
import signal
import sys

from collections.abc import Callable
from contextlib import asynccontextmanager
from connectrpc.server import ConnectASGIApplication
from connectrpc_grpcreflect import ServerReflectionASGIApplication, ServerReflectionService
from starlette.applications import Starlette
from starlette.routing import Mount


def _setup_logging(log_level: str = "INFO"):
    log_format = "[%(levelname)s] {%(name)s} %(message)s"
    
    # 1. Configure standard stdout handler
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(logging.Formatter(log_format))
    
    # 2. Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers = [stream_handler] # Replace default handlers


class ServerApp(Starlette):
    def __init__(
            self, 
            apps: list[ConnectASGIApplication],
            descriptors: list[protobuf.DescFile],
            stop_handler: Callable[[], None] | None = None,
            dev: bool = False,
        ):
        self._stop_handler = stop_handler

        reflection_app = ServerReflectionASGIApplication(ServerReflectionService(*descriptors))
        all_apps = apps + [reflection_app]

        super().__init__(
            routes=[Mount(app.path, app) for app in all_apps],
            lifespan=self.lifespan if stop_handler else None,
        )

        _setup_logging("DEBUG" if dev else "INFO")

    def _stop(self) -> None:
        if self._stop_handler:
            self._stop_handler()

    @asynccontextmanager
    async def lifespan(self, app: Starlette):
        loop = asyncio.get_running_loop()

        # Install in the worker, where the service instances live. The reload
        # supervisor also receives terminal Ctrl+C and initiates worker shutdown.
        previous_handlers = {
            sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)
        }
        for sig in previous_handlers:
            loop.add_signal_handler(sig, self._stop)
        try:
            yield
        finally:
            self._stop()
            for sig, previous_handler in previous_handlers.items():
                loop.remove_signal_handler(sig)
                signal.signal(sig, previous_handler)
