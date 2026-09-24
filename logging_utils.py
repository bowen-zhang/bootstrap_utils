import logging
import sys

class _EnrichedLogger(logging.Logger):

    def makeRecord(
        self,
        name,
        level,
        fn,
        lno,
        msg,
        args,
        exc_info,
        func=None,
        extra=None,
        sinfo=None,
    ):
        # Check if there is extra data and that it's a dictionary
        if extra and isinstance(extra, dict):
            # Format the extra data into a string (e.g., key=value)
            extra_str = " | ".join(f"{k}={v}" for k, v in extra.items())

            # Append it to the main message string
            msg = f"{msg} ({extra_str})"

            # Clear extra out so it doesn't get unpacked onto the LogRecord
            extra = None

        # Pass everything to the original parent method
        return super().makeRecord(
            name, level, fn, lno, msg, args, exc_info, func, extra, sinfo
        )


def setup_logging(log_level: str = "INFO"):
    log_format = "[%(levelname)s] {%(name)s} %(message)s"

    logging.setLoggerClass(_EnrichedLogger)
    for _, logger_obj in logging.Logger.manager.loggerDict.items():
        if isinstance(logger_obj, logging.Logger):
            logger_obj.__class__ = _EnrichedLogger

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(logging.Formatter(log_format))
    
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers = [stream_handler] # Replace default handlers
