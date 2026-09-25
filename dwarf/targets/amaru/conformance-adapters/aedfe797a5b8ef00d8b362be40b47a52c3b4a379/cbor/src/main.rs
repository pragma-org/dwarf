use amaru_kernel::{PlutusData, from_cbor_no_leftovers, to_cbor};
use serde_json::json;
use std::io::{self, Read};
use std::process::ExitCode;
use std::time::Instant;

const SOURCE_REVISION: &str = "aedfe797a5b8ef00d8b362be40b47a52c3b4a379";

fn hex(bytes: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        output.push(DIGITS[(byte >> 4) as usize] as char);
        output.push(DIGITS[(byte & 0x0f) as usize] as char);
    }
    output
}

fn main() -> ExitCode {
    let mut input = Vec::new();
    if io::stdin().read_to_end(&mut input).is_err() {
        return ExitCode::from(2);
    }
    let started = Instant::now();
    let decoded = from_cbor_no_leftovers::<PlutusData>(&input);
    let elapsed_nanos = started.elapsed().as_nanos() as u64;
    let mut result = json!({
        "schema_version": "v1",
        "implementation": "amaru",
        "source_revision": SOURCE_REVISION,
        "boundary": "production-codec-only",
        "elapsed_nanos": elapsed_nanos,
        "elapsed_micros": elapsed_nanos / 1_000,
    });
    let exit = match decoded {
        Ok(value) => {
            let first = to_cbor(&value);
            match from_cbor_no_leftovers::<PlutusData>(&first) {
                Ok(second_value) => {
                    let second = to_cbor(&second_value);
                    result["outcome"] = json!("accepted");
                    result["first_encode_hex"] = json!(hex(&first));
                    result["second_encode_hex"] = json!(hex(&second));
                    result["second_decode_outcome"] = json!("accepted");
                    ExitCode::SUCCESS
                }
                Err(error) => {
                    result["outcome"] = json!("accepted");
                    result["first_encode_hex"] = json!(hex(&first));
                    result["second_decode_outcome"] = json!("rejected");
                    result["roundtrip_error"] = json!(error.to_string());
                    ExitCode::SUCCESS
                }
            }
        }
        Err(error) => {
            result["outcome"] = json!("rejected");
            result["error_class"] = json!("decode-error");
            result["error"] = json!(error.to_string());
            ExitCode::from(1)
        }
    };
    println!("{}", result);
    exit
}
