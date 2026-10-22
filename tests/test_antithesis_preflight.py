import copy
import yaml
import pytest


def package(**updates):
    doc = {'services': {'node': {'image': 'example/node:experimental', 'container_name': 'node',
            'hostname': 'node', 'init': True, 'volumes': ['./config.json:/config.json:ro']}},
           'networks': {'default': {'internal': False}}}
    doc['services']['node'].update(updates)
    return {'docker-compose.yaml': (yaml.safe_dump(doc).encode(), 0o644), 'config.json': (b'{}', 0o644)}


def report(files):
    from profile_manager.antithesis_preflight import inspect_package
    return inspect_package(files)


def failed(result):
    return {r['code'] for r in result['checks'] if r['state'] == 'fail'}


def test_structural_success_is_not_runtime_qualification_and_preserves_inputs():
    files = package(); before = copy.deepcopy(files)
    result = report(files)
    assert not failed(result)
    assert result['execution_ready'] is False and result['submission_ready'] is False
    assert result['state'] == 'review'
    assert {r['contract'] for r in result['checks']} >= {'C01','C02','C03','C04','C05','C06','C07','C08','C09','C10','C11','C12','C13','C14','C15'}
    assert any(r['code'] == 'image-digest' and r['state'] == 'warn' for r in result['checks'])
    assert all(r['state'] == 'not_checked' for r in result['checks'] if r['code'] in ('setup-event','command-catalog','assertion-encounters'))
    assert files == before


@pytest.mark.parametrize('changes,code', [
    ({'pull_policy':'always'},'pull-policy'), ({'logging':{'driver':'local'}},'logging-driver'),
    ({'container_name':'other'},'container-name'), ({'hostname':'bad_host'},'hostname'),
    ({'platform':'linux/arm64'},'platform'), ({'build':'.'},'image-build'),
    ({'image':'${NODE_IMAGE}'},'image-reference'), ({'image':None},'image-reference'),
    ({'labels':{'com.antithesis.exclude_from_faults':True}},'fault-label'),
    ({'labels':{'com.antithesis.exclude_from_faults':'network,invalid'}},'fault-label'),
    ({'volumes':['./missing:/data']},'missing-input'),
    ({'volumes':['/host/data:/data']},'external-input'),
    ({'volumes':['undeclared:/data']},'volume-reference'),
    ({'configs':['missing']},'config-reference'),
    ({'depends_on':['missing']},'dependency-reference'),
    ({'networks':['missing']},'network-reference'),
    ({'volumes':7},'compose-shape'),
    ({'env_file':['missing.env']},'missing-input'),
])
def test_concrete_failures(changes, code):
    assert code in failed(report(package(**changes)))


def test_missing_init_is_a_review_warning_not_silent_pass():
    result = report(package(init=False))
    assert any(r['code']=='init' and r['state']=='warn' for r in result['checks'])


def test_internal_network_and_moog_substitution_are_distinguished():
    files = package()
    doc = yaml.safe_load(files['docker-compose.yaml'][0]);doc['networks']['default']['internal']=True
    files['docker-compose.yaml']=(yaml.safe_dump(doc).encode(),0o644)
    assert 'internal-network' in failed(report(files))
    doc['networks']['default']['internal']='${INTERNAL_NETWORK}'
    files['docker-compose.yaml']=(yaml.safe_dump(doc).encode(),0o644)
    result=report(files)
    assert 'internal-network' not in failed(result)
    assert any(r['code']=='moog-substitution' and r['state']=='warn' for r in result['checks'])


@pytest.mark.parametrize('text', ['services: [a]', 'services: &s {node: *s}', 'services: {}',
    'services:\n  node:\n    image: a\n    image: b', 'services: {1: {image: a}}'])
def test_invalid_or_ambiguous_yaml_is_a_finding(text):
    assert failed(report({'docker-compose.yaml':(text.encode(),0o644)}))


def test_no_root_compose_and_dockerignore_metadata():
    files=package();files['config/docker-compose.yaml']=files.pop('docker-compose.yaml')
    assert 'compose-root' in failed(report(files))
    files=package();files['.DS_Store']=(b'x',0o644)
    assert 'package-metadata' in failed(report(files))


def test_long_syntax_bind_and_declared_named_volume():
    files=package(volumes=[{'type':'bind','source':'config.json','target':'/config.json','read_only':True},
                           {'type':'volume','source':'state','target':'/state'}])
    doc=yaml.safe_load(files['docker-compose.yaml'][0]);doc['volumes']={'state':{}}
    files['docker-compose.yaml']=(yaml.safe_dump(doc).encode(),0o644)
    assert not failed(report(files))


def test_escaped_container_variable_is_not_host_interpolation():
    assert 'compose-interpolation' not in failed(report(package(command=['sh','-c','echo $$HOME'])))
    assert 'compose-interpolation' in failed(report(package(command=['echo','$HOME'])))


@pytest.mark.parametrize('changes', [
    {'volumes':[{'type':'bind','source':'./config.json'}]}, {'volumes':['']},
    {'depends_on':{'node':{'condition':'made_up'}}}, {'networks':{'default':7}},
    {'labels':7}, {'command':False}, {'volumes':[{'type':'volume','source':[],'target':'/data'}]},
    {'labels':['com.antithesis.exclude_from_faults=network','com.antithesis.exclude_from_faults=kill']},
])
def test_invalid_nested_service_shapes_fail(changes):
    assert 'compose-shape' in failed(report(package(**changes)))


@pytest.mark.parametrize('reference',['Example/Bad@sha256:nope','example/node@sha256:123','example/node::bad','example//node:tag','example/node@bad'])
def test_malformed_image_syntax_is_not_accepted(reference):
    assert 'image-reference' in failed(report(package(image=reference)))


def test_non_utf8_yaml_returns_a_finding():
    files=package();files['docker-compose.yaml']=(files['docker-compose.yaml'][0].decode().encode('utf-16'),0o644)
    assert 'compose-shape' in failed(report(files))
