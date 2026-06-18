from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, URL, make_url
from sqlalchemy.exc import DBAPIError, OperationalError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DatabaseTarget:
    host: str
    database: str
    is_neon: bool
    uses_pooler: bool


@dataclass
class ReadQueryResult:
    ok: bool
    value: Any = None
    error: Exception | None = None
    attempts: int = 0
    recovered: bool = False


def database_target(database_url: str) -> DatabaseTarget:
    try:
        url = make_url(database_url)
    except Exception:
        return DatabaseTarget(host="", database="", is_neon=False, uses_pooler=False)
    host = url.host or ""
    database = (url.database or "").lstrip("/")
    host_lower = host.lower()
    return DatabaseTarget(
        host=host,
        database=database,
        is_neon="neon.tech" in host_lower,
        uses_pooler="-pooler" in host_lower,
    )


def _postgres_connect_args(url: URL) -> dict[str, Any]:
    query = {key.lower(): value for key, value in dict(url.query).items()}
    connect_args: dict[str, Any] = {}
    if "connect_timeout" not in query:
        connect_args["connect_timeout"] = 15
    if "sslmode" not in query:
        connect_args["sslmode"] = "require"
    return connect_args


def resilient_engine_kwargs(database_url: str) -> dict[str, Any]:
    url = make_url(database_url)
    kwargs: dict[str, Any] = {
        "future": True,
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }
    if url.get_backend_name().startswith("postgresql"):
        kwargs.update(
            {
                "pool_size": 3,
                "max_overflow": 2,
                "pool_timeout": 30,
            }
        )
        connect_args = _postgres_connect_args(url)
        if connect_args:
            kwargs["connect_args"] = connect_args
    return kwargs


def create_resilient_engine(database_url: str) -> Engine:
    return create_engine(database_url, **resilient_engine_kwargs(database_url))


def is_stale_connection_error(exc: Exception) -> bool:
    if isinstance(exc, (OperationalError, DBAPIError)) and getattr(exc, "connection_invalidated", False):
        return True
    message = str(exc).lower()
    return isinstance(exc, (OperationalError, DBAPIError)) and any(
        marker in message
        for marker in (
            "ssl connection has been closed unexpectedly",
            "server closed the connection unexpectedly",
            "connection already closed",
        )
    )


def execute_read_with_retry(
    engine: Engine,
    statement: Any,
    params: dict[str, Any] | None = None,
    retries: int = 1,
    read_fn: Callable[[Any, Any, dict[str, Any] | None], Any] | None = None,
) -> ReadQueryResult:
    """Run a read with one bounded reconnect retry for stale pooled connections."""
    attempts = 0
    last_error: Exception | None = None
    max_attempts = max(1, retries + 1)
    while attempts < max_attempts:
        attempts += 1
        logger.info("database read started")
        try:
            with engine.connect() as connection:
                if read_fn is not None:
                    value = read_fn(connection, statement, params)
                else:
                    value = connection.execute(statement, params or {}).mappings().all()
            if attempts > 1:
                logger.info("database read retry succeeded")
            return ReadQueryResult(ok=True, value=value, attempts=attempts, recovered=attempts > 1)
        except Exception as exc:
            last_error = exc
            if not is_stale_connection_error(exc) or attempts >= max_attempts:
                if attempts > 1:
                    logger.warning("database read retry failed")
                return ReadQueryResult(ok=False, error=exc, attempts=attempts, recovered=False)
            logger.warning("stale database connection detected")
            engine.dispose()
            logger.info("database pool disposed")
            sleep_seconds = min(1.0, 0.25 * attempts)
            time.sleep(sleep_seconds)
            logger.info("database read retry attempted")
    return ReadQueryResult(ok=False, error=last_error, attempts=attempts, recovered=False)
