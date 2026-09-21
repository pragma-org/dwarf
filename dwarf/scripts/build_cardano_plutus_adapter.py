#!/usr/bin/env python3
"""Build the revision-locked Cardano Plutus V2 conformance adapter."""
from __future__ import annotations
import argparse, json, os, shutil, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
from scripts import build_cardano_conformance_adapter as common
from scripts import build_cardano_measurement_target as exact_builder

REVISION=common.REVISION
ROOT=Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST=ROOT/"targets"/"cardano-node"/"conformance-adapters"/REVISION/"plutus-manifest.json"
BuildContractError=exact_builder.BuildContractError

def build_adapter(*,manifest_path,source_repository,output_dir,registry_root,cabal_binary,ghc_binary):
    if output_dir.exists(): raise BuildContractError(f"output directory already exists: {output_dir}")
    manifest=exact_builder.load_manifest(manifest_path)
    if manifest.get("kind")!="production-plutus-v2-conformance" or manifest.get("implementation")!="cardano-node": raise BuildContractError("wrong adapter manifest")
    if manifest["source"]["revision"]!=REVISION: raise BuildContractError("wrong source revision")
    verified=common.verify_adapter_set(manifest_path.parent,manifest)
    source=exact_builder.clone_exact_source(source_repository,output_dir/"work"/"source",REVISION)
    stage=source/"dwarf-conformance-adapters"/"cardano-plutus"
    for item in verified["files"]:
        rel=Path(item["path"]).relative_to("plutus"); dest=stage/rel
        dest.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(manifest_path.parent/item["path"],dest)
    env=dict(os.environ); env.update(manifest["build"]["environment"])
    command=list(manifest["build"]["command"]); command[0]=str(cabal_binary); command[command.index("ghc-9.6.7")]=str(ghc_binary)
    log=output_dir/"logs"/"cabal-build.log"; exact_builder.run_logged(command,cwd=source,log_path=log,env=env)
    query=list(manifest["build"]["binary_query"]); query[0]=str(cabal_binary); query[query.index("ghc-9.6.7")]=str(ghc_binary)
    executable=Path(common._run_capture(query,cwd=source,env=env)).resolve()
    result={"schema_version":1,"created_at":datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z"),
      "kind":manifest["kind"],"implementation":"cardano-node","source_revision":REVISION,"source_release":manifest["source"]["release"],
      "measurement_boundary":manifest["measurement_boundary"],"production_entrypoint":manifest["production_entrypoint"],
      "toolchain":{"ghc":common._tool_version(ghc_binary),"cabal":common._tool_version(cabal_binary)},
      "adapter_set_sha256":verified["adapter_set_sha256"],"adapter_files":verified["files"],"build_command":command,
      "build_log_sha256":exact_builder.sha256_file(log),"executable":str(executable),"executable_sha256":exact_builder.sha256_file(executable)}
    evidence=output_dir/"evidence"/"build-result.json"; evidence.parent.mkdir(parents=True,exist_ok=True); evidence.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    result["build_result_sha256"]=exact_builder.sha256_file(evidence)
    record=registry_root/"cardano-node"/"plutus-conformance"/REVISION/f"{verified['adapter_set_sha256']}.json"; record.parent.mkdir(parents=True,exist_ok=True); record.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n"); result["record_path"]=str(record)
    return result

def main(argv=None):
    p=argparse.ArgumentParser(); p.add_argument("--manifest",type=Path,default=DEFAULT_MANIFEST); p.add_argument("--source-repository",default="https://github.com/IntersectMBO/cardano-node.git"); p.add_argument("--output-dir",type=Path,required=True); p.add_argument("--target-registry",type=Path,default=common._registry_root()); p.add_argument("--cabal",type=Path,default=Path.home() / ".ghcup/bin/cabal-3.16.0.0"); p.add_argument("--ghc",type=Path,default=Path.home() / ".ghcup/bin/ghc-9.6.7")
    a=p.parse_args(argv)
    try: result=build_adapter(manifest_path=a.manifest.resolve(),source_repository=a.source_repository,output_dir=a.output_dir.resolve(),registry_root=a.target_registry.resolve(),cabal_binary=a.cabal.resolve(),ghc_binary=a.ghc.resolve())
    except BuildContractError as e: print(f"error: {e}",file=sys.stderr); return 2
    print(json.dumps(result,indent=2,sort_keys=True)); return 0
if __name__=="__main__": raise SystemExit(main())
