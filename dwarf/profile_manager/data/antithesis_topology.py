"""HTTP boundary for remote topology configuration, separate from Local APIs."""
import hmac
import json
from urllib.parse import urlsplit

from profile_manager.antithesis_topology import (
    TopologyError, resolve_topology, save_configuration, state_directory, topology_catalog,
)

PREFIX = '/api/antithesis-run/topology/'
MAX_BODY_BYTES = 8192


def dispatch_topology_request(*, method, path, body=b'', expected_token=None):
    route = urlsplit(path).path
    if route not in {PREFIX + key for key in ('catalog', 'resolve', 'configurations')}:
        return None

    def response(status, payload):
        return status, 'application/json; charset=utf-8', json.dumps(payload).encode()

    action = route[len(PREFIX):]
    if method != ('GET' if action == 'catalog' else 'POST'):
        return response(405, {'ok': False, 'error': {'message': 'Method is not supported for this topology endpoint.'}})
    if len(body) > MAX_BODY_BYTES:
        return response(413, {'ok': False, 'error': {'message': 'Topology request is too large.'}})
    try:
        if action == 'catalog':
            return response(200, {'ok': True, **topology_catalog(state_dir=state_directory())})
        value = json.loads(body.decode('utf-8'))
        if not isinstance(value, dict):
            raise TopologyError('Request must be a JSON object.', field='request')
        if action == 'resolve':
            return response(200, {'ok': True, 'topology': resolve_topology(value, state_dir=state_directory())})
        token = value.get('token')
        if not isinstance(token, str) or not token or not expected_token or not hmac.compare_digest(token.encode(), expected_token.encode()):
            return response(403, {'ok': False, 'error': {'message': 'A valid dashboard token is required to save a configuration.'}})
        if set(value) != {'token', 'request', 'topology_digest'}:
            raise TopologyError('Save requires only token, request and topology_digest.', field='request')
        record = save_configuration(value['request'], value['topology_digest'], state_dir=state_directory())
        return response(200, {'ok': True, 'configuration': record})
    except (UnicodeDecodeError, json.JSONDecodeError):
        return response(400, {'ok': False, 'error': {'field': 'request', 'message': 'Request must be valid JSON.'}})
    except TopologyError as error:
        return response(error.status, {'ok': False, 'error': {'field': error.field, 'message': str(error)}})
    except OSError:
        return response(503, {'ok': False, 'error': {'message': 'Antithesis configuration storage is unavailable.'}})
