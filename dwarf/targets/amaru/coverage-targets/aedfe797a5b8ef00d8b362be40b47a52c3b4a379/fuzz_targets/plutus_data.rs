#![no_main]

use amaru_kernel::{from_cbor_no_leftovers, PlutusData};
use libfuzzer_sys::fuzz_target;

fuzz_target!(|data: &[u8]| {
    let _ = from_cbor_no_leftovers::<PlutusData>(data);
});
