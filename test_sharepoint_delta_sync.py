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
        if self.engine.fail_on_exit_count > 0 and self.engine.begin_exits > self.engine.fail_on_exit_after:
            self.engine.fail_on_exit_count -= 1
            raise sp.OperationalError("COMMIT", {}, Exception("SSL SYSCALL error: Operation timed out"))
        return False


class FakeEngine:
    def __init__(self):
        self.conn = FakeConnection()
        self.lock_conn = FakeConnection()
        self.begin_entries = 0
        self.begin_exits = 0
        self.dispose_calls = 0
        self.fail_on_exit_count = 0
        self.fail_on_exit_after = 0

    def begin(self):
        return FakeContext(self, self.conn)

    def connect(self):
        return self.lock_conn

    def dispose(self):
        self.dispose_calls += 1


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


class FakeMappingResult:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class FakeStatusConnection(FakeConnection):
    def __init__(self, rows):
        super().__init__()
        self.rows = rows

    def execute(self, statement, params=None):
        self.executed.append((str(statement), params or {}))
        return FakeMappingResult(self.rows)


class FakeStatusEngine(FakeEngine):
    def __init__(self, rows):
        super().__init__()
        self.conn = FakeStatusConnection(rows)


class InventoryFakeConnection(FakeConnection):
    def __init__(self, existing_rows=None):
        super().__init__()
        self.existing_rows = existing_rows or []
        self.written_rows = []

    def execute(self, statement, params=None):
        self.executed.append((str(statement), params or {}))
        if isinstance(params, list):
            self.written_rows.extend(params)
            return FakeResult(True)
        if "FROM sharepoint_drive_items" in str(statement):
            return FakeMappingResult(self.existing_rows)
        return FakeResult(True)


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
        "checkpoints": [],
        "cleared_checkpoints": [],
        "bulk_results": [],
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
    monkeypatch.setattr(sp, "mark_delta_failed_at_page", lambda conn, drive_id, error, page_number: calls["failed"].append((error, page_number)))
    monkeypatch.setattr(sp, "mark_delta_interrupted", lambda conn, drive_id, message="Delta sync interrupted": calls["interrupted"].append(message))
    monkeypatch.setattr(sp, "mark_delta_partial", lambda conn, drive_id, stats: calls["partial"].append((drive_id, stats.items_returned)))
    monkeypatch.setattr(sp, "save_delta_checkpoint", lambda conn, drive_id, next_link, page_number, items_seen: calls["checkpoints"].append((drive_id, page_number, items_seen, bool(next_link))))
    monkeypatch.setattr(sp, "clear_delta_checkpoint", lambda conn, drive_id: calls["cleared_checkpoints"].append(drive_id))
    monkeypatch.setattr(sp, "soft_delete_inventory_item", lambda conn, drive_id, item: calls["deleted"].append(item["id"]))

    def upsert(_conn, row):
        calls["upserted"].append(row)
        previous = previous_by_id.get(row["drive_item_id"])
        previous_by_id[row["drive_item_id"]] = dict(row)
        return sp.changed_item_kind(previous, row)

    monkeypatch.setattr(sp, "upsert_inventory_item", upsert)

    def bulk_upsert(_conn, rows, *, force_updates=False):
        out = {}
        inserted = 0
        updated = 0
        skipped = 0
        for row in rows:
            previous = previous_by_id.get(str(row["drive_item_id"]))
            kind = upsert(_conn, row)
            out[str(row["drive_item_id"])] = kind
            if previous is None:
                inserted += 1
            elif kind == "unchanged" and not force_updates:
                skipped += 1
            else:
                updated += 1
        result = sp.InventoryUpsertResult(change_kinds=out, inserted=inserted, updated=updated, skipped_unchanged=skipped)
        calls["bulk_results"].append(result)
        return result

    monkeypatch.setattr(sp, "upsert_inventory_items_bulk", bulk_upsert)

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


def inventory_row(item_id="item-1", **overrides):
    row = {
        "drive_id": "drive-1",
        "drive_item_id": item_id,
        "parent_item_id": "parent-1",
        "name": "Doc.pdf",
        "web_url": "https://sp/doc.pdf",
        "parent_path": "Jobs/Acme",
        "relative_path": "Jobs/Acme/Doc.pdf",
        "is_folder": False,
        "is_file": True,
        "mime_type": "application/pdf",
        "size_bytes": 100,
        "etag": "etag-1",
        "ctag": "ctag-1",
        "last_modified_at": "2026-01-02T00:00:00Z",
        "metadata_json": "{}",
    }
    row.update(overrides)
    return row


def existing_inventory_row(item_id="item-1", **overrides):
    row = inventory_row(item_id, **overrides)
    row["deleted_at"] = overrides.get("deleted_at")
    return row


def test_existing_unchanged_inventory_row_is_skipped_not_updated() -> None:
    conn = InventoryFakeConnection([existing_inventory_row()])

    result = sp.upsert_inventory_items_bulk(conn, [inventory_row()])

    assert result.inserted == 0
    assert result.updated == 0
    assert result.skipped_unchanged == 1
    assert result.change_kinds == {"item-1": "unchanged"}
    assert conn.written_rows == []


def test_changed_etag_inventory_row_is_updated() -> None:
    conn = InventoryFakeConnection([existing_inventory_row(etag="old")])

    result = sp.upsert_inventory_items_bulk(conn, [inventory_row(etag="new")])

    assert result.updated == 1
    assert result.skipped_unchanged == 0
    assert result.change_kinds == {"item-1": "modified"}
    assert conn.written_rows == [inventory_row(etag="new")]


def test_changed_path_and_name_inventory_row_is_updated() -> None:
    conn = InventoryFakeConnection([existing_inventory_row(name="Old.pdf", relative_path="Jobs/Acme/Old.pdf")])
    row = inventory_row(name="New.pdf", relative_path="Jobs/Acme/New.pdf")

    result = sp.upsert_inventory_items_bulk(conn, [row])

    assert result.updated == 1
    assert result.change_kinds == {"item-1": "moved"}
    assert conn.written_rows == [row]


def test_new_inventory_row_is_inserted() -> None:
    conn = InventoryFakeConnection([])
    row = inventory_row()

    result = sp.upsert_inventory_items_bulk(conn, [row])

    assert result.inserted == 1
    assert result.updated == 0
    assert result.skipped_unchanged == 0
    assert result.change_kinds == {"item-1": "new"}
    assert conn.written_rows == [row]


def test_deleted_facet_row_marks_inventory_deleted(monkeypatch) -> None:
    conn = InventoryFakeConnection([])
    monkeypatch.setattr(sp, "upsert_inventory_items_bulk", lambda *_args, **_kwargs: sp.InventoryUpsertResult(change_kinds={}))
    monkeypatch.setattr(sp, "reconcile_documents_for_items", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(sp, "mark_deleted_documents", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(sp, "affected_jobs_for_items", lambda *_args, **_kwargs: set())

    counts = sp.process_delta_page(
        conn,
        drive_id="drive-1",
        page={"value": [{"id": "deleted-1", "deleted": {}}]},
        roots=[],
        stats=sp.DeltaSyncStats(mode="initial", drive_id="drive-1"),
    )

    assert counts["deleted"] == 1
    assert any("UPDATE sharepoint_drive_items" in statement for statement, _params in conn.executed)
    assert any((params or {}).get("drive_item_id") == "deleted-1" for _statement, params in conn.executed)


def test_force_inventory_updates_rewrites_unchanged_rows() -> None:
    conn = InventoryFakeConnection([existing_inventory_row()])
    row = inventory_row()

    result = sp.upsert_inventory_items_bulk(conn, [row], force_updates=True)

    assert result.inserted == 0
    assert result.updated == 1
    assert result.skipped_unchanged == 0
    assert result.change_kinds == {"item-1": "unchanged"}
    assert conn.written_rows == [row]


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
    assert calls["checkpoints"] == [("drive-1", 1, 1, True)]


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


def test_db_operational_error_retries_same_page_before_next_graph(monkeypatch) -> None:
    calls = install_db_fakes(monkeypatch)
    monkeypatch.setattr(sp.time, "sleep", lambda _seconds: None)
    engine = FakeEngine()
    engine.fail_on_exit_count = 1
    engine.fail_on_exit_after = 2
    observations = []

    def observe_graph_page(_client, url):
        observations.append((url, len(calls["upserted"])))

    client = FakeGraphClient(
        {
            "/drives/drive-1/root/delta": {
                "value": [{"id": "1", "name": "First.pdf", "file": {}, "parentReference": {"path": "/drives/drive-1/root:/2026 ROOFING/PROPOSED/Job"}}],
                "@odata.nextLink": "next",
            },
            "next": {"value": [], "@odata.deltaLink": "delta-final"},
        },
        on_get_json=observe_graph_page,
    )

    stats = sp.run_delta_sync(engine=engine, client=client, target=target())

    assert client.urls == ["/drives/drive-1/root/delta", "next"]
    assert observations[1] == ("next", 2)
    assert stats.db_retries == 1
    assert engine.dispose_calls == 1
    assert stats.items_returned == 1
    assert calls["checkpoints"][-1] == ("drive-1", 1, 1, True)


def test_checkpoint_does_not_advance_when_page_db_retries_fail(monkeypatch) -> None:
    calls = install_db_fakes(monkeypatch)
    monkeypatch.setattr(sp.time, "sleep", lambda _seconds: None)

    def fail_page(*_args, **_kwargs):
        raise sp.OperationalError("INSERT", {}, Exception("could not receive data from server: Operation timed out"))

    monkeypatch.setattr(sp, "process_delta_page", fail_page)
    client = FakeGraphClient(
        {
            "/drives/drive-1/root/delta": {
                "value": [{"id": "1", "name": "First.pdf", "file": {}, "parentReference": {"path": "/drives/drive-1/root:/2026 ROOFING/PROPOSED/Job"}}],
                "@odata.nextLink": "next",
            }
        }
    )

    with pytest.raises(sp.OperationalError):
        sp.run_delta_sync(engine=FakeEngine(), client=client, target=target())

    assert calls["checkpoints"] == []
    assert calls["succeeded"] == []
    assert calls["failed"][0][1] == 1
    assert calls["released"] == ["drive-1"]


def test_delta_incremental_uses_saved_delta_link(monkeypatch) -> None:
    calls = install_db_fakes(monkeypatch, state={"delta_link": "saved-delta"})
    client = FakeGraphClient({"saved-delta": {"value": [], "@odata.deltaLink": "new-delta"}})

    stats = sp.run_delta_sync(engine=FakeEngine(), client=client, target=target())

    assert stats.mode == "incremental"
    assert client.urls == ["saved-delta"]
    assert calls["succeeded"] == ["new-delta"]


def test_incremental_delta_does_not_write_initial_checkpoint(monkeypatch) -> None:
    calls = install_db_fakes(monkeypatch, state={"delta_link": "saved-delta"})
    client = FakeGraphClient(
        {
            "saved-delta": {"value": [{"id": "1", "name": "A.pdf", "file": {}, "parentReference": {"path": "/drives/drive-1/root:/Jobs"}}], "@odata.nextLink": "next"},
            "next": {"value": [], "@odata.deltaLink": "new-delta"},
        }
    )

    stats = sp.run_delta_sync(engine=FakeEngine(), client=client, target=target())

    assert stats.mode == "incremental"
    assert calls["checkpoints"] == []
    assert calls["succeeded"] == ["new-delta"]


def test_initial_delta_resumes_from_checkpoint_next_link(monkeypatch, capsys) -> None:
    calls = install_db_fakes(
        monkeypatch,
        state={"delta_link": None, "checkpoint_next_link": "checkpoint-next", "checkpoint_page": 7, "checkpoint_items_seen": 700},
    )
    client = FakeGraphClient({"checkpoint-next": {"value": [], "@odata.deltaLink": "delta-final"}})

    stats = sp.run_delta_sync(engine=FakeEngine(), client=client, target=target())

    out = capsys.readouterr().out
    assert stats.mode == "initial_resume"
    assert client.urls == ["checkpoint-next"]
    assert "Resuming initial delta from checkpoint page 7" in out
    assert "checkpoint-next" not in out
    assert calls["succeeded"] == ["delta-final"]


def test_restart_initial_delta_clears_checkpoint_only_when_requested(monkeypatch) -> None:
    calls = install_db_fakes(
        monkeypatch,
        state={"delta_link": None, "checkpoint_next_link": "checkpoint-next", "checkpoint_page": 7, "checkpoint_items_seen": 700},
    )
    client = FakeGraphClient({"/drives/drive-1/root/delta": {"value": [], "@odata.deltaLink": "delta-final"}})

    stats = sp.run_delta_sync(engine=FakeEngine(), client=client, target=target(), restart_initial_delta=True)

    assert stats.mode == "initial"
    assert client.urls == ["/drives/drive-1/root/delta"]
    assert calls["cleared_checkpoints"] == ["drive-1"]
    assert calls["succeeded"] == ["delta-final"]


def test_invalid_initial_checkpoint_410_requires_explicit_restart(monkeypatch, capsys) -> None:
    calls = install_db_fakes(
        monkeypatch,
        state={"delta_link": None, "checkpoint_next_link": "expired-checkpoint", "checkpoint_page": 7, "checkpoint_items_seen": 700},
    )
    client = FakeGraphClient({"expired-checkpoint": GraphError("Graph GET failed with 410: Gone")})

    with pytest.raises(GraphError):
        sp.run_delta_sync(engine=FakeEngine(), client=client, target=target())

    out = capsys.readouterr().out
    assert "checkpoint is no longer valid" in out
    assert calls["cleared_checkpoints"] == []
    assert calls["succeeded"] == []


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
    assert calls["checkpoints"] == [("drive-1", 1, 1, True)]
    assert client.urls == ["/drives/drive-1/root/delta"]


def test_checkpoint_advances_when_all_inventory_rows_are_skipped(monkeypatch) -> None:
    calls = install_db_fakes(monkeypatch)

    def skip_all(_conn, rows, *, force_updates=False):
        return sp.InventoryUpsertResult(
            change_kinds={str(row["drive_item_id"]): "unchanged" for row in rows},
            skipped_unchanged=len(rows),
        )

    monkeypatch.setattr(sp, "upsert_inventory_items_bulk", skip_all)
    client = FakeGraphClient(
        {
            "/drives/drive-1/root/delta": {
                "value": [{"id": "1", "name": "AlreadyThere.pdf", "file": {}, "parentReference": {"path": "/drives/drive-1/root:/2026 ROOFING/PROPOSED/Job"}}],
                "@odata.nextLink": "next",
            },
            "next": {"value": [], "@odata.deltaLink": "delta-final"},
        }
    )

    stats = sp.run_delta_sync(engine=FakeEngine(), client=client, target=target())

    assert stats.inventory_skipped_unchanged == 1
    assert calls["checkpoints"] == [("drive-1", 1, 1, True)]
    assert calls["succeeded"] == ["delta-final"]


def test_add_update_move_rename_and_delete_handling() -> None:
    previous = {"name": "Old.pdf", "relative_path": "A/Old.pdf", "etag": "1", "ctag": "1", "size_bytes": 1}
    assert sp.changed_item_kind(None, previous) == "new"
    assert sp.changed_item_kind(previous, {**previous, "etag": "2"}) == "modified"
    assert sp.changed_item_kind(previous, {**previous, "relative_path": "B/Old.pdf"}) == "moved"
    assert sp.changed_item_kind(previous, {**previous, "name": "New.pdf"}) == "renamed"
    assert sp.changed_item_kind(previous, dict(previous)) == "unchanged"


def test_image_metadata_is_slimmed_by_default() -> None:
    row = sp.item_inventory_row(
        "drive-1",
        {
            "id": "img-1",
            "name": "CompanyCam.jpg",
            "file": {"mimeType": "image/jpeg", "hashes": {"quickXorHash": "secret-heavy"}},
            "photo": {"takenDateTime": "2026-01-01T12:00:00Z"},
            "parentReference": {"path": "/drives/drive-1/root:/CompanyCam Pics 2026"},
            "webUrl": "https://example/image",
            "size": 123,
        },
    )

    metadata = sp.json.loads(row["metadata_json"])
    assert metadata == {
        "metadata_slimmed": True,
        "original_type": "image/jpeg",
        "photo_takenDateTime": "2026-01-01T12:00:00Z",
    }


def test_non_image_metadata_remains_full_and_full_image_flag_preserves_raw() -> None:
    pdf_row = sp.item_inventory_row("drive-1", {"id": "pdf-1", "name": "Doc.pdf", "file": {"mimeType": "application/pdf"}, "custom": "kept"})
    image_row = sp.item_inventory_row(
        "drive-1",
        {"id": "img-1", "name": "Image.jpg", "file": {"mimeType": "image/jpeg"}, "custom": "kept"},
        store_full_image_metadata=True,
    )

    assert sp.json.loads(pdf_row["metadata_json"])["custom"] == "kept"
    assert sp.json.loads(image_row["metadata_json"])["custom"] == "kept"


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
    assert "inserted=1" in out
    assert "updated=0" in out
    assert "skipped_unchanged=0" in out
    assert "secret" not in out


def test_delta_link_not_logged_in_stats_output(capsys) -> None:
    stats = sp.DeltaSyncStats(mode="incremental", drive_id="drive-1", delta_token_saved=True)
    sp.print_delta_stats(stats)
    out = capsys.readouterr().out
    assert "deltaLink" not in out
    assert "token" not in out.lower()


def test_delta_status_reports_checkpoint_without_leaking_links(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sp, "ensure_delta_tables", lambda conn: None)
    engine = FakeStatusEngine(
        [
            {
                "drive_id": "drive-1",
                "library_name": "Documents",
                "sync_status": "failed",
                "sync_started_at": "start",
                "sync_completed_at": "done",
                "last_successful_sync_at": None,
                "items_seen": 359013,
                "changes_applied": 100,
                "checkpoint_page": 1565,
                "checkpoint_items_seen": 359013,
                "checkpoint_updated_at": "checkpoint-time",
                "last_error_page": 1566,
                "last_error_message": "could not receive data from server",
                "total_inventory_rows": 313031,
                "has_delta_link": False,
                "has_checkpoint": True,
            }
        ]
    )

    sp.print_delta_status(engine)

    out = capsys.readouterr().out
    assert "Checkpoint stored: yes" in out
    assert "Checkpoint page: 1565" in out
    assert "Total inventory rows: 313031" in out
    assert "Last error page: 1566" in out
    assert "could not receive data from server" in out
    assert "nextLink" not in out
    assert "deltaLink" not in out


def inv_row(item_id, *, name="Doc.pdf", web_url=None, parent_path="Jobs/Acme", relative_path=None, drive_id="drive-1"):
    return {
        "drive_id": drive_id,
        "drive_item_id": item_id,
        "name": name,
        "web_url": web_url,
        "parent_path": parent_path,
        "relative_path": relative_path or f"{parent_path}/{name}",
        "is_file": True,
        "mime_type": "application/pdf",
        "size_bytes": 100,
        "last_modified_at": "2026-01-02T00:00:00Z",
    }


def doc_row(document_id, *, file_name="Doc.pdf", drive_id=None, drive_item_id=None, sharepoint_url=None, folder_path="Jobs/Acme", relative_path=None):
    return {
        "document_id": document_id,
        "job_id": "JOB",
        "file_name": file_name,
        "drive_id": drive_id,
        "drive_item_id": drive_item_id,
        "sharepoint_url": sharepoint_url,
        "folder_path": folder_path,
        "relative_path": relative_path,
    }


def update_by_doc(updates):
    return {row["document_id"]: row for row in updates}


def test_reconciliation_drive_item_id_match_fills_missing_drive_id() -> None:
    updates, stats = sp.match_document_drive_metadata(
        [doc_row("doc-1", drive_item_id="item-1")],
        [inv_row("item-1")],
    )

    assert update_by_doc(updates)["doc-1"]["drive_id"] == "drive-1"
    assert update_by_doc(updates)["doc-1"]["drive_item_id"] == "item-1"
    assert update_by_doc(updates)["doc-1"]["strategy"] == "drive_item_id"
    assert stats.matched_by_drive_item_id == 1


def test_reconciliation_document_id_driveitem_prefix_fills_identifiers() -> None:
    updates, stats = sp.match_document_drive_metadata(
        [doc_row("driveitem-item-2")],
        [inv_row("item-2")],
    )

    update = update_by_doc(updates)["driveitem-item-2"]
    assert update["drive_id"] == "drive-1"
    assert update["drive_item_id"] == "item-2"
    assert update["strategy"] == "document_id_drive_item_id"
    assert stats.matched_by_document_id_drive_item_id == 1


def test_reconciliation_preserves_existing_complete_identifiers() -> None:
    updates, stats = sp.match_document_drive_metadata(
        [doc_row("doc-1", drive_id="existing-drive", drive_item_id="existing-item")],
        [inv_row("existing-item", drive_id="new-drive")],
    )

    assert updates == []
    assert stats.missing_before == 0


def test_reconciliation_does_not_match_inventory_without_drive_id() -> None:
    updates, stats = sp.match_document_drive_metadata(
        [doc_row("doc-1", drive_item_id="item-1")],
        [inv_row("item-1", drive_id=None)],
    )

    assert updates == []
    assert stats.documents_updated == 0


def test_reconciliation_url_decoded_path_matching() -> None:
    updates, stats = sp.match_document_drive_metadata(
        [doc_row("doc-1", sharepoint_url="https://tenant.sharepoint.com/sites/Ops/Shared%20Documents/Jobs/Acme/Proposal%20Final.pdf", file_name="Proposal Final.pdf")],
        [inv_row("item-1", name="Proposal Final.pdf", web_url="https://tenant.sharepoint.com/sites/Ops/Shared Documents/Jobs/Acme/Proposal Final.pdf")],
    )

    assert update_by_doc(updates)["doc-1"]["strategy"] in {"exact_url", "url_path"}
    assert stats.matched_by_exact_url + stats.matched_by_url_path == 1


def test_reconciliation_relative_path_matching() -> None:
    updates, stats = sp.match_document_drive_metadata(
        [doc_row("doc-1", relative_path="Jobs/Acme/Estimate.xlsx", file_name="Estimate.xlsx")],
        [inv_row("item-1", name="Estimate.xlsx", relative_path="Jobs/Acme/Estimate.xlsx")],
    )

    assert update_by_doc(updates)["doc-1"]["strategy"] == "relative_path"
    assert stats.matched_by_relative_path == 1


def test_reconciliation_folder_path_plus_file_name_matching() -> None:
    updates, stats = sp.match_document_drive_metadata(
        [doc_row("doc-1", folder_path="Jobs/Acme", file_name="Contract.pdf")],
        [inv_row("item-1", name="Contract.pdf", relative_path="Jobs/Acme/Contract.pdf")],
    )

    assert update_by_doc(updates)["doc-1"]["strategy"] == "folder_file"
    assert stats.matched_by_folder_file == 1


def test_reconciliation_parent_path_plus_name_matching() -> None:
    updates, stats = sp.match_document_drive_metadata(
        [doc_row("doc-1", folder_path="Jobs/Acme", file_name="Tracking.xlsx")],
        [inv_row("item-1", name="Tracking.xlsx", parent_path="Jobs/Acme", relative_path="Different/Tracking.xlsx")],
    )

    assert update_by_doc(updates)["doc-1"]["strategy"] == "parent_name"
    assert stats.matched_by_parent_name == 1


def test_reconciliation_unique_filename_fallback_only_when_unique() -> None:
    updates, stats = sp.match_document_drive_metadata(
        [doc_row("doc-1", folder_path="Unknown", file_name="Only.pdf")],
        [inv_row("item-1", name="Only.pdf", parent_path="Inventory/Elsewhere", relative_path="Inventory/Elsewhere/Only.pdf")],
    )

    assert update_by_doc(updates)["doc-1"]["strategy"] == "unique_filename"
    assert update_by_doc(updates)["doc-1"]["confidence"] == "low"
    assert stats.matched_by_unique_filename == 1


def test_reconciliation_ambiguous_filename_fallback_skipped() -> None:
    updates, stats = sp.match_document_drive_metadata(
        [doc_row("doc-1", folder_path="Unknown", file_name="Repeat.pdf")],
        [
            inv_row("item-1", name="Repeat.pdf", parent_path="A", relative_path="A/Repeat.pdf"),
            inv_row("item-2", name="Repeat.pdf", parent_path="B", relative_path="B/Repeat.pdf"),
        ],
    )

    assert updates == []
    assert stats.ambiguous_skipped == 1
    assert stats.unmatched == 1


def test_reconciliation_is_idempotent_after_identifiers_are_filled() -> None:
    documents = [doc_row("doc-1", drive_item_id="item-1")]
    inventory = [inv_row("item-1")]
    updates, _stats = sp.match_document_drive_metadata(documents, inventory)
    first = update_by_doc(updates)["doc-1"]
    documents[0]["drive_id"] = first["drive_id"]
    documents[0]["drive_item_id"] = first["drive_item_id"]

    second_updates, second_stats = sp.match_document_drive_metadata(documents, inventory)

    assert second_updates == []
    assert second_stats.missing_before == 0
