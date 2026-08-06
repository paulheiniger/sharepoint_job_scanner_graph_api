from __future__ import annotations

from pathlib import Path

from jobscan.estimator.sharepoint_staging import stage_estimate_workbook


class _Response:
    def json(self) -> dict:
        return {"webUrl": "https://spraytec.sharepoint.com/staging/draft.xlsx"}


class _GraphClient:
    def __init__(self) -> None:
        self.request_url = ""
        self.uploaded = b""

    def get_json(self, url: str) -> dict:
        assert url.startswith("/shares/u!")
        return {
            "id": "folder-item-id",
            "folder": {},
            "parentReference": {"driveId": "drive-id"},
        }

    def request(self, method: str, url: str, **kwargs) -> _Response:
        assert method == "PUT"
        self.request_url = url
        self.uploaded = kwargs["data"]
        return _Response()


def test_staging_resolves_configured_sharepoint_folder_link(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workbook = tmp_path / "Assistant Draft.xlsx"
    workbook.write_bytes(b"xlsx bytes")
    monkeypatch.setenv(
        "ESTIMATOR_SHAREPOINT_STAGING_FOLDER_URL",
        "https://spraytec.sharepoint.com/:f:/s/Data/example?e=test",
    )
    graph = _GraphClient()

    result = stage_estimate_workbook(
        workbook,
        graph_client_factory=lambda: graph,
    )

    assert result.endswith("/staging/draft.xlsx")
    assert graph.request_url == (
        "/drives/drive-id/items/folder-item-id:/Assistant%20Draft.xlsx:/content"
    )
    assert graph.uploaded == b"xlsx bytes"
