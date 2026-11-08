import json
import logging
import protobuf
import typing
import yaml

from pathlib import Path


_T = typing.TypeVar('_T', bound=protobuf.Message)
_logger = logging.getLogger(__name__)


class YamlLoader(typing.Generic[_T]):
    def __init__(self, proto_cls: typing.Type[_T]):
        self._proto_cls = proto_cls

    def load(self, config_path: Path) -> _T:
        with open(config_path, "r", encoding="utf-8") as fh:
            parsed = yaml.safe_load(fh) or {}

        message = self._proto_cls.from_json(json.dumps(parsed))
   
        return message

    def load_all(self, config_path: Path) -> list[_T]:
        configs = []
        for file_path in config_path.glob("*.yaml"):
            configs.append(self.load(file_path))

        _logger.info(f"Loaded {len(configs)} {self._proto_cls.__name__}.")
        return configs
