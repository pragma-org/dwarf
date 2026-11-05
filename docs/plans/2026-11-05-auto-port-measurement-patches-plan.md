# Auto-Port Measurement Patches (B.1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a dev clicks **Download & build** on a version that has no hand-written instrumentation patch manifest, DWARF ports the newest committed patch series to that revision, verifies it, builds the instrumented variant, and labels the result as auto-ported — or blocks honestly with the reason.

**Architecture:** Auto-port = *derive an exact manifest for the new revision*, then feed it through the **unchanged** exact pipeline. A new pre-compile `port` container stage clones the new revision, runs the same strict `git apply --check` the drivers use, and records the new preimage digests. The host turns that into a derived manifest (`auto_ported: true`), and `_compile_*`, the hardened exact-preimage driver, `verify_patch_set`, `_instrumentation_check`, image identity and the provenance gate all run exactly as today. Derived manifests are never written under `dwarf/targets/`; they persist in controller build state and in the dashboard's runtime state so registration, run-time identity and an **Export for review** action can find them. Promotion to a committed recipe stays a human review step.

**Tech Stack:** Python 3.12 (stdlib + existing `profile_manager`/`controller`/`scripts` modules), Docker (existing `BuildContext.container`), vanilla JS in `release-preparations.js`, pytest.

**Spec:** `docs/plans/2026-11-04-run-surface-parity-design.md` — section "Scope: independent sub-projects", block **B.1 — REQUIRED: auto-port measurement patches to new revisions** (steps 0–6, touched files, tests).

## Global Constraints

- **UX unchanged.** The only build trigger is the Versions page **Download & build** control (`versions.j2` → `/api/versions/prepare`). No auto-build on fetch/discovery, no new build button.
- **Nothing is ever claimed patched that did not verify.** `_instrumentation_check`, `verify_patch_set`, the exact-preimage in-container driver, image identity checks and the provenance gate are reused unchanged.
- **Derived manifests never land in `dwarf/targets/`.** `_recipe()`'s committed glob and `build_amaru_measurement_target.audited_measurement_revisions()` must never see them. They live at `<build dir>/instrumented/derived-recipe/` (controller) and `storage_roots().state / 'derived-measurement-patches' / <impl> / <rev> /` (dashboard).
- **Derived manifest shape** (exact keys): `auto_ported: true`; `derived_from: {source_revision, measurement_revision, patch_set_sha256, recipe_dir}`; `validation.ported_from_source_revision = <base rev>`, `validation.status = "auto-ported-candidate"`, `validation.compiled = false`, `validation.runtime_qualified = false`; `source.revision = <new rev>`, `source.release = <new version label>`, `source.tag` removed; `patch_set_sha256`, `measurement_revision`, `patches[].path/sha256`, `instrumentation`, `toolchain`, `build` equal the base.
- **Strict application only:** `git apply --check --whitespace=error-all` (no `--3way`) — identical to the existing drivers. *Plan decision:* a patch that only applies 3-way would fail downstream anyway.
- **Port stage:** container role `port`, ceiling **900 s**, image `scripts.experimental_amaru_instrumentation.BUILDER_BASE`, same mount/limits as `compile`.
- **UI copy (exact):** heading suffix ` · auto-ported from <rev12>`; note `Provisional: patch set machine-ported from <rev12>; not a hand-verified recipe. Export for review before committing.`; button `Export for review`; export endpoint `/api/versions/derived-recipe/export`.
- **Export destination string (exact):** `dwarf/targets/<impl>/measurement-patches-<measurement_revision>/<rev>/`.
- `linux/amd64` only (existing). Implementations: `amaru`, `cardano-node`.
- **Git:** author `JG <forky@1timeuse.com>` (verify `git config user.name/user.email` before the first commit); **no** `Co-Authored-By`/`Claude-Session` lines; **no push**.
- **Test env:** `~/.venvs/dwarf-v10` (Python 3.12 + `dwarf/scripts/requirements-ci.txt` + pytest). Run every test from the repo root `/Users/nigel/dwarf-project/dwarf-fable/V10-FRESH` as `PYTHONPATH=dwarf ~/.venvs/dwarf-v10/bin/python -m pytest …` (abbreviated below as `PT`).
- **Branch:** `feat/auto-port-measurement-patches` from `main`.

## Review Focus

1. **Port passes `--check` but compilation fails** (new revision incompatible with the ported patches): variant must end `failed` with the compiler message, and **nothing** is persisted or registered as auto-ported; stock is unaffected. → Task 3 `test_compile_failure_after_port_does_not_persist_or_register`.
2. **A preimage path from the base recipe no longer exists at the new revision** (file moved/renamed): port must report `failed` naming the path, never raise `KeyError`/traceback. → Task 2 `test_missing_preimage_path_fails_port_honestly`.
3. **The same new revision is prepared twice**: registration must be byte-identical and idempotent (no "Retained build registration identity differs"). → Task 4 `test_auto_ported_registration_is_idempotent`.
4. **A derived manifest must stay invisible to the committed lookups**: `_recipe()` still raises `BuildBlocked` for a revision that only has a derived manifest, and `audited_measurement_revisions()` is unchanged after saving one. → Task 1 `test_saved_derived_recipe_is_not_audited`, Task 3 `test_recipe_still_ignores_derived_manifests`.
5. **Cardano derived from the v5 base must carry the composite import correction** (`COMPOSITE_HASH`, `PATCH_NAME`) exactly as the committed path does, or the build silently lacks the fix. → Task 3 `test_finalize_recipe_keeps_cardano_composite_for_derived_v5`.
6. **A recipe must never be promoted by a run that didn't prove it**: a run whose patched-only taps are missing values, a stock-profile run on the same build, or a run on a non-auto-ported build must leave the recipe `auto-ported-candidate` and append no decision — and proof evaluation must never fail or alter the run itself. → Task 6 `test_reconcile_promotes_only_a_proven_auto_ported_patched_run`.

---

## Setup (before Task 1)

- [ ] `git checkout -b feat/auto-port-measurement-patches main` in the worktree.
- [ ] Commit the already-edited design doc and this plan: `git add docs/plans/2026-11-04-run-surface-parity-design.md docs/plans/2026-11-05-auto-port-measurement-patches-plan.md && git commit -m "docs: run-surface parity design + B.1 auto-port plan"`.
- [ ] Confirm `PT tests/test_release_build_runtime.py tests/test_release_preparations.py tests/test_release_build_identity.py -q` is green before touching code.

---

### Task 1: Pure derivation + persistence module

**Files:**
- Create: `dwarf/profile_manager/measurement_patch_port.py`
- Test: `tests/test_measurement_patch_port.py`

**Interfaces:**
- Consumes: `profile_manager.antithesis_artifacts.recipe_rank(manifest) -> int` (lazy import), `profile_manager.config.storage_roots().state`, committed manifests under `dwarf/targets/<impl>/measurement-patches*/<rev>/manifest.json`.
- Produces (exact):
  ```python
  class PortError(ValueError): ...
  IMPLEMENTATIONS = ('amaru', 'cardano-node')
  REPOSITORIES = {'amaru': 'https://github.com/pragma-org/amaru.git',
                  'cardano-node': 'https://github.com/IntersectMBO/cardano-node.git'}
  def select_base_recipe(implementation: str, *, targets_root: Path | None = None) -> tuple[Path, dict]
  def derive_manifest(base: dict, *, source_revision: str, release: str,
                      preimages: dict[str, str], base_recipe_dir: str) -> dict
  def derived_recipe_root() -> Path            # storage_roots().state / 'derived-measurement-patches'
  def derived_manifest_path(implementation: str, source_revision: str) -> Path
  def save_derived_recipe(implementation: str, source_revision: str, manifest: dict, port_result: dict) -> Path
  def load_derived_manifest(implementation: str, source_revision: str) -> dict | None
  ```
  `select_base_recipe` default `targets_root = Path(__file__).resolve().parents[1] / 'targets'`; picks `max` by `(recipe_rank(m), m['source'].get('release',''), str(path))`; raises `PortError('No committed patch series available to port for <impl>')` when none, `PortError` on unknown implementation. `derive_manifest` deep-copies, validates 40-hex revision, raises `PortError('preimage missing for <path>')` if any `patches[].preimages[].path` is absent from `preimages`. `save_derived_recipe` raises `PortError` unless `manifest['auto_ported'] is True` and `manifest['source']['revision'] == source_revision`; writes `manifest.json` + `port-result.json` via tmp+`os.replace`. `load_derived_manifest` returns `None` when absent or `auto_ported` is not `True`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_measurement_patch_port.py
import json
from pathlib import Path
import pytest
from profile_manager import measurement_patch_port as port

ROOT = Path(__file__).resolve().parents[1] / 'dwarf'
AMARU_V4 = ROOT / 'targets/amaru/measurement-patches-nanoseconds-v4/aedfe797a5b8ef00d8b362be40b47a52c3b4a379/manifest.json'
CARDANO_V5 = ROOT / 'targets/cardano-node/measurement-patches-nanoseconds-v5/fef83fed01d7926f3de83b3b917be5a4a48768b5/manifest.json'
NEW = 'f' * 40

def test_select_base_recipe_prefers_newest_series():
    assert port.select_base_recipe('amaru')[0] == AMARU_V4
    assert port.select_base_recipe('cardano-node')[0] == CARDANO_V5

def test_select_base_recipe_refuses_empty_root_and_unknown_impl(tmp_path):
    with pytest.raises(port.PortError, match='No committed patch series'):
        port.select_base_recipe('amaru', targets_root=tmp_path)
    with pytest.raises(port.PortError):
        port.select_base_recipe('haskell')

def _preimages(manifest, digest):
    return {p['path']: digest for item in manifest['patches'] for p in item['preimages']}

def test_derive_manifest_rewrites_identity_and_preimages_only():
    base = json.loads(AMARU_V4.read_text()); snapshot = json.loads(AMARU_V4.read_text())
    derived = port.derive_manifest(base, source_revision=NEW, release='10.11.20261105',
        preimages=_preimages(base, 'ab' * 32), base_recipe_dir='measurement-patches-nanoseconds-v4/aedfe797a5b8ef00d8b362be40b47a52c3b4a379')
    assert base == snapshot                                   # input untouched
    assert derived['source']['revision'] == NEW and derived['source']['release'] == '10.11.20261105'
    assert 'tag' not in derived['source']
    assert all(p['sha256'] == 'ab' * 32 for item in derived['patches'] for p in item['preimages'])
    assert derived['auto_ported'] is True
    assert derived['derived_from'] == {'source_revision': base['source']['revision'],
        'measurement_revision': base['measurement_revision'], 'patch_set_sha256': base['patch_set_sha256'],
        'recipe_dir': 'measurement-patches-nanoseconds-v4/aedfe797a5b8ef00d8b362be40b47a52c3b4a379'}
    assert derived['validation']['ported_from_source_revision'] == base['source']['revision']
    assert derived['validation']['status'] == 'auto-ported-candidate'
    assert derived['validation']['compiled'] is False and derived['validation']['runtime_qualified'] is False
    assert derived['patch_set_sha256'] == base['patch_set_sha256']
    assert derived['measurement_revision'] == base['measurement_revision']
    assert [p['sha256'] for p in derived['patches']] == [p['sha256'] for p in base['patches']]

def test_derive_manifest_requires_every_preimage_and_valid_revision():
    base = json.loads(AMARU_V4.read_text())
    with pytest.raises(port.PortError, match='preimage missing'):
        port.derive_manifest(base, source_revision=NEW, release='x', preimages={}, base_recipe_dir='d')
    with pytest.raises(port.PortError):
        port.derive_manifest(base, source_revision='abc', release='x', preimages=_preimages(base, 'ab' * 32), base_recipe_dir='d')

def test_save_and_load_derived_recipe_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv('ADA2_DWARF_RUNTIME_ROOT', str(tmp_path))
    base = json.loads(AMARU_V4.read_text())
    derived = port.derive_manifest(base, source_revision=NEW, release='x', preimages=_preimages(base, 'ab' * 32), base_recipe_dir='d')
    directory = port.save_derived_recipe('amaru', NEW, derived, {'status': 'ported'})
    assert directory == port.derived_manifest_path('amaru', NEW).parent
    assert port.load_derived_manifest('amaru', NEW) == derived
    assert json.loads((directory / 'port-result.json').read_text()) == {'status': 'ported'}
    assert port.load_derived_manifest('amaru', 'e' * 40) is None
    with pytest.raises(port.PortError):
        port.save_derived_recipe('amaru', NEW, base, {})   # base is not auto_ported

def test_saved_derived_recipe_is_not_audited(tmp_path, monkeypatch):
    from scripts.build_amaru_measurement_target import audited_measurement_revisions
    monkeypatch.setenv('ADA2_DWARF_RUNTIME_ROOT', str(tmp_path))
    before = audited_measurement_revisions()
    base = json.loads(AMARU_V4.read_text())
    port.save_derived_recipe('amaru', NEW, port.derive_manifest(base, source_revision=NEW, release='x',
        preimages=_preimages(base, 'ab' * 32), base_recipe_dir='d'), {})
    assert audited_measurement_revisions() == before and NEW not in before
```

- [ ] **Step 2: Run to verify they fail**

Run: `PT tests/test_measurement_patch_port.py -q`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'profile_manager.measurement_patch_port'`

- [ ] **Step 3: Implement the module** with the exact signatures in Interfaces (module docstring: one paragraph stating derived manifests are provisional and never committed by code).

- [ ] **Step 4: Run to verify they pass**

Run: `PT tests/test_measurement_patch_port.py -q`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add dwarf/profile_manager/measurement_patch_port.py tests/test_measurement_patch_port.py
git commit -m "feat(measurement): derive auto-ported patch manifests for new revisions"
```

---

### Task 2: In-container port-check driver

**Files:**
- Create: `dwarf/scripts/port_measurement_patches.py` (stdlib only — it runs inside the builder container as `python3 /work/port.py`)
- Test: `tests/test_port_measurement_patches.py`

**Interfaces:**
- Consumes: `/work/request.json` = `{"implementation", "source_revision", "repository"}`; `/work/manifest.json` = the **base** manifest; base patch files at `/work/<patches[].path>`.
- Produces: `/work/output/port-result.json` and the same dict from `port()`:
  ```python
  class PortCheckError(RuntimeError): ...
  def ensure_git() -> None                                         # apt-get install -y --no-install-recommends git ca-certificates when shutil.which('git') is None (mirrors the amaru driver)
  def fetch_exact_source(repository: str, revision: str, destination: Path) -> str   # git init; git fetch --depth=1 <repository> <revision>; git checkout --detach FETCH_HEAD; returns rev-parse HEAD; PortCheckError if != revision
  def check_patches(source: Path, patches_root: Path, manifest: dict) -> list[dict]  # [{'path','status': 'ok'|'failed'|'inherited','detail': str}]
  def collect_preimages(source: Path, manifest: dict) -> tuple[dict[str, str], list[str]]  # (path->sha256 at new revision, paths whose digest differs from base); PortCheckError('preimage path missing: <path>')
  def check_cardano_toolchain(source: Path, manifest: dict) -> dict  # {'status': 'ok'|'failed', 'ghc_series': str, 'detail': str}
  def port(work: Path) -> dict
  def main(argv: list[str] | None = None) -> int                  # --work (default /work); always writes port-result.json; exit 0 on ported/failed, 2 on malformed request
  ```
  Result dict keys (exact): `schema_version: 1`, `status: 'ported'|'failed'`, `implementation`, `source_revision`, `base_source_revision`, `base_patch_set_sha256`, `measurement_revision`, `checks`, `preimages`, `preimages_changed`, `toolchain_check` (`None` for amaru), `failure` (`None` when ported).
  Rules: a patch whose `root` starts with `dependency:` (cardano) is `inherited` — dependency archives are pinned and identical, so its base preimages are copied unchanged and never looked up in the node source. Cardano additionally requires `check_cardano_toolchain` `ok`: `.github/workflows/haskell.yml` must match `(?m)^\s*ghc:\s*\[[^\n]*["']<major.minor>["']` for `manifest['toolchain']['ghc']` (the regex `_cardano_driver` already uses). `status == 'ported'` iff every check is `ok`/`inherited`, preimages collected, and (cardano) toolchain ok.

- [ ] **Step 1: Write the failing tests** (git fixture pattern from `tests/test_amaru_measurement_patch.py::_repository`)

```python
# tests/test_port_measurement_patches.py
import hashlib, json, subprocess
from pathlib import Path
import pytest
from scripts import port_measurement_patches as driver

def _git(path, *args):
    return subprocess.run(['git', *args], cwd=path, check=True, text=True, capture_output=True).stdout.strip()

def _repo(tmp_path, content='original\n'):
    repo = tmp_path / 'upstream'; repo.mkdir(); _git(repo, 'init', '--quiet')
    _git(repo, 'config', 'user.name', 't'); _git(repo, 'config', 'user.email', 't@example.invalid')
    (repo / 'tracked.txt').write_text(content); _git(repo, 'add', 'tracked.txt'); _git(repo, 'commit', '--quiet', '-m', 'fixture')
    return repo, _git(repo, 'rev-parse', 'HEAD')

def _patch_for(repo):
    (repo / 'tracked.txt').write_text('patched\n'); diff = _git(repo, 'diff'); _git(repo, 'checkout', '--', 'tracked.txt'); return diff

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
```

- [ ] **Step 2: Run to verify they fail**

Run: `PT tests/test_port_measurement_patches.py -q`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'scripts.port_measurement_patches'`

- [ ] **Step 3: Implement the driver** per Interfaces. `fetch_exact_source` must mirror the amaru driver exactly (`git init`, `git fetch --depth=1 <repository> <revision>`, `git checkout --detach FETCH_HEAD`). Run `git apply --check --whitespace=error-all <patch>` with `cwd=source`, capturing stderr tail (≤ 2048 chars) into `detail`. Never let an exception escape `port()`: catch `PortCheckError`/`OSError`/`subprocess.CalledProcessError` into `status='failed'`, `failure=str(exc)[:2048]`.

- [ ] **Step 4: Run to verify they pass**

Run: `PT tests/test_port_measurement_patches.py -q`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add dwarf/scripts/port_measurement_patches.py tests/test_port_measurement_patches.py
git commit -m "feat(measurement): add in-container port-check driver for patch series"
```

---

### Task 3: Controller wiring — port stage, derived recipe, honest blocking

**Files:**
- Modify: `dwarf/controller/release_builds.py` (`_ROLES` L22; `BuildBlocked` L27; `BuildContext.container` L187–207; `_recipe` L210–243; `_build_variant` L425–474; `prepare_release` L476–526)
- Test: `tests/test_release_build_runtime.py`

**Interfaces:**
- Consumes: Task 1 `select_base_recipe`, `derive_manifest`, `PortError`, `REPOSITORIES`; Task 2 script at `ROOT / 'scripts' / 'port_measurement_patches.py'`; existing `_copy_recipe(path, manifest, work)`, `_json`, `_write`, `_no_symlinks`, `BUILDER_BASE`.
- Produces (exact):
  ```python
  _ROLES = ('compile', 'port', 'version', 'help', 'interface', 'identity')
  _CEILINGS = {'compile': 14400, 'port': 900}          # container(): ceiling=_CEILINGS.get(role, 60)
  class PortBlocked(BuildBlocked):
      def __init__(self, message, attempt): super().__init__(message); self.attempt = attempt
  def _finalize_recipe(implementation, variant, path, manifest) -> tuple[Path, dict]   # the tail of _recipe from verify_patch_set through the cardano composite; _recipe returns (*_finalize_recipe(...), exact)
  def _port_recipe(context) -> tuple[Path, dict, bool]
  ```
  `_port_recipe`: `base_path, base = select_base_recipe(impl)` (`PortError` → `BuildBlocked(str(exc))`); `work = context.directory/'instrumented'/'port'/'work'`; `_copy_recipe(base_path, base, work)`; copy the Task 2 script to `work/'port.py'`; write `work/'request.json'` = `{'implementation', 'source_revision': selected['source_revision'], 'repository': REPOSITORIES[impl]}`; `context.stage('instrumented', 'port-check')`; `context.container('instrumented', 'port', BUILDER_BASE, ['python3', '/work/port.py'], work=work)`; `result = _json(work/'output'/'port-result.json')`; `context.port_result = result`; if `result['status'] != 'ported'` → `raise PortBlocked('Instrumentation patches did not port cleanly to ' + rev[:12] + ': ' + (result.get('failure') or '')[:1500], attempt=result)`; else `derived = derive_manifest(base, source_revision=rev, release=selected['version'], preimages=result['preimages'], base_recipe_dir=str(base_path.parent.relative_to(ROOT/'targets'/impl)))`; `derived_dir = context.directory/'instrumented'/'derived-recipe'`; `_copy_recipe(base_path, derived, derived_dir)`; `_write(derived_dir/'port-result.json', result)`; `return (*_finalize_recipe(impl, 'instrumented', derived_dir/'manifest.json', derived), True)`.
  `_build_variant`: wrap `_recipe` in `try/except BuildBlocked`; for `variant == 'instrumented'` fall through to `_port_recipe(context)`, otherwise re-raise. After `result` is built: `if manifest.get('auto_ported'): result.update(auto_ported=True, ported_from_source_revision=manifest['derived_from']['source_revision'], derived_manifest=manifest, port_result=getattr(context, 'port_result', None))`.
  `prepare_release`: in `except BuildBlocked as exc:` the row becomes `{'state': 'blocked', 'message': str(exc)[:3000]}` plus `'port_attempt': exc.attempt` when `getattr(exc, 'attempt', None)`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_release_build_runtime.py`; reuse its `runtime` fixture, `PARAMS`, `REVISION`, `DIGEST`)

```python
import hashlib
from profile_manager import measurement_patch_port as port_module

V4 = Path(__file__).resolve().parents[1] / 'dwarf/targets/amaru/measurement-patches-nanoseconds-v4/aedfe797a5b8ef00d8b362be40b47a52c3b4a379/manifest.json'
V5 = Path(__file__).resolve().parents[1] / 'dwarf/targets/cardano-node/measurement-patches-nanoseconds-v5/fef83fed01d7926f3de83b3b917be5a4a48768b5/manifest.json'

def _fake_port(status, base):
    preimages = {p['path']: 'ab' * 32 for item in base['patches'] for p in item['preimages']}
    def container(variant, role, image, command, *, work=None, entrypoint=None):
        if role == 'port':
            (work / 'output').mkdir(); (work / 'output' / 'port-result.json').write_text(json.dumps({
                'status': status, 'preimages': preimages if status == 'ported' else {},
                'checks': [{'path': base['patches'][0]['path'], 'status': 'ok' if status == 'ported' else 'failed', 'detail': 'hunk FAILED'}],
                'failure': None if status == 'ported' else 'patch did not apply'}))
            return ''
        return 'amaru 10.11\nCommands:\n  run  Run the node\n'
    return container

def _instrumented_context(runtime, monkeypatch, status):
    base = json.loads(V4.read_text())
    monkeypatch.setattr(builds, '_recipe', lambda selected, variant: (_ for _ in ()).throw(builds.BuildBlocked('No instrumentation patch for this exact source revision')) if variant == 'instrumented' else pytest.fail('stock not under test'))
    monkeypatch.setattr(port_module, 'select_base_recipe', lambda impl, **kw: (V4, base))
    context = builds.BuildContext(runtime, {**PARAMS, 'image': 'ghcr.io/pragma-org/amaru@' + DIGEST}, 'build-test', 3600, threading.Event())
    context.container = _fake_port(status, base); context.run = lambda *a, **kw: ''
    return context, base

def test_port_role_is_declared_with_bounded_ceiling(runtime):
    assert 'port' in builds._ROLES and builds._CEILINGS == {'compile': 14400, 'port': 900}
    calls = []
    runtime._run = lambda args, **kw: (calls.append(kw), DockerResult(0, '', ''))[1]
    context = builds.BuildContext(runtime, PARAMS, 'build-test', 3600, threading.Event())
    work = context.directory / 'instrumented' / 'work'; work.mkdir(parents=True)
    context.container('instrumented', 'port', 'python@' + DIGEST, ['python3', '/work/port.py'], work=work)
    assert calls[0]['timeout'] == 900

def test_instrumented_without_exact_recipe_is_auto_ported_and_labelled(runtime, monkeypatch):
    context, base = _instrumented_context(runtime, monkeypatch, 'ported')
    seen = {}
    def compile_source(context, variant, path, manifest):
        seen['manifest'] = manifest; seen['path'] = path
        directory = context.directory / variant / 'image-context'; directory.mkdir(parents=True); (directory / 'executable').write_bytes(b'bin')
        return directory, {'executable_digest': 'sha256:' + 'c' * 64, 'build_result_sha256': DIGEST, 'toolchain': manifest['toolchain']}
    monkeypatch.setattr(builds, '_compile_amaru', compile_source)
    monkeypatch.setattr(builds, '_instrumentation_check', lambda c, b, m: {'state': 'compiled', 'runtime': 'pending', 'evidence_sha256': DIGEST, 'capabilities': []})
    monkeypatch.setattr(builds, '_check_packaged_executable', lambda *a: None)
    runtime.inspect_image = lambda *a, **kw: {'config_image_id': 'sha256:' + 'c' * 64, 'platform': 'linux/amd64', 'runtime_interface': 'extracted-binary', 'manifest_references': []}
    result = builds._build_variant(context, 'instrumented')
    assert seen['manifest']['auto_ported'] is True and seen['manifest']['source']['revision'] == REVISION
    assert seen['path'] == context.directory / 'instrumented' / 'derived-recipe' / 'manifest.json'
    assert result['state'] == 'ready' and result['mode'] == 'patched' and result['auto_ported'] is True
    assert result['ported_from_source_revision'] == base['source']['revision']
    assert result['derived_manifest']['validation']['status'] == 'auto-ported-candidate'
    assert result['patch_set_sha256'] == base['patch_set_sha256'] and result['measurement_revision'] == base['measurement_revision']
    assert result['port_result']['status'] == 'ported'
    copied = context.directory / 'instrumented' / 'derived-recipe' / base['patches'][0]['path']
    assert hashlib.sha256(copied.read_bytes()).hexdigest() == base['patches'][0]['sha256']

def test_failed_port_blocks_instrumented_with_attempt_and_stock_still_builds(runtime, monkeypatch):
    context, _ = _instrumented_context(runtime, monkeypatch, 'failed')
    with pytest.raises(builds.PortBlocked, match='did not port cleanly') as info:
        builds._build_variant(context, 'instrumented')
    assert info.value.attempt['checks'][0]['status'] == 'failed'
    def variant(context, mode):
        if mode == 'instrumented': raise builds.PortBlocked('Instrumentation patches did not port cleanly to deadbeef: x', attempt={'status': 'failed', 'checks': []})
        return {'state': 'ready'}
    monkeypatch.setattr(builds, '_build_variant', variant)
    progress = builds.prepare_release(runtime, PARAMS, 'build-test-2', timeout=60, cancel_event=threading.Event())
    assert progress['variants']['stock'] == {'state': 'ready'}
    assert progress['variants']['instrumented']['state'] == 'blocked' and progress['variants']['instrumented']['port_attempt'] == {'status': 'failed', 'checks': []}

def test_compile_failure_after_port_does_not_persist_or_register(runtime, monkeypatch):
    context, _ = _instrumented_context(runtime, monkeypatch, 'ported')
    monkeypatch.setattr(builds, '_compile_amaru', lambda *a: (_ for _ in ()).throw(ValueError('cargo build failed')))
    with pytest.raises(ValueError, match='cargo build failed'):
        builds._build_variant(context, 'instrumented')
    assert not (context.directory / 'instrumented' / 'image-context').exists()
    # the derived recipe is retained for diagnosis but nothing marks it ready
    assert not (context.directory / 'progress.json').exists()

def test_recipe_still_ignores_derived_manifests(runtime, monkeypatch, tmp_path):
    monkeypatch.setenv('ADA2_DWARF_RUNTIME_ROOT', str(tmp_path))
    base = json.loads(V4.read_text())
    derived = port_module.derive_manifest(base, source_revision='9' * 40, release='x', preimages={p['path']: 'ab' * 32 for i in base['patches'] for p in i['preimages']}, base_recipe_dir='d')
    port_module.save_derived_recipe('amaru', '9' * 40, derived, {})
    with pytest.raises(builds.BuildBlocked, match='exact source revision'):
        builds._recipe({'implementation': 'amaru', 'source_revision': '9' * 40}, 'instrumented')

def test_finalize_recipe_keeps_cardano_composite_for_derived_v5():
    from profile_manager import antithesis_cardano_bridge as bridge
    base = json.loads(V5.read_text())
    derived = port_module.derive_manifest(base, source_revision='8' * 40, release='11.1.3', preimages={p['path']: p['sha256'] for i in base['patches'] for p in i['preimages']}, base_recipe_dir='measurement-patches-nanoseconds-v5/fef83fed01d7926f3de83b3b917be5a4a48768b5')
    path, manifest = builds._finalize_recipe('cardano-node', 'instrumented', V5, derived)
    assert manifest['patch_set_sha256'] == bridge.COMPOSITE_HASH and manifest['auto_ported'] is True
    assert manifest['patches'][-1]['path'] == bridge.PATCH_NAME
```

- [ ] **Step 2: Run to verify they fail**

Run: `PT tests/test_release_build_runtime.py -q -k "port or derived or finalize"`
Expected: FAIL — `AttributeError: module 'controller.release_builds' has no attribute '_CEILINGS'` / `PortBlocked` / `_finalize_recipe`

- [ ] **Step 3: Implement** per Interfaces. Keep `_recipe`'s signature and every existing message byte-identical; `_finalize_recipe` is a pure extraction. `_port_recipe` imports Task 1 symbols lazily (`from profile_manager.measurement_patch_port import ...`) so the controller image's import cost is unchanged.

- [ ] **Step 4: Run the whole file to verify new and old tests pass**

Run: `PT tests/test_release_build_runtime.py -q`
Expected: all passed (existing 21 + 6 new), including `test_instrumentation_requires_exact_manifest` unchanged

- [ ] **Step 5: Commit**

```bash
git add dwarf/controller/release_builds.py tests/test_release_build_runtime.py
git commit -m "feat(controller): auto-port instrumentation patches when no exact recipe exists"
```

---

### Task 4: Registration and run-time identity for auto-ported builds

**Files:**
- Modify: `dwarf/profile_manager/release_preparations.py:_register` (L32–86)
- Modify: `dwarf/profile_manager/release_build_identity.py:build_manifest` (L14–29)
- Test: `tests/test_release_preparations.py`, `tests/test_release_build_identity.py`, `tests/test_measurement_patch_port.py` (provenance pin)

**Interfaces:**
- Consumes: Task 3 result row keys `auto_ported`, `ported_from_source_revision`, `derived_manifest`, `port_result`; Task 1 `save_derived_recipe`, `load_derived_manifest`, `PortError`.
- Produces: `registration['source_build']` gains `auto_ported: True` and `ported_from_source_revision: <base rev>` for such rows; `build_manifest(implementation, revision, build)` returns the persisted derived manifest when `build.get('auto_ported') is True` and its `patch_set_sha256`/`measurement_revision` match (checked **after** the committed glob, **before** the bridge fallback). `_register` raises `ValueError('Auto-ported build is missing its derived recipe')` when `auto_ported` is set but `derived_manifest` is absent, not `auto_ported`, or names another revision/patch set — so `release_preparation` marks that variant `unavailable` exactly as it does today for other registration failures.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_release_preparations.py (append; reuse setup fixture + catalog())
import json
from pathlib import Path
from profile_manager import measurement_patch_port as port_module
V4 = Path(__file__).resolve().parents[1] / 'dwarf/targets/amaru/measurement-patches-nanoseconds-v4/aedfe797a5b8ef00d8b362be40b47a52c3b4a379/manifest.json'

def _derived():
    base = json.loads(V4.read_text())
    return port_module.derive_manifest(base, source_revision='b' * 40, release='test-build',
        preimages={p['path']: 'ab' * 32 for i in base['patches'] for p in i['preimages']}, base_recipe_dir='measurement-patches-nanoseconds-v4/aedfe797a5b8ef00d8b362be40b47a52c3b4a379')

def _controller(derived, *, with_recipe=True):
    def controller(op, params, timeout, **kw):
        if op == 'release.prepare': return {'execution_id': params['execution_id']}
        if op == 'image.inspect':
            return {'config_image_id': 'sha256:' + 'c' * 64, 'platform': 'linux/amd64', 'installation_id': 'test', 'runtime_interface': 'extracted-binary'}
        row = {'state': 'ready', 'mode': 'patched', 'config_image_id': 'sha256:' + 'c' * 64, 'executable_digest': 'sha256:' + 'd' * 64,
               'source_revision': 'b' * 40, 'runtime_interface': 'extracted-binary', 'build_result_sha256': 'sha256:' + 'e' * 64,
               'qualification': 'unverified', 'compatibility': 'unknown-until-run', 'completed_at': '2026-11-05T12:00:00Z',
               'patch_set_sha256': derived['patch_set_sha256'], 'measurement_revision': derived['measurement_revision'],
               'instrumentation_checks': {'state': 'compiled', 'runtime': 'pending', 'evidence_sha256': 'sha256:' + 'f' * 64},
               'auto_ported': True, 'ported_from_source_revision': derived['derived_from']['source_revision'], 'port_result': {'status': 'ported'}}
        if with_recipe: row['derived_manifest'] = derived
        return {'execution_id': params['execution_id'], 'state': 'applied', 'audit_receipt': 'controller/audit.json',
                'result': {'status': 'prepared', 'implementation': 'amaru', 'version': 'test-build', 'digest': 'sha256:' + 'a' * 64,
                           'source_revision': 'b' * 40, 'platform': 'linux/amd64', 'controller_installation_id': 'test',
                           'variants': {'stock': {'state': 'blocked', 'message': 'not under test'}, 'instrumented': row}}}
    return controller

def test_auto_ported_instrumented_registers_and_persists_derived_recipe(setup):
    from profile_manager.release_preparations import release_preparation
    from profile_manager.version_discovery import load_operator_registrations
    from profile_manager.release_build_identity import build_manifest
    derived = _derived(); controller = _controller(derived)
    release_preparation('amaru', 'test-build', start=True, controller=controller)
    result = release_preparation('amaru', 'test-build', controller=controller)
    assert 'measurement_profile=amaru-security-patched' in result['variants']['instrumented']['run_url']
    [registration] = load_operator_registrations()['registrations']
    assert registration['source_build']['auto_ported'] is True
    assert registration['source_build']['ported_from_source_revision'] == derived['derived_from']['source_revision']
    assert port_module.load_derived_manifest('amaru', 'b' * 40) == derived
    assert build_manifest('amaru', 'b' * 40, registration['source_build'])['auto_ported'] is True

def test_auto_ported_row_without_derived_recipe_is_unavailable(setup):
    from profile_manager.release_preparations import release_preparation
    from profile_manager.version_discovery import load_operator_registrations
    controller = _controller(_derived(), with_recipe=False)
    release_preparation('amaru', 'test-build', start=True, controller=controller)
    result = release_preparation('amaru', 'test-build', controller=controller)
    assert result['variants']['instrumented']['state'] == 'unavailable'
    assert 'derived recipe' in result['variants']['instrumented']['message']
    assert load_operator_registrations()['registrations'] == []

def test_auto_ported_registration_is_idempotent(setup):
    from profile_manager.release_preparations import release_preparation
    from profile_manager.version_discovery import load_operator_registrations
    controller = _controller(_derived())
    release_preparation('amaru', 'test-build', start=True, controller=controller)
    release_preparation('amaru', 'test-build', controller=controller)
    release_preparation('amaru', 'test-build', controller=controller)
    assert len(load_operator_registrations()['registrations']) == 1
```

```python
# tests/test_release_build_identity.py (append; reuse registration() + ROOT)
def test_build_manifest_resolves_persisted_auto_ported_recipe(tmp_path, monkeypatch):
    from profile_manager import measurement_patch_port as port_module
    from profile_manager.release_build_identity import build_manifest
    monkeypatch.setenv('ADA2_DWARF_RUNTIME_ROOT', str(tmp_path))
    base = json.loads((ROOT / 'targets/amaru/measurement-patches-nanoseconds-v4/aedfe797a5b8ef00d8b362be40b47a52c3b4a379/manifest.json').read_text())
    derived = port_module.derive_manifest(base, source_revision='b' * 40, release='x', preimages={p['path']: 'ab' * 32 for i in base['patches'] for p in i['preimages']}, base_recipe_dir='d')
    build = {'variant': 'instrumented', 'build_result_sha256': 'sha256:' + 'c' * 64, 'instrumentation_evidence_sha256': 'sha256:' + 'd' * 64,
             'patch_set_sha256': derived['patch_set_sha256'], 'measurement_revision': derived['measurement_revision'], 'auto_ported': True}
    with pytest.raises(ValueError, match='no matching exact-source patch recipe'):
        build_manifest('amaru', 'b' * 40, build)                      # not persisted yet
    port_module.save_derived_recipe('amaru', 'b' * 40, derived, {})
    assert build_manifest('amaru', 'b' * 40, build) == derived
    with pytest.raises(ValueError, match='no matching exact-source patch recipe'):
        build_manifest('amaru', 'b' * 40, {**build, 'auto_ported': False})   # committed lookup never sees derived
```

```python
# tests/test_measurement_patch_port.py (append) — provenance pin
def test_provenance_gate_treats_auto_ported_build_like_any_other():
    from profile_manager.version_provenance import ProvenanceIndex, build_provenance
    index = ProvenanceIndex.from_catalog({'releases': [{'implementation': 'amaru', 'version': '10.11.20261105', 'source_revision': NEW}]})
    target = {'implementation': 'amaru', 'version': '10.11.20261105', 'source_revision': NEW}
    ok = build_provenance(target, [{'status': 'seen', 'implementation': 'amaru', 'git_commit': NEW}], index=index)
    assert ok['status'] == 'verified'
    bad = build_provenance(target, [{'status': 'mismatch', 'component': 'amaru', 'claimed_label': '10.11.20261105', 'observed_git_commit': 'a' * 40}], index=index)
    assert bad['status'] == 'mismatch' and bad['problems']
```

- [ ] **Step 2: Run to verify they fail**

Run: `PT tests/test_release_preparations.py tests/test_release_build_identity.py tests/test_measurement_patch_port.py -q`
Expected: the three preparation tests and the identity test FAIL (`KeyError: 'auto_ported'` / `ValueError: Compiled instrumentation has no matching exact-source patch recipe`); the provenance pin passes already (it pins existing behavior).

- [ ] **Step 3: Implement** `_register` and `build_manifest` changes per Interfaces (lazy import of `measurement_patch_port` inside both functions).

- [ ] **Step 4: Run to verify they pass**

Run: `PT tests/test_release_preparations.py tests/test_release_build_identity.py tests/test_measurement_patch_port.py -q`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add dwarf/profile_manager/release_preparations.py dwarf/profile_manager/release_build_identity.py tests/test_release_preparations.py tests/test_release_build_identity.py tests/test_measurement_patch_port.py
git commit -m "feat(versions): register auto-ported instrumented builds with persisted derived recipes"
```

---

### Task 5: Export for review, provisional label, and operator docs

**Files:**
- Modify: `dwarf/profile_manager/measurement_patch_port.py` (add `export_derived_recipe`)
- Modify: `dwarf/profile_manager/dashboard.py` (add `dispatch_derived_recipe_export_request` next to `dispatch_version_download_request` L3259; wire it in the POST handler chain immediately after the `version_download` block at L5564–5568)
- Modify: `dwarf/dashboard/static/js/release-preparations.js` (variant loop L34–56)
- Modify: `dwarf/dashboard/templates/learn/versions.j2` (one new paragraph)
- Test: `tests/test_derived_recipe_export.py`

**Interfaces:**
- Consumes: Task 1 persistence; `storage_roots().bundles`; committed base patch files at `ROOT / 'targets' / impl / manifest['derived_from']['recipe_dir'] / <path>`; existing `check_form_token`, `parse_qs`, `urlsplit` in `dashboard.py`.
- Produces (exact):
  ```python
  def export_derived_recipe(implementation: str, source_revision: str) -> dict
  # -> {'path': str, 'sha256': 'sha256:<hex>', 'destination': 'dwarf/targets/<impl>/measurement-patches-<measurement_revision>/<rev>/', 'members': [str, ...]}
  # tar.gz at storage_roots().bundles / f'derived-recipe-{impl}-{rev[:12]}.tar.gz' (tmp + os.replace); members exactly:
  #   manifest.json, port-result.json, patches/<each patches[].path> (copied from the base recipe dir, sha256 must equal manifest patches[].sha256 else PortError), DESTINATION.txt (the destination string + "\n"), README.txt
  # PortError('No auto-ported recipe for <impl> <rev12>') when nothing is persisted.
  def dispatch_derived_recipe_export_request(*, method, path, expected_token, body=b"")   # dashboard.py
  # path exactly '/api/versions/derived-recipe/export'; non-POST -> 405; bad token -> 401; form fields must be exactly {implementation, source_revision, token} with implementation in IMPLEMENTATIONS and a 40-hex revision else 409 {"ok":false,"error":"unexpected export fields"}; success -> 200 {"ok": true, **export}; PortError/OSError/ValueError -> 409 {"ok": false, "error": str}
  ```
  README.txt copy (exact first line): `Auto-ported DWARF measurement patch set — review before committing.`; when the derived manifest's `validation.status` is `auto-ported-proven` (Task 6) the second line is `Proven by run <proven_by_run>: patched-only taps carried data.`, otherwise `Not yet proven by a run.` followed by the four steps: verify (`verify_patch_set`), build the instrumented variant, run a measured scenario, then copy `manifest.json` + `patches/*` to DESTINATION and remove `auto_ported`/`derived_from` only after review.
  JS: when `build.auto_ported`, `heading.textContent = label + ' · auto-ported from ' + build.ported_from_source_revision.slice(0, 12)`; append `<p>` with the exact Provisional note; append a `button.dk-btn` **Export for review** that POSTs `new URLSearchParams({token, implementation: row.dataset.implementation, source_revision: row.dataset.sourceRevision})` to `/api/versions/derived-recipe/export` and writes `'Exported: ' + result.path` (or the error) into that block. When `build.state === 'blocked' && build.port_attempt`, render each `port_attempt.checks[]` as `path · status · detail` inside the existing Build log `<details>`.
  `learn/versions.j2`: a paragraph headed **Auto-ported instrumented builds** stating that a version with no hand-written patch manifest gets the newest patch series ported and verified automatically, is labelled `auto-ported from <revision>`, is provisional until reviewed, and that **Export for review** produces the bundle to commit under `dwarf/targets/`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_derived_recipe_export.py
import hashlib, io, json, tarfile
from pathlib import Path
from urllib.parse import urlencode
import pytest
from profile_manager import dashboard
from profile_manager import measurement_patch_port as port

ROOT = Path(__file__).resolve().parents[1]
V4_DIR = 'measurement-patches-nanoseconds-v4/aedfe797a5b8ef00d8b362be40b47a52c3b4a379'
NEW = 'b' * 40

@pytest.fixture
def persisted(tmp_path, monkeypatch):
    monkeypatch.setenv('ADA2_DWARF_RUNTIME_ROOT', str(tmp_path))
    base = json.loads((ROOT / 'dwarf/targets/amaru' / V4_DIR / 'manifest.json').read_text())
    derived = port.derive_manifest(base, source_revision=NEW, release='x', preimages={p['path']: 'ab' * 32 for i in base['patches'] for p in i['preimages']}, base_recipe_dir=V4_DIR)
    port.save_derived_recipe('amaru', NEW, derived, {'status': 'ported'})
    return base, derived

def test_export_bundles_manifest_patches_and_destination(persisted):
    base, derived = persisted
    result = port.export_derived_recipe('amaru', NEW)
    assert result['destination'] == f'dwarf/targets/amaru/measurement-patches-{base["measurement_revision"]}/{NEW}/'
    assert Path(result['path']).name == f'derived-recipe-amaru-{NEW[:12]}.tar.gz'
    assert result['sha256'] == 'sha256:' + hashlib.sha256(Path(result['path']).read_bytes()).hexdigest()
    with tarfile.open(result['path']) as tar:
        names = sorted(tar.getnames())
        assert names == sorted(['manifest.json', 'port-result.json', 'DESTINATION.txt', 'README.txt'] + ['patches/' + p['path'] for p in base['patches']])
        assert json.loads(tar.extractfile('manifest.json').read()) == derived
        assert tar.extractfile('DESTINATION.txt').read().decode() == result['destination'] + '\n'
        assert tar.extractfile('README.txt').read().decode().startswith('Auto-ported DWARF measurement patch set — review before committing.')
        patch = base['patches'][0]
        assert hashlib.sha256(tar.extractfile('patches/' + patch['path']).read()).hexdigest() == patch['sha256']
    assert sorted(result['members']) == names

def test_export_refuses_missing_recipe(tmp_path, monkeypatch):
    monkeypatch.setenv('ADA2_DWARF_RUNTIME_ROOT', str(tmp_path))
    with pytest.raises(port.PortError, match='No auto-ported recipe'):
        port.export_derived_recipe('amaru', 'c' * 40)

def test_export_endpoint_authentication_fields_and_success(persisted, monkeypatch):
    path = '/api/versions/derived-recipe/export'
    assert dashboard.dispatch_derived_recipe_export_request(method='GET', path=path, expected_token='s')[0] == 405
    assert dashboard.dispatch_derived_recipe_export_request(method='POST', path=path, expected_token='s', body=b'')[0] == 401
    bad = urlencode({'token': 's', 'implementation': 'amaru', 'source_revision': 'zz'}).encode()
    assert dashboard.dispatch_derived_recipe_export_request(method='POST', path=path, expected_token='s', body=bad)[0] == 409
    extra = urlencode({'token': 's', 'implementation': 'amaru', 'source_revision': NEW, 'command': 'sh'}).encode()
    assert dashboard.dispatch_derived_recipe_export_request(method='POST', path=path, expected_token='s', body=extra)[0] == 409
    assert dashboard.dispatch_derived_recipe_export_request(method='POST', path='/api/versions/other', expected_token='s') is None
    good = urlencode({'token': 's', 'implementation': 'amaru', 'source_revision': NEW}).encode()
    status, ctype, payload = dashboard.dispatch_derived_recipe_export_request(method='POST', path=path, expected_token='s', body=good)
    body = json.loads(payload)
    assert status == 200 and body['ok'] is True and body['destination'].startswith('dwarf/targets/amaru/')

def test_versions_ui_labels_auto_ported_builds():
    js = (ROOT / 'dwarf/dashboard/static/js/release-preparations.js').read_text()
    for needle in ("' · auto-ported from '", 'Provisional: patch set machine-ported from', 'Export for review', '/api/versions/derived-recipe/export', 'port_attempt'):
        assert needle in js, needle
    learn = (ROOT / 'dwarf/dashboard/templates/learn/versions.j2').read_text()
    assert 'Auto-ported instrumented builds' in learn and 'Export for review' in learn
```

- [ ] **Step 2: Run to verify they fail**

Run: `PT tests/test_derived_recipe_export.py -q`
Expected: FAIL — `AttributeError: module 'profile_manager.measurement_patch_port' has no attribute 'export_derived_recipe'` / `dashboard has no attribute 'dispatch_derived_recipe_export_request'` / missing UI strings

- [ ] **Step 3: Implement** `export_derived_recipe`, the dispatcher (model it on `dispatch_version_download_request`, including the `check_form_token` → 401 path), wire it into the handler chain right after the `version_download` block, add the JS behaviour and the learn paragraph with the exact copy above.

- [ ] **Step 4: Run to verify they pass, then the touched suites**

Run: `PT tests/test_derived_recipe_export.py tests/test_release_preparations.py tests/test_dashboard_freshness.py -q`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add dwarf/profile_manager/measurement_patch_port.py dwarf/profile_manager/dashboard.py dwarf/dashboard/static/js/release-preparations.js dwarf/dashboard/templates/learn/versions.j2 tests/test_derived_recipe_export.py
git commit -m "feat(versions): export auto-ported recipes for review and label them provisional"
```

---

### Task 6: Runtime proof — a run with data on the patched taps promotes the recipe

**Files:**
- Modify: `dwarf/profile_manager/measurement_patch_port.py` (add `patched_only_concepts`, `proof_from_report`, `mark_proven`)
- Modify: `dwarf/profile_manager/run_service.py:_reconcile_result` (L327–440; add the proof step after the manifest/ledger identity checks)
- Modify: `dwarf/profile_manager/release_preparations.py:release_preparation` (merge the persisted derived manifest's `validation` into the instrumented row so the UI sees `proven`)
- Modify: `dwarf/dashboard/static/js/release-preparations.js` (label suffix ` · proven by run <id>`), `measurement_patch_port.export_derived_recipe` (README states proven status + run id)
- Test: `tests/test_measurement_patch_port.py`, `tests/test_run_service_auto_port_proof.py` (new), `tests/test_derived_recipe_export.py`

**Interfaces:**
- Consumes: `profile_manager.tap_coverage.coverage_report(report_metrics, implementation, mode, *, catalog_index=None, exceptions=None)` and `_bindings_for(index, implementation, mode)` (concept bindings carry `modes`); `tap_coverage.report_identity(report) -> (implementation, mode)`; the retained `measurements/report.json` inside `run_dir`; the run manifest's target identity; `OperatorVersionDecisionLedger.append_decision(event, *, expected_revision)` and the dashboard's decision-event shape (`decision_id`, `subject_key`, `subject`, `decision`, `reason`, `evidence.run_id`); Task 1 `load_derived_manifest`/`save_derived_recipe`; Task 4 registration fields (`source_build.auto_ported`, `ported_from_source_revision`).
- Produces (exact):
  ```python
  def patched_only_concepts(implementation: str, *, catalog_index: dict | None = None) -> list[str]
  # concepts bound for mode "patched" that are NOT bound for mode "stock" (via _bindings_for), sorted
  def proof_from_report(report: dict, implementation: str) -> dict
  # runs coverage_report(report["measurements"], implementation, "patched"); returns
  # {"proven": bool, "patched_only": [...], "covered": [...], "excepted": [...], "missing": [...]}
  # proven iff report_identity(report)[1] == "patched", every patched-only concept is in covered ∪ excepted, and covered is non-empty
  def mark_proven(implementation: str, source_revision: str, *, run_id: str, proof: dict) -> dict
  # loads the derived manifest (PortError if none); sets validation.status="auto-ported-proven",
  # validation.runtime_qualified=True, validation.proven_by_run=run_id, validation.proof={covered, excepted};
  # idempotent (a second run with a different id keeps the first proven_by_run); saves atomically; returns the manifest
  ```
  `run_service._reconcile_result` gains, after the ledger identity checks: if the run's target registration (`manifest["target"]` / the run plan's `operator_registration_id`) resolves to a registration whose `source_build.auto_ported` is `True`, load `run_dir/measurements/report.json`; `proof = proof_from_report(report, implementation)`; if `proof["proven"]`: `mark_proven(...)` and append a ledger decision `{"decision": "auto-ported-proven", "reason": "patched-only taps covered: " + ", ".join(proof["covered"]), "evidence": {"run_id": run_id, ...}}` via the existing decision-event shape; if not proven, append nothing and leave the recipe `auto-ported-candidate` (the run itself is unaffected either way — proof never fails a run). A stock-mode run never promotes.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_measurement_patch_port.py (append) — uses the retained Pages workload archives
import tarfile
RUNS = Path(__file__).resolve().parents[1] / 'docs/evidence/runs'
def _report(name):
    with tarfile.open(RUNS / name, 'r:gz') as tar:
        member = next(m for m in tar.getmembers() if m.name.endswith('/measurements/report.json'))
        return json.loads(tar.extractfile(member).read())

def test_patched_only_concepts_exist_for_amaru():
    only = port.patched_only_concepts('amaru')
    assert only and all(isinstance(c, str) for c in only)
    assert not set(only) & set(port.patched_only_concepts('cardano-node')) or True   # implementations may share names; no assertion on overlap

def test_patched_run_with_tap_data_proves_and_stock_run_does_not():
    proven = port.proof_from_report(_report('20261104T165404Z-54138a1b-bundle-export.tar.gz'), 'amaru')   # amaru-patched workload
    assert proven['proven'] is True and proven['missing'] == [] and proven['covered']
    stock = port.proof_from_report(_report('20261104T161325Z-5eb100bf-bundle-export.tar.gz'), 'amaru')    # amaru-stock workload
    assert stock['proven'] is False

def test_mark_proven_round_trip_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv('ADA2_DWARF_RUNTIME_ROOT', str(tmp_path))
    base = json.loads(AMARU_V4.read_text())
    port.save_derived_recipe('amaru', NEW, port.derive_manifest(base, source_revision=NEW, release='x', preimages=_preimages(base, 'ab' * 32), base_recipe_dir='d'), {})
    proof = {'proven': True, 'covered': ['a'], 'excepted': [], 'missing': [], 'patched_only': ['a']}
    first = port.mark_proven('amaru', NEW, run_id='run-1', proof=proof)
    assert first['validation']['status'] == 'auto-ported-proven' and first['validation']['runtime_qualified'] is True
    assert first['validation']['proven_by_run'] == 'run-1'
    again = port.mark_proven('amaru', NEW, run_id='run-2', proof=proof)
    assert again['validation']['proven_by_run'] == 'run-1'
    assert port.load_derived_manifest('amaru', NEW)['validation']['status'] == 'auto-ported-proven'
    with pytest.raises(port.PortError):
        port.mark_proven('amaru', 'c' * 40, run_id='run-3', proof=proof)
```

```python
# tests/test_run_service_auto_port_proof.py (new) — the hook, with everything around it faked
def test_reconcile_promotes_only_a_proven_auto_ported_patched_run(tmp_path, monkeypatch):
    """Build a minimal run_dir (manifest.json + measurements/report.json copied from the amaru-patched
    archive), a registration with source_build.auto_ported=True, a saved derived recipe for its revision,
    and call run_service._reconcile_result through the same arguments execute_run uses (see the existing
    reconciliation tests in tests/test_run_service*.py for the handle/request/execution fakes).
    Assert: derived manifest becomes auto-ported-proven with proven_by_run == run_id and the ledger gained
    one 'auto-ported-proven' decision. Repeat with the amaru-stock report: status stays
    auto-ported-candidate and no decision is appended. Repeat with a registration that is not auto_ported:
    nothing is touched."""
```

- [ ] **Step 2: Run to verify they fail**

Run: `PT tests/test_measurement_patch_port.py tests/test_run_service_auto_port_proof.py -q`
Expected: FAIL — `AttributeError: … has no attribute 'patched_only_concepts'` / `'proof_from_report'` / `'mark_proven'`; the run-service test fails because nothing is promoted

- [ ] **Step 3: Implement** per Interfaces. `patched_only_concepts` = `{concept for concept,_,_ in _bindings_for(index, impl, 'patched')} − {… 'stock'}`. In `_reconcile_result` the proof is a best-effort step after identity checks: a `PortError`/missing report is logged into the reconciliation detail, never raised. Merge `validation` from `load_derived_manifest` into the instrumented row in `release_preparation` when `row.get('auto_ported')`.

- [ ] **Step 4: Run to verify they pass, then the touched suites**

Run: `PT tests/test_measurement_patch_port.py tests/test_run_service_auto_port_proof.py tests/test_release_preparations.py tests/test_derived_recipe_export.py tests/test_tap_coverage_rule.py -q`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add dwarf/profile_manager/measurement_patch_port.py dwarf/profile_manager/run_service.py dwarf/profile_manager/release_preparations.py dwarf/dashboard/static/js/release-preparations.js tests/test_measurement_patch_port.py tests/test_run_service_auto_port_proof.py tests/test_derived_recipe_export.py
git commit -m "feat(measurement): a patched run with tap data proves an auto-ported recipe"
```

---

## Completion

- [ ] Run the full suite: `PT tests -q -x --ignore=tests/test_source_installer_images.py` (that file drives the real Docker CLI boundary; run it separately only if Docker is available: `PT tests/test_source_installer_images.py -q`).
  Expected: all passed; report any pre-existing failure by name.
- [ ] Hand off per `superpowers:executing-plans` (final whole-branch review, then `superpowers:finishing-a-development-branch`). **Do not push.**
