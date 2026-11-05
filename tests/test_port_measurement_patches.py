import hashlib, json, subprocess
from pathlib import Path
import pytest
from scripts import port_measurement_patches as driver


def _git(path, *args):
    return subprocess.run(['git', *args], cwd=path, check=True, text=True, capture_output=True).stdout.strip()


def _repo(tmp_path, content='original\n'):
    repo = tmp_path / 'upstream'; repo.mkdir(parents=True); _git(repo, 'init', '--quiet')
    _git(repo, 'config', 'user.name', 't'); _git(repo, 'config', 'user.email', 't@example.invalid')
    (repo / 'tracked.txt').write_text(content); _git(repo, 'add', 'tracked.txt'); _git(repo, 'commit', '--quiet', '-m', 'fixture')
    return repo, _git(repo, 'rev-parse', 'HEAD')


def _patch_for(repo):
    (repo / 'tracked.txt').write_text('patched\n'); diff = _git(repo, 'diff'); _git(repo, 'checkout', '--', 'tracked.txt'); return diff + '\n'   # _git strips; a patch needs its final newline


def _work(tmp_path, repo, revision, patch_text, *, implementation='amaru', root=None, toolchain=None):
    work = tmp_path / 'work'; work.mkdir()
    (work / '0001.patch').write_text(patch_text)
    patch = {'path': '0001.patch', 'sha256': hashlib.sha256(patch_text.encode()).hexdigest(),
             'preimages': [{'path': 'tracked.txt', 'sha256': 'ab' * 32}]}
    if root: patch['root'] = root
    (work / 'manifest.json').write_text(json.dumps({'source': {'revision': 'a' * 40}, 'measurement_revision': 'nanoseconds-v4',
        'patch_set_sha256': 'c' * 64, 'patches': [patch], 'toolchain': toolchain or 'nightly'}))
    (work / 'request.json').write_text(json.dumps({'implementation': implementation, 'source_revision': revision, 'repository': str(repo)}))
    return work


def test_clean_apply_ports_and_records_new_preimages(tmp_path):
    repo, rev = _repo(tmp_path); work = _work(tmp_path, repo, rev, _patch_for(repo))
    result = driver.port(work)
    assert result['status'] == 'ported' and result['failure'] is None
    assert result['checks'] == [{'path': '0001.patch', 'status': 'ok', 'detail': ''}]
    assert result['preimages'] == {'tracked.txt': hashlib.sha256(b'original\n').hexdigest()}
    assert result['preimages_changed'] == ['tracked.txt']          # base digest 'ab'*32 differs
    assert result['source_revision'] == rev and result['base_source_revision'] == 'a' * 40
    assert json.loads((work / 'output' / 'port-result.json').read_text()) == result


def test_non_applying_patch_fails_with_git_detail(tmp_path):
    repo, rev = _repo(tmp_path, content='something else\n'); other, _ = _repo(tmp_path / 'o')
    work = _work(tmp_path, repo, rev, _patch_for(other))
    result = driver.port(work)
    assert result['status'] == 'failed' and result['checks'][0]['status'] == 'failed'
    assert 'tracked.txt' in result['checks'][0]['detail'] and result['failure']


def test_missing_preimage_path_fails_port_honestly(tmp_path):
    repo, rev = _repo(tmp_path); patch_text = _patch_for(repo)
    work = _work(tmp_path, repo, rev, patch_text)
    manifest = json.loads((work / 'manifest.json').read_text()); manifest['patches'][0]['preimages'].append({'path': 'gone.rs', 'sha256': 'cd' * 32})
    (work / 'manifest.json').write_text(json.dumps(manifest))
    result = driver.port(work)
    assert result['status'] == 'failed' and 'preimage path missing: gone.rs' in result['failure']


def test_wrong_revision_is_refused(tmp_path):
    repo, rev = _repo(tmp_path); work = _work(tmp_path, repo, 'b' * 40, _patch_for(repo))
    result = driver.port(work)
    assert result['status'] == 'failed' and result['checks'] == []


def test_cardano_dependency_patches_are_inherited_and_toolchain_checked(tmp_path):
    repo, rev = _repo(tmp_path)
    (repo / '.github' / 'workflows').mkdir(parents=True)
    (repo / '.github/workflows/haskell.yml').write_text("jobs:\n  build:\n    strategy:\n      matrix:\n        ghc: ['9.6.7']\n")
    _git(repo, 'add', '.'); _git(repo, 'commit', '--quiet', '-m', 'ci'); rev = _git(repo, 'rev-parse', 'HEAD')
    work = _work(tmp_path, repo, rev, 'irrelevant\n', implementation='cardano-node', root='dependency:ouroboros-network-1.2.0.0', toolchain={'ghc': '9.6.7', 'cabal': '3.16.0.0'})
    (repo / '.github/workflows/haskell.yml').write_text("jobs:\n  build:\n    strategy:\n      matrix:\n        ghc: ['9.6']\n"); _git(repo, 'commit', '--quiet', '-am', 'series'); rev = _git(repo, 'rev-parse', 'HEAD')
    json.dump({'implementation': 'cardano-node', 'source_revision': rev, 'repository': str(repo)}, open(work / 'request.json', 'w'))
    result = driver.port(work)
    assert result['status'] == 'ported' and result['checks'][0]['status'] == 'inherited'
    assert result['preimages'] == {'tracked.txt': 'ab' * 32} and result['preimages_changed'] == []
    assert result['toolchain_check'] == {'status': 'ok', 'ghc_series': '9.6', 'detail': ''}


def test_cardano_other_ghc_series_fails_port(tmp_path):
    repo, rev = _repo(tmp_path)
    (repo / '.github' / 'workflows').mkdir(parents=True)
    (repo / '.github/workflows/haskell.yml').write_text("ghc: ['9.8.4']\n"); _git(repo, 'add', '.'); _git(repo, 'commit', '--quiet', '-m', 'ci'); rev = _git(repo, 'rev-parse', 'HEAD')
    work = _work(tmp_path, repo, rev, 'x\n', implementation='cardano-node', root='dependency:x-1', toolchain={'ghc': '9.6.7', 'cabal': '3.16.0.0'})
    result = driver.port(work)
    assert result['status'] == 'failed' and result['toolchain_check']['status'] == 'failed' and 'GHC series' in result['failure']


def test_ghc_series_pattern_is_the_bare_series_shared_with_the_compile_gate():
    """Review fix 3: port stage and compile gate must agree, or the port stage says ok
    and the compile fails forty minutes later."""
    import re
    from controller import release_builds
    pattern = driver.ghc_series_pattern('9.6')
    assert re.search(pattern, "matrix:\n  ghc: ['9.6', '9.8']\n") and re.search(pattern, 'ghc: ["9.6"]')
    assert not re.search(pattern, "ghc: ['9.6.7']") and not re.search(pattern, "ghc: ['19.6']")
    assert repr(driver.GHC_SERIES_TEMPLATE) in release_builds._cardano_driver()
