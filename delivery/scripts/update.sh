#!/usr/bin/env bash
set -euo pipefail

PROGRAM=dwarf-update
install_root=${DWARF_INSTALL_ROOT:-${XDG_DATA_HOME:-${HOME}/.local/share}/dwarf-install}
version=
bundle_dir=
test_public_key=

die() { printf '%s: %s\n' "${PROGRAM}" "$*" >&2; exit 1; }
while (($#)); do
  case "$1" in
    --version) (($# >= 2)) || die "--version requires a value"; version=$2; shift 2 ;;
    --bundle-dir) (($# >= 2)) || die "--bundle-dir requires a value"; bundle_dir=$2; shift 2 ;;
    --test-public-key) (($# >= 2)) || die "--test-public-key requires a value"; test_public_key=$2; shift 2 ;;
    --help|-h)
      echo "Usage: update.sh (--version VERSION | --bundle-dir DIR) [--test-public-key FILE]"
      exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done
[[ -f "${install_root}/install-receipt" ]] || die "no verified DWARF installation found at ${install_root}"
[[ -x "${install_root}/current/install.sh" ]] || die "installed bootstrap is missing"
[[ -n "${version}" || -n "${bundle_dir}" ]] || die "provide --version or --bundle-dir"
[[ -z "${version}" || -z "${bundle_dir}" ]] || die "choose either --version or --bundle-dir"

args=(--install-root "${install_root}")
runtime_root=$(sed -n 's/^runtime_root=//p' "${install_root}/install-receipt")
[[ -n ${runtime_root} ]] || die "installation receipt has no runtime_root"
args+=(--runtime-root "${runtime_root}")
[[ -n ${version} ]] && args+=(--version "${version}")
[[ -n ${bundle_dir} ]] && args+=(--bundle-dir "${bundle_dir}")
[[ -n ${test_public_key} ]] && args+=(--test-public-key "${test_public_key}")

previous=$(readlink "${install_root}/current" 2>/dev/null || true)
printf 'Updating DWARF; previous release is %s\n' "${previous:-none}"
if ! "${install_root}/current/install.sh" "${args[@]}"; then
  printf '%s: update failed; installer rollback was requested and the previous release remains selected\n' "${PROGRAM}" >&2
  exit 1
fi
printf 'DWARF update completed. Previous release retained at %s for rollback.\n' "${previous:-none}"
