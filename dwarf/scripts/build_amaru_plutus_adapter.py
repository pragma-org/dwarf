#!/usr/bin/env python3
"""Build the revision-locked Amaru Plutus V2 conformance adapter."""
from __future__ import annotations
import argparse, hashlib, json, os, shutil, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
from scripts import build_amaru_measurement_target as exact_builder

REVISION = "b159172f25a9c389f82f20bca4f15e3032791638"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "targets" / "amaru" / "conformance-adapters" / REVISION / "plutus-manifest.json"
BuildContractError = exact_builder.BuildContractError

def _verify(manifest_path, manifest):
    rows=[]; files=[]
    for item in manifest.get("adapter_files") or []:
        rel=exact_builder._safe_relative_path(item["path"], field="adapter_files.path")
        digest=exact_builder.sha256_file(manifest_path.parent / rel)
        if digest != item["sha256"]: raise BuildContractError(f"adapter file digest mismatch: {rel}")
        rows.append((rel.as_posix(),digest)); files.append({"path":rel.as_posix(),"sha256":digest})
    digest=hashlib.sha256("".join(f"{d}  {p}\n" for p,d in rows).encode()).hexdigest()
    if not files or digest != manifest.get("adapter_set_sha256"): raise BuildContractError("adapter-set digest mismatch")
    return files,digest

def build_adapter(*, manifest_path, source_repository, output_dir, registry_root, cargo_binary, rustc_binary):
    if output_dir.exists(): raise BuildContractError(f"output directory already exists: {output_dir}")
    manifest=exact_builder.load_manifest(manifest_path)
    if manifest.get("kind")!="production-plutus-v2-conformance" or manifest.get("implementation")!="amaru": raise BuildContractError("wrong adapter manifest")
    exact_builder.require_audited_revision(manifest_path, manifest)
    files,set_digest=_verify(manifest_path,manifest)
    source=exact_builder.clone_exact_source(source_repository,output_dir/"work"/"source",REVISION)
    stage=source/"dwarf-conformance-adapters"/"amaru-plutus"
    for item in files:
        rel=Path(item["path"]).relative_to("plutus"); dest=stage/rel
        dest.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(manifest_path.parent/item["path"],dest)
    env=dict(os.environ); env.update(manifest["build"]["environment"])
    command=list(manifest["build"]["command"]); command[0]=str(cargo_binary)
    log=output_dir/"logs"/"cargo-build.log"; exact_builder.run_logged(command,cwd=source,log_path=log,env=env)
    executable=stage/"target"/"release"/"dwarf-amaru-plutus-conformance"
    if not executable.is_file(): raise BuildContractError("Amaru Plutus adapter executable was not produced")
    result={"schema_version":1,"created_at":datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z"),
      "kind":manifest["kind"],"implementation":"amaru","source_revision":REVISION,"source_release":manifest["source"]["release"],
      "measurement_boundary":manifest["measurement_boundary"],"production_entrypoint":manifest["production_entrypoint"],
      "toolchain":{"rustc":subprocess.check_output([str(rustc_binary),f"+{manifest['toolchain']}","--version"],text=True).strip(),
      "cargo":subprocess.check_output([str(cargo_binary),f"+{manifest['toolchain']}","--version"],text=True).strip()},
      "adapter_set_sha256":set_digest,"adapter_files":files,"build_command":command,
      "build_log_sha256":exact_builder.sha256_file(log),"executable":str(executable.resolve()),"executable_sha256":exact_builder.sha256_file(executable)}
    evidence=output_dir/"evidence"/"build-result.json"; evidence.parent.mkdir(parents=True)
    evidence.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    result["build_result_sha256"]=exact_builder.sha256_file(evidence)
    record=registry_root/"amaru"/"plutus-conformance"/REVISION/f"{set_digest}.json"; record.parent.mkdir(parents=True,exist_ok=True)
    record.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n"); result["record_path"]=str(record)
    return result

def main(argv=None):
    p=argparse.ArgumentParser(); p.add_argument("--manifest",type=Path,default=DEFAULT_MANIFEST); p.add_argument("--source-repository",default="https://github.com/pragma-org/amaru.git"); p.add_argument("--output-dir",type=Path,required=True)
    p.add_argument("--cargo",type=Path,default=Path("/home/nigel/.cargo/bin/cargo")); p.add_argument("--rustc",type=Path,default=Path("/home/nigel/.cargo/bin/rustc")); p.add_argument("--target-registry",type=Path,default=exact_builder.default_target_registry_root())
    a=p.parse_args(argv)
    try: result=build_adapter(manifest_path=a.manifest.resolve(),source_repository=a.source_repository,output_dir=a.output_dir.resolve(),registry_root=a.target_registry.resolve(),cargo_binary=a.cargo.absolute(),rustc_binary=a.rustc.absolute())
    except BuildContractError as e: print(f"error: {e}",file=sys.stderr); return 2
    print(json.dumps(result,indent=2,sort_keys=True)); return 0
if __name__=="__main__": raise SystemExit(main())
