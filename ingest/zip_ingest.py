from __future__ import annotations

import zipfile
from pathlib import Path, PurePosixPath


def safe_zip_member(member_name: str) -> tuple[bool, str | None]:
    path = PurePosixPath(member_name)
    parts = path.parts
    if not parts or member_name.endswith("/"):
        return False, None
    if any(part in {"", ".", ".."} for part in parts) or path.is_absolute():
        return False, f"Skipped unsafe ZIP path: {member_name}"
    if any(part == "__MACOSX" for part in parts) or path.name == ".DS_Store":
        return False, None
    return True, None


def safe_zip_pdf_member(member_name: str) -> tuple[bool, str | None]:
    ok, warning = safe_zip_member(member_name)
    if not ok:
        return ok, warning
    if PurePosixPath(member_name).suffix.lower() != ".pdf":
        return False, f"Skipped non-PDF ZIP member: {member_name}"
    return True, None


def infer_zip_member_type(member_name: str) -> str:
    path = PurePosixPath(member_name)
    name = path.name.lower()
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        if " page " in name or " page_" in name or " page-" in name:
            return "pdf_plan_sheet"
        return "original_plan_pdf"
    if suffix == ".csv":
        if "takeoff" in name or "quantity" in name or "stack" in name:
            return "takeoff_quantity_csv"
        return "takeoff_quantity_csv"
    if suffix in {".xlsx", ".xls", ".xlsm"}:
        return "excel_estimate_export"
    if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".heic"}:
        return "image"
    return "metadata_other"


def inspect_zip_members(zip_path: Path) -> tuple[list[dict[str, object]], list[str]]:
    members: list[dict[str, object]] = []
    warnings: list[str] = []
    try:
        with zipfile.ZipFile(zip_path) as archive:
            for member in archive.infolist():
                ok, warning = safe_zip_member(member.filename)
                if warning:
                    warnings.append(warning)
                if not ok:
                    continue
                path = PurePosixPath(member.filename)
                members.append(
                    {
                        "internal_path": member.filename,
                        "member_name": member.filename,
                        "filename": path.name,
                        "extension": path.suffix.lower(),
                        "compressed_size": int(member.compress_size or 0),
                        "uncompressed_size": int(member.file_size or 0),
                        "crc": int(member.CRC or 0),
                        "inferred_type": infer_zip_member_type(member.filename),
                    }
                )
    except zipfile.BadZipFile:
        warnings.append(f"Skipped {zip_path.name}: not a readable ZIP file")
    return members, warnings


def inspect_zip_pdfs(zip_path: Path) -> tuple[list[dict[str, object]], list[str]]:
    members, warnings = inspect_zip_members(zip_path)
    return [member for member in members if str(member.get("extension") or "").lower() == ".pdf"], warnings


def extract_zip_member(zip_path: Path, member_name: str, target_path: Path) -> bytes:
    ok, warning = safe_zip_member(member_name)
    if not ok:
        raise ValueError(warning or f"Unsafe ZIP member: {member_name}")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        content = archive.read(member_name)
    target_path.write_bytes(content)
    return content


def extract_zip_pdf_member(zip_path: Path, member_name: str, target_path: Path) -> bytes:
    ok, warning = safe_zip_pdf_member(member_name)
    if not ok:
        raise ValueError(warning or f"Unsafe or unsupported ZIP member: {member_name}")
    return extract_zip_member(zip_path, member_name, target_path)
