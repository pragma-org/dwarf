import json
from pathlib import Path

from profile_manager import dashboard
from profile_manager.data.operate_profiles import operate_profile_entries
from profile_manager.data.operate_versions import version_catalog_view


ROOT = Path(__file__).resolve().parents[1]


def _profiles(tmp_path: Path, monkeypatch):
    root = tmp_path / "profiles"
    for profile_id, body in {
        "profile-latest": {
            "id": "profile-latest",
            "label": "Latest Cardano",
            "node_type": "cardano-node",
            "node_count": 1,
            "amaru_node_count": 0,
            "network_magic": 42,
            "peer_sharing": False,
            "version_policy": "latest-stable",
        },
        "profile-mixed": {
            "id": "profile-mixed",
            "label": "Confirmed mixed",
            "node_type": "mixed",
            "node_count": 1,
            "amaru_node_count": 1,
            "network_magic": 42,
            "peer_sharing": False,
            "version_policy": "latest-confirmed",
        },
    }.items():
        directory = root / profile_id
        directory.mkdir(parents=True)
        (directory / "profile.yaml").write_text(json.dumps(body) + "\n", encoding="utf-8")
    monkeypatch.setenv("ADA2_DWARF_PROFILES_DIR", str(root))
    from profile_manager import profiles
    from profile_manager.data import operate_profiles

    monkeypatch.setattr(profiles, "PROFILE_ROOT", root)
    monkeypatch.setattr(operate_profiles, "PROFILE_ROOT", root)
    return root


def test_version_catalog_view_separates_releases_pairs_defaults_and_evidence():
    view = version_catalog_view()

    cardano = next(row for row in view["releases"] if row["implementation"] == "cardano-node" and row["version"] == "11.1.2")
    pair = next(row for row in view["pairs"] if row["id"] == "cardano-10.7.1__amaru-10.11.0")
    assert cardano["artifact_digest"].startswith("sha256:")
    assert cardano["scope_statuses"]["cardano-only"]["status"] == "confirmed"
    assert cardano["scope_statuses"]["cardano-only"]["default"] is True
    assert pair["status"] == "confirmed"
    assert pair["default"] is True
    assert pair["evidence"]
    assert view["catalog_revision"]


def test_versions_route_is_filterable_and_explains_claim_boundaries():
    html = dashboard.render_route_html("/operate/versions")

    assert html is not None
    assert "Node versions" in html
    assert 'data-version-filter' in html
    assert "11.1.2" in html
    assert "cardano-10.7.1__amaru-10.11.0" in html
    assert "stable is not the same as confirmed" in html.lower()
    assert "sha256:6365403f" in html
    assert "Check for new versions" in html
    assert "Last successful check" in html
    assert "Amaru target" in html
    assert "supporting Cardano-node" in html
    assert "Qualification reason" in html
    assert "Evidence" in html
    assert "Related issues" in html


def test_manual_version_refresh_is_token_gated_and_serialized(monkeypatch):
    calls = []

    def start(*, manual):
        calls.append(manual)
        return {"started": True, "state": "running"}

    monkeypatch.setattr("profile_manager.version_discovery.start_release_refresh", start)

    denied = dashboard.dispatch_version_refresh_request(
        method="POST", path="/api/versions/refresh?token=wrong", expected_token="right"
    )
    accepted = dashboard.dispatch_version_refresh_request(
        method="POST", path="/api/versions/refresh?token=right", expected_token="right"
    )

    assert denied[0] == 401
    assert accepted[0] == 202
    assert json.loads(accepted[2])["state"] == "running"
    assert calls == [True]


def test_profile_builder_uses_catalog_backed_release_and_pair_selectors():
    html = dashboard.render_route_html("/operate/profiles/new?template=mixed-minimal")

    assert 'data-field="version_policy"' in html
    assert 'data-field="cardano_version"' in html
    assert '<option value="11.1.2"' in html
    assert 'data-field="amaru_version"' in html
    assert '<option value="10.11.20260912"' in html
    assert 'data-field="compatibility_pair"' in html
    assert '<option value="cardano-10.7.1__amaru-10.11.0"' in html
    assert "/operate/versions" in html


def test_profile_catalog_shows_policy_status_exact_versions_and_preview_first(tmp_path, monkeypatch):
    _profiles(tmp_path, monkeypatch)

    entries = operate_profile_entries()
    html = dashboard.render_route_html("/operate/profiles")
    latest = next(entry for entry in entries if entry["id"] == "profile-latest")
    mixed = next(entry for entry in entries if entry["id"] == "profile-mixed")

    assert latest["version_status"] == "confirmed"
    assert latest["version_summary"] == "cardano-node 11.1.2"
    assert mixed["version_status"] == "confirmed"
    assert "10.7.1" in mixed["version_summary"] and "10.11.0" in mixed["version_summary"]
    assert "latest-stable" in html
    assert "confirmed" in html
    assert "Preview deploy" in html
    assert "/api/deploy/preview" in html
    assert "Deploy resolved version" in html


def test_profile_detail_includes_version_intent(tmp_path, monkeypatch):
    _profiles(tmp_path, monkeypatch)

    html = dashboard.render_route_html("/operate/profiles/profile-mixed")

    assert "version policy" in html.lower()
    assert "latest-confirmed" in html


def test_versions_navigation_and_responsive_contract_are_present():
    from profile_manager.data.sub_nav import OPERATE_SUB_NAV

    css = (ROOT / "dwarf/dashboard/static/css/base.css").read_text(encoding="utf-8")
    assert any(item["url"] == "/operate/versions" for item in OPERATE_SUB_NAV)
    assert ".version-catalog" in css
    assert ".version-refresh" in css
    assert ".version-card__evidence" in css
    assert "@media (max-width: 700px)" in css or "@media (max-width: 640px)" in css
