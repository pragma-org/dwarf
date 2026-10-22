"""Dedicated preview and authenticated Antithesis asset preparation boundary."""
import hmac
import http.client
import json
import threading
import yaml
from urllib.parse import urlsplit

from profile_manager.antithesis_package import PackageError, _shape, preview_package
from profile_manager.antithesis_preparation import prepare_package, read_preparation
from profile_manager import antithesis_image_build, antithesis_publication, antithesis_package_validation, antithesis_submission

PREFIX='/api/antithesis-run/package/'
MAX_BODY_BYTES=32768
_PREPARING = threading.BoundedSemaphore(1)


def dispatch_package_request(*, method, path, body=b'', expected_token=None):
    route=urlsplit(path).path
    if not route.startswith(PREFIX):
        return None
    def reply(status,payload):
        return status,'application/json; charset=utf-8',json.dumps(payload).encode()
    def error(status,message):
        return reply(status,{'ok':False,'error':{'message':message}})
    action = route[len(PREFIX):]
    if action not in ('preview','prepare','read','node-start','node-status','build','build-status','build-result','publication-create','publication-action','publication-status','publication-read','publication-result','validate','validation-status','readiness','submission-status','submit','submission-refresh'):
        return error(404,'Unknown package endpoint.')
    if method != 'POST':
        return error(405,'Package endpoints require POST.')
    if len(body)>MAX_BODY_BYTES:
        return error(413,'Package request is too large.')
    def reject_number(_value):
        raise ValueError('Only finite integer JSON numbers are accepted')
    try:
        value=json.loads(body.decode('utf-8'),parse_constant=reject_number,parse_float=reject_number)
        # Bound shape before any recursive copying, validation or rendering.
        pending=[(value,0)]
        while pending:
            item,depth=pending.pop()
            if depth>8:
                raise ValueError('Request nesting is too deep')
            if isinstance(item,dict): pending.extend((v,depth+1) for v in item.values())
            elif isinstance(item,list): pending.extend((v,depth+1) for v in item)
    except (ValueError,UnicodeError,RecursionError):
        return error(400,'Request must be bounded JSON with whole-number counts.')
    try:
        if action == 'preview':
            return reply(200,{'ok':True,'package':preview_package(value)})
        token = value.get('token') if isinstance(value,dict) else None
        if not isinstance(token,str) or not token or not expected_token or not hmac.compare_digest(token.encode(),expected_token.encode()):
            return error(403,'A valid dashboard token is required to acquire or read prepared assets.')
        if action in ('node-start','node-status'):
            from profile_manager import antithesis_artifacts
            if action=='node-start':
                _shape(value,{'token','request','request_id'},'request')
                artifact=antithesis_artifacts.start(value['request'],value['request_id'])
            else:
                _shape(value,{'token','artifact_id'},'request')
                artifact=antithesis_artifacts.read(value['artifact_id'])
            return reply(202 if artifact['state']=='building' else 200,{'ok':True,'artifact':artifact})
        if action == 'readiness':
            _shape(value, {'token','source','target','request_id'}, 'request')
            return reply(202,{'ok':True,'submission':antithesis_submission.start_readiness(value['source'],value['target'],value['request_id'])})
        if action in ('submit','submission-refresh'):
            _shape(value, {'token','submission_id'}, 'request')
            operation=antithesis_submission.submit if action=='submit' else antithesis_submission.refresh
            return reply(202,{'ok':True,'submission':operation(value['submission_id'])})
        if action == 'submission-status':
            _shape(value, {'token','submission_id'}, 'request')
            return reply(200,{'ok':True,'submission':antithesis_submission.read_job(value['submission_id'])})
        if action == 'validate':
            _shape(value, {'token','source','mode','request_id'}, 'request')
            return reply(202,{'ok':True,'validation':antithesis_package_validation.start_validation(value['source'],value['mode'],value['request_id'])})
        if action == 'validation-status':
            _shape(value, {'token','validation_id'}, 'request')
            return reply(200,{'ok':True,'validation':antithesis_package_validation.read_validation(value['validation_id'])})
        if action.startswith('publication-'):
            shapes = {
                'publication-create': {'token','build_id','registry_prefix','request_id'},
                'publication-action': {'token','handoff_id','action','target','request_id'},
                'publication-status': {'token','handoff_id','operation_id'},
                'publication-read': {'token','handoff_id'},
                'publication-result': {'token','handoff_id'},
            }
            _shape(value, shapes[action], 'request')
            if action == 'publication-create':
                return reply(200,{'ok':True,'handoff':antithesis_publication.create_handoff(
                    value['build_id'],value['registry_prefix'],request_id=value['request_id'])})
            if action == 'publication-action':
                return reply(202,{'ok':True,'operation':antithesis_publication.start_operation(
                    value['handoff_id'],value['action'],target=value['target'],request_id=value['request_id'])})
            if action == 'publication-status':
                return reply(200,{'ok':True,'operation':antithesis_publication.read_operation(value['handoff_id'],value['operation_id'])})
            if action == 'publication-read':
                return reply(200,{'ok':True,'handoff':antithesis_publication.read_handoff(value['handoff_id'])})
            return reply(200,{'ok':True,'preparation':antithesis_publication.read_result(value['handoff_id'])})
        if action in ('build','build-status','build-result'):
            field = 'preparation_id' if action == 'build' else 'build_id'
            _shape(value, {'token',field} | ({'request_id'} if action == 'build' and 'request_id' in value else set()), 'request')
            if action == 'build':
                return reply(202,{'ok':True,'build':antithesis_image_build.start_build(value[field], request_id=value.get('request_id'))})
            if action == 'build-status':
                return reply(200,{'ok':True,'build':antithesis_image_build.read_build(value[field])})
            return reply(200,{'ok':True,'preparation':antithesis_image_build.read_build_result(value[field])})
        if action == 'read':
            _shape(value, {'token','preparation_id'}, 'request')
            return reply(200,{'ok':True,'preparation':read_preparation(value['preparation_id'])})
        _shape(value, {'token','request','source'}, 'request')
        if not _PREPARING.acquire(blocking=False):
            return error(409,'Another Antithesis package is being prepared. Retry after it finishes.')
        try:
            prepared = prepare_package(value['request'], value['source'])
        finally:
            _PREPARING.release()
        return reply(200,{'ok':True,'preparation':prepared})
    except PackageError as exc:
        return reply(exc.status,{'ok':False,'error':{'field':exc.field,'message':str(exc)}})
    except (OSError,ValueError,KeyError,TypeError,RecursionError,yaml.YAMLError,http.client.HTTPException):
        if action in ('readiness','submission-status','submit','submission-refresh'):
            return error(503,'Submission operation could not be completed. Recover the saved request status before any further action.')
        if action in ('validate','validation-status'):
            return error(503,'Validation request could not be completed. Recover the saved validation status before retrying; no test submission was requested.')
        if action.startswith('publication-'):
            return error(503,'Publication request could not be completed. Recover the saved operation before any retry; no test submission was requested.')
        if action in ('build','build-status','build-result'):
            return error(503,'Image build request could not be completed. Check the saved job status before retrying; no publication or submission was requested.')
        return error(503,'Package source inputs could not be read or rendered. No build or publication was attempted.')
