"""Dashboard handles for audited controller image downloads."""
from __future__ import annotations

import fcntl
import hashlib
import json
import uuid
from urllib.parse import urlencode

from profile_manager.config import storage_roots
from profile_manager.release_images import installation_platform, resolve_release_image
from profile_manager.topology_actions import TopologyActionError, controller_request


def release_download(implementation, version, *, digest=None, start=False, controller=None):
    selected = resolve_release_image(implementation, version, platform=installation_platform(), digest=digest)
    controller = controller or controller_request
    root = storage_roots().state / 'release-downloads'
    root.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest()
    path = root / (key + '.json')
    # Keep concurrent clicks/reloaded pages bound to one controller execution.
    with (root / (key + '.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        job = json.loads(path.read_text()) if path.exists() else None
        state = 'missing'
        failure = None
        if job:
            try:
                status = controller('execution.status', {'execution_id': job['execution_id']}, 10)
            except TopologyActionError as exc:
                if str(exc) != 'execution is not managed by this controller':
                    raise
                status = {'state': 'interrupted'}
            state = status['state']
            failure = (status.get('failure') or {}).get('message')
        if state not in {'running', 'cancelling'}:
            # Availability belongs to Docker, not to our download-job history.
            # A failed check propagates; it must never authorize a fresh pull.
            observed = controller('image.availability', {
                'implementation': implementation, 'version': version, 'digest': selected['digest']}, 20)
            state = observed.get('state')
            if state not in {'available', 'missing', 'wrong-platform'}:
                raise ValueError('Controller returned an invalid image availability state')
            if state == 'available' and (
                    observed.get('platform') != selected['platform']
                    or selected['image'] not in observed.get('manifest_references', [])):
                raise ValueError('Local image identity differs from the selected release')
        if start and state not in {'running', 'cancelling', 'available'}:
            execution_id = 'download-' + uuid.uuid4().hex
            job = {'execution_id': execution_id, **selected}
            # Persist the handle before submission so a lost response can be reconciled.
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps(job))
            temporary.replace(path)
            controller('image.download', {'implementation': implementation, 'version': version,
                'digest': selected['digest'], 'execution_id': execution_id}, 1800,
                idempotency_key=execution_id)
            state = 'running'
        params = {'version_policy': 'exact', 'release_digest': selected['digest'],
                  'amaru_version' if implementation == 'amaru' else 'cardano_version': version}
        return {'ok': True, **selected, 'state': state, 'failure': failure,
                'execution_id': job['execution_id'] if job else None,
                'run_url': '/run?' + urlencode(params)}
