"""Host-built runtime tools: build plan, record, and resolution from the record."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import build_runtime_tools as b
from scripts import runtime_common as rc


def _tool(root: Path, name: str, body: bytes = b"\x7fELF tool") -> str:
    path = root / "bin" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    path.chmod(0o755)
    return hashlib.sha256(body).hexdigest()


def _record(base: Path, key: str, tools: dict[str, str]) -> Path:
    (base / key).mkdir(parents=True, exist_ok=True)
    record = {"schema_version": 1, "key": key, "tools": {n: {"path": f"{key}/bin/{n}", "sha256": s} for n, s in tools.items()}}
    path = base / "record.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def test_resolver_returns_the_recorded_tool(tmp_path, monkeypatch):
    sha = _tool(tmp_path / "k1", "dwarf-amaru-runtime-blockfetch")
    record = _record(tmp_path, "k1", {"dwarf-amaru-runtime-blockfetch": sha})
    monkeypatch.setenv("DWARF_RUNTIME_TOOLS_RECORD", str(record))
    assert rc.runtime_tool("dwarf-amaru-runtime-blockfetch") == str(tmp_path / "k1" / "bin" / "dwarf-amaru-runtime-blockfetch")


def test_resolver_refuses_a_tool_whose_digest_changed(tmp_path, monkeypatch):
    _tool(tmp_path / "k1", "dwarf-amaru-runtime-blockfetch")
    record = _record(tmp_path, "k1", {"dwarf-amaru-runtime-blockfetch": "0" * 64})
    monkeypatch.setenv("DWARF_RUNTIME_TOOLS_RECORD", str(record))
    with pytest.raises(RuntimeError, match="digest"):
        rc.runtime_tool("dwarf-amaru-runtime-blockfetch")


def test_resolver_names_the_build_script_when_no_record_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("DWARF_RUNTIME_TOOLS_RECORD", str(tmp_path / "missing.json"))
    monkeypatch.setattr(rc, "_LEGACY_RUNTIME_TOOLS", {})
    with pytest.raises(RuntimeError, match="build_runtime_tools.py"):
        rc.runtime_tool("dwarf-amaru-runtime-chainsync")


def test_resolver_falls_back_to_a_present_legacy_path(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy-blockfetch"
    legacy.write_bytes(b"x")
    monkeypatch.setenv("DWARF_RUNTIME_TOOLS_RECORD", str(tmp_path / "missing.json"))
    monkeypatch.setattr(rc, "_LEGACY_RUNTIME_TOOLS", {"dwarf-amaru-runtime-blockfetch": str(legacy)})
    assert rc.runtime_tool("dwarf-amaru-runtime-blockfetch") == str(legacy)


def test_chainsync_command_uses_the_pallas_client_and_the_given_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "runtime_tool", lambda name: f"/tools/{name}")
    seen = {}
    monkeypatch.setattr(rc, "run", lambda args, **kw: seen.setdefault("args", args))
    rc.run_chainsync_fetch(rc.PROFILE_A_CONFIG, 3001, "5.ab", tmp_path, host="10.1.0.5")
    # Two headers per point, as Amaru's fetch-chain-headers (BOOTSTRAP_HEADERS_PER_POINT) wrote;
    # the burst and multi-peer checks count exactly two.
    assert seen["args"][2:] == ["/tools/dwarf-amaru-runtime-chainsync", "10.1.0.5:3001", rc.PROFILE_A_CONFIG.network_magic, "5.ab", str(tmp_path), "2"]


def test_blockfetch_command_defaults_to_loopback_and_accepts_an_endpoint(monkeypatch):
    monkeypatch.setattr(rc, "runtime_tool", lambda name: f"/tools/{name}")
    calls = []
    monkeypatch.setattr(rc, "run", lambda args, **kw: calls.append(args))
    rc.run_blockfetch(rc.PROFILE_A_CONFIG, 3001, "1.aa", "2.bb")
    rc.run_blockfetch(rc.PROFILE_A_CONFIG, 3001, "1.aa", "2.bb", host="10.1.0.5")
    assert calls[0][2:4] == ["/tools/dwarf-amaru-runtime-blockfetch", "127.0.0.1:3001"]
    assert calls[1][3] == "10.1.0.5:3001"


def test_build_plan_lays_out_the_crate_beside_the_checkouts(tmp_path):
    plan = b.build_plan(dwarf_root=Path("/src/dwarf"), amaru_src=Path("/c/amaru"), pallas_src=Path("/c/pallas"),
                        work_dir=tmp_path, toolchain="nightly-2026-09-04", cargo="/h/.cargo/bin/cargo")
    assert plan["links"] == {str(tmp_path / "codebases" / "amaru"): "/c/amaru", str(tmp_path / "codebases" / "pallas"): "/c/pallas"}
    shims = plan["commands"][0]
    assert shims[:3] == ["/h/.cargo/bin/cargo", "+nightly-2026-09-04", "build"] and "--release" in shims
    assert [shims[i + 1] for i, a in enumerate(shims) if a == "--bin"] == list(b.SHIM_TOOLS)
    assert plan["commands"][1][-1].endswith("cardano-debug-epoch-state-extractor/Cargo.toml")


def test_record_lists_every_tool_with_its_digest_and_sources(tmp_path):
    for name in (*b.SHIM_TOOLS, b.EXTRACTOR_TOOL):
        _tool(tmp_path / "out", name, name.encode())
    record = b.write_record(output_root=tmp_path, key="out", revisions={"amaru": "a" * 40, "pallas": "b" * 40, "dwarf": "c" * 40},
                            toolchain="nightly-2026-09-04")
    assert set(record["tools"]) == {*b.SHIM_TOOLS, b.EXTRACTOR_TOOL}
    assert record["tools"]["dwarf-amaru-runtime-chainsync"]["sha256"] == hashlib.sha256(b"dwarf-amaru-runtime-chainsync").hexdigest()
    assert json.loads((tmp_path / "record.json").read_text())["revisions"]["amaru"] == "a" * 40


def test_lsq_extract_command_prefers_the_recorded_tool_and_falls_back_to_cargo(monkeypatch):
    from profile_manager import primitives as p
    monkeypatch.setattr(rc, "runtime_tool", lambda name: f"/tools/{name}")
    cmd = p.build_runtime_cardano_lsq_extract_command(socket_path="/s", network_magic=42, era=6, output_path=Path("/o"))
    assert cmd[:3] == ["/tools/cardano-debug-epoch-state-extractor", "--socket", "/s"]

    def missing(name):
        raise RuntimeError("not built")

    monkeypatch.setattr(rc, "runtime_tool", missing)
    cmd = p.build_runtime_cardano_lsq_extract_command(socket_path="/s", network_magic=42, era=6, output_path=Path("/o"))
    assert cmd[:2] == ["cargo", "run"] and cmd[cmd.index("--") + 1] == "--socket"


def test_published_host_port_maps_to_the_container_endpoint(tmp_path, monkeypatch):
    import dataclasses
    (tmp_path / "runtime.json").write_text(json.dumps({
        "haskell_nodes": [{"id": "node1", "name": None, "port": 35781, "container_ip": "10.192.5.4",
                           "container_listen_address": "0.0.0.0:3001"}],
        "amaru_nodes": [{"id": "node3", "port": 35783, "container_ip": "10.192.5.6",
                         "container_listen_address": "0.0.0.0:3000"}]}), encoding="utf-8")
    config = dataclasses.replace(rc.PROFILE_A_CONFIG, runtime_root=tmp_path)
    assert rc.peer_endpoint(config, 35781) == ("10.192.5.4", 3001)
    assert rc.peer_endpoint(config, 35783) == ("10.192.5.6", 3000)
    assert rc.peer_endpoint(config, 40000) == ("127.0.0.1", 40000)
    monkeypatch.setattr(rc, "runtime_tool", lambda name: f"/tools/{name}")
    calls = []
    monkeypatch.setattr(rc, "run", lambda args, **kw: calls.append(args))
    rc.run_blockfetch(config, 35781, "1.aa", "2.bb")
    assert calls[0][3] == "10.192.5.4:3001"
