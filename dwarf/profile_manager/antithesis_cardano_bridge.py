"""One verified import-only Cardano build correction for the v5 patch set.

The original v5 measurement patch set remains unchanged. This supplement has a
new composite identity; existing collector logic is reused with that identity
restored before any result is emitted. No global collector state is modified.
It was first scoped to Antithesis packages; the controller's local release
preparation applies the same correction because v5 alone does not compile.
"""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
from profile_manager.measurement_collectors import cardano_patched as shared

BASE_HASH = '95991a736b2e187ace2ec9873f0f10ffdc0ebd432269857dfbc2c74041371d0c'
COMPOSITE_HASH = '1d45c191ead71ce56049e2dc759d5778868ab93693211e2ebe348afb1109a937'
SOURCE_REVISION = 'fef83fed01d7926f3de83b3b917be5a4a48768b5'
REVISION = 'nanoseconds-v5-antithesis1'
BASE_REVISION = 'nanoseconds-v5'
ENGINES = frozenset({'antithesis', 'local'})
BASE_MANIFEST = Path(__file__).resolve().parents[1] / 'targets/cardano-node/measurement-patches-nanoseconds-v5' / SOURCE_REVISION / 'manifest.json'
PATCH_NAME = '0008-antithesis-header-import.patch'
PREIMAGE_PATH = 'ouroboros-consensus/src/ouroboros-consensus/Ouroboros/Consensus/HeaderValidation.hs'
PREIMAGE_HASH = 'd865f35e33e1cd658f3bc6cf8500e7316644641b9f5514be70e1308b928f0132'
CORRECTION_HASH = '686644db2cf1e451a3c63fd25a38ee095ac231ebff893303e473749fe8470191'
CORRECTION = '--- a/ouroboros-consensus/src/ouroboros-consensus/Ouroboros/Consensus/HeaderValidation.hs\n+++ b/ouroboros-consensus/src/ouroboros-consensus/Ouroboros/Consensus/HeaderValidation.hs\n@@ -75,11 +75,11 @@\n import Control.Monad (unless, when)\n import Control.Monad.Except\n   ( Except\n-  , except\n   , runExcept\n   , throwError\n   , withExcept\n   )\n+import Control.Monad.Trans.Except (except)\n import Data.Coerce\n import Data.Kind (Type)\n import qualified Data.Map.Strict as Map\n'


def contract():
    return {'base_patch_set_sha256': BASE_HASH, 'correction_sha256': CORRECTION_HASH,
            'patch_set_sha256': COMPOSITE_HASH, 'revision': REVISION}


def composite_manifest(manifest):
    """Return the v5 recipe plus the correction patch; writes nothing."""
    if manifest['patch_set_sha256'] != BASE_HASH:
        raise ValueError('Antithesis Cardano correction requires the exact v5 recipe')
    assert hashlib.sha256(CORRECTION.encode()).hexdigest() == CORRECTION_HASH
    result = copy.deepcopy(manifest)
    result['patches'].append({'path': PATCH_NAME, 'sha256': CORRECTION_HASH,
        'root': 'dependency:ouroboros-consensus-4.2.1.0',
        'preimages': [{'path': PREIMAGE_PATH, 'sha256': PREIMAGE_HASH}]})
    payload = ''.join(f"{row['sha256']}  {row['path']}\n" for row in result['patches'])
    if hashlib.sha256(payload.encode()).hexdigest() != COMPOSITE_HASH:
        raise ValueError('Antithesis Cardano correction composite differs')
    result.update(patch_set_sha256=COMPOSITE_HASH, measurement_revision=REVISION,
                  compatibility=contract())
    return result


def supplement_manifest(manifest, directory):
    result = composite_manifest(manifest)
    (directory / PATCH_NAME).write_text(CORRECTION)
    return result


def base_patch_manifest():
    """The packaged v5 recipe the correction is defined against."""
    manifest = json.loads(BASE_MANIFEST.read_text())
    if manifest.get('patch_set_sha256') != BASE_HASH or manifest.get('measurement_revision') != BASE_REVISION:
        raise ValueError('Packaged v5 Cardano recipe differs from the correction base')
    return manifest


def is_composite_build(implementation, revision, build):
    return (implementation == 'cardano-node' and revision == SOURCE_REVISION and isinstance(build, dict)
            and build.get('patch_set_sha256') == COMPOSITE_HASH and build.get('measurement_revision') == REVISION)


def _base_identity(identity):
    if (identity.get('compatibility') != contract()
            or identity.get('patch_set_sha256') != COMPOSITE_HASH
            or identity.get('measurement_revision') != REVISION
            or identity.get('engine', 'local') not in ENGINES
            or identity.get('implementation') != 'cardano-node'
            or identity.get('source_revision') != SOURCE_REVISION
            or identity.get('version') != '11.1.2'
            or identity.get('mode') != 'patched'
            or identity.get('toolchain') != shared.CARDANO_HEADER_TOOLCHAIN):
        raise ValueError('Antithesis Cardano compatibility identity differs')
    # Only the import location changed; v5's telemetry and boundary checks apply.
    base = {**copy.deepcopy(identity), 'patch_set_sha256': BASE_HASH, 'measurement_revision': BASE_REVISION}
    build = base.get('source_build')
    if isinstance(build, dict):
        # A controller-compiled local build is validated against the packaged
        # v5 recipe; the composite identity is restored on every emitted result.
        base['source_build'] = {**build, 'patch_set_sha256': BASE_HASH, 'measurement_revision': BASE_REVISION}
    return base


class CompatibleCollector:
    def __init__(self, collector, identity):
        self.collector = collector
        self.identity = copy.deepcopy(identity)
        self.collector.target_identity = copy.deepcopy(identity)

    def __getattr__(self, name):
        return getattr(self.collector, name)

    def prepare(self, context):
        original = self.collector.target_identity
        self.collector.target_identity = _base_identity(self.identity)
        try:
            self.collector.prepare(context)
        finally:
            self.collector.target_identity = original
        self.collector.measurement_revision = REVISION


def build_factories(*, target_identity, **options):
    if target_identity.get('patch_set_sha256') != COMPOSITE_HASH and not target_identity.get('compatibility'):
        return shared.build_cardano_patched_factories(target_identity=target_identity, **options)
    base = _base_identity(target_identity)
    factories = shared.build_cardano_patched_factories(target_identity=base, **options)
    return {name: (lambda entry, factory=factory: CompatibleCollector(factory(entry), target_identity))
            for name, factory in factories.items()}
