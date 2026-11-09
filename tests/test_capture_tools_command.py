"""F113: container-node captures run the pinned capture-tools image with the narrowest rights."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts import runtime_common as rc

IMAGE_ID = "sha256:" + "e" * 64


def _runner(label="rev-1"):
    def run(argv, **kwargs):
        if argv[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(returncode=0, stdout=f"{IMAGE_ID} {label}\n", stderr="")
        raise AssertionError(argv)
    return run


def test_network_capture_joins_only_the_namespace_with_net_raw(monkeypatch):
    monkeypatch.setenv("DWARF_SOURCE_REVISION", "rev-1")
    argv = rc.capture_tools_command(["tcpdump", "-i", "any"], network_container="ctrl", capabilities=("NET_RAW", "NET_ADMIN"),
                                    runner=_runner())
    joined = " ".join(argv)
    assert argv[:3] == ["docker", "run", "--rm"] and "--network container:ctrl" in joined
    assert "--cap-drop ALL --cap-add NET_RAW --cap-add NET_ADMIN" in joined and "--security-opt no-new-privileges" in joined
    assert "--user 0:0" in joined and IMAGE_ID in argv and argv[-3:] == ["tcpdump", "-i", "any"]
    assert "--pid" not in argv


def test_process_trace_joins_the_node_pid_namespace_with_sys_ptrace(monkeypatch):
    monkeypatch.setenv("DWARF_SOURCE_REVISION", "rev-1")
    argv = rc.capture_tools_command(["strace", "-c"], pid_container="node1", capabilities=("SYS_PTRACE",), runner=_runner())
    assert "--pid" in argv and argv[argv.index("--pid") + 1] == "container:node1"
    assert argv[argv.index("--network") + 1] == "none" and "SYS_PTRACE" in argv


def test_an_image_from_another_revision_is_refused(monkeypatch):
    monkeypatch.setenv("DWARF_SOURCE_REVISION", "rev-1")
    with pytest.raises(RuntimeError, match="revision"):
        rc.capture_tools_command(["tcpdump"], network_container="c", capabilities=("NET_RAW",), runner=_runner("rev-0"))
