#!/usr/bin/env python3
"""Antithesis Test Composer singleton driver: release the DWARF scenario that the
container entrypoint has already prepared, then report its outcome.

The entrypoint emits setup_complete only after prepare and the readiness probe
succeeded, then blocks on /status/go. This driver writes that marker, waits for
/status/done, and exits with the scenario's recorded exit code so the platform
sees one timeline per scenario execution."""
import pathlib
import sys
import time

STATUS = pathlib.Path('/status')


def main():
    STATUS.mkdir(parents=True, exist_ok=True)
    (STATUS / 'go').write_text('go\n')
    while not (STATUS / 'done').exists():
        time.sleep(2)
    try:
        code = int((STATUS / 'exit').read_text().strip() or '2')
    except (OSError, ValueError):
        code = 2
    sys.exit(code)


if __name__ == '__main__':
    main()
