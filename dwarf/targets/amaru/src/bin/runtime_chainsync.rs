//! Node-to-node chain-sync header fetch for runtime checks.
//!
//! Replaces the Amaru CLI's removed `fetch-chain-headers` command with a plain pallas-network
//! client, so the check works for every Amaru and cardano-node release that speaks N2N chain-sync.
//! Writes each received header as `header.<index>.cbor` into the headers directory and prints one
//! JSON summary line. Exit 0 on success, 1 with stderr "ERR ..." on failure.
use std::{env, fs, path::PathBuf, process, time::Duration};

use pallas_network::{
    facades::PeerClient,
    miniprotocols::{chainsync::NextResponse, Point},
};

fn parse_point(raw: &str) -> Result<Point, String> {
    if raw.eq_ignore_ascii_case("origin") {
        return Ok(Point::Origin);
    }
    let (slot_raw, hash_raw) = raw
        .split_once('.')
        .ok_or_else(|| "point must be SLOT.HEXHASH or 'origin'".to_string())?;
    let slot = slot_raw
        .parse::<u64>()
        .map_err(|_| "point slot must be a non-negative integer".to_string())?;
    let hash = hex::decode(hash_raw).map_err(|e| format!("invalid point hash hex: {e}"))?;
    if hash.len() != 32 {
        return Err(format!("point hash must be 32 bytes, got {}", hash.len()));
    }
    Ok(Point::Specific(slot, hash))
}

fn usage() -> &'static str {
    "usage: dwarf-amaru-runtime-chainsync <peer-address> <network-magic> <parent-point> <headers-dir> [max-headers]"
}

#[tokio::main]
async fn main() {
    if let Err(err) = run().await {
        eprintln!("ERR {err}");
        process::exit(1);
    }
}

async fn run() -> Result<(), String> {
    let args: Vec<String> = env::args().collect();
    if args.len() < 5 || args.len() > 6 {
        return Err(usage().to_string());
    }
    let peer = &args[1];
    let magic = args[2].parse::<u64>().map_err(|_| "network magic must be an integer".to_string())?;
    let parent = parse_point(&args[3])?;
    let headers_dir = PathBuf::from(&args[4]);
    let max_headers = match args.get(5) {
        Some(raw) => raw.parse::<usize>().map_err(|_| "max-headers must be an integer".to_string())?,
        None => 10,
    };
    fs::create_dir_all(&headers_dir).map_err(|e| format!("cannot create headers dir: {e}"))?;

    let mut client = PeerClient::connect(peer.as_str(), magic)
        .await
        .map_err(|e| format!("connect to {peer} failed: {e}"))?;
    let chainsync = client.chainsync();
    let (intersection, tip) = chainsync
        .find_intersect(vec![parent.clone()])
        .await
        .map_err(|e| format!("find_intersect failed: {e}"))?;
    if intersection.is_none() {
        return Err(format!("peer has no intersection with {:?} (tip {:?})", parent, tip.0));
    }

    let mut written = 0usize;
    let mut rollbacks = 0usize;
    while written < max_headers {
        let next = if chainsync.has_agency() {
            tokio::time::timeout(Duration::from_secs(5), chainsync.request_next())
                .await
                .map_err(|_| "request_next timed out".to_string())?
        } else {
            tokio::time::timeout(Duration::from_secs(5), chainsync.recv_while_must_reply())
                .await
                .map_err(|_| "await reply timed out".to_string())?
        }
        .map_err(|e| format!("chain-sync failed: {e}"))?;
        match next {
            NextResponse::RollForward(header, _tip) => {
                let path = headers_dir.join(format!("header.{written:04}.cbor"));
                fs::write(&path, &header.cbor).map_err(|e| format!("cannot write {}: {e}", path.display()))?;
                written += 1;
            }
            NextResponse::RollBackward(_point, _tip) => rollbacks += 1,
            NextResponse::Await => break,
        }
    }
    println!(
        "{{\"peer\":\"{peer}\",\"headers\":{written},\"rollbacks\":{rollbacks},\"tip_slot\":{}}}",
        match tip.0 { Point::Specific(slot, _) => slot, Point::Origin => 0 }
    );
    if written == 0 {
        return Err("peer returned no headers after the parent point".to_string());
    }
    Ok(())
}
