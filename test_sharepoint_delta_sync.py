from __future__ import annotations

import pytest

from jobscan.graph_client import GraphError, SharePointTarget
from jobscan import sharepoint_sync as sp


class FakeConnection:
    def __init__(self):
        self.closed = False
        self.commits = 0
        self.executed = []
        self.in_transaction = False

    def execute(self, statement, params=None):
        self.executed.append((str(statement), params or {}))
        return FakeResult(True)

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


class FakeResult:
    def __init__(self, scalar_value=None):
        self.scalar_value = scalar_value

    def scalar(self):
        return self.scalar_value


class FakeContext:
    def __init__(self, engine, conn):
        self.engine = engine
        self.conn = conn

    def __enter__(self):
        self.engine.begin_entries += 1
        self.conn.in_transaction = True
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        self.conn.in_transaction = False
        self.engine.begin_exits += 1
        return False


class FakeEngine:
    def __init__(self):
        self.conn = FakeConnection()
        self.lock_conn = FakeConnection()
        self.begin_entries = 0
        self.begin_exits = 0

    def begin(self):
        return FakeContext(self, self.conn)

    def connect(self):
        return self.lock_conn


class FakeGraphClient:
    def __init__(self, pages_by_url, on_get_json=None):
        self.pages_by_url = dict(pages_by_url)
        self.on_get_json = on_get_json
        self.urls = []

    def get_site(self, hostname, site_path):
        return {"id": "site-1"}

    def get_drive_by_name(self, site_id, library):
        return {"id": "drive-1"}

    def get_json(self, url):
        self.urls.append(url)
        if self.on_get_json:
            self.on_get_json(self, url)
        value = self.pages_by_url[url]
        if isinstance(value, BaseException):
            raise value
        return value


def install_db_fakes(monkeypatch, *, state=None, lock=True, patch_lock=True):
    calls = {
        "started": [],
        "succeeded": [],
        "failed": [],
        "interrupted": [],
        "partial": [],
        "upserted": [],
        "deleted": [],
        "reconciled_rows": [],
        "affected_rows": [],
        "released": [],
    }
    previous_by_id = {}

    monkeypatch.setattr(sp, "ensure_delta_tables", lambda conn: None)
    if patch_lock:
        monkeypatch.setattr(sp, "try_advisory_lock", lambda conn, drive_id: lock)
        monkeypatch.setattr(sp, "release_advisory_lock", lambda conn, drive_id: calls["released"].append(drive_id))
    monkeypatch.setattr(sp, "get_delta_state", lambda conn, drive_id: state)
    monkeypatch.setattr(sp, "mark_delta_started", lambda conn, site_id, drive_id, library, mode: calls["started"].append(mode))
    monkeypatch.setattr(sp, "mark_delta_succeeded", lambda conn, drive_id, delta_link, stats: calls["succeeded"].append(delta_link))
    monkeypatch.setattr(sp, "mark_delta_failed", lambda conn, drive_id, error: calls["failed"].append(error))
    monkeypatch.setattr(sp, "mark_delta_interrupted", lambda conn, drive_id, message="Delta sync interrupted": calls["interrupted"].append(message))
    monkeypatch.setattr(sp, "mark_delta_partial", lambda conn, drive_id, stats: calls["partial"].append((drive_id, stats.items_returned)))
    monkeypatch.setattr(sp, "soft_delete_inventory_item", lambda conn, drive_id, item: calls["deleted"].append(item["id"]))

    def upsert(_conn, row):
        calls["upserted"].append(row)
        previous = previous_by_id.get(row["drive_item_id"])
        previous_by_id[row["drive_item_id"]] = dict(row)
        return sp.changed_item_kind(previous, row)

    monkeypatch.setattr(sp, "upsert_inventory_item", upsert)

    def reconcile(_conn, drive_id, rows):
        calls["reconciled_rows"].extend(rows)
        return len(rows)

    monkeypatch.setattr(sp, "reconcile_documents_for_items", reconcile)
    monkeypatch.setattr(sp, "mark_deleted_documents", lambda conn, drive_id, ids: len(ids))

    def affected(_conn, drive_id, rows, deleted_ids):
        calls["affected_rows"].extend(rows)
        return {"JOB-1"} if rows or deleted_ids else set()

    monkeypatch.setattr(sp, "affected_jobs_for_items", affected)
    return calls


def target():
    return SharePointTarget.from_url("https://contoso.sharepoint.com/sites/Data", library="Documents")


def test_delta_initial_enumeration_multiple_pages_persists_final_delta_link(monkeypatch) -> None:
    calls = install_db_fakes(monkeypatch)
    client = FakeGraphClient(
        {
            "/drives/drive-1/root/delta": {
                "value": [{"id": "1", "name": "Job", "folder": {}, "parentReference": {"path": "/drives/drive-1/root:"}}],
                "@odata.nextLink": "next",
            },
            "next": {
                "value": [{"id": "2", "name": "Estimate.xlsx", "file": {"mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}, "webUrl": "https://sp/est.xlsx", "parentReference": {"path": "/drives/drive-1/root:/2026 ROOFING/PROPOSED/Job"}}],
                "@odata.deltaLink": "delta-secret-token",
            },
        }
    )

    stats = sp.run_delta_sync(engine=FakeEngine(), client=client, target=target())

    assert stats.mode == "initial"
    assert stats.pages_processed == 2
    assert stats.graph_requests == 2
    assert stats.delta_token_saved is True
    assert calls["succeeded"] == ["delta-secret-token"]


def test_advisory_lock_transaction_is_committed_before_graph_wait(monkeypatch) -> None:
    install_db_fakes(monkeypatch, patch_lock=False)
    engine = FakeEngine()
    observations = []

    def observe_graph_wait(_client, _url):
        observations.append((engine.lock_conn.commits, engine.conn.in_transaction, engine.begin_entries, engine.begin_exits))

    client = FakeGraphClient(
        {
            "/drives/drive-1/root/delta": {
                "value": [{"id": "1", "name": "Job", "folder": {}, "parentReference": {"path": "/drives/drive-1/root:"}}],
                "@odata.deltaLink": "delta-1",
            }
        },
        on_get_json=observe_graph_wait,
    )

    sp.run_delta_sync(engine=engine, client=client, target=target())

    assert observations
    commits_before_graph, in_transaction, begin_entries, begin_exits = observations[0]
    assert commits_before_graph >= 1
    assert in_transaction is False
    assert begin_entries == begin_exits
    assert engine.lock_conn.closed is True


def test_delta_pages_upsert_and_commit_incrementally_before_next_graph_page(monkeypatch) -> None:
    calls = install_db_fakes(monkeypatch)
    engine = FakeEngine()
    observations = []

    def observe_graph_page(_client, url):
        observations.append((url, len(calls["upserted"]), engine.begin_entries, engine.begin_exits))

    client = FakeGraphClient(
        {
            "/drives/drive-1/root/delta": {
                "value": [{"id": "1", "name": "First.pdf", "file": {}, "parentReference": {"path": "/drives/drive-1/root:/2026 ROOFING/PROPOSED/Job"}}],
                "@odata.nextLink": "next",
            },
            "next": {
                "value": [{"id": "2", "name": "Second.pdf", "file": {}, "parentReference": {"path": "/drives/drive-1/root:/2026 ROOFING/PROPOSED/Job"}}],
                "@odata.deltaLink": "delta-1",
            },
        },
        on_get_json=observe_graph_page,
    )

    sp.run_delta_sync(engine=engine, client=client, target=target())

    next_observation = [item for item in observations if item[0] == "next"][0]
    assert next_observation[1] == 1
    assert next_observation[2] == next_observation[3]
    assert len(calls["upserted"]) == 2


def test_delta_incremental_uses_saved_delta_link(monkeypatch) -> None:
    calls = install_db_fakes(monkeypatch, state={"delta_link": "saved-delta"})
    client = FakeGraphClient({"saved-delta": {"value": [], "@odata.deltaLink": "new-delta"}})

    stats = sp.run_delta_sync(engine=FakeEngine(), client=client, target=target())

    assert stats.mode == "incremental"
    assert client.urls == ["saved-delta"]
    assert calls["succeeded"] == ["new-delta"]


def test_delta_link_not_replaced_after_partial_failure(monkeypatch) -> None:
    calls = install_db_fakes(monkeypatch, state={"delta_link": "saved-delta"})
    client = FakeGraphClient({"saved-delta": RuntimeError("network down")})

    with pytest.raises(RuntimeError):
        sp.run_delta_sync(engine=FakeEngine(), client=client, target=target())

    assert calls["succeeded"] == []
    assert calls["failed"]


def test_interrupted_run_does_not_replace_delta_and_releases_lock(monkeypatch) -> None:
    calls = install_db_fakes(monkeypatch, state={"delta_link": "saved-delta"})
    client = FakeGraphClient({"saved-delta": KeyboardInterrupt()})

    with pytest.raises(KeyboardInterrupt):
        sp.run_delta_sync(engine=FakeEngine(), client=client, target=target())

    assert calls["succeeded"] == []
    assert calls["interrupted"] == ["Delta sync interrupted"]
    assert calls["released"] == ["drive-1"]


def test_410_gone_recovery_starts_fresh_full_delta_without_deleting_inventory(monkeypatch) -> None:
    calls = install_db_fakes(monkeypatch, state={"delta_link": "expired-delta"})
    client = FakeGraphClient(
        {
            "expired-delta": GraphError("Graph GET failed with 410: Gone"),
            "/drives/drive-1/root/delta": {"value": [], "@odata.deltaLink": "fresh-delta"},
        }
    )

    stats = sp.run_delta_sync(engine=FakeEngine(), client=client, target=target())

    assert stats.mode == "token_expired_full_refresh"
    assert calls["succeeded"] == ["fresh-delta"]
    assert calls["deleted"] == []


def test_limit_pages_writes_inventory_but_does_not_save_final_delta_link(monkeypatch) -> None:
    calls = install_db_fakes(monkeypatch)
    client = FakeGraphClient(
        {
            "/drives/drive-1/root/delta": {
                "value": [{"id": "1", "name": "Partial.pdf", "file": {}, "parentReference": {"path": "/drives/drive-1/root:/2026 ROOFING/PROPOSED/Job"}}],
                "@odata.nextLink": "next",
            },
            "next": {
                "value": [{"id": "2", "name": "Final.pdf", "file": {}, "parentReference": {"path": "/drives/drive-1/root:/2026 ROOFING/PROPOSED/Job"}}],
                "@odata.deltaLink": "delta-final",
            },
        }
    )

    stats = sp.run_delta_sync(engine=FakeEngine(), client=client, target=target(), limit_pages=1)

    assert stats.partial is True
    assert stats.pages_processed == 1
    assert len(calls["upserted"]) == 1
    assert calls["succeeded"] == []
    assert calls["partial"] == [("drive-1", 1)]
    assert client.urls == ["/drives/drive-1/root/delta"]


def test_add_update_move_rename_and_delete_handling() -> None:
    previous = {"name": "Old.pdf", "relative_path": "A/Old.pdf", "etag": "1", "ctag": "1", "size_bytes": 1}
    assert sp.changed_item_kind(None, previous) == "new"
    assert sp.changed_item_kind(previous, {**previous, "etag": "2"}) == "modified"
    assert sp.changed_item_kind(previous, {**previous, "relative_path": "B/Old.pdf"}) == "moved"
    assert sp.changed_item_kind(previous, {**previous, "name": "New.pdf"}) == "renamed"
    assert sp.changed_item_kind(previous, dict(previous)) == "unchanged"


def test_root_path_filtering() -> None:
    roots = ["2026 ROOFING/PROPOSED", "2026 FLOORING/COMPLETED"]
    assert sp.is_relevant_path("2026 ROOFING/PROPOSED/Job/Estimate.xlsx", roots)
    assert not sp.is_relevant_path("Archive/Job/Estimate.xlsx", roots)


def test_document_reconciliation_and_pending_only_for_changed_files(monkeypatch) -> None:
    calls = install_db_fakes(monkeypatch)
    unchanged_item = {"id": "1", "name": "Same.xlsx", "file": {}, "eTag": "same", "parentReference": {"path": "/drives/drive-1/root:/2026 ROOFING/PROPOSED/Job"}}
    changed_item = {"id": "2", "name": "Changed.xlsx", "file": {}, "eTag": "changed", "webUrl": "https://sp/changed", "parentReference": {"path": "/drives/drive-1/root:/2026 ROOFING/PROPOSED/Job"}}
    client = FakeGraphClient(
        {
            "/drives/drive-1/root/delta": {
                "value": [unchanged_item, changed_item],
                "@odata.deltaLink": "delta-1",
            }
        }
    )

    sp.run_delta_sync(engine=FakeEngine(), client=client, target=target())
    sp.run_delta_sync(engine=FakeEngine(), client=client, target=target(), full_refresh=True)

    reconciled_names = [row["name"] for row in calls["reconciled_rows"]]
    assert "Changed.xlsx" in reconciled_names


def test_advisory_lock_blocks_parallel_sync(monkeypatch) -> None:
    install_db_fakes(monkeypatch, lock=False)
    client = FakeGraphClient({})

    with pytest.raises(RuntimeError, match="already running"):
        sp.run_delta_sync(engine=FakeEngine(), client=client, target=target())


def test_advisory_lock_released_on_success(monkeypatch) -> None:
    calls = install_db_fakes(monkeypatch)
    client = FakeGraphClient({"/drives/drive-1/root/delta": {"value": [], "@odata.deltaLink": "delta-1"}})

    sp.run_delta_sync(engine=FakeEngine(), client=client, target=target())

    assert calls["released"] == ["drive-1"]


def test_advisory_lock_released_on_failure(monkeypatch) -> None:
    calls = install_db_fakes(monkeypatch, state={"delta_link": "saved-delta"})
    client = FakeGraphClient({"saved-delta": RuntimeError("network down")})

    with pytest.raises(RuntimeError):
        sp.run_delta_sync(engine=FakeEngine(), client=client, target=target())

    assert calls["released"] == ["drive-1"]


def test_progress_output_reports_startup_and_page_counts(monkeypatch, capsys) -> None:
    install_db_fakes(monkeypatch)
    client = FakeGraphClient(
        {
            "/drives/drive-1/root/delta": {
                "value": [{"id": "1", "name": "Progress.pdf", "file": {}, "parentReference": {"path": "/drives/drive-1/root:/2026 ROOFING/PROPOSED/Job"}}],
                "@odata.deltaLink": "delta-1",
            }
        }
    )

    sp.run_delta_sync(
        engine=FakeEngine(),
        client=client,
        target=target(),
        database_url="postgresql+psycopg2://user:secret@db.example.com/spraytec_ops",
    )

    out = capsys.readouterr().out
    assert "Starting Microsoft Graph delta sync" in out
    assert "Site URL: https://contoso.sharepoint.com/sites/Data" in out
    assert "Library: Documents" in out
    assert "Database target: db.example.com/spraytec_ops" in out
    assert "Mode: initial delta" in out
    assert "Previous delta state exists: no" in out
    assert "Delta page 1:" in out
    assert "items=1" in out
    assert "sharepoint_drive_items_upserted=1" in out
    assert "secret" not in out


def test_delta_link_not_logged_in_stats_output(capsys) -> None:
    stats = sp.DeltaSyncStats(mode="incremental", drive_id="drive-1", delta_token_saved=True)
    sp.print_delta_stats(stats)
    out = capsys.readouterr().out
    assert "deltaLink" not in out
    assert "token" not in out.lower()
