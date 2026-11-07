"""Select Amaru's CLI command tree from the exact executable, not its version."""
from __future__ import annotations


def launcher_script() -> str:
    """Keep the target bytes intact; adapt the bootstrap wrapper's invocation.

    ``AMARU_RUN_EXTRA_ARGS`` (space-separated) is appended to ``run`` only: the
    rig's ``amaru-relay-bootstrap`` wrapper passes no listen/submit flags, and
    the generated Antithesis rig needs the N2N and submit-API ports it declares.
    """
    return '''#!/bin/sh
set -eu
extra=
if [ "${1:-}" = run ]; then
  extra="${AMARU_RUN_EXTRA_ARGS:-}"
fi
case "${1:-}" in
  run|bootstrap)
    if /target/amaru node run --help >/dev/null 2>&1; then
      exec /target/amaru node "$@" $extra
    fi
    ;;
esac
exec /target/amaru "$@" $extra
'''


def shell_command_selection() -> str:
    # The Compose command uses positional parameters as a safe command array.
    return ('set -- amaru; '
            'if amaru node run --help >/dev/null 2>&1; then set -- amaru node; fi')
