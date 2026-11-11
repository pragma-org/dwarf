#!/usr/bin/env python3
"""Antithesis Test Composer singleton driver: release the DWARF scenario that the
container entrypoint has already prepared, then report its outcome.

The entrypoint emits setup_complete only after prepare and the readiness probe
succeeded, then blocks on /status/go. This driver writes that marker, waits for
/status/done, and exits 0 when the scenario ran (its verdicts are SDK assertions) or with
the error code when it could not run."""
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
    # A scenario that ran (pass 0 / fail 1) reports its verdicts through the SDK assertions,
    # measured-only under faults; its local fail is not a command failure (F176). Error,
    # precondition and revision-mismatch codes stay non-zero.
    sys.exit(0 if code in (0, 1) else code)


if __name__ == '__main__':
    main()
