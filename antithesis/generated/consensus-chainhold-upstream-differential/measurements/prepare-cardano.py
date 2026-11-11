import json,shutil
from pathlib import Path
CARDANO_MEASUREMENT_TRACE_NAMESPACES=('Net.Handshake.Remote', 'ChainSync.Client', 'ChainSync.ServerHeader', 'BlockFetch.Client', 'BlockFetch.Server', 'TxSubmission.TxInbound', 'TxSubmission.TxOutbound', 'Mempool', 'KeepAlive.Remote', 'ChainDB', 'Forge.Loop', 'LedgerMetrics', 'Resources')
CARDANO_MEASUREMENT_TRACE_SEVERITIES={'KeepAlive.Remote': 'Debug', 'Mempool': 'Debug'}
CARDANO_MEASUREMENT_NODE_SETTINGS={'TxSubmissionLogicVersion': 'TxSubmissionLogicV2'}
def _enable_cardano_measurement_traces(config_path: Path) -> dict:
    """Enable audited machine namespaces only for an opt-in measurement profile."""
    body = json.loads(config_path.read_text(encoding="utf-8"))
    options = body.get("TraceOptions")
    if not isinstance(options, dict):
        options = {}
    for namespace in CARDANO_MEASUREMENT_TRACE_NAMESPACES:
        current = options.get(namespace)
        configured = dict(current) if isinstance(current, dict) else {}
        configured.update({
            "severity": CARDANO_MEASUREMENT_TRACE_SEVERITIES.get(namespace, "Info"),
            "detail": "DDetailed",
        })
        options[namespace] = configured
    body["TraceOptions"] = options
    body.update(CARDANO_MEASUREMENT_NODE_SETTINGS)
    config_path.write_text(json.dumps(body, indent=4) + "\n", encoding="utf-8")
    return {
        "enabled": True,
        "namespaces": sorted(CARDANO_MEASUREMENT_TRACE_NAMESPACES),
        "config_path": str(config_path),
        # Every non-default node setting applied, so a retained run never reads as
        # plain stock when it ran with measurement settings.
        "node_settings": dict(CARDANO_MEASUREMENT_NODE_SETTINGS),
    }

if __name__=='__main__':
    shutil.copytree('/configs','/target',dirs_exist_ok=True)
    path=Path('/target/configs/config.json')
    _enable_cardano_measurement_traces(path)
    body=json.loads(path.read_text())
    for name,options in body['TraceOptions'].items():
        if any(name==ns or name.startswith(ns+'.') for ns in CARDANO_MEASUREMENT_TRACE_NAMESPACES):
            options.update(severity=CARDANO_MEASUREMENT_TRACE_SEVERITIES.get(name,'Info'),detail='DDetailed')
            options.pop('maxFrequency',None)
    path.write_text(json.dumps(body,indent=2)+'\n')
