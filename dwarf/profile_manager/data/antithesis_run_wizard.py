"""Read-only definitions for an unsubmitted Antithesis draft.

Deliberately excludes the Local wizard's resolver, runtime health, local image
registrations and qualification results. Catalog presence is not remote support.
"""
from profile_manager.data.catalog_definitions import list_definitions
from profile_manager.data.scenarios import _list_scenarios_for_compare
from profile_manager.operator_version_decisions import OperatorVersionDecisionLedger
from profile_manager.profiles import load_profiles
from profile_manager.version_catalog import load_version_catalog

IMPLEMENTATIONS = ('cardano-node', 'amaru')


def prepared_build_options(ledger_state: dict | None = None) -> dict[str, list[dict]]:
    """Operator-prepared releases the Antithesis wizard can select, per
    implementation, labelled the way the Versions page labels them."""
    state = ledger_state if ledger_state is not None else OperatorVersionDecisionLedger().read()
    proofs = {}
    for proof in state.get('auto_port_proofs') or []:
        if isinstance(proof, dict) and proof.get('registration_id') and proof.get('run_id'):
            proofs.setdefault(proof['registration_id'], proof['run_id'])
    options: dict[str, list[dict]] = {name: [] for name in IMPLEMENTATIONS}
    for row in state.get('registrations') or []:
        implementation = row.get('implementation')
        if implementation not in options or not isinstance(row.get('source_build'), dict):
            continue
        build = row['source_build']
        mode = 'patched' if build.get('variant') == 'instrumented' else 'stock'
        label = f"{row.get('version')} · {mode}"
        if build.get('auto_ported'):
            label += ' · auto-ported from ' + str(build.get('ported_from_source_revision') or '')[:12]
            proven_by = proofs.get(row.get('registration_id'))
            if proven_by:
                label += ' · proven by run ' + str(proven_by)
        options[implementation].append({
            'registration_id': row.get('registration_id'), 'version': row.get('version'), 'mode': mode,
            'source_revision': (row.get('provenance') or {}).get('source_revision'), 'label': label})
    return options


def antithesis_draft_catalog() -> dict:
    return {
        "prepared_builds": prepared_build_options(),
        "profiles": [{"id": p.id, "label": p.label,
                      "cardano_count": p.node_count, "amaru_count": p.amaru_node_count}
                     for p in load_profiles()],
        "versions": [{"implementation": r["implementation"], "version": r["version"],
                      "channel": r["channel"], "source_revision": r["source_revision"]}
                     for r in load_version_catalog()["releases"]],
        "scenarios": [{"id": r["id"], "title": r.get("title") or r["id"],
                       "implementation": r.get("target_impl") or "unspecified"}
                      for r in _list_scenarios_for_compare()],
        "measurement_profiles": [
            {"id": r.definition_id, "title": r.data.get("title") or r.definition_id,
             "tap_ids": [s["id"] for s in r.data.get("measurements", []) if s.get("enabled") is True]}
            for r in list_definitions("measurement-profiles")],
        "taps": [{"id": r.definition_id, "title": r.data.get("title") or r.definition_id,
                  "description": r.data.get("description") or "",
                  "mode": r.data.get("collection_mode") or "unspecified",
                  "target_modes": (r.data.get("compatibility") or {}).get("target_modes", [])}
                 for r in list_definitions("measurements")],
    }
