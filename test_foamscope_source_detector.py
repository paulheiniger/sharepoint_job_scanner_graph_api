from __future__ import annotations

from pathlib import Path

from ingest.sharepoint_package_ingest import SHAREPOINT_NOT_CONFIGURED_MESSAGE, inspect_sharepoint_url_package
from intake.source_detector import detect_source_type


SHAREPOINT_URL = "https://aro365531128.sharepoint.com/:f:/s/Data/IgBieWKZG3_lSYqUTNNJyBC-ASuE-Wbpwr036zJTYZDGnZA?e=PdRfUf"


def test_sharepoint_url_detected_as_sharepoint_url() -> None:
    assert detect_source_type(SHAREPOINT_URL) == "sharepoint_url"


def test_local_filesystem_path_still_detects(tmp_path) -> None:
    folder = tmp_path / "bid-package"
    folder.mkdir()

    assert detect_source_type(folder) == "local_path"


def test_sharepoint_url_detection_does_not_call_path_exists(monkeypatch) -> None:
    def fail_exists(self):
        raise AssertionError("Path.exists should not be called for SharePoint URLs")

    monkeypatch.setattr(Path, "exists", fail_exists)

    assert detect_source_type(SHAREPOINT_URL) == "sharepoint_url"


def test_sharepoint_url_without_graph_configuration_returns_helpful_message(monkeypatch) -> None:
    for key in ("MS_TENANT_ID", "MS_CLIENT_ID", "MS_CLIENT_SECRET"):
        monkeypatch.delenv(key, raising=False)

    inspection = inspect_sharepoint_url_package(SHAREPOINT_URL)

    assert inspection.candidates == []
    assert any(SHAREPOINT_NOT_CONFIGURED_MESSAGE in warning for warning in inspection.warnings)
