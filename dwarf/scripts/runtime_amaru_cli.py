"""Select Amaru's CLI command tree from the exact executable, not its version."""
from __future__ import annotations


def launcher_script() -> str:
    """Keep the target bytes intact; adapt the bootstrap wrapper's invocation."""
    return '''#!/bin/sh
set -eu
case "${1:-}" in
  run|bootstrap)
    if /target/amaru node run --help >/dev/null 2>&1; then
      exec /target/amaru node "$@"
    fi
    ;;
esac
exec /target/amaru "$@"
'''


def shell_command_selection() -> str:
    # The Compose command uses positional parameters as a safe command array.
    return ('set -- amaru; '
            'if amaru node run --help >/dev/null 2>&1; then set -- amaru node; fi')
