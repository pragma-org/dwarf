"""Read-only definitions for an unsubmitted Antithesis draft.

Deliberately excludes the Local wizard's resolver, runtime health, local image
registrations and qualification results. Catalog presence is not remote support.
"""
from profile_manager.data.catalog_definitions import list_definitions
from profile_manager.data.scenarios import _list_scenarios_for_compare
from profile_manager.profiles import load_profiles
from profile_manager.version_catalog import load_version_catalog


def antithesis_draft_catalog() -> dict:
    return {
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
