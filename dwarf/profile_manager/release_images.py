"""Resolve downloadable official images without granting qualification."""
from __future__ import annotations

import os
import re
from typing import Any

_REPOSITORIES = {
    'amaru': 'ghcr.io/pragma-org/amaru',
    'cardano-node': 'ghcr.io/intersectmbo/cardano-node',
}


def installation_platform() -> str:
    arch = os.environ.get('DWARF_HOST_ARCH', '').strip()
    return 'linux/' + arch if arch in {'amd64', 'arm64'} else ''


def resolve_release_image(implementation: str, version: str, *, platform: str,
                          catalog: dict | None = None, digest: str | None = None) -> dict[str, Any]:
    from profile_manager.version_discovery import load_effective_version_catalog
    if implementation not in _REPOSITORIES or platform not in {'linux/amd64', 'linux/arm64'}:
        raise ValueError('Release implementation or installation platform is unavailable')
    catalog = catalog if catalog is not None else load_effective_version_catalog()
    releases = [r for r in catalog['releases']
                if r['implementation'] == implementation and r['version'] == version]
    if len(releases) != 1:
        raise ValueError('Release is missing or ambiguous in the current catalog')
    repository = _REPOSITORIES[implementation]
    for artifact in releases[0].get('artifacts', []):
        reference = str(artifact.get('reference') or '')
        pinned = str(artifact.get('digest') or '')
        if (artifact.get('kind') == 'oci' and artifact.get('availability') == 'available'
                and artifact.get('public') is True and platform in artifact.get('platforms', [])
                and re.fullmatch(r'sha256:[0-9a-f]{64}', pinned)
                and (reference.startswith(repository + ':') or reference.startswith(repository + '@'))
                and (digest is None or digest == pinned)):
            return {'implementation': implementation, 'version': version, 'digest': pinned,
                    'image': repository + '@' + pinned, 'platform': platform}
    raise ValueError('No verified public image for this release and installation platform; refresh the catalog')
