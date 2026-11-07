#!/usr/bin/env python3
"""Antithesis Test Composer anytime command: every mapped node is TCP reachable.

Reads the package runtime.json, connects to each node's listen address and emits
one `sometimes` property. It never fails the timeline on its own: reachability
during faults is exactly what the platform perturbs."""
import json
import os
import pathlib
import socket
import sys

sys.path.insert(0, '/home/dwarf/dwarf-fw/dwarf')
from profile_manager import antithesis_sdk as sdk  # noqa: E402

PACKAGE = pathlib.Path(os.environ.get('DWARF_ANTITHESIS_PACKAGE_DIR', '/opt/dwarf/package'))


def reachable(address, timeout=3.0):
    host, _, port = address.rpartition(':')
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def main():
    runtime = json.loads((PACKAGE / 'runtime.json').read_text(encoding='utf-8'))
    unreachable = [node['id'] for node in runtime.get('nodes', []) if not reachable(node['listen_address'])]
    sdk.emit_assert('sometimes', 'dwarf_nodes_reachable', not unreachable, details={'unreachable': unreachable})
    return 0


if __name__ == '__main__':
    sys.exit(main())
