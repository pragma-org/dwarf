//! amaru-cbor-decode-block-header shim.

use amaru_kernel::{Header, from_cbor_no_leftovers};
use std::io::{self, Read};
use std::process::ExitCode;

fn main() -> ExitCode {
    let mut buf = Vec::new();
    if io::stdin().read_to_end(&mut buf).is_err() {
        println!("ERR stdin read failed");
        return ExitCode::from(1);
    }
    match from_cbor_no_leftovers::<Header>(&buf) {
        Ok(_header) => {
            println!("OK");
            ExitCode::SUCCESS
        }
        Err(e) => {
            println!("ERR {}", e);
            ExitCode::from(1)
        }
    }
}
