from __future__ import annotations

import abc
import logging

from pathlib import Path
from typing import Any
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.pool import StaticPool


_logger = logging.getLogger(__name__)


class ProtoSqliteTable:
    def __init__(self, connection: Connection, table_name: str, subject_class: type, auto_id: bool = True):
        self._connection = connection
        self._table_name = table_name
        self._subject_class = subject_class
        self._auto_id = auto_id

    @property
    def table_name(self) -> str:
        return self._table_name

    @property
    def subject_class(self) -> type:
        return self._subject_class

    @property
    def auto_id(self) -> bool:
        return self._auto_id

    def execute(self, query: str, params: dict[str, Any] | None = None):
        return self._connection.execute(text(query), params or {})

    def commit(self):
        self._connection.commit()

    def create(self):
        self.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._table_name} (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL,

                CONSTRAINT valid_json CHECK (json_valid(data))
            )
            """
        )
        self.commit()

    def save(self, subject: Any) -> None:
        if not isinstance(subject, self._subject_class):
            raise ValueError(f"Expected subject of type {self._subject_class.__name__}, got {type(subject).__name__}")

        self._connection.execute(
            text(f"INSERT OR REPLACE INTO {self._table_name} (id, data) VALUES (:id, :data)"),
            {"id": subject.id, "data": subject.to_json()},
        )
        self._connection.commit()

    def get(self, id: str) -> Any | None:
        return self.find_one(filter_clause="id = :subject_id", filter_params={"subject_id": id})

    def find_one(self, filter_clause: str | None = None, filter_params: dict[str, Any] | None = None) -> Any | None:
        row = self._connection.execute(
            text(f"SELECT id, data FROM {self._table_name}" + (f" WHERE {filter_clause}" if filter_clause else "") + " LIMIT 1"),
            filter_params or {},
        ).fetchone()
        if row is None:
            return None
        return self._deserialize_subject(row.id, row.data)

    def find_all(self, filter_clause: str | None = None, filter_params: dict[str, Any] | None = None):
        rows = self._connection.execute(
            text(f"SELECT id, data FROM {self._table_name}" + (f" WHERE {filter_clause}" if filter_clause else "") + " ORDER BY id"),
            filter_params or {}
        ).fetchall()
        for row in rows:
            yield self._deserialize_subject(row.id, row.data)

    def delete(self, filter_clause: str | None = None, filter_params: dict[str, Any] | None = None) -> int:
        result = self._connection.execute(
            text(f"DELETE FROM {self._table_name}" + (f" WHERE {filter_clause}" if filter_clause else "")),
            filter_params or {},
        )
        self._connection.commit()
        return result.rowcount

    def count(self, filter_clause: str | None = None, filter_params: dict[str, Any] | None = None) -> int:
        result = self._connection.execute(
            text(f"SELECT COUNT(*) FROM {self._table_name}" + (f" WHERE {filter_clause}" if filter_clause else "")),
            filter_params or {},
        ).scalar()
        return result or 0

    def _deserialize_subject(self, id: str, data: str) -> Any:
        subject_cls = self._subject_class()
        subject = subject_cls.from_json(data)        
        subject.id = id
        return subject


class ProtoSqliteDatabase(abc.ABC):
    _connection: Connection
    _tables_by_class: dict[type, ProtoSqliteTable]

    def __init__(self, connection: Connection):
        self._connection = connection
        self._tables_by_class = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._connection.close()

    def get_table_by_subject(self, subject: Any) -> ProtoSqliteTable:
        cls = type(subject)
        return self.get_table_by_subject_class(cls)

    def get_table_by_subject_class(self, cls: type) -> ProtoSqliteTable:
        if cls not in self._tables_by_class:
            self._tables_by_class[cls] = self._get_table(self._connection, cls)
        return self._tables_by_class[cls]

    @abc.abstractmethod
    def _get_table(self, connection: Connection, subject_cls: type) -> ProtoSqliteTable:
        pass


class ProtoSqliteManager[T: ProtoSqliteDatabase]:
    def __init__(self, database_path: Path, database_cls: type[T]):
        _logger.info(f"Opening database at {database_path}...")
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._database_cls = database_cls

        self._engine = create_engine(
            f"sqlite:///{database_path}",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )

    def close(self):
        self._engine.dispose()

    def get_database(self) -> T:
        return self._database_cls(self._engine.connect())
