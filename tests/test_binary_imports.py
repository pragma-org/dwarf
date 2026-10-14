from __future__ import annotations

import hashlib
import http.client
import io
import json
import os
import stat
import tarfile
import threading
import gzip
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

import pytest

from profile_manager.binary_imports import (
    BinaryImportError,
    ImportLimits,
    MAX_IMPORT_REQUEST_BYTES,
    begin_binary_import,
    cleanup_expired_binary_imports,
    extract_uploaded_binary,
    parse_binary_import_multipart,
    poll_binary_import,
    retained_import_executable_digest,
    stage_binary_import,
)
from profile_manager import dashboard
from profile_manager.operator_version_decisions import (
    OperatorVersionDecisionLedger,
    StaleOperatorVersionState,
)


def _elf(*, machine: int = 62, payload: bytes = b"fixture") -> bytes:
    header = bytearray(64)
    header[:4] = b"\x7fELF"
    header[4] = 2  # ELFCLASS64
    header[5] = 1  # little endian
    header[6] = 1
    header[18:20] = machine.to_bytes(2, "little")
    return bytes(header) + payload


def _archive(entries: list[tuple[tarfile.TarInfo, bytes]]) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for info, body in entries:
            archive.addfile(info, io.BytesIO(body) if info.isfile() else None)
    return stream.getvalue()


def _file(name: str, body: bytes) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.size = len(body)
    info.mode = 0o755
    return info, body


def _multipart(fields, *, filename="developer-build", payload=None, boundary="dwarf-boundary"):
    chunks = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                str(value).encode(),
                b"\r\n",
            ]
        )
    if payload is not None:
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                (
                    'Content-Disposition: form-data; name="artifact"; '
                    f'filename="{filename}"\r\n'
                ).encode(),
                b"Content-Type: application/octet-stream\r\n\r\n",
                payload,
                b"\r\n",
            ]
        )
    chunks.append(f"--{boundary}--\r\n".encode())
    return f"multipart/form-data; boundary={boundary}", b"".join(chunks)


def test_raw_amaru_elf_freezes_content_and_platform():
    payload = _elf()

    result = extract_uploaded_binary(
        payload,
        filename="amaru",
        implementation="amaru",
        host_arch="amd64",
    )

    assert result.executable_name == "amaru"
    assert result.platform == "linux/amd64"
    assert result.upload_sha256 == hashlib.sha256(payload).hexdigest()
    assert result.executable_sha256 == hashlib.sha256(payload).hexdigest()
    assert result.executable == payload


def test_archive_extracts_only_expected_binary_without_writing_members(tmp_path):
    executable = _elf(machine=183)
    readme = b"not executable"
    payload = _archive(
        [
            _file("release/bin/amaru", executable),
            _file("release/README.md", readme),
        ]
    )

    result = extract_uploaded_binary(
        payload,
        filename="amaru-linux-arm64.tar.gz",
        implementation="amaru",
        host_arch="arm64",
    )

    assert result.executable == executable
    assert result.platform == "linux/arm64"
    assert list(tmp_path.iterdir()) == []


def test_official_archive_ignores_same_basename_completion_text():
    executable = _elf()
    payload = _archive(
        [
            _file("amaru-10.11.20260925-linux-x86_64/bin/amaru", executable),
            _file(
                "amaru-10.11.20260925-linux-x86_64/share/bash-completion/completions/amaru",
                b"complete -W 'run help' amaru\n",
            ),
        ]
    )

    result = extract_uploaded_binary(
        payload,
        filename="amaru-10.11.20260925-linux-x86_64.tar.gz",
        implementation="amaru",
        host_arch="amd64",
    )

    assert result.executable == executable
    assert result.executable_sha256 == hashlib.sha256(executable).hexdigest()


def test_archive_selects_the_only_elf_independent_of_path_or_mode():
    executable = _elf()
    completion = _file("release/bin/amaru", b"completion text")
    completion[0].mode = 0o755
    binary = _file("release/assets/amaru", executable)
    binary[0].mode = 0o644
    payload = _archive([completion, binary])

    result = extract_uploaded_binary(
        payload,
        filename="amaru.tar.gz",
        implementation="amaru",
        host_arch="amd64",
    )

    assert result.executable == executable


@pytest.mark.parametrize(
    "entries",
    [
        [_file("one/amaru", _elf()), _file("two/amaru", _elf())],
        [_file("one/amaru", _elf(machine=62)), _file("two/amaru", _elf(machine=183))],
        [_file("same/amaru", _elf(payload=b"one")), _file("same/amaru", _elf(payload=b"two"))],
    ],
)
def test_archive_rejects_multiple_elf_candidates(entries):
    payload = _archive(entries)

    with pytest.raises(BinaryImportError, match="exactly one amaru"):
        extract_uploaded_binary(
            payload,
            filename="amaru.tar.gz",
            implementation="amaru",
            host_arch="amd64",
        )


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"completion text", "exactly one amaru"),
        (b"\x7fELFshort", "supported 64-bit Linux ELF"),
        (_elf(machine=3), "ELF architecture is unsupported"),
    ],
)
def test_archive_rejects_missing_malformed_or_unsupported_elf(body, message):
    payload = _archive([_file("release/amaru", body)])

    with pytest.raises(BinaryImportError, match=message):
        extract_uploaded_binary(
            payload,
            filename="amaru.tar.gz",
            implementation="amaru",
            host_arch="amd64",
        )


@pytest.mark.parametrize(
    ("name", "info"),
    [
        ("traversal", _file("../amaru", _elf())[0]),
        ("absolute", _file("/tmp/amaru", _elf())[0]),
    ],
)
def test_archive_rejects_unsafe_member_paths(name, info):
    info.size = len(_elf())
    payload = _archive([(info, _elf())])

    with pytest.raises(BinaryImportError, match="unsafe archive member path"):
        extract_uploaded_binary(
            payload,
            filename=f"{name}.tar.gz",
            implementation="amaru",
            host_arch="amd64",
        )


@pytest.mark.parametrize("member_type", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE])
def test_archive_rejects_links_and_devices(member_type):
    info = tarfile.TarInfo("release/amaru")
    info.type = member_type
    info.linkname = "elsewhere"
    payload = _archive([(info, b"")])

    with pytest.raises(BinaryImportError, match="regular files and directories"):
        extract_uploaded_binary(
            payload,
            filename="amaru.tar.gz",
            implementation="amaru",
            host_arch="amd64",
        )


def test_archive_rejects_duplicate_expected_binary():
    executable = _elf()
    payload = _archive(
        [_file("one/amaru", executable), _file("two/amaru", executable)]
    )

    with pytest.raises(BinaryImportError, match="exactly one amaru"):
        extract_uploaded_binary(
            payload,
            filename="amaru.tar.gz",
            implementation="amaru",
            host_arch="amd64",
        )


def test_archive_limits_members_and_expanded_bytes():
    executable = _elf()
    payload = _archive(
        [_file("release/amaru", executable), _file("release/note", b"12345")]
    )

    with pytest.raises(BinaryImportError, match="too many entries"):
        extract_uploaded_binary(
            payload,
            filename="amaru.tar.gz",
            implementation="amaru",
            host_arch="amd64",
            limits=ImportLimits(max_archive_members=1),
        )
    with pytest.raises(BinaryImportError, match="expanded content is too large"):
        extract_uploaded_binary(
            payload,
            filename="amaru.tar.gz",
            implementation="amaru",
            host_arch="amd64",
            limits=ImportLimits(max_expanded_bytes=len(executable)),
        )


def test_archive_member_limit_rejects_without_loading_the_whole_table(monkeypatch):
    payload = _archive(
        [_file("release/amaru", _elf()), _file("release/extra", b"unused")]
    )

    def forbidden_getmembers(_archive):
        raise AssertionError("the importer parsed the complete member table")

    monkeypatch.setattr(tarfile.TarFile, "getmembers", forbidden_getmembers)
    with pytest.raises(BinaryImportError, match="too many entries"):
        extract_uploaded_binary(
            payload,
            filename="amaru.tar.gz",
            implementation="amaru",
            host_arch="amd64",
            limits=ImportLimits(max_archive_members=1),
        )


def test_archive_rejects_large_hidden_pax_metadata_before_member_yield():
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as archive:
        info = tarfile.TarInfo("amaru")
        info.size = len(_elf())
        info.pax_headers = {"comment": "x" * 8192}
        archive.addfile(info, io.BytesIO(_elf()))
    compressed = gzip.compress(raw.getvalue(), compresslevel=9)

    with pytest.raises(BinaryImportError, match="metadata record is too large"):
        extract_uploaded_binary(
            compressed,
            filename="amaru.tar.gz",
            implementation="amaru",
            host_arch="amd64",
            limits=ImportLimits(max_archive_metadata_bytes=1024),
        )


@pytest.mark.parametrize(
    ("implementation", "host_arch", "machine", "message"),
    [
        ("unknown", "amd64", 62, "implementation is unsupported"),
        ("amaru", "mips", 62, "deployment architecture is unsupported"),
        ("amaru", "arm64", 62, "does not match deployment architecture"),
        ("amaru", "amd64", 3, "ELF architecture is unsupported"),
    ],
)
def test_binary_identity_rejects_unknown_or_wrong_architecture(
    implementation, host_arch, machine, message
):
    with pytest.raises(BinaryImportError, match=message):
        extract_uploaded_binary(
            _elf(machine=machine),
            filename="amaru",
            implementation=implementation,
            host_arch=host_arch,
        )


def test_binary_size_is_bounded():
    payload = _elf(payload=b"0123456789")
    with pytest.raises(BinaryImportError, match="executable is too large"):
        extract_uploaded_binary(
            payload,
            filename="amaru",
            implementation="amaru",
            host_arch="amd64",
            limits=ImportLimits(max_executable_bytes=64),
        )


def test_stage_binary_import_writes_private_regular_files(tmp_path):
    payload = _elf()
    artifact = extract_uploaded_binary(
        payload,
        filename="amaru",
        implementation="amaru",
        host_arch="amd64",
    )

    descriptor = stage_binary_import(
        tmp_path,
        import_id="import-0123456789abcdef01234567",
        artifact=artifact,
        version="10.11.local-test",
        declared_source_revision="a" * 40,
        created_at="2026-10-13T12:00:00Z",
    )

    import_root = tmp_path / descriptor["import_id"]
    executable = import_root / "executable"
    retained = import_root / "request.json"
    assert executable.read_bytes() == payload
    assert stat.S_IMODE(executable.stat().st_mode) == 0o600
    assert stat.S_IMODE(retained.stat().st_mode) == 0o600
    assert descriptor["executable_sha256"] == hashlib.sha256(payload).hexdigest()
    assert descriptor["declared_source_revision"] == "a" * 40
    assert "filename" not in descriptor
    assert "path" not in descriptor


def test_stage_binary_import_rejects_symlinked_root(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "imports"
    link.symlink_to(target, target_is_directory=True)
    artifact = extract_uploaded_binary(
        _elf(), filename="amaru", implementation="amaru", host_arch="amd64"
    )

    with pytest.raises(BinaryImportError, match="staging root is unsafe"):
        stage_binary_import(
            link,
            import_id="import-0123456789abcdef01234567",
            artifact=artifact,
            version="local",
            declared_source_revision=None,
            created_at="2026-10-13T12:00:00Z",
        )


@pytest.mark.parametrize(
    ("version", "source", "message"),
    [
        ("", None, "version is invalid"),
        ("bad label with spaces", None, "version is invalid"),
        ("local", "not-a-commit", "source revision is invalid"),
    ],
)
def test_stage_binary_import_rejects_untrusted_metadata(
    tmp_path, version, source, message
):
    artifact = extract_uploaded_binary(
        _elf(), filename="amaru", implementation="amaru", host_arch="amd64"
    )
    with pytest.raises(BinaryImportError, match=message):
        stage_binary_import(
            tmp_path,
            import_id="import-0123456789abcdef01234567",
            artifact=artifact,
            version=version,
            declared_source_revision=source,
            created_at="2026-10-13T12:00:00Z",
        )


def test_expired_staging_cleanup_removes_only_verified_owned_bytes(tmp_path):
    root = tmp_path / "imports"
    expired = stage_binary_import(
        root,
        import_id="import-0123456789abcdef01234567",
        artifact=extract_uploaded_binary(
            _elf(), filename="anything", implementation="amaru", host_arch="amd64"
        ),
        version="expired-build",
        declared_source_revision=None,
        created_at="2026-10-13T10:00:00Z",
        limits=ImportLimits(staging_lifetime_seconds=60),
    )
    active = stage_binary_import(
        root,
        import_id="import-1123456789abcdef01234567",
        artifact=extract_uploaded_binary(
            _elf(payload=b"active"),
            filename="anything",
            implementation="amaru",
            host_arch="amd64",
        ),
        version="active-build",
        declared_source_revision=None,
        created_at="2026-10-13T12:00:00Z",
        limits=ImportLimits(staging_lifetime_seconds=3600),
    )

    cleaned = cleanup_expired_binary_imports(
        root, now=datetime(2026, 10, 13, 12, 30, tzinfo=timezone.utc)
    )

    assert cleaned == 1
    assert not (root / expired["import_id"] / "executable").exists()
    expired_record = json.loads(
        (root / expired["import_id"] / "request.json").read_text()
    )
    assert expired_record["state"] == "expired"
    assert (root / active["import_id"] / "executable").is_file()


def test_multipart_authenticates_body_token_before_extracting(monkeypatch):
    content_type, body = _multipart(
        {
            "token": "wrong",
            "expected_revision": "sha256:" + "a" * 64,
            "implementation": "amaru",
            "version": "local",
            "declared_source_revision": "",
        },
        payload=_elf(),
    )
    called = []
    monkeypatch.setattr(
        "profile_manager.binary_imports.extract_uploaded_binary",
        lambda *args, **kwargs: called.append((args, kwargs)),
    )

    with pytest.raises(BinaryImportError, match="authentication failed"):
        parse_binary_import_multipart(
            content_type,
            body,
            expected_token="correct-secret-token",
            host_arch="amd64",
        )
    assert called == []


def test_multipart_returns_only_strict_validated_fields():
    content_type, body = _multipart(
        {
            "token": "correct-secret-token",
            "expected_revision": "sha256:" + "a" * 64,
            "implementation": "amaru",
            "version": "local-build",
            "declared_source_revision": "b" * 40,
        },
        payload=_elf(),
    )

    parsed = parse_binary_import_multipart(
        content_type,
        body,
        expected_token="correct-secret-token",
        host_arch="amd64",
    )

    assert parsed.expected_revision == "sha256:" + "a" * 64
    assert parsed.version == "local-build"
    assert parsed.declared_source_revision == "b" * 40
    assert parsed.artifact.executable_name == "amaru"


def test_binary_import_request_ceiling_rejects_before_reading_body():
    handler = dashboard.serve_dashboard_handler_factory("correct-secret-token")
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection(
        "127.0.0.1", server.server_address[1], timeout=1
    )
    try:
        connection.putrequest("POST", "/api/versions/import-binary")
        connection.putheader("Content-Type", "multipart/form-data; boundary=x")
        connection.putheader("Content-Length", str(MAX_IMPORT_REQUEST_BYTES + 1))
        connection.endheaders()
        response = connection.getresponse()
        assert response.status == 413
        assert response.read() == b"binary import request is too large\n"
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    "fields,expected_status",
    (
        (
            {
                "token": "wrong-token",
                "expected_revision": "sha256:" + "a" * 64,
                "implementation": "amaru",
                "version": "developer-build",
            },
            401,
        ),
        (
            {
                "token": "correct-secret-token",
                "expected_revision": "sha256:" + "a" * 64,
                "implementation": "unknown-node",
                "version": "developer-build",
            },
            409,
        ),
    ),
)
def test_binary_import_route_rejects_auth_and_unknown_implementation_before_controller(
    monkeypatch, fields, expected_status
):
    from profile_manager import topology_actions

    controller_calls = []
    monkeypatch.setattr(
        topology_actions,
        "controller_request",
        lambda *args, **kwargs: controller_calls.append((args, kwargs)),
    )
    content_type, body = _multipart(fields, payload=_elf())

    status, content, response = dashboard.dispatch_binary_import_request(
        method="POST",
        path="/api/versions/import-binary",
        expected_token="correct-secret-token",
        content_type=content_type,
        body=body,
    )

    assert status == expected_status
    assert content == "application/json; charset=utf-8"
    assert json.loads(response)["ok"] is False
    assert controller_calls == []


def test_begin_import_stages_once_and_starts_one_controller_execution(tmp_path):
    ledger = OperatorVersionDecisionLedger(tmp_path / "state")
    revision = ledger.read()["revision"]
    content_type, body = _multipart(
        {
            "token": "correct-secret-token",
            "expected_revision": revision,
            "implementation": "amaru",
            "version": "developer-build-1",
            "declared_source_revision": "",
        },
        payload=_elf(),
    )
    calls = []

    def request_controller(operation, parameters, timeout, idempotency_key=None):
        calls.append((operation, parameters, timeout, idempotency_key))
        return {
            "status": "accepted",
            "execution_id": parameters["execution_id"],
        }

    status, response = begin_binary_import(
        content_type,
        body,
        expected_token="correct-secret-token",
        host_arch="amd64",
        staging_root=tmp_path / "imports",
        ledger=ledger,
        request_controller=request_controller,
        import_id="import-0123456789abcdef01234567",
        execution_id="execution-0123456789abcdef0123456789abcdef",
        created_at="2026-10-13T12:00:00Z",
    )

    assert status == 202
    assert response == {
        "ok": True,
        "state": "running",
        "import_id": "import-0123456789abcdef01234567",
        "execution_id": "execution-0123456789abcdef0123456789abcdef",
    }
    assert len(calls) == 1
    operation, parameters, timeout, idempotency_key = calls[0]
    assert operation == "artifact.package"
    assert timeout == 1800
    assert idempotency_key == "package-import-0123456789abcdef01234567"
    assert parameters["platform"] == "linux/amd64"
    assert "path" not in parameters and "filename" not in parameters
    retained = json.loads(
        (tmp_path / "imports" / response["import_id"] / "request.json").read_text()
    )
    assert retained["state"] == "running"
    assert retained["execution_id"] == response["execution_id"]


@pytest.mark.parametrize(
    ("implementation", "runtime_interface", "concurrent_winner"),
    (
        ("amaru", "extracted-binary", None),
        ("cardano-node", None, None),
        ("amaru", "extracted-binary", "exact"),
        ("amaru", "extracted-binary", "different"),
    ),
)
def test_poll_terminal_import_registers_exact_identity_once_and_removes_bytes(
    tmp_path, implementation, runtime_interface, concurrent_winner
):
    ledger = OperatorVersionDecisionLedger(tmp_path / "state")
    revision = ledger.read()["revision"]
    content_type, body = _multipart(
        {
            "token": "correct-secret-token",
            "expected_revision": revision,
            "implementation": implementation,
            "version": "developer-build-1",
            "declared_source_revision": "c" * 40,
        },
        payload=_elf(),
    )
    import_id = "import-0123456789abcdef01234567"
    execution_id = "execution-0123456789abcdef0123456789abcdef"
    begin_binary_import(
        content_type,
        body,
        expected_token="correct-secret-token",
        host_arch="amd64",
        staging_root=tmp_path / "imports",
        ledger=ledger,
        request_controller=lambda _o, p, _t, idempotency_key=None: {
            "status": "accepted",
            "execution_id": p["execution_id"],
        },
        import_id=import_id,
        execution_id=execution_id,
        created_at="2026-10-13T12:00:00Z",
    )
    digest = "sha256:" + hashlib.sha256(_elf()).hexdigest()
    result = {
        "status": "packaged",
        "implementation": implementation,
        "version": "developer-build-1",
        "config_image_id": "sha256:" + "d" * 64,
        "manifest_references": [],
        "platform": "linux/amd64",
        "runtime_interface": runtime_interface,
        "upload_sha256": digest,
        "executable_sha256": digest,
        "declared_source_revision": "c" * 40,
        "provenance": "operator-upload",
        "qualification": "unverified",
        "compatibility": "unknown-until-run",
        "probe": {
            "kind": "isolated-executable-and-run-help-smoke",
            "status": "passed",
        },
        "controller_installation_id": "install-test",
        "packaged_at": "2026-10-13T12:01:00Z",
    }

    def terminal(operation, parameters, timeout, idempotency_key=None):
        assert operation == "execution.status"
        assert parameters == {"execution_id": execution_id}
        return {
            "schema_version": "v1",
            "execution_id": execution_id,
            "topology_id": None,
            "state": "applied",
            "accepted_at": "2026-10-13T12:00:01Z",
            "completed_at": "2026-10-13T12:01:00Z",
            "audit_receipt": "controller/controller-audit/audit-test.json",
            "run_id": None,
            "result": result,
            "failure": None,
        }

    poll_body = urlencode(
        {"token": "correct-secret-token", "import_id": import_id, "execution_id": execution_id}
    ).encode()
    if concurrent_winner:
        append_registration = ledger.append_registration

        def append_after_concurrent_winner(registration, *, expected_revision):
            winner = dict(registration)
            if concurrent_winner == "different":
                winner["version"] = "different-build"
            append_registration(
                winner, expected_revision=ledger.read()["revision"]
            )
            raise StaleOperatorVersionState(
                "operator version state revision is stale"
            )

        ledger.append_registration = append_after_concurrent_winner
    if concurrent_winner == "different":
        with pytest.raises(
            BinaryImportError,
            match="binary import registration changed concurrently",
        ):
            poll_binary_import(
                poll_body,
                expected_token="correct-secret-token",
                staging_root=tmp_path / "imports",
                ledger=ledger,
                request_controller=terminal,
            )
        assert len(ledger.read()["registrations"]) == 1
        return
    status, response = poll_binary_import(
        poll_body,
        expected_token="correct-secret-token",
        staging_root=tmp_path / "imports",
        ledger=ledger,
        request_controller=terminal,
    )
    assert status == 200 and response["state"] == "registered"
    assert not (tmp_path / "imports" / import_id / "executable").exists()
    state = ledger.read()
    assert len(state["registrations"]) == 1
    registration = state["registrations"][0]
    assert registration["registration_id"] == "registration-0123456789abcdef01234567"
    assert registration["image_reference"] == "sha256:" + "d" * 64
    assert registration["config_image_id"] == "sha256:" + "d" * 64
    assert registration["executable_digest"] == digest
    assert registration["manifest_digest"] is None
    assert registration["runtime_interface"] == runtime_interface
    assert registration["provenance"] == {
        "source_revision": "c" * 40,
        "public": None,
        "qualification": "unverified",
    }

    second_status, second = poll_binary_import(
        poll_body,
        expected_token="correct-secret-token",
        staging_root=tmp_path / "imports",
        ledger=ledger,
        request_controller=terminal,
    )
    assert second_status == 200 and second == response
    assert len(ledger.read()["registrations"]) == 1


def test_legacy_import_digest_recovery_requires_exact_retained_finalization(tmp_path):
    import_id = "import-0123456789abcdef01234567"
    registration_id = "registration-0123456789abcdef01234567"
    root = tmp_path / "imports"
    job = root / import_id
    job.mkdir(parents=True)
    record = {
        "schema_version": "v1",
        "import_id": import_id,
        "execution_id": "execution-" + "1" * 32,
        "implementation": "amaru",
        "version": "developer-build-1",
        "platform": "linux/amd64",
        "upload_sha256": "3" * 64,
        "executable_sha256": "3" * 64,
        "executable_size": 123,
        "created_at": "2026-10-13T12:00:00Z",
        "expires_at": "2026-10-13T13:00:00Z",
        "state": "registered",
        "registration_id": registration_id,
        "config_image_id": "sha256:" + "4" * 64,
        "completed_at": "2026-10-13T12:01:00Z",
        "audit_receipt": "controller/controller-audit/audit-test.json",
        "declared_source_revision": None,
    }
    (job / "request.json").write_text(
        json.dumps(record, sort_keys=True) + "\n", encoding="utf-8"
    )
    registration = {
        "registration_id": registration_id,
        "implementation": "amaru",
        "version": "developer-build-1",
        "config_image_id": "sha256:" + "4" * 64,
        "image_reference": "sha256:" + "4" * 64,
        "manifest_digest": None,
        "runtime_interface": "extracted-binary",
        "platform": "linux/amd64",
        "controller_installation_id": "install-test",
        "inspected_at": "2026-10-13T12:01:00Z",
        "provenance": {
            "source_revision": None,
            "public": None,
            "qualification": "unverified",
        },
    }

    with pytest.raises(BinaryImportError, match="controller evidence is unavailable"):
        retained_import_executable_digest(registration, staging_root=root)

    controller_result = {
        "status": "packaged",
        "implementation": "amaru",
        "version": "developer-build-1",
        "config_image_id": "sha256:" + "4" * 64,
        "manifest_references": [],
        "platform": "linux/amd64",
        "runtime_interface": "extracted-binary",
        "upload_sha256": "sha256:" + "3" * 64,
        "executable_sha256": "sha256:" + "3" * 64,
        "declared_source_revision": None,
        "provenance": "operator-upload",
        "qualification": "unverified",
        "compatibility": "unknown-until-run",
        "controller_installation_id": "install-test",
        "packaged_at": "2026-10-13T12:01:00Z",
    }

    def retained_status(operation, parameters, timeout, idempotency_key=None):
        assert operation == "execution.status"
        assert parameters == {"execution_id": record["execution_id"]}
        assert timeout == 10
        assert idempotency_key is None
        return {
            "execution_id": record["execution_id"],
            "state": "applied",
            "audit_receipt": record["audit_receipt"],
            "result": controller_result,
            "failure": None,
        }

    assert retained_import_executable_digest(
        registration,
        staging_root=root,
        request_controller=retained_status,
    ) == "sha256:" + "3" * 64
    assert retained_import_executable_digest(
        {**registration, "executable_digest": "sha256:" + "3" * 64},
        staging_root=root,
        request_controller=retained_status,
    ) == "sha256:" + "3" * 64
    with pytest.raises(BinaryImportError, match="differs"):
        retained_import_executable_digest(
            {**registration, "executable_digest": "sha256:" + "9" * 64},
            staging_root=root,
            request_controller=retained_status,
        )

    with pytest.raises(BinaryImportError, match="differs"):
        retained_import_executable_digest(
            {**registration, "config_image_id": "sha256:" + "5" * 64},
            staging_root=root,
            request_controller=retained_status,
        )
    with pytest.raises(BinaryImportError, match="unavailable"):
        retained_import_executable_digest(
            {
                **registration,
                "registration_id": "registration-aaaaaaaaaaaaaaaaaaaaaaaa",
            },
            staging_root=root,
            request_controller=retained_status,
        )


def test_legacy_import_digest_recovery_rejects_mismatched_controller_evidence(tmp_path):
    import_id = "import-0123456789abcdef01234567"
    execution_id = "execution-" + "1" * 32
    registration_id = "registration-0123456789abcdef01234567"
    root = tmp_path / "imports"
    job = root / import_id
    job.mkdir(parents=True)
    record = {
        "schema_version": "v1",
        "import_id": import_id,
        "execution_id": execution_id,
        "implementation": "amaru",
        "version": "developer-build-1",
        "platform": "linux/amd64",
        "upload_sha256": "3" * 64,
        "executable_sha256": "3" * 64,
        "executable_size": 123,
        "created_at": "2026-10-13T12:00:00Z",
        "expires_at": "2026-10-13T13:00:00Z",
        "state": "registered",
        "registration_id": registration_id,
        "config_image_id": "sha256:" + "4" * 64,
        "completed_at": "2026-10-13T12:01:00Z",
        "audit_receipt": "controller/controller-audit/audit-test.json",
        "declared_source_revision": None,
    }
    (job / "request.json").write_text(
        json.dumps(record, sort_keys=True) + "\n", encoding="utf-8"
    )
    registration = {
        "registration_id": registration_id,
        "implementation": "amaru",
        "version": "developer-build-1",
        "image_reference": "sha256:" + "4" * 64,
        "manifest_digest": None,
        "config_image_id": "sha256:" + "4" * 64,
        "platform": "linux/amd64",
        "runtime_interface": "extracted-binary",
        "controller_installation_id": "install-test",
        "inspected_at": "2026-10-13T12:01:00Z",
        "provenance": {
            "source_revision": None,
            "public": None,
            "qualification": "unverified",
        },
    }
    result = {
        "status": "packaged",
        "implementation": "amaru",
        "version": "developer-build-1",
        "config_image_id": "sha256:" + "4" * 64,
        "manifest_references": [],
        "platform": "linux/amd64",
        "runtime_interface": "extracted-binary",
        "upload_sha256": "sha256:" + "3" * 64,
        "executable_sha256": "sha256:" + "3" * 64,
        "declared_source_revision": None,
        "provenance": "operator-upload",
        "qualification": "unverified",
        "compatibility": "unknown-until-run",
        "controller_installation_id": "install-test",
        "packaged_at": "2026-10-13T12:01:00Z",
    }

    for field, changed in (
        ("audit_receipt", "controller/controller-audit/audit-other.json"),
        ("config_image_id", "sha256:" + "5" * 64),
        ("platform", "linux/arm64"),
    ):
        observed = dict(result)
        if field != "audit_receipt":
            observed[field] = changed

        def status(_operation, _parameters, _timeout, idempotency_key=None):
            return {
                "execution_id": execution_id,
                "state": "applied",
                "audit_receipt": (
                    changed if field == "audit_receipt" else record["audit_receipt"]
                ),
                "result": observed,
                "failure": None,
            }

        with pytest.raises(BinaryImportError, match="controller evidence"):
            retained_import_executable_digest(
                registration,
                staging_root=root,
                request_controller=status,
            )
