"""Attach reviewed existing remote workloads to generated configuration only."""
from __future__ import annotations

import copy
import hashlib
import inspect
import json
from pathlib import Path

import yaml

from profile_manager import antithesis_topology as topology

SOURCE = Path(__file__).resolve().parents[2] / 'antithesis/cardano_amaru_miniprotocol_security'
SCENARIO = 'cardano-amaru-miniprotocol-security-local'
SCENARIO_SHA256 = 'ac36cd4b640a613184965018523677eb6459c5cb2465b0a398ef492c8f56ed19'
# Reviewed recipe at cb72871. A source change requires re-tracing its consumers.
SOURCE_SHA256 = {
    'docker-compose.yaml': 'a1888be32cba0d896432431597692e7ea302e27ec263b37f8a97997a1b53e62c',
    'sm-cardano-topology.json': '836006973c51c515c6834e73a9532327af607fc161f50b7cee268b07be41482d',
    'workload/Dockerfile': 'e9cfd733d883eaa4c96cd3cf573612ff9181c0e27b8469d5c455231ece46ec94',
    'workload/miniprotocol_observer.py': '455e4fb870b9658b3a48cfde5f0b79f54833160bc8ddd56113503b7ff7803732',
    'workload/workload_entrypoint.py': '6ff87697dc73ba5df0c129eb19a0c7be4efafb273877100741689df1e0edffe2',
    'workload/test/v1/mixed-miniprotocol-security/anytime_containment.py': '3d272ae71e13d5fd838b42dce97bee8157f6b48e89faea1c99ccf9468aa64c03',
    'workload/test/v1/mixed-miniprotocol-security/eventually_recovery.py': '6bdaa061ad06f0f6ebfb753389f5f1e9f99e7b4a9861ee06f815006b64563bf2',
    'workload/test/v1/mixed-miniprotocol-security/parallel_driver_fuzz_observe.py': 'a0f0538522829452f2afef64061dd235aba7d9d9090523c19a977ad4ef095543',
}
ADDITIONS = {'sm-cardano-seed', 'sm-cardano-victim', 'sm-amaru-victim',
             'sm-cardano-fuzzer', 'sm-amaru-fuzzer', 'sm-workload'}
COMPOSE = 'environment/docker-compose.yaml'


def _read(name):
    path = SOURCE/name
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Linked workload source')
    return path.read_bytes()


def _service(name, source, *, control=False):
    service = copy.deepcopy(source)
    service.update(container_name=name, platform='linux/amd64', init=True)
    if control:
        service.setdefault('labels', {})['com.antithesis.exclude_from_faults'] = topology.FAULT_EXCLUSION
    return service


def _guard_runtime_evidence(additions):
    """Patch only generated sources; keep the shared Local recipe unchanged."""
    observer = 'environment/miniprotocol/workload/miniprotocol_observer.py'
    entrypoint = 'environment/miniprotocol/workload/workload_entrypoint.py'
    anytime = 'environment/miniprotocol/workload/test/v1/mixed-miniprotocol-security/anytime_containment.py'
    recovery = 'environment/miniprotocol/workload/test/v1/mixed-miniprotocol-security/eventually_recovery.py'
    # JSON tracing is needed by measurement collectors. Reuse the existing
    # runtime parser instead of leaving the observer on text-only tip records.
    from scripts.runtime_amaru_preview_proof import extract_latest_adopted_tip, ADOPTED_TIP_PATTERN, CONSENSUS_TIP_ADOPT_PATTERN
    text=additions[observer]
    start=text.index('def parse_amaru_tip(');end=text.index('\ndef query_tip(',start)
    parser=('ADOPTED_TIP_PATTERN = re.compile('+repr(ADOPTED_TIP_PATTERN.pattern)+')\n'
        +'CONSENSUS_TIP_ADOPT_PATTERN = re.compile('+repr(CONSENSUS_TIP_ADOPT_PATTERN.pattern)+')\n'
        +inspect.getsource(extract_latest_adopted_tip)
        +'\ndef parse_amaru_tip(text: str) -> dict[str, Any]:\n'
        +'    tip = extract_latest_adopted_tip(text)\n'
        +'    if tip is None:\n'
        +'        return {"status":"unavailable", "error":"no valid Amaru tip record"}\n'
        +'    return {"status":"ok", "slot":tip["slot"], "hash":tip["hash"].lower(), "block":tip["block_height"]}\n\n')
    additions[observer]=text[:start]+parser+text[end:]
    anchor='        value = json.loads(result.stdout)\n'
    if additions[observer].count(anchor)!=1:
        raise ValueError('Cardano tip parser anchor changed')
    additions[observer]=additions[observer].replace(anchor,anchor+
        '        if not (isinstance(value, dict)\n'
        '                and all(type(value.get(key)) is int and value[key] >= 0 for key in ("slot", "block"))\n'
        '                and isinstance(value.get("hash"), str)\n'
        '                and re.fullmatch(r"[0-9a-fA-F]{64}", value["hash"])):\n'
        '            raise ValueError("tip requires nonnegative integer slot/block and a 64-hex hash")\n')
    command=additions[entrypoint]
    anchor='    status.mkdir(parents=True, exist_ok=True)\n'
    baseline='            atomic_json(status / "prefault.json", baseline)\n'
    if command.count(anchor)!=1 or command.count(baseline)!=1:
        raise ValueError('Workload baseline readiness anchor changed')
    command=command.replace(anchor,anchor+'    (status / "prefault-ready").unlink(missing_ok=True)\n')
    command=command.replace(baseline,
        '            if not all(tip.get("status") == "ok" for tip in baseline["tips"].values()):\n'
        '                time.sleep(1)\n                continue\n'+baseline)
    additions[entrypoint]=command
    anchor = '    evaluation = evaluate_runtime(**inputs)\n'
    replacement = (
        '    runtime_log_available = {"cardano": bool(cardano_text.strip()), "amaru": bool(amaru_text.strip())}\n'
        + anchor +
        '    if not all(runtime_log_available.values()):\n'
        '        evaluation.update(classifiable=False, safe=None)\n'
        '    convergence_observation = {"status": "observed-match" if inputs["converged"] else "unavailable",\n'
        '        "reason": "matching sampled hashes" if inputs["converged"] else "sequential latest tips do not establish divergence"}\n'
        '    if not inputs["converged"]:\n'
        '        known_fatal = any(inputs["fatal"][name] and runtime_log_available[name] for name in runtime_log_available)\n'
        '        evaluation["safe"] = False if known_fatal else None\n'
        '    evaluation["convergence_observation"] = convergence_observation\n'
        '    evaluation["runtime_log_available"] = runtime_log_available\n')
    if additions[observer].count(anchor) != 1:
        raise ValueError('Observer evidence anchor changed')
    additions[observer] = additions[observer].replace(anchor, replacement)
    # Latest tips are sampled sequentially. A mismatch is not a fork proof:
    # the consumer can already have adopted a newer block. Keep fatal checks
    # independent and require positive convergence evidence for containment.
    text=additions[observer]
    start=text.index('    if result["classifiable"]:\n',text.index('def emit_assertions('))
    end=text.index('\n\ndef wait_until(',start)
    assertions='    details = {**details, "tips": result["tips"], "convergence_observation": result["convergence_observation"]}\n'
    for target in ('cardano','amaru'):
        assertions+=(f'    if result["runtime_log_available"]["{target}"]:\n'
            f'        always(not inputs["fatal"].get("{target}", True),\n'
            f'            "mixed_sm_{target}_no_panic_or_fatal_termination", details)\n')
    assertions+=('    sometimes(inputs["converged"], "mixed_sm_convergence_observation_available", details)\n'
        '    if result["classifiable"] and result["safe"] is not None:\n'
        '        always(result["safe"], "mixed_sm_illegal_sessions_contained", details)\n')
    additions[observer]=text[:start]+assertions+text[end:]
    command = additions[anytime]
    for target in ('cardano', 'amaru'):
        block = (f'always(\n    not inputs["fatal"]["{target}"],\n'
                 f'    "mixed_sm_{target}_no_panic_or_fatal_termination",\n    details,\n)\n')
        if command.count(block) != 1:
            raise ValueError('Anytime assertion anchor changed')
        guarded = (f'if result["runtime_log_available"]["{target}"]:\n'
                   + ''.join('    '+line+'\n' for line in block.splitlines()))
        command = command.replace(block, guarded)
    command += ('\nsometimes(all(result["runtime_log_available"].values()),\n'
                '    "mixed_sm_runtime_log_evidence_available", result["runtime_log_available"])\n')
    additions[anytime] = command
    anchor = '    recovered = (\n        '
    if additions[recovery].count(anchor) != 1:
        raise ValueError('Recovery assertion anchor changed')
    additions[recovery] = additions[recovery].replace(anchor, anchor +
        'result["classifiable"]\n        and ')
    return [observer, entrypoint, anytime, recovery]


def bind_workload(files, scenario, resolved, version_binding):
    report = {'status':'recorded', 'scenario_id':scenario.id, 'runtime_verified':False,
              'targets':[], 'issues':[], 'source_digests':{}}
    def gap(code, message):
        report['issues'].append({'code':code, 'field':'scenario_id',
                                'kind':'implementation-gap', 'message':message})
    if scenario.id != SCENARIO or resolved is None:
        return report
    if hashlib.sha256(scenario.raw_bytes).hexdigest() != SCENARIO_SHA256:
        gap('workload-scenario-contract-changed', 'The selected scenario differs from the reviewed mini-protocol recipe; its workload was not attached.')
        return report
    if resolved['request']['template_id'] != 'mixed-control-v1':
        gap('workload-topology-not-supported', 'The paired mini-protocol workload requires the mixed control configuration; it was not attached to this topology.')
        return report
    try:
        sources = {name:_read(name) for name in SOURCE_SHA256}
        if any(hashlib.sha256(sources[name]).hexdigest()!=digest for name,digest in SOURCE_SHA256.items()):
            raise ValueError('Workload source changed')
        original = yaml.safe_load(sources['docker-compose.yaml'])
        baseline = resolved['compose']
        sidecar_digest = hashlib.sha256(json.dumps(baseline['services']['sidecar'],
            sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if sidecar_digest != 'a1db2a0f4dbb9d3c3faea9a80973339e9a927d35e268812f73590882a14fb609':
            raise ValueError('Control setup gate changed')
        if set(original['services']) != set(baseline['services']) | ADDITIONS:
            raise ValueError('Workload service inventory changed')
        # The additive recipe must still share the complete control environment.
        for name, service in baseline['services'].items():
            if name != 'sidecar' and service != _service(name, original['services'][name], control=True):
                raise ValueError('Control service contract changed')
        for name in resolved['input_digests']:
            if name != 'docker-compose.yaml' and _read(name).decode() != files['environment/'+name]:
                raise ValueError('Control asset contract changed')
        compose = yaml.safe_load(files[COMPOSE])
        binding = copy.deepcopy(version_binding)
        rows = {s['service']:s for s in binding['services']}
        if binding.get('target_contract') is None or binding['target_contract']['scope'] != 'mixed':
            raise ValueError('Unbound control target contract')
        for name in sorted(ADDITIONS | {'sidecar'}):
            compose['services'][name] = _service(name, original['services'][name])
        for name, value in original['volumes'].items():
            if name not in compose['volumes']:
                compose['volumes'][name] = copy.deepcopy(value)
        for victim, relay in [('sm-cardano-victim','relay1'),('sm-amaru-victim','amaru-relay-1')]:
            target, selected = compose['services'][victim], compose['services'][relay]
            row = copy.deepcopy(rows[relay])
            row.update(service=victim, baseline_image=target['image'])
            target['image'] = selected['image']
            if victim == 'sm-amaru-victim':
                for key in ('AMARU_BIN','AMARU_MIGRATE_CHAIN_DB'):
                    if key in selected['environment']:
                        target['environment'][key] = selected['environment'][key]
                for mount in selected['volumes']:
                    if isinstance(mount,dict) and mount.get('target') == '/target':
                        target['volumes'].append(copy.deepcopy(mount))
                if 'amaru-target-extract' in selected['depends_on']:
                    target['depends_on']['amaru-target-extract'] = copy.deepcopy(selected['depends_on']['amaru-target-extract'])
                if row.get('target_binary',{}).get('mode') == 'legacy-wrapper':
                    row['target_binary']['source_service'] = victim
            row['image'] = target['image']
            binding['services'].append(row)
            binding['target_contract']['services'].append(victim)
        # Build the reviewed sources, not an assumed match inside a remote image.
        workload = compose['services']['sm-workload']
        declared_image = workload.pop('image')
        workload['build'] = {'context':'./miniprotocol', 'dockerfile':'workload/Dockerfile'}
        for name in sorted(ADDITIONS - {'sm-cardano-victim','sm-amaru-victim'}):
            binding['services'].append({'service':name, 'role':'bootstrap' if name=='sm-cardano-seed' else 'workload',
                'implementation':None, 'image':compose['services'][name].get('image'),
                'baseline_image':original['services'][name].get('image'), 'status':'unchanged'})
        additions = {'environment/sm-cardano-topology.json':sources['sm-cardano-topology.json'].decode()}
        additions.update({'environment/miniprotocol/'+name:data.decode()
                          for name,data in sources.items() if name.startswith('workload/')})
        patched = _guard_runtime_evidence(additions)
        additions[COMPOSE] = yaml.safe_dump(compose,sort_keys=False)
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
        gap('workload-source-contract-changed', 'The existing workload or its control interface changed; no workload changes were applied. Review the recipe before reconnecting it.')
        return report
    files.update(additions)
    version_binding.clear()
    version_binding.update(binding)
    report.update(status='applied', targets=['sm-cardano-victim','sm-amaru-victim'],
        recipe='cardano_amaru_miniprotocol_security', evidence='emitted-configuration',
        source_digests={name:'sha256:'+digest for name,digest in SOURCE_SHA256.items()},
        source_patches=[{'path':name, 'sha256':'sha256:'+hashlib.sha256(additions[name].encode()).hexdigest(),
                         'reason':'Require sampled runtime evidence, parse existing JSON/text tips and gate setup on actual baseline tips.'}
                        for name in patched],
        adapter_digest='sha256:'+hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        workload_image={'status':'build-required', 'service':'sm-workload', 'original_reference':declared_image},
        test_commands=[name.removeprefix('workload/test/') for name in SOURCE_SHA256 if name.startswith('workload/test/')],
        setup_gate='Matching logged injection descriptions across 24 protocol/class cells, equal seed, reachable targets and observed baseline tips; then sidecar prefault-ready gate.',
        observations=['fuzzer log descriptions and counts','target runtime fatal markers',
                      'Cardano socket tips','Amaru tip.adopt log records','TCP reachability',
                      'producer and Amaru-fed consumer progress/convergence'],
        limitations=['Local probe orchestration and its exit-code assertion are not translated.',
                     'Log descriptions are not captured wire bytes or proof of target receipt.',
                     'Missing logs or version-specific formats can prevent classification; runtime parsing is unverified.',
                     'Recovery command invokes a fault-stop hook only when ANTITHESIS_STOP_FAULTS is supplied.',
                     'Workload build, node bootstrap, runtime compatibility and Antithesis execution remain unverified.'])
    gap('scenario-local-orchestration-not-translated', 'The existing remote workload and observing test commands are connected. The scenario Local probe orchestration and exit-code assertion remain untranslated; scenario application is partial.')
    gap('workload-image-build-required', 'The existing mini-protocol observer Dockerfile and test commands are staged for a build. No workload image was built or published.')
    return report
