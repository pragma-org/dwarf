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
