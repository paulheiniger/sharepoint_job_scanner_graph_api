from __future__ import annotations

from pathlib import Path

import requests

from jobscan.graph_client import SharePointTarget
from jobscan.models import JobRecord
from jobscan import sharepoint_sync as sp
from jobscan.sharepoint_sync import sync_sharepoint_folder


class FakeFolderSyncClient:
    def __init__(self, failures_by_item: dict[str, int] | None = None):
        self.failures_by_item = dict(failures_by_item or {})
        self.downloaded: list[str] = []
        self.root_paths: list[str] = []

    def get_site(self, hostname: str, site_path: str) -> dict[str, str]:
        return {"id": "site-1", "name": "Data"}

    def get_drive_by_name(self, site_id: str, library: str) -> dict[str, str]:
        return {"id": "drive-1", "name": library}

    def get_root_or_path_item(self, drive_id: str, folder_path: str) -> dict[str, object]:
        self.root_paths.append(folder_path)
        return {"id": "root", "name": "2025", "folder": {}, "webUrl": "https://sharepoint.example/2025"}

    def list_children(self, drive_id: str, item_id: str) -> list[dict[str, object]]:
        if item_id != "root":
            return []
        return [
            {
                "id": "timeout-file",
                "name": "Timeout Estimate.xlsx",
                "size": 100,
                "eTag": "a",
                "webUrl": "https://sharepoint.example/timeout.xlsx",
                "file": {},
                "parentReference": {"driveId": drive_id, "path": "/drive/root:/2025"},
            },
            {
                "id": "ok-file",
                "name": "Good Estimate.xlsx",
                "size": 50,
                "eTag": "b",
                "webUrl": "https://sharepoint.example/good.xlsx",
                "file": {},
                "parentReference": {"driveId": drive_id, "path": "/drive/root:/2025"},
            },
        ]

    def download_item(self, drive_id: str, item_id: str, destination: Path) -> None:
        remaining_failures = self.failures_by_item.get(item_id, 0)
        if remaining_failures > 0:
            self.failures_by_item[item_id] = remaining_failures - 1
            raise requests.exceptions.ConnectTimeout("connect timed out")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(f"downloaded {item_id}", encoding="utf-8")
        self.downloaded.append(item_id)


def test_targeted_folder_scan_skips_file_after_download_timeout(tmp_path: Path, capsys) -> None:
    client = FakeFolderSyncClient({"timeout-file": 3})
    target = SharePointTarget("example.sharepoint.com", "/sites/Data", library="Documents", folder_path="2025")

    cache_root, stats = sync_sharepoint_folder(client=client, target=target, cache_dir=tmp_path, skip_images=True)

    assert stats.files_seen == 2
    assert stats.downloaded_files == 1
    assert stats.files_skipped == 1
    assert stats.download_failures == 1
    assert client.downloaded == ["ok-file"]
    assert (cache_root / "Good Estimate.xlsx").exists()
    assert not (cache_root / "Timeout Estimate.xlsx").exists()
    assert "WARNING: skipped SharePoint file after download timeout/error" in capsys.readouterr().out


def test_targeted_folder_scan_retries_transient_download_timeout(tmp_path: Path) -> None:
    client = FakeFolderSyncClient({"timeout-file": 1})
    target = SharePointTarget("example.sharepoint.com", "/sites/Data", library="Documents", folder_path="2025")

    cache_root, stats = sync_sharepoint_folder(client=client, target=target, cache_dir=tmp_path, skip_images=True)

    assert stats.files_seen == 2
    assert stats.downloaded_files == 2
    assert stats.files_skipped == 0
    assert stats.download_failures == 0
    assert sorted(client.downloaded) == ["ok-file", "timeout-file"]
    assert (cache_root / "Timeout Estimate.xlsx").exists()


def test_non_delta_config_roots_scan_configured_paths_only(monkeypatch, tmp_path: Path, capsys) -> None:
    config = tmp_path / "roots.yaml"
    config.write_text(
        """
        roots:
          - division: Roofing
            pipeline_status: Completed
            path: "2025 MASTER FILES/2025 ROOFING/COMPLETED"
          - division: Flooring
            pipeline_status: Proposed
            path: "2025 MASTER FILES/2025 FLOORING/PROPOSED"
        """,
        encoding="utf-8",
    )
    roots = sp.load_configured_folder_roots(
        config,
        default_site_url="https://example.sharepoint.com/sites/Data",
        default_library="Documents",
    )
    client = FakeFolderSyncClient()

    def fake_scan_root(cache_root: Path, scan_context: str = ""):
        return [
            JobRecord(
                job_id=f"job-{scan_context}",
                folder_name="Demo Job",
                folder_path=scan_context,
            )
        ]

    monkeypatch.setattr(sp, "scan_root", fake_scan_root)

    records, summaries = sp.sync_configured_sharepoint_folders(
        client=client,
        roots=roots,
        cache_dir=tmp_path / "cache",
        max_depth=1,
        max_file_mb=50,
        force=False,
        skip_images=True,
    )

    assert client.root_paths == [
        "2025 MASTER FILES/2025 ROOFING/COMPLETED",
        "2025 MASTER FILES/2025 FLOORING/PROPOSED",
    ]
    assert "" not in client.root_paths
    assert [(record.division, record.pipeline_status) for record in records] == [
        ("Roofing", "Completed"),
        ("Flooring", "Proposed"),
    ]
    assert [record.scan_root for record in records] == client.root_paths
    assert len(summaries) == 2
    output = capsys.readouterr().out
    assert "SharePoint root: 2025 MASTER FILES/2025 ROOFING/COMPLETED" in output
    assert "jobs found: 1" in output
