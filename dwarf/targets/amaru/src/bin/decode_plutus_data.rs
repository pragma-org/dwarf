//! Decode raw Plutus data using Amaru's typed kernel decoder.

use amaru_kernel::{PlutusData, from_cbor_no_leftovers};
use std::io::{self, Read};
use std::process::ExitCode;

fn main() -> ExitCode {
    let mut buf = Vec::new();
    if io::stdin().read_to_end(&mut buf).is_err() {
        println!("ERR stdin read failed");
        return ExitCode::from(1);
    }
    match from_cbor_no_leftovers::<PlutusData>(&buf) {
        Ok(_) => {
            println!("OK");
            ExitCode::SUCCESS
        }
        Err(error) => {
            println!("ERR {}", error);
            ExitCode::from(1)
        }
    }
}
