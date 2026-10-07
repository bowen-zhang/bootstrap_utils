import json
import logging
import protobuf
import typing
import yaml

from pathlib import Path


_T = typing.TypeVar('_T', bound=protobuf.Message)
_logger = logging.getLogger(__name__)


class SettingsLoader(typing.Generic[_T]):
    def __init__(self, proto_cls: typing.Type[_T]):
        self._proto_cls = proto_cls

    def load(self, config_path: Path) -> _T:
        print(f"Loading settings from {config_path}...")
        _logger.info("Loading settings from %s", config_path)
        with config_path.open("r", encoding="utf-8") as fh:
            parsed = yaml.safe_load(fh) or {}

        message = self._proto_cls.from_json(json.dumps(parsed))
   
        return message