"""Multipart parsing + bundle import handler for /api/bundle/import.

Operators upload a previously-exported bundle ``tar.gz`` and its detached
``signature.json``. The framework writes both to temporary paths and runs the
existing ``scripts/bundle_import.py`` helper to verify before unpacking into
``runs/<run_id>/``.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from html import escape
from pathlib import Path

from profile_manager.config import storage_roots


def _runs_dir() -> Path:
    return storage_roots().runs


def _extract_boundary(content_type: str) -> str | None:
    m = re.search(r"boundary=([^;]+)", content_type)
    if not m:
        return None
    boundary = m.group(1).strip().strip('"')
    if not boundary or "\r" in boundary or "\n" in boundary:
        return None
    try:
        boundary.encode("ascii")
    except UnicodeEncodeError:
        return None
    return boundary


def _parse_file_parts(
    body: bytes, boundary: str
) -> tuple[dict[str, tuple[str, bytes]], str | None]:
    """Return the two named file fields from the dashboard form."""
    sep = b"--" + boundary.encode("ascii")
    parts = body.split(sep)
    files: dict[str, tuple[str, bytes]] = {}
    for part in parts:
        if not part or part in (b"--", b"--\r\n"):
            continue
        # Strip leading \r\n
        if part.startswith(b"\r\n"):
            part = part[2:]
        # Find headers/body split
        head_split = part.find(b"\r\n\r\n")
        if head_split == -1:
            continue
        headers = part[:head_split].decode("utf-8", errors="replace")
        content = part[head_split + 4:]
        # Trim trailing \r\n that precedes the next boundary marker
        if content.endswith(b"\r\n"):
            content = content[:-2]
        field_match = re.search(r'(?:^|;)\s*name="([^"]+)"', headers)
        if not field_match:
            continue
        field_name = field_match.group(1)
        if field_name not in {"bundle", "signature"} or field_name in files:
            continue
        filename_match = re.search(r'filename="([^"]*)"', headers)
        default_name = "uploaded.tar.gz" if field_name == "bundle" else "signature.json"
        filename = filename_match.group(1) if filename_match else default_name
        files[field_name] = (filename, content)
    missing = [name for name in ("bundle", "signature") if name not in files]
    if missing:
        return files, f"missing required upload field: {', '.join(missing)}"
    return files, None


def multipart_text_field(body: bytes, boundary: str, field_name: str) -> str | None:
    """Return one non-file text field, or ``None`` when it is absent.

    This uses the same bounded multipart shape as the import parser so request
    authorization can happen before any upload is written or verified.
    """
    sep = b"--" + boundary.encode("ascii")
    for part in body.split(sep):
        if part.startswith(b"\r\n"):
            part = part[2:]
        head_split = part.find(b"\r\n\r\n")
        if head_split == -1:
            continue
        headers = part[:head_split].decode("utf-8", errors="replace")
        match = re.search(r'(?:^|;)\s*name="([^"]+)"', headers)
        if not match or match.group(1) != field_name or 'filename="' in headers:
            continue
        content = part[head_split + 4:]
        if content.endswith(b"\r\n"):
            content = content[:-2]
        return content.decode("utf-8", errors="replace")
    return None


def _validated_filename(name: str, *, default: str) -> str | None:
    """Preserve a signature-bound basename exactly or reject it."""
    candidate = name or default
    if (
        candidate in {".", ".."}
        or Path(candidate).name != candidate
        or "/" in candidate
        or "\\" in candidate
        or "\x00" in candidate
        or any(ord(char) < 32 or ord(char) == 127 for char in candidate)
    ):
        return None
    try:
        if len(os.fsencode(candidate)) > 255:
            return None
    except UnicodeEncodeError:
        return None
    return candidate


def _result_page(title: str, body: str, *, status: int) -> tuple[int, str]:
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Dwarf — Bundle import</title>"
        "<link rel='stylesheet' href='/static/css/tokens.css'>"
        "<link rel='stylesheet' href='/static/css/themes.css'>"
        "<link rel='stylesheet' href='/static/css/base.css'>"
        "</head><body data-density='reading'>"
        f"<main class='shell-main'><span class='eyebrow'>Operate · Bundles</span><h1>{escape(title)}</h1>"
        f"{body}"
        "<p><a href='/operate/bundles'>↩ back to bundles</a></p>"
        "</main></body></html>"
    )
    return status, html


def handle_bundle_import_post(content_type: str, body: bytes) -> tuple[int, str]:
    """Top-level handler. Returns (status, html). Errors render an HTML
    page rather than raw text so the form submission lands somewhere
    readable."""
    boundary = _extract_boundary(content_type)
    if not boundary:
        return _result_page(
            "Import failed",
            "<p class='status-tile__errors-raw'>Missing multipart boundary in Content-Type header.</p>",
            status=400,
        )
    files, err = _parse_file_parts(body, boundary)
    if err:
        return _result_page("Import failed", f"<p class='status-tile__errors-raw'>{escape(err)}</p>", status=400)
    bundle_filename, bundle_content = files["bundle"]
    signature_filename, signature_content = files["signature"]
    if not bundle_content or not signature_content:
        return _result_page("Import failed", "<p class='status-tile__errors-raw'>Bundle and detached signature uploads must not be empty.</p>", status=400)
    bundle_filename = _validated_filename(
        bundle_filename, default="uploaded.tar.gz"
    )
    signature_filename = _validated_filename(
        signature_filename, default="signature.json"
    )
    if bundle_filename is None or signature_filename is None:
        return _result_page(
            "Import failed",
            "<p class='status-tile__errors-raw'>Upload filenames must be safe basenames without path separators or control characters.</p>",
            status=400,
        )
    base = _runs_dir()
    runs_dir = base
    helper = Path(__file__).resolve().parents[2] / "scripts" / "bundle_import.py"
    if not helper.is_file():
        return _result_page(
            "Import unavailable",
            f"<p class='status-tile__errors-raw'>Helper script not found at {escape(str(helper))}. Use the CLI: <code>cardano-profile bundle import &lt;path&gt;</code>.</p>",
            status=503,
        )
    # The export signature binds the archive filename as well as its bytes.
    # Preserve each uploaded basename exactly in separate private directories;
    # NamedTemporaryFile prefixes would make every valid browser upload fail
    # signature verification.
    with tempfile.TemporaryDirectory(prefix="dwarf-bundle-import-") as tmpdir:
        snapshot_root = Path(tmpdir)
        bundle_root = snapshot_root / "bundle"
        signature_root = snapshot_root / "signature"
        bundle_root.mkdir(mode=0o700)
        signature_root.mkdir(mode=0o700)
        bundle_path = bundle_root / bundle_filename
        signature_path = signature_root / signature_filename
        bundle_path.write_bytes(bundle_content)
        signature_path.write_bytes(signature_content)
        proc = subprocess.run(
            [
                sys.executable,
                str(helper),
                str(bundle_path),
                "--signature-path",
                str(signature_path),
                "--runs-dir",
                str(runs_dir),
            ],
            text=True, capture_output=True, check=False,
        )
        ok = proc.returncode == 0
        body_html = (
            f"<p class='page-subhero'>Uploaded {escape(bundle_filename)} ({len(bundle_content)} bytes) "
            f"with {escape(signature_filename)} ({len(signature_content)} bytes), "
            f"helper exit {proc.returncode}.</p>"
            f"<h2>stdout</h2><pre class='run-payload-pre'>{escape(proc.stdout) or '(empty)'}</pre>"
            f"<h2>stderr</h2><pre class='run-payload-pre'>{escape(proc.stderr) or '(empty)'}</pre>"
        )
        return _result_page(
            "Import succeeded" if ok else "Import failed",
            body_html,
            status=200 if ok else 502,
        )
