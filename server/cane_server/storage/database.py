import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from cane_server.storage.models import Base


class Database:
    def __init__(self, url: str) -> None:
        self._url = url
        self.engine = create_async_engine(url)
        self._sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self._write_lock = asyncio.Lock()
        if self._is_sqlite:
            event.listen(self.engine.sync_engine, "connect", _configure_sqlite_connection)

    @property
    def _is_sqlite(self) -> bool:
        return make_url(self._url).get_backend_name() == "sqlite"

    async def create_schema(self) -> None:
        self._ensure_sqlite_directory()
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        async with self._write_lock, self._sessions() as session, session.begin():
            yield session

    async def dispose(self) -> None:
        await self.engine.dispose()

    def _ensure_sqlite_directory(self) -> None:
        if not self._is_sqlite:
            return
        database_path = make_url(self._url).database
        if database_path and database_path != ":memory:":
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)


def _configure_sqlite_connection(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()
