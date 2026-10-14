#!/usr/bin/env bash
# DWARF devnet-smoke CI runner (self-hosted runner only).
#
# Runs a curated subset of self-provisioning cardano-node devnet scenarios through
# the composer, then GUARANTEES teardown of anything THIS run created — without ever
# touching containers it did not start (e.g. long-running client devnets on the host).
#
# Isolation model:
#   - Each `scenario run` composes a project named dwarf-substrate-<run-id>.
#   - Before the run we snapshot existing dwarf-substrate-* projects; after (always)
#     we tear down only the projects that appeared during this run.
#
# Exit non-zero if any scenario fails its assertions.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

RUNS_DIR="${DWARF_CI_RUNS_DIR:-$REPO_ROOT/ci-devnet-runs}"
STATE_DIR="${DWARF_CI_STATE_DIR:-$REPO_ROOT/ci-devnet-state}"
IMPORTS_DIR="${DWARF_CI_IMPORTS_DIR:-$REPO_ROOT/ci-devnet-imported}"
mkdir -p "$RUNS_DIR" "$STATE_DIR" "$IMPORTS_DIR"

# Curated smoke subset: 2-node cardano-node, testnet_42 (fast fake epochs),
# all-cardano assertions (no amaru serve-only observation gaps). Keep it short.
SCENARIOS=(
  "dwarf/scenarios/runtime-substrate-honest-baseline-docker-mode-example-smoke.yaml"
  "dwarf/scenarios/runtime-substrate-cardano-node-mixed-minor-phase1-smoke.yaml"
)
[ "$#" -gt 0 ] && SCENARIOS=("$@")

projects_now() {
  local containers networks
  containers="$(docker ps -a --format '{{.Label "com.docker.compose.project"}}')" || return 1
  networks="$(docker network ls --format '{{.Label "com.docker.compose.project"}}')" || return 1
  printf '%s\n%s\n' "$containers" "$networks" \
    | awk '/^dwarf-substrate-[a-z0-9]+(-[a-z0-9]+)*$/' \
    | sort -u
}

remove_project() {
  local project="$1" container_ids network_ids
  container_ids="$(docker ps -aq --filter "label=com.docker.compose.project=$project")" || return 1
  if [ -n "$container_ids" ]; then
    # Docker IDs contain no shell whitespace except the separating newlines.
    # shellcheck disable=SC2086
    docker rm -f $container_ids >/dev/null || return 1
  fi
  network_ids="$(docker network ls -q --filter "label=com.docker.compose.project=$project")" || return 1
  if [ -n "$network_ids" ]; then
    # shellcheck disable=SC2086
    docker network rm $network_ids >/dev/null || return 1
  fi
}

if ! BEFORE="$(projects_now)"; then
  echo "FATAL: unable to capture complete pre-run Docker inventory; no scenarios were started" >&2
  exit 1
fi

cleanup() {
  local original_status=$?
  local after new proj cleanup_failed=0
  echo "== cleanup: tearing down only this run's substrate projects =="
  trap - EXIT
  if ! after="$(projects_now)"; then
    echo "FATAL: unable to capture complete post-run Docker inventory; cleanup skipped" >&2
    exit 1
  fi
  new="$(comm -13 <(printf '%s\n' "$BEFORE") <(printf '%s\n' "$after"))"
  for proj in $new; do
    echo "  removing $proj"
    if ! remove_project "$proj"; then
      echo "FATAL: failed to remove exact disposable project $proj" >&2
      cleanup_failed=1
    fi
  done
  # never touch anything not in $new (client devnets stay up)
  if [ "$cleanup_failed" -ne 0 ]; then
    exit 1
  fi
  exit "$original_status"
}
trap cleanup EXIT

fail=0
for scn in "${SCENARIOS[@]}"; do
  echo "==================================================================="
  echo "== scenario: $scn"
  echo "==================================================================="
  output="$(timeout 900 python3 dwarf/cardano-profile scenario run "$scn" \
        --backend local-devnet --origin ci --runs-dir "$RUNS_DIR" \
        --state-dir "$STATE_DIR" 2>&1)"
  status=$?
  printf '%s\n' "$output"
  run_id="$(printf '%s\n' "$output" | sed -n 's/^run_id: //p' | tail -n 1)"
  if [ "$status" -ne 0 ] || [ -z "$run_id" ]; then
    echo "== FAIL: $scn (exit $status; canonical run id: ${run_id:-missing})"
    fail=1
    continue
  fi

  echo "== in-bundle sign + signed export + clean import verification: $run_id"
  if ! python3 dwarf/cardano-profile bundle sign "$run_id" \
      --runs-dir "$RUNS_DIR" --signing-actor ci; then
    echo "== FAIL: in-bundle signing failed for $run_id"
    fail=1
    continue
  fi
  if ! python3 dwarf/cardano-profile bundle export "$run_id" \
      --runs-dir "$RUNS_DIR" --signing-actor ci; then
    echo "== FAIL: signed export failed for $run_id"
    fail=1
    continue
  fi
  export_dir="$RUNS_DIR/$run_id/outputs/export"
  tarball="$export_dir/$run_id-bundle-export.tar.gz"
  signature="$export_dir/signature.json"
  if ! python3 dwarf/cardano-profile bundle import "$tarball" \
      --signature-path "$signature" --runs-dir "$IMPORTS_DIR"; then
    echo "== FAIL: signed import verification failed for $run_id"
    fail=1
    continue
  fi
  if ! python3 dwarf/cardano-profile bundle verify "$run_id" \
      --runs-dir "$IMPORTS_DIR" --json; then
    echo "== FAIL: imported in-bundle signature/forensic verification failed for $run_id"
    fail=1
    continue
  fi
  echo "== PASS: $scn (run $run_id; signed bundle round-trip verified)"
done

echo "==================================================================="
[ "$fail" -eq 0 ] && echo "devnet-smoke: all scenarios passed" \
                  || echo "devnet-smoke: at least one scenario FAILED"
exit "$fail"
