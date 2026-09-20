use std::{io::{self, Read}, process::ExitCode, time::Instant};

use amaru_kernel::{PlutusVersion, protocol_version::PROTOCOL_VERSION_10};
use amaru_uplc::{
    arena::Arena, binder::DeBruijn, constant::Constant, data::PlutusData, flat,
    machine::{CostModel, ExBudget}, term::Term,
};
use serde::{Deserialize, Serialize};

#[derive(Deserialize)]
struct Request {
    schema_version: String,
    plutus_version: String,
    protocol_version: u64,
    script_cbor_hex: String,
    cost_model: Vec<i64>,
    cost_model_sha256: String,
}

#[derive(Serialize)]
struct Response {
    schema_version: &'static str,
    implementation: &'static str,
    source_revision: &'static str,
    boundary: &'static str,
    plutus_version: &'static str,
    cost_model_sha256: String,
    outcome: &'static str,
    cpu_budget: i64,
    memory_budget: i64,
    elapsed_nanos: u128,
    elapsed_micros: u128,
}

fn run(request: Request) -> Result<Response, String> {
    if request.schema_version != "v1" || request.plutus_version != "v2" || request.protocol_version != 10 {
        return Err("unsupported request identity".to_owned());
    }
    let cbor = hex::decode(&request.script_cbor_hex).map_err(|e| e.to_string())?;
    let mut envelope_decoder = minicbor::Decoder::new(&cbor);
    let script_cbor = envelope_decoder.bytes().map_err(|e| e.to_string())?;
    if envelope_decoder.position() != cbor.len() {
        return Err("script CBOR has trailing bytes".to_owned());
    }
    let mut script_decoder = minicbor::Decoder::new(script_cbor);
    let flat_bytes = script_decoder.bytes().map_err(|e| e.to_string())?;

    let arena = Arena::new();
    let (base, _) = flat::decode::<DeBruijn>(&arena, flat_bytes, PROTOCOL_VERSION_10)
        .map_err(|e| e.to_string())?;
    let unit_data = PlutusData::constr(&arena, 0, &[]);
    let constant = arena.alloc(Constant::Data(unit_data));
    let argument = arena.alloc(Term::Constant(constant));
    let mut program = base;
    for _ in 0..3 {
        program = program.apply(&arena, argument);
    }

    let started = Instant::now();
    let result = program.eval(
        &arena,
        CostModel::new(PlutusVersion::V2, PROTOCOL_VERSION_10, &request.cost_model),
        ExBudget::max(),
    );
    let rejected = matches!(result.term, Err(_) | Ok(Term::Error));
    let elapsed_nanos = started.elapsed().as_nanos();
    let budget = result.info.consumed_budget;
    Ok(Response {
        schema_version: "v1",
        implementation: "amaru",
        source_revision: "b159172f25a9c389f82f20bca4f15e3032791638",
        boundary: "production-plutus-v2-vm-only",
        plutus_version: "v2",
        cost_model_sha256: request.cost_model_sha256,
        outcome: if rejected { "rejected" } else { "accepted" },
        cpu_budget: budget.cpu,
        memory_budget: budget.mem,
        elapsed_nanos,
        elapsed_micros: elapsed_nanos / 1000,
    })
}

fn main() -> ExitCode {
    let mut input = String::new();
    if let Err(error) = io::stdin().read_to_string(&mut input) {
        eprintln!("{error}");
        return ExitCode::from(2);
    }
    match serde_json::from_str::<Request>(&input).map_err(|e| e.to_string()).and_then(run) {
        Ok(response) => {
            println!("{}", serde_json::to_string(&response).expect("response serialization"));
            if response.outcome == "accepted" { ExitCode::SUCCESS } else { ExitCode::from(1) }
        }
        Err(error) => {
            eprintln!("{error}");
            ExitCode::from(2)
        }
    }
}
