"""Workflow trust boundaries and executable hosted discovery, without Actions."""
import ast
import json
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/dwarf-security-campaign.yml'
SHARD_ACTION = ROOT / '.github/actions/dwarf-campaign-shard/action.yml'


def workflow():
    assert WORKFLOW.is_file(), 'security campaign workflow is missing'
    # BaseLoader preserves the Actions `on` key (YAML 1.1 treats it as a bool).
    return yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)


def step(job, name):
    return next(item for item in workflow()['jobs'][job]['steps'] if item.get('name') == name)


def python_script(job, name):
    return step(job, name)['run'].split("python3 - <<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]


def discovery():
    namespace = {'__name__': 'workflow_contract'}
    exec(compile(python_script('discover', 'Discover and validate source candidates'), str(WORKFLOW), 'exec'), namespace)
    return namespace


def source(root, name):
    candidate = root / 'dwarf/testplease/amaru' / name
    files = {
        'Cargo.toml': '[workspace]\nmembers=["crates/amaru"]\n',
        'Cargo.lock': 'version = 4\n',
        'crates/amaru/Cargo.toml': '[package]\nname="amaru"\nversion="999.0.0"\n',
        'crates/amaru/src/main.rs': 'fn main() {}\n',
        'rust-toolchain.toml': '[toolchain]\nchannel="nightly-2026-09-04"\n',
        'build.rs': 'panic!("MUST NOT EXECUTE DURING DISCOVERY");\n',
    }
    for relative, content in files.items():
        path = candidate / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return candidate


def test_triggers_are_controlled_and_have_no_pr_execution():
    data = workflow()
    assert set(data['on']) == {'workflow_dispatch', 'schedule', 'push'}
    assert data['on']['push']['paths'] == ['dwarf/testplease/**']
    assert data['on']['schedule'][0]['cron']
    assert not data['on']['workflow_dispatch'].get('inputs')
    assert data['permissions'] == {'contents': 'read'}


def test_prepare_builds_the_trusted_controller_once_and_exports_it():
    build = step('prepare', 'Build trusted campaign controller once')
    assert build['working-directory'] == 'campaign-trusted'
    assert 'infrastructure/docker/dwarf-fw.Dockerfile' in build['run']
    assert 'infrastructure/docker/dwarf-controller.Dockerfile' in build['run']
    assert '--build-arg DWARF_SOURCE_REVISION="$TRUSTED_COMMIT"' in build['run']
    assert 'controller-image-id.txt' in build['run']
    assert 'docker save --output ../controller-image.tar' in build['run']
    workflow_text = WORKFLOW.read_text()
    assert workflow_text.count('infrastructure/docker/dwarf-controller.Dockerfile') == 1


def test_discovery_uses_trusted_hosted_code_and_read_only_checkout():
    job = workflow()['jobs']['discover']
    assert job['runs-on'] == 'ubuntu-latest'
    assert job['permissions'] == {'contents': 'read'}
    checkouts = [s for s in job['steps'] if s.get('uses', '').startswith('actions/checkout@')]
    assert checkouts[0]['with']['ref'] == '${{ github.event.repository.default_branch }}'
    assert {s['with']['path'] for s in checkouts} == {'trusted', 'candidate-source'}
    assert all(s['with']['persist-credentials'] == 'false' for s in checkouts)
    assert checkouts[1]['with']['fetch-depth'] == '0'
    run = step('discover', 'Discover and validate source candidates')
    assert run['working-directory'] == 'trusted'
    assert run['env']['PYTHONPATH'] == 'dwarf'
    assert 'candidate-source' not in run['env']['PYTHONPATH']


def test_changed_paths_include_all_candidates_renames_and_deletions():
    api = discovery()
    paths = [b'dwarf/testplease/amaru/one/src/main.rs', b'dwarf/testplease/amaru/two/Cargo.toml',
             b'dwarf/testplease/cardano-node/three/cabal.project', b'dwarf/testplease/amaru/one/deleted.rs',
             b'dwarf/testplease/README.md', b'docs/elsewhere']
    assert api['changed_candidate_keys'](paths) == {('amaru', 'one'), ('amaru', 'two'), ('cardano-node', 'three')}
    assert '--no-renames' in python_script('discover', 'Discover and validate source candidates')
    assert '-z' in python_script('discover', 'Discover and validate source candidates')
    assert 'ls-tree' in python_script('discover', 'Discover and validate source candidates')


def test_source_intake_needs_no_manifest_and_never_executes_build_hooks(tmp_path, monkeypatch):
    api = discovery()
    for name in ('one', 'two', 'unchanged'):
        source(tmp_path, name)
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('discovery executed a subprocess'))
    rows, deleted = api['source_records'](tmp_path, 'a' * 40, {('amaru', 'one'), ('amaru', 'two'), ('amaru', 'removed')})
    assert [row['candidate_name'] for row in rows] == ['one', 'two']
    assert deleted == [['amaru', 'removed']]
    assert all(len(row['source_tree_digest']) == 64 for row in rows)
    assert all(row['repository_commit'] == 'a' * 40 for row in rows)
    for row in rows:
        assert [plan['variant'] for plan in row['build_plans']] == ['stock', 'patched', 'compiler-coverage']
        assert all(plan['status'] == 'unresolved' and plan['ready'] is False for plan in row['build_plans'])
        assert all(plan['unresolved'] and plan['plan_digest'] for plan in row['build_plans'])


def test_source_digest_covers_changed_build_support(tmp_path):
    api = discovery()
    candidate = source(tmp_path, 'one')
    before, _ = api['source_records'](tmp_path, 'a' * 40, {('amaru', 'one')})
    (candidate / 'build.rs').write_text('changed hook')
    after, _ = api['source_records'](tmp_path, 'a' * 40, {('amaru', 'one')})
    assert before[0]['source_tree_digest'] != after[0]['source_tree_digest']


def test_untrusted_symlink_source_rejected(tmp_path):
    api = discovery()
    candidate = source(tmp_path, 'one')
    (candidate / 'external').symlink_to('/etc/passwd')
    with pytest.raises(ValueError, match='symlink escapes'):
        api['source_records'](tmp_path, 'a' * 40, {('amaru', 'one')})


def test_official_metadata_is_review_only_and_suites_are_fixed():
    job = workflow()['jobs']['discover']
    refresh = step('discover', 'Discover official release metadata for review')
    assert "github.event_name != 'push'" in refresh['if']
    assert 'dwarf/scripts/refresh_version_catalog.py' in refresh['run']
    script = python_script('discover', 'Discover and validate source candidates')
    assert 'security-suites/github-security-regression-v1.yaml' in script
    assert 'qualification-suites/node-measurements-v1.yaml' in script
    assert 'versions/catalog.json' in script
    assert 'official-selection-review-required' in script
    assert "'validated' if push or manual_stock" in script
    api = discovery()
    suites = api['trusted_inputs'](ROOT)
    assert len(suites['security']['document']['groups']) == 6
    assert all(value['sha256'] for value in suites.values())


def test_execution_uses_existing_local_run_path_in_ordered_runner_phases():
    data = workflow()
    expected = {
        'amaru_solo': (['discover', 'prepare', 'select'], 'amaru_solo_matrix'),
        'cardano_solo': (['discover', 'prepare', 'select', 'amaru_solo'], 'cardano_solo_matrix'),
        'mixed': (['discover', 'prepare', 'select', 'cardano_solo'], 'mixed_matrix'),
    }
    for name, (needs, matrix) in expected.items():
        job = data['jobs'][name]
        assert job['runs-on'] == 'ubuntu-latest'
        assert job['needs'] == needs
        assert job['permissions'] == {'contents': 'read'}
        assert job['strategy']['fail-fast'] == 'false'
        assert job['strategy']['max-parallel'] == '6'
        assert job['strategy']['matrix'] == '${{ fromJSON(needs.select.outputs.' + matrix + ') }}'
        assert job['timeout-minutes'] == '90'
        checkouts = [s for s in job['steps'] if s.get('uses', '').startswith('actions/checkout@')]
        assert len(checkouts) == 1
        assert checkouts[0]['with']['ref'] == '${{ needs.discover.outputs.trusted_revision }}'
        assert checkouts[0]['with']['persist-credentials'] == 'false'
        action = next(s for s in job['steps'] if s.get('uses', '').endswith('dwarf-campaign-shard'))
        assert action['with']['shard'] == '${{ matrix.shard }}'
        text = json.dumps(job)
        assert 'secrets.' not in text
        assert 'contents: write' not in text
        assert 'docker system prune' not in text

    assert data['jobs']['amaru_solo']['if'] == "needs.select.result == 'success'"
    assert "needs.amaru_solo.result != 'cancelled'" in data['jobs']['cardano_solo']['if']
    assert "needs.cardano_solo.result != 'cancelled'" in data['jobs']['mixed']['if']

    action = yaml.load(SHARD_ACTION.read_text(), Loader=yaml.BaseLoader)
    action_text = json.dumps(action)
    assert 'scripts.ci_hosted_campaign_runtime outer' in action_text
    assert '--work-item ' in action_text
    assert '--work-item-inventory ' in action_text
    executor = (ROOT / 'dwarf/scripts/ci_security_campaign.py').read_text()
    assert '"dwarf/cardano-profile", "scenario", "run"' in executor
    assert '[*base, "export", run_id' in executor
    upload = next(s for s in action['runs']['steps'] if s.get('name') == 'Retain local run evidence bundle')
    assert upload['if'] == 'always()'
    assert '${{ inputs.shard }}' in upload['with']['name']


def test_fanout_stages_are_identity_bound_and_aggregate_before_publication():
    gate = step('prepare', 'Validate hosted build transfer')
    assert gate['working-directory'] == 'campaign-trusted'
    assert gate['env']['SOURCE_COMMIT'] == '${{ github.sha }}'
    assert gate['env']['TRUSTED_COMMIT'] == '${{ needs.discover.outputs.trusted_revision }}'
    assert 'python3 -m scripts.ci_hosted_campaign_stage' in gate['run']
    plan = step('prepare', 'Register candidate and freeze initial campaign')
    assert 'python3 -m scripts.ci_hosted_campaign_plan' in plan['run']
    assert 'python3 -m scripts.ci_campaign_fanout prepare' in plan['run']

    calibrate = workflow()['jobs']['calibrate']
    assert calibrate['strategy']['fail-fast'] == 'false'
    assert calibrate['strategy']['max-parallel'] == '2'
    assert calibrate['permissions'] == {'contents': 'read'}
    assert '--work-item ' in step('calibrate', 'Run one calibration item')['run']
    assert step('calibrate', 'Retain calibration shard')['if'] == 'always()'

    select_job = workflow()['jobs']['select']
    assert select_job['if'] == "always() && needs.prepare.result == 'success'"
    selector = step('select', 'Select calibrated mode and freeze execution matrix')
    assert 'ci_campaign_fanout select' in selector['run']
    assert '--calibration-root ../calibration-input' in selector['run']

    aggregate = workflow()['jobs']['aggregate']
    assert aggregate['needs'] == ['discover', 'select', 'amaru_solo', 'cardano_solo', 'mixed']
    assert aggregate['if'] == "always() && needs.select.result == 'success'"
    merger = step('aggregate', 'Verify and aggregate every campaign shard')
    assert 'ci_campaign_fanout aggregate' in merger['run']
    assert '--inventory ../campaign-selected/execution-work-items.json' in merger['run']
    assert step('aggregate', 'Retain aggregate campaign inputs and status')['if'] == 'always()'


def test_publication_is_isolated_from_candidate_execution_and_release_only():
    data = workflow()
    publication = data['jobs']['publication']
    assert publication['needs'] == ['discover', 'aggregate']
    assert publication['if'] == "needs.aggregate.result == 'success'"
    assert publication['permissions'] == {'actions': 'read', 'contents': 'write'}
    assert publication['concurrency']['cancel-in-progress'] in {False, 'false'}
    assert step('publication', 'Download completed campaign')['with']['name'] == 'dwarf-security-campaign-status'
    verifier = step('publication', 'Reverify and sign opaque publication assets')
    assert 'DWARF_CAMPAIGN_PUBLISHING_KEY_PEM' in json.dumps(verifier)
    assert 'ci_publish_campaign.py' in verifier['run']
    assert '--manifest publication-input/campaign-runtime/selected-manifest.json' in verifier['run']
    assert '--state publication-input/campaign-runtime/selected/state.json' in verifier['run']
    release = step('publication', 'Publish immutable campaign assets and rolling indexes')['run']
    assert 'gh release create' in release and 'gh release upload' in release
    assert 'cmp ' in release
    text = WORKFLOW.read_text()
    assert 'pages: write' not in text
    assert 'id-token: write' not in text
    assert 'git push' not in text
    assert 'gh pr merge' not in text


def test_all_inline_python_and_shell_syntax_is_valid():
    data = workflow()
    for name, job in data['jobs'].items():
        for entry in job['steps']:
            if "python3 - <<'PY'\n" in entry.get('run', ''):
                ast.parse(python_script(name, entry['name']))
            if 'run' in entry:
                subprocess.run(['bash', '-n'], input=entry['run'], text=True, check=True, capture_output=True)
    action = yaml.load(SHARD_ACTION.read_text(), Loader=yaml.BaseLoader)
    for entry in action['runs']['steps']:
        if 'run' in entry:
            subprocess.run(['bash', '-n'], input=entry['run'], text=True, check=True, capture_output=True)


def test_runbook_exposes_blockers_resume_and_owned_cleanup():
    doc = ROOT / 'dwarf/docs/security-campaign-automation.md'
    assert doc.is_file()
    text = doc.read_text()
    for requirement in ('runtime-compatibility-qualification-pending', '--resume', '30 days',
                        'ci_security_campaign.py', 'stock', 'patched', 'compiler-coverage',
                        'ubuntu-latest', 'synthetic-git.json', 'contents: write'):
        assert requirement in text


@pytest.mark.parametrize('initial', [False, True])
def test_complete_push_discovery_retains_every_changed_directory(tmp_path, monkeypatch, initial):
    api = discovery()
    trusted = tmp_path / 'trusted'
    trusted.mkdir()
    submitted = tmp_path / 'candidate-source'
    for name in ('one', 'two', 'unchanged'):
        source(submitted, name)
    monkeypatch.chdir(trusted)
    outputs, summary = tmp_path / 'outputs', tmp_path / 'summary'
    for key, value in {
        'SOURCE_COMMIT': 'a' * 40, 'BEFORE_COMMIT': ('0' if initial else 'b') * 40,
        'TRUSTED_COMMIT': 'c' * 40, 'EVENT_NAME': 'push', 'WORKFLOW_RUN_ID': '123',
        'GITHUB_OUTPUT': str(outputs), 'GITHUB_STEP_SUMMARY': str(summary),
    }.items():
        monkeypatch.setenv(key, value)
    calls = []
    paths = b'dwarf/testplease/amaru/one/build.rs\0dwarf/testplease/amaru/two/Cargo.toml\0dwarf/testplease/amaru/removed/old.rs\0'
    def local_git(argv, **kwargs):
        calls.append((argv, kwargs))
        assert argv[0] == 'git'
        return subprocess.CompletedProcess(argv, 0, stdout=paths)
    monkeypatch.setattr(subprocess, 'run', local_git)
    api['trusted_inputs'] = lambda root: {'test-reference': {'sha256': 'd' * 64}}
    api['main']()
    document = json.loads((tmp_path / 'campaign-discovery/discovery.json').read_text())
    assert [row['candidate_name'] for row in document['candidates']] == ['one', 'two']
    assert document['removed_candidates'] == [['amaru', 'removed']]
    assert document['trusted_revision'] == 'c' * 40
    assert document['execution_blocker'] == 'candidate-build-prerequisites-unresolved'
    assert document['official_selection'] == []
    assert outputs.read_text() == 'has_work=true\n'
    assert calls[0][0][1] == ('ls-tree' if initial else 'diff')
    assert calls[0][1]['cwd'] == submitted


def test_official_preview_has_no_implicit_release_selection(tmp_path, monkeypatch):
    api = discovery()
    trusted = tmp_path / 'trusted'
    trusted.mkdir()
    output = tmp_path / 'campaign-discovery'
    output.mkdir()
    (output / 'official-catalog-candidate.json').write_text('{"candidate": "future-release"}')
    monkeypatch.chdir(trusted)
    for key, value in {
        'SOURCE_COMMIT': 'a' * 40, 'BEFORE_COMMIT': '', 'TRUSTED_COMMIT': 'c' * 40,
        'EVENT_NAME': 'schedule', 'WORKFLOW_RUN_ID': '123',
        'GITHUB_OUTPUT': str(tmp_path / 'outputs'), 'GITHUB_STEP_SUMMARY': str(tmp_path / 'summary'),
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('official preview executed candidate code'))
    api['trusted_inputs'] = lambda root: {}
    api['main']()
    document = json.loads((output / 'discovery.json').read_text())
    assert document['status'] == 'official-selection-review-required'
    assert document['official_selection'] == []
    assert document['candidates'] == []
    assert len(document['official_metadata_sha256']) == 64
