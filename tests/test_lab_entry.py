"""Navigation and isolation contract for the two execution destinations."""
from html.parser import HTMLParser

import pytest

from profile_manager import dashboard


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def matching(self, tag, **attrs):
        return [a for t, a in self.elements if t == tag and all(a.get(k) == v for k, v in attrs.items())]


@pytest.mark.parametrize('route', ['/', '/operate'])
def test_lab_entry_is_reachable_from_home_and_operate(route):
    html = dashboard.render_route_html(route)
    assert Elements(html).matching('a', href='/lab')
    assert 'Enter the lab' in html


@pytest.mark.parametrize('route', ['/lab', '/lab/', '/lab?view=advanced'])
def test_lab_cards_lead_to_separate_wizards(route):
    html = dashboard.render_route_html(route)
    assert html is not None
    cards = [a for a in Elements(html).matching('a') if 'data-lab-destination' in a]
    assert [(a['data-lab-destination'], a['href']) for a in cards] == [
        ('local', '/run'), ('antithesis', '/run/antithesis')]
    assert 'dk-spread--two' in html


def test_antithesis_page_does_not_resolve_or_dispatch_a_local_run(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Antithesis rendering called the local run pipeline')

    monkeypatch.setattr('profile_manager.data.operate_run_wizard.resolve_run_plan', forbidden)
    monkeypatch.setattr('profile_manager.run_plan.resolve_run_plan', forbidden)
    monkeypatch.setattr(dashboard, 'render_operate_run_wizard', forbidden)
    html = dashboard.render_route_html('/run/antithesis', token='must-not-leak')
    assert html is not None
    elements = Elements(html)
    for step in ['topology', 'versions', 'scenario', 'measurements', 'review', 'launch']:
        assert elements.matching('section', id=f'run-stage-{step}')
    for button_id in ['antithesis-prepare', 'antithesis-submit']:
        button, = elements.matching('button', id=button_id)
        assert 'disabled' in button
    assert 'antithesis-run-wizard.js' in html
    assert '/static/js/run-wizard.js' not in html
    assert '/api/run/' not in html
    assert 'data-antithesis-token="must-not-leak"' in html
    assert 'Prepare, verify delivery, then submit through MOOG' in html
    assert 'antithesis-submission.js' in html
    assert 'No submission' in html


def test_local_run_still_uses_its_existing_pipeline():
    html = dashboard.render_route_html('/run', token='local-token')
    assert 'data-prepare-url="/api/run/prepare"' in html
    assert 'data-start-url="/api/run/start"' in html
    assert 'data-dashboard-token="local-token"' in html
    assert 'antithesis-run-wizard.js' not in html


_REGISTRATIONS = [
    {'registration_id': 'registration-release-build-' + '1' * 32 + '-stock', 'implementation': 'amaru',
     'version': '10.11.20260918', 'config_image_id': 'sha256:' + 'a' * 64,
     'provenance': {'source_revision': 'aedfe797a5b8ef00d8b362be40b47a52c3b4a379', 'qualification': 'unverified'},
     'source_build': {'variant': 'stock', 'execution_id': 'release-build-' + '1' * 32, 'build_result_sha256': 'sha256:' + 'c' * 64}},
    {'registration_id': 'registration-release-build-' + '2' * 32 + '-instrumented', 'implementation': 'amaru',
     'version': '10.11.20261029', 'config_image_id': 'sha256:' + 'b' * 64,
     'provenance': {'source_revision': 'b' * 40, 'qualification': 'unverified'},
     'source_build': {'variant': 'instrumented', 'execution_id': 'release-build-' + '2' * 32, 'build_result_sha256': 'sha256:' + 'c' * 64,
                      'patch_set_sha256': 'sha256:' + 'd' * 64, 'measurement_revision': 'nanoseconds-v4',
                      'instrumentation_evidence_sha256': 'sha256:' + 'e' * 64,
                      'auto_ported': True, 'ported_from_source_revision': 'aedfe797a5b8ef00d8b362be40b47a52c3b4a379'}},
]
_PROOFS = [{'proof_id': 'auto-port-proof-' + 'f' * 32, 'registration_id': _REGISTRATIONS[1]['registration_id'],
            'implementation': 'amaru', 'source_revision': 'b' * 40, 'run_id': '20261105T123349Z-41454fc8'}]


class _Ledger:
    state = {'revision': 'r', 'registrations': [], 'auto_port_proofs': []}

    def __init__(self, *args, **kwargs):
        pass

    def read(self):
        return self.state


def test_prepared_build_options_label_auto_ported_and_proven(monkeypatch):
    from profile_manager.data import antithesis_run_wizard as wizard
    options = wizard.prepared_build_options({'revision': 'r', 'registrations': _REGISTRATIONS, 'auto_port_proofs': _PROOFS})
    assert set(options) == {'amaru', 'cardano-node'} and options['cardano-node'] == []
    stock, patched = options['amaru']
    assert stock == {'registration_id': _REGISTRATIONS[0]['registration_id'], 'version': '10.11.20260918', 'mode': 'stock',
                     'source_revision': 'aedfe797a5b8ef00d8b362be40b47a52c3b4a379', 'label': '10.11.20260918 · stock'}
    assert patched['mode'] == 'patched'
    assert patched['label'] == '10.11.20261029 · patched · auto-ported from aedfe797a5b8 · proven by run 20261105T123349Z-41454fc8'


def test_wizard_lists_prepared_builds_per_implementation(monkeypatch):
    from profile_manager.data import antithesis_run_wizard as wizard
    _Ledger.state = {'revision': 'r', 'registrations': _REGISTRATIONS, 'auto_port_proofs': _PROOFS}
    monkeypatch.setattr(wizard, 'OperatorVersionDecisionLedger', _Ledger)
    html = dashboard.render_route_html('/run/antithesis', token='t')
    elements = Elements(html)
    selects = elements.matching('select')
    assert any('data-node-registration' in a for a in selects)
    options = [a for a in elements.matching('option') if a.get('value', '').startswith('registration-release-build-')]
    assert {a['value'] for a in options} == {r['registration_id'] for r in _REGISTRATIONS}
    assert {a.get('data-mode') for a in options} == {'stock', 'patched'}
    assert 'auto-ported from aedfe797a5b8 · proven by run 20261105T123349Z-41454fc8' in html
    assert 'Experimental node source, binary or image' not in html
    assert 'node-start' not in html
    assert [a for a in elements.matching('button') if 'data-node-select' in a]


def test_wizard_without_prepared_builds_points_to_versions_page(monkeypatch):
    from profile_manager.data import antithesis_run_wizard as wizard
    _Ledger.state = {'revision': 'r', 'registrations': [], 'auto_port_proofs': []}
    monkeypatch.setattr(wizard, 'OperatorVersionDecisionLedger', _Ledger)
    html = dashboard.render_route_html('/run/antithesis', token='t')
    assert 'No prepared build for this implementation' in html
    assert Elements(html).matching('a', href='/operate/versions')


def test_antithesis_page_tells_the_user_the_saved_pat_is_stored_unencrypted():
    html = dashboard.render_route_html('/run/antithesis', token='t')
    assert 'stored unencrypted' in html, 'the save opt-in must say the PAT is kept in plaintext'
