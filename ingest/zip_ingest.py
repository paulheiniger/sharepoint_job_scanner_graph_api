from __future__ import annotations

import zipfile
from pathlib import Path, PurePosixPath


def safe_zip_pdf_member(member_name: str) -> tuple[bool, str | None]:
    path = PurePosixPath(member_name)
    parts = path.parts
    if not parts or member_name.endswith("/"):
        return False, None
    if any(part in {"", ".", ".."} for part in parts) or path.is_absolute():
        return False, f"Skipped unsafe ZIP path: {member_name}"
    if any(part == "__MACOSX" for part in parts) or path.name == ".DS_Store":
        return False, None
    if path.suffix.lower() != ".pdf":
        return False, f"Skipped non-PDF ZIP member: {member_name}"
    return True, None


def inspect_zip_pdfs(zip_path: Path) -> tuple[list[dict[str, object]], list[str]]:
    pdfs: list[dict[str, object]] = []
    warnings: list[str] = []
    try:
        with zipfile.ZipFile(zip_path) as archive:
            for member in archive.infolist():
                ok, warning = safe_zip_pdf_member(member.filename)
                if warning:
                    warnings.append(warning)
                if not ok:
                    continue
                pdfs.append(
                    {
                        "member_name": member.filename,
                        "filename": PurePosixPath(member.filename).name,
                        "compressed_size": int(member.compress_size or 0),
                        "uncompressed_size": int(member.file_size or 0),
                        "crc": int(member.CRC or 0),
                    }
                )
    except zipfile.BadZipFile:
        warnings.append(f"Skipped {zip_path.name}: not a readable ZIP file")
    return pdfs, warnings


def extract_zip_pdf_member(zip_path: Path, member_name: str, target_path: Path) -> bytes:
    ok, warning = safe_zip_pdf_member(member_name)
    if not ok:
        raise ValueError(warning or f"Unsafe or unsupported ZIP member: {member_name}")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        content = archive.read(member_name)
    target_path.write_bytes(content)
    return content
