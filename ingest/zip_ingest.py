from __future__ import annotations

import tempfile
import zipfile
from io import BytesIO
from pathlib import Path, PurePosixPath


def _safe_pdf_member(member_name: str) -> tuple[bool, str | None]:
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


def extract_zip_pdfs(zip_name: str, zip_bytes: bytes, *, starting_index: int = 0):
    """Extract PDF members from a ZIP into a temp project folder and return normalized document inputs."""
    from .package_ingest import normalize_pdf_document

    documents = []
    warnings: list[str] = []
    project_dir = Path(tempfile.mkdtemp(prefix="foamscope_package_"))
    try:
        with zipfile.ZipFile(BytesIO(zip_bytes)) as archive:
            for member in archive.infolist():
                ok, warning = _safe_pdf_member(member.filename)
                if warning:
                    warnings.append(warning)
                if not ok:
                    continue
                member_path = PurePosixPath(member.filename)
                filename = member_path.name
                target = (project_dir / filename).resolve()
                if project_dir.resolve() not in target.parents:
                    warnings.append(f"Skipped unsafe ZIP path: {member.filename}")
                    continue
                try:
                    content = archive.read(member)
                except Exception as exc:
                    warnings.append(f"Skipped {member.filename}: could not read ZIP member ({type(exc).__name__})")
                    continue
                try:
                    target.write_bytes(content)
                except OSError as exc:
                    warnings.append(f"Could not stage {member.filename}: {exc}")
                source_path = f"{zip_name}:{member.filename}"
                documents.append(
                    normalize_pdf_document(
                        filename,
                        content,
                        index=starting_index + len(documents),
                        source_path=source_path,
                    )
                )
    except zipfile.BadZipFile:
        warnings.append(f"Skipped {zip_name}: not a readable ZIP file")
    return documents, warnings
