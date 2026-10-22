"""Read-only version API for the separate Antithesis wizard."""
import json
import math
from urllib.parse import urlsplit

from profile_manager.antithesis_versions import VersionError, resolve_versions, versions_catalog

PREFIX = '/api/antithesis-run/versions/'
MAX_BODY_BYTES = 16384


def dispatch_versions_request(*, method, path, body=b''):
    route = urlsplit(path).path
    if not route.startswith(PREFIX):
        return None

    def response(status, payload):
        return status, 'application/json; charset=utf-8', json.dumps(payload).encode()

    action = route[len(PREFIX):]
    if action not in {'catalog', 'resolve'}:
        return response(404, {'ok': False, 'error': {'message': 'Unknown Antithesis version endpoint.'}})
    if method != ('GET' if action == 'catalog' else 'POST'):
        return response(405, {'ok': False, 'error': {'message': 'Method is not supported for this version endpoint.'}})
    if len(body) > MAX_BODY_BYTES:
        return response(413, {'ok': False, 'error': {'message': 'Version request is too large.'}})
    if action == 'resolve':
        def reject_constant(_value):
            raise ValueError('Non-finite JSON value')
        def finite_float(raw):
            number = float(raw)
            if not math.isfinite(number):
                raise ValueError('Non-finite JSON value')
            return number
        try:
            value = json.loads(body.decode('utf-8'), parse_constant=reject_constant, parse_float=finite_float)
        except (UnicodeDecodeError, ValueError, RecursionError):
            return response(400, {'ok': False, 'error': {'message': 'Request must be valid finite JSON.'}})
    try:
        if action == 'catalog':
            return response(200, {'ok': True, **versions_catalog()})
        return response(200, {'ok': True, 'assessment': resolve_versions(value)})
    except VersionError as error:
        return response(error.status, {'ok': False, 'error': {'field': error.field, 'message': str(error)}})
