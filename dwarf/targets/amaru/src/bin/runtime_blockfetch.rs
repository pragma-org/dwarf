use std::{env, process};

use pallas_network::{
    facades::PeerClient,
    miniprotocols::{Point, blockfetch},
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
    "usage: dwarf-amaru-runtime-blockfetch <peer-address> <network-magic> <from-point> <to-point> [expect-count] [expect-point]"
}

/// The returned block's point, read straight from its CBOR so it does not depend on any one
/// Amaru kernel API: a network block is `[era, block]` (optionally inside tag 24 bytes), a block
/// is `[header, ...]`, a header is `[header_body, signature]` with `header_body[1]` the slot, and
/// the block hash is BLAKE2b-256 of the header bytes (Shelley and later).
fn decode_returned_block_point(body: &[u8]) -> Result<Point, String> {
    let err = |e: minicbor::decode::Error| format!("returned block decode failed: {e}");
    let mut d = minicbor::Decoder::new(body);
    if d.datatype().map_err(err)? == minicbor::data::Type::Tag {
        d.tag().map_err(err)?;
        let inner = d.bytes().map_err(err)?;
        return decode_returned_block_point(inner);
    }
    d.array().map_err(err)?;
    let _era = d.u16().map_err(err)?;
    d.array().map_err(err)?;
    let start = d.position();
    d.skip().map_err(err)?;
    let header = &body[start..d.position()];
    let mut h = minicbor::Decoder::new(header);
    h.array().map_err(err)?;
    h.array().map_err(err)?;
    let _block_number = h.u64().map_err(err)?;
    let slot = h.u64().map_err(err)?;
    let hash = pallas_crypto::hash::Hasher::<256>::hash(header);
    Ok(Point::Specific(slot, hash.to_vec()))
}

fn validate_expected_point(body: &[u8], expected: &Point) -> Result<(), String> {
    let returned = decode_returned_block_point(body)?;
    if &returned == expected {
        Ok(())
    } else {
        Err(format!(
            "returned block point mismatch: expected {:?}, got {:?}",
            expected, returned
        ))
    }
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
    if args.len() < 5 || args.len() > 7 {
        return Err(usage().to_string());
    }

    let peer_address = &args[1];
    let network_magic = args[2]
        .parse::<u64>()
        .map_err(|_| "network-magic must be a non-negative integer".to_string())?;
    let from = parse_point(&args[3])?;
    let to = parse_point(&args[4])?;
    let expect_count = if args.len() >= 6 {
        Some(
            args[5]
                .parse::<usize>()
                .map_err(|_| "expect-count must be a non-negative integer".to_string())?,
        )
    } else {
        None
    };
    let expect_point = if args.len() == 7 {
        Some(parse_point(&args[6])?)
    } else {
        None
    };

    let mut peer = PeerClient::connect(peer_address, network_magic)
        .await
        .map_err(|e| format!("connect failed: {e}"))?;

    let blocks_result = if expect_point.is_some() && from == to {
        let single_result = peer
            .blockfetch
            .fetch_single(from.clone())
            .await
            .map_err(|e: blockfetch::ClientError| format!("blockfetch failed: {e}"));
        if single_result.is_ok() {
            peer.blockfetch
                .send_done()
                .await
                .map_err(|e: blockfetch::ClientError| format!("blockfetch send_done failed: {e}"))?;
        }
        single_result.map(|body| vec![body])
    } else {
        peer.blockfetch
            .fetch_range((from.clone(), to.clone()))
            .await
            .map_err(|e: blockfetch::ClientError| format!("blockfetch failed: {e}"))
    };

    let blocks = blocks_result?;

    if let Some(expected) = expect_count {
        if blocks.len() != expected {
            return Err(format!("expected {expected} blocks, got {}", blocks.len()));
        }
    }

    if let Some(expected_point) = &expect_point {
        if blocks.len() != 1 {
            return Err(format!(
                "expect-point requires exactly 1 returned block, got {}",
                blocks.len()
            ));
        }
        validate_expected_point(&blocks[0], expected_point)?;
    }

    println!(
        "OK peer={} from={:?} to={:?} block_count={}",
        peer_address,
        from,
        to,
        blocks.len()
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use amaru_kernel::cardano::network_block::CONWAY_BLOCK;
    use pallas_network::facades::PeerServer;
    use tokio::{net::TcpListener, time::{timeout, Duration}};

    #[test]
    fn decode_returned_block_point_extracts_conway_fixture_tip() {
        let point = decode_returned_block_point(&CONWAY_BLOCK).expect("fixture should decode");
        match point {
            Point::Specific(slot, hash) => {
                assert_eq!(slot, 27953668);
                assert_eq!(
                    hex::encode(hash),
                    "e58480b4e8fac2c75d2322543fcd2473fae351346e90a13b2233337aae34c529"
                );
            }
            Point::Origin => panic!("expected a specific point"),
        }
    }

    #[test]
    fn validate_expected_point_rejects_stale_block_body() {
        let expected = parse_point(
            "1385973.79674b4e0518f10e5e9db7111110ceb8a4dd6e5091597a633c08ebcd1be11c55",
        )
        .expect("expected point should parse");
        let err = validate_expected_point(&CONWAY_BLOCK, &expected).expect_err("mismatch must fail");
        assert!(err.contains("returned block point mismatch"), "{err}");
    }

    #[tokio::test]
    async fn stale_server_path_completes_and_returns_point_mismatch() {
        let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind should work");
        let addr = listener.local_addr().expect("local addr should be available");
        let expected = parse_point(
            "1385973.79674b4e0518f10e5e9db7111110ceb8a4dd6e5091597a633c08ebcd1be11c55",
        )
        .expect("expected point should parse");

        let server = tokio::spawn(async move {
            let mut peer = PeerServer::accept(&listener, 42).await.expect("accept should work");
            let request = peer
                .blockfetch()
                .recv_while_idle()
                .await
                .expect("request should decode");
            assert!(request.is_some(), "client should issue a blockfetch request");
            peer.blockfetch()
                .send_block_range(vec![CONWAY_BLOCK.clone()])
                .await
                .expect("server should send stale block");
            let done = peer
                .blockfetch()
                .recv_while_idle()
                .await
                .expect("client should send done");
            assert!(done.is_none(), "client should send ClientDone after single fetch");
            peer.abort().await;
        });

        let client = async {
            let mut peer = PeerClient::connect(addr, 42)
                .await
                .expect("client should connect");
            let body = peer
                .blockfetch
                .fetch_single(expected.clone())
                .await
                .expect("fetch_single should complete");
            peer.blockfetch
                .send_done()
                .await
                .expect("send_done should complete");
            validate_expected_point(&body, &expected).expect_err("stale response must mismatch");
        };

        timeout(Duration::from_secs(3), client)
            .await
            .expect("client must not hang");
        timeout(Duration::from_secs(3), server)
            .await
            .expect("server task must not hang")
            .expect("server task must succeed");
    }
}
