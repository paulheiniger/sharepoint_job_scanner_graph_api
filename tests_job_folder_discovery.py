from __future__ import annotations

import tempfile
from pathlib import Path

from jobscan.scan import records_as_dicts, scan_root


def test_immediate_child_folders_emit_minimal_records() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "2026 ROOFING" / "CONTRACTED REPAIRS"
        repair = root / "Smith Repair"
        repair.mkdir(parents=True)
        (repair / "notes.pdf").write_text("notes", encoding="utf-8")

        image_folder = root / "Pics"
        image_folder.mkdir()
        (image_folder / "photo.jpg").write_text("image", encoding="utf-8")

        records = scan_root(root, scan_context="2026 ROOFING/CONTRACTED REPAIRS")
        rows = records_as_dicts(records)

    assert len(rows) == 1
    row = rows[0]
    assert row["folder_name"] == "Smith Repair"
    assert row["status"] == "Contracted Repairs"
    assert row["estimate_file"] is None
    assert "No estimate workbook found" in row["warnings"]
    assert "folder_url" in row


if __name__ == "__main__":
    test_immediate_child_folders_emit_minimal_records()
    print("job folder discovery ok")
