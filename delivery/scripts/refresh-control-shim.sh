#!/usr/bin/env bash
# Refresh the installed forced-command control shim from this release (F38).
#
# provision-control-channel.sh installs the shim once; a later redeploy kept
# shim mode but left that old shim in place, so shim fixes never reached a host
# unless copied by hand. This script finds the installed shim (the command= of
# the dwarf-control-channel line in authorized_keys, else INSTALL_DIR, else
# ~/dwarf-control), replaces it atomically when it differs from the release,
# and keeps the previous copy next to it. Key, authorized_keys and config are
# never touched. It never fails the install: a missing shim is reported.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CONTROL_PLANE_DIR=${CONTROL_PLANE_DIR:-${SCRIPT_DIR}/../control-plane}
SOURCE=${CONTROL_PLANE_DIR}/dwarf-deploy-shim.py
AUTHORIZED_KEYS=${DWARF_AUTHORIZED_KEYS:-${HOME}/.ssh/authorized_keys}

if [[ ! -f "${SOURCE}" ]]; then
  echo "    control shim source ${SOURCE} is missing from this release; nothing refreshed" >&2
  exit 0
fi

target=""
if [[ -f "${AUTHORIZED_KEYS}" ]]; then
  target=$(grep -F 'dwarf-control-channel' "${AUTHORIZED_KEYS}" | sed -nE 's/.*command="([^"]+)".*/\1/p' | head -n 1 || true)
fi
target=${target:-${INSTALL_DIR:-${HOME}/dwarf-control}/dwarf-deploy-shim}

if [[ ! -f "${target}" ]]; then
  echo "    control shim expected at ${target} but not found; run delivery/control-plane/provision-control-channel.sh to (re)install the channel"
  exit 0
fi
if cmp -s "${SOURCE}" "${target}"; then
  echo "    control shim at ${target} already matches this release"
  exit 0
fi

cp -p "${target}" "${target}.prev"
staged=$(mktemp "$(dirname "${target}")/.dwarf-deploy-shim.XXXXXX")
cp "${SOURCE}" "${staged}"
chmod 755 "${staged}"
mv -f "${staged}" "${target}"
echo "    refreshed the installed control shim at ${target} (previous copy kept as ${target}.prev)"
