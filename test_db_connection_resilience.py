from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy.exc import OperationalError, SQLAlchemyError

from jobscan.db_connections import (
    database_target,
    execute_read_with_retry,
    resilient_engine_kwargs,
)


class FakeConnection:
    def __init__(self, engine):
        self.engine = engine

    def __enter__(self):
        self.engine.open_connections += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        self.engine.closed_connections += 1
        return False


class FakeEngine:
    def __init__(self, failures):
        self.failures = list(failures)
        self.disposed = 0
        self.open_connections = 0
        self.closed_connections = 0

    def connect(self):
        if self.failures:
            failure = self.failures.pop(0)
            if failure is not None:
                raise failure
        return FakeConnection(self)

    def dispose(self):
        self.disposed += 1


def stale_error() -> OperationalError:
    return OperationalError("SELECT 1", {}, Exception("SSL connection has been closed unexpectedly"), connection_invalidated=True)


def sql_error() -> SQLAlchemyError:
    return SQLAlchemyError("syntax error at or near SELECTT")


def test_resilient_engine_kwargs_enable_pre_ping_and_postgres_pool_settings() -> None:
    kwargs = resilient_engine_kwargs("postgresql+psycopg2://user:pass@example.neon.tech/db")

    assert kwargs["pool_pre_ping"] is True
    assert kwargs["pool_recycle"] == 300
    assert kwargs["pool_size"] == 3
    assert kwargs["max_overflow"] == 2
    assert kwargs["pool_timeout"] == 30
    assert kwargs["connect_args"]["connect_timeout"] == 15
    assert kwargs["connect_args"]["sslmode"] == "require"


def test_resilient_engine_kwargs_preserves_explicit_sslmode() -> None:
    kwargs = resilient_engine_kwargs("postgresql+psycopg2://user:pass@example.neon.tech/db?sslmode=verify-full")

    assert "sslmode" not in kwargs["connect_args"]


def test_database_target_is_safe_and_detects_neon_pooler() -> None:
    target = database_target("postgresql+psycopg2://user:secret@ep-old-field-pooler.us-east-2.aws.neon.tech/spraytec?sslmode=require")

    assert target.host == "ep-old-field-pooler.us-east-2.aws.neon.tech"
    assert target.database == "spraytec"
    assert target.is_neon is True
    assert target.uses_pooler is True


def test_stale_connection_operational_error_retries_once_and_disposes() -> None:
    engine = FakeEngine([stale_error(), None])

    result = execute_read_with_retry(engine, "SELECT 1", read_fn=lambda *_args: "ok")

    assert result.ok is True
    assert result.value == "ok"
    assert result.recovered is True
    assert result.attempts == 2
    assert engine.disposed == 1


def test_successful_retry_hides_initial_error() -> None:
    engine = FakeEngine([stale_error(), None])

    result = execute_read_with_retry(engine, "SELECT 1", read_fn=lambda *_args: {"rows": 1})

    assert result.ok is True
    assert result.error is None
    assert result.value == {"rows": 1}


def test_second_failure_returns_controlled_error() -> None:
    engine = FakeEngine([stale_error(), stale_error()])

    result = execute_read_with_retry(engine, "SELECT 1", read_fn=lambda *_args: "never")

    assert result.ok is False
    assert isinstance(result.error, OperationalError)
    assert result.attempts == 2
    assert engine.disposed == 1


def test_sql_errors_unrelated_to_disconnects_are_not_retried() -> None:
    engine = FakeEngine([sql_error()])

    result = execute_read_with_retry(engine, "SELECTT 1", read_fn=lambda *_args: "never")

    assert result.ok is False
    assert result.attempts == 1
    assert engine.disposed == 0


def test_connections_are_closed_after_reads() -> None:
    engine = FakeEngine([None])

    result = execute_read_with_retry(engine, "SELECT 1", read_fn=lambda *_args: "ok")

    assert result.ok is True
    assert engine.open_connections == 1
    assert engine.closed_connections == 1


def test_dashboard_user_message_omits_docker_and_database_url(monkeypatch) -> None:
    import dashboard.app as app

    captured: list[str] = []

    class FakeExpander:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeStreamlit:
        def error(self, value):
            captured.append(str(value))

        def button(self, *_args, **_kwargs):
            return False

        def expander(self, *_args, **_kwargs):
            return FakeExpander()

        def write(self, value):
            captured.append(str(value))

        def warning(self, value):
            captured.append(str(value))

        def caption(self, value):
            captured.append(str(value))

    secret_url = "postgresql+psycopg2://user:secret@ep-test.neon.tech/spraytec"
    monkeypatch.setattr(app, "st", FakeStreamlit())
    monkeypatch.setattr(app, "DATABASE_URL", secret_url)

    app.show_database_error(RuntimeError(f"could not connect to {secret_url}"))

    combined = "\n".join(captured)
    assert "Docker" not in combined
    assert secret_url not in combined
    assert "secret" not in combined
    assert "temporarily unavailable" in combined
