import base64
import copy
import io
import json
from pathlib import Path
import zipfile

import pytest
import yaml

from test_antithesis_version_binding import catalog, chosen, compose, generate, request


CASES = [
    (f'cardano-node-cbor-{shape}-fuzz{suffix}', protocol, shape)
    for protocol, shape in [('chainsync', 'block-header'), ('blockfetch', 'block'),
                            ('txsubmission', 'tx-body'), ('txsubmission', 'certificate'),
                            ('txsubmission', 'auxiliary-data')]
    for suffix in ('', '-structured')
] + [
    (f'cardano-node-cov-{surface}-aflpp-smoke', protocol, shape)
    for surface, protocol, shape in [('header', 'chainsync', 'block-header'),
                                     ('block', 'blockfetch', 'block'),
                                     ('tx', 'txsubmission', 'tx-body'),
                                     ('applytx', 'txsubmission', 'tx-body'),
                                     ('applyblock', 'txsubmission', 'tx-body'),
                                     ('ledger', 'txsubmission', 'tx-body')]
]
MODES = CASES[::2][:5]


def test_cases_cover_every_shipped_native_renderable_scenario():
    from profile_manager.scenario import load_scenario
    from profile_manager.antithesis_generator import render_bundle, GeneratorError
    rendered = set()
    for path in sorted((Path(__file__).resolve().parents[1]/'dwarf/scenarios').rglob('*.yaml')):
        scenario = load_scenario(path)
        try:
            render_bundle(scenario, registry='')
        except GeneratorError:
            continue
        rendered.add(scenario.id)
    assert rendered == {case[0] for case in CASES}


@pytest.mark.parametrize('scenario_id,protocol,shape', CASES)
def test_each_native_scenario_binds_only_its_actual_target(catalog, scenario_id, protocol, shape):
    baseline = generate(request(scenario_id=scenario_id))
    outputs = [generate(request(scenario_id=scenario_id, versions=chosen(v))) for v in ('10.7.1', '10.6.2')]
    original = compose(baseline)
    for output in outputs:
        binding = output['version_binding']
        assert binding['status'] == 'applied'
        target, = [s for s in binding['services'] if s['role']=='target']
        assert target['service'] == 'relay2'
        expected = copy.deepcopy(original)
        expected['services']['relay2']['image'] = target['image']
        assert compose(output) == expected
        assert output['engine_handoff']['adversary'] == {'protocol':protocol, 'shape':shape}
        contract = binding['target_contract']
        assert contract['service']=='relay2' and contract['protocol']==protocol and contract['shape']==shape
        topology = 'relay-eclipse-topology.json' if protocol=='blockfetch' else 'relay-dwarf-topology.json'
        assert contract['topology_file'] == 'environment/'+topology
        assert contract['evidence'] == 'emitted-configuration'
        for path, content in baseline['files'].items():
            if path.startswith('environment/') and path != 'environment/docker-compose.yaml':
                assert output['files'][path] == content
        assert json.loads(output['files']['package-manifest.json'])['version_binding'] == binding
        assert output['execution_ready'] is False
    assert compose(outputs[0])['services']['relay2']['image'] != compose(outputs[1])['services']['relay2']['image']


@pytest.mark.parametrize('scenario_id,protocol,shape', MODES)
def test_each_mode_survives_staging_and_download(catalog, tmp_path, scenario_id, protocol, shape):
    from profile_manager.antithesis_preparation import prepare_package, read_preparation
    result = prepare_package(request(scenario_id=scenario_id, versions=chosen('10.6.2')), {},
                             state_dir=tmp_path/'state')
    receipt = result['receipt']
    assert receipt['version_binding']['status']=='applied'
    assert receipt['version_binding']['target_contract']['protocol']==protocol
    archive = zipfile.ZipFile(io.BytesIO(base64.b64decode(result['archive_base64'])))
    output = yaml.safe_load(archive.read('docker-compose.yaml'))
    target, = [s for s in receipt['version_binding']['services'] if s['role']=='target']
    assert output['services']['relay2']['image']==target['image']
    topology = receipt['version_binding']['target_contract']['topology_file'].removeprefix('environment/')
    assert archive.read(topology)
    assert read_preparation(receipt['id'], state_dir=tmp_path/'state')['receipt']==receipt
    assert receipt['execution_ready'] is False and receipt['submission_ready'] is False


@pytest.mark.parametrize('scenario_id,protocol,shape', MODES)
@pytest.mark.parametrize('variant,version,code', [
    ('stock', 'custom-experiment', 'unknown-release'),
    ('instrumented', '10.7.1', 'patched-build-not-implemented'),
])
def test_existing_gaps_are_honest_in_every_mode(catalog, scenario_id, protocol, shape, variant, version, code):
    output = generate(request(scenario_id=scenario_id, versions=chosen(version, build_variant=variant)))
    assert output['version_binding']['status']=='recorded'
    assert code in {i['code'] for i in output['issues']}
    assert compose(output)==compose(generate(request(scenario_id=scenario_id)))


@pytest.mark.parametrize('scenario_id,protocol,shape', MODES)
@pytest.mark.parametrize('drift', [
    'protocol', 'shape', 'selftest', 'mount', 'command', 'port', 'peer', 'inactive-root',
    'network-disconnected', 'entrypoint', 'duplicate-mount', 'duplicate-topology-option', 'adversary-image',
])
def test_mode_target_contract_drift_preserves_images(catalog, monkeypatch, scenario_id, protocol, shape, drift):
    from profile_manager import antithesis_package
    original = antithesis_package.native.render_bundle
    baseline = {}

    def changed(*args, **kwargs):
        generated = original(*args, **kwargs)
        body = yaml.safe_load(generated.files['config/docker-compose.yaml'])
        target = body['services']['relay2']
        adversary = body['services']['dwarf-adversary']
        command = adversary['command']
        topology_name = 'relay-eclipse-topology.json' if protocol=='blockfetch' else 'relay-dwarf-topology.json'
        topology = json.loads(generated.files[topology_name])
        if drift=='protocol':
            command[command.index('--protocol')+1]='other-mode'
        elif drift=='shape':
            command[command.index('--cbor-shape')+1]='other-shape'
        elif drift=='adversary-image':
            adversary['image']='busybox:latest'
        elif drift=='selftest':
            command.append('--selftest')
        elif drift=='mount':
            target['volumes']=[m.replace(topology_name, 'testnet.yaml') for m in target['volumes']]
        elif drift=='command':
            target['command']=target['command'].replace('--topology /configs/configs/topology.json', '--topology /other.json')
        elif drift=='port':
            command[command.index('--listen-port')+1]='9999'
        elif drift=='peer':
            topology['localRoots'][0]['accessPoints'][0]['address']='p1.example'
        elif drift=='inactive-root':
            topology['localRoots'][0]['valency']=0
        elif drift=='network-disconnected':
            target['networks']=['disconnected']
        elif drift=='entrypoint':
            target['entrypoint']=['/bin/true']
        elif drift=='duplicate-mount':
            target['volumes'].append('./testnet.yaml:/configs/configs/topology.json:ro')
        elif drift=='duplicate-topology-option':
            target['command']+=' --topology=/other.json'
        generated.files[topology_name]=json.dumps(topology)
        generated.files['config/docker-compose.yaml']=yaml.safe_dump(body)
        baseline['image']=target['image']
        return generated

    monkeypatch.setattr(antithesis_package.native, 'render_bundle', changed)
    result=generate(request(scenario_id=scenario_id, versions=chosen()))
    assert result['version_binding']['status']=='recorded'
    assert compose(result)['services']['relay2']['image']==baseline['image']
    assert 'native-target-contract-changed' in {i['code'] for i in result['issues']}
    assert result['version_binding'].get('target_contract') is None


@pytest.mark.parametrize('drift', [
    'extra-peer', 'ledger-enabled', 'public-peer', 'network-leak',
    'missing-bridge', 'missing-network-definition', 'other-network-participant',
])
def test_block_fetch_requires_the_emitted_eclipse_contract(catalog, monkeypatch, drift):
    from profile_manager import antithesis_package
    original=antithesis_package.native.render_bundle
    def changed(*args, **kwargs):
        generated=original(*args, **kwargs)
        body=yaml.safe_load(generated.files['config/docker-compose.yaml'])
        topology=json.loads(generated.files['relay-eclipse-topology.json'])
        if drift=='extra-peer':
            topology['localRoots'][0]['accessPoints'].append({'address':'p1.example','port':3001})
        elif drift=='ledger-enabled':
            topology['useLedgerAfterSlot']=0
        elif drift=='public-peer':
            topology['publicRoots']=[{'accessPoints':[{'address':'p1.example','port':3001}]}]
        elif drift=='network-leak':
            body['services']['relay2']['networks'].append('default')
        elif drift=='other-network-participant':
            body['services']['p1']['networks']=['default','eclipse']
        elif drift=='missing-bridge':
            del body['services']['dwarf-adversary']['networks']['default']
        elif drift=='missing-network-definition':
            del body['networks']['eclipse']
        generated.files['relay-eclipse-topology.json']=json.dumps(topology)
        generated.files['config/docker-compose.yaml']=yaml.safe_dump(body)
        return generated
    monkeypatch.setattr(antithesis_package.native,'render_bundle',changed)
    result=generate(request(scenario_id='cardano-node-cbor-block-fuzz', versions=chosen()))
    assert result['version_binding']['status']=='recorded'
    assert 'native-target-contract-changed' in {i['code'] for i in result['issues']}
