use std::{env, process};

use amaru_kernel::cardano::network_block::CONWAY_BLOCK;
use pallas_network::facades::PeerServer;
use tokio::net::TcpListener;

fn usage() -> &'static str {
    "usage: dwarf-amaru-runtime-blockfetch-stale-server <bind-address> <network-magic>"
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
    if args.len() != 3 {
        return Err(usage().to_string());
    }

    let bind_address = &args[1];
    let network_magic = args[2]
        .parse::<u64>()
        .map_err(|_| "network-magic must be a non-negative integer".to_string())?;

    let listener = TcpListener::bind(bind_address)
        .await
        .map_err(|e| format!("bind failed: {e}"))?;

    let mut peer = PeerServer::accept(&listener, network_magic)
        .await
        .map_err(|e| format!("accept failed: {e}"))?;

    let request = peer
        .blockfetch()
        .recv_while_idle()
        .await
        .map_err(|e| format!("recv request failed: {e}"))?;

    let Some(request) = request else {
        peer.abort().await;
        return Err("client closed without requesting a range".to_string());
    };

    peer.blockfetch()
        .send_block_range(vec![CONWAY_BLOCK.clone()])
        .await
        .map_err(|e| format!("send stale block failed: {e}"))?;

    println!("OK stale_response_sent request={:?} block_count=1", request.0);

    let done = peer
        .blockfetch()
        .recv_while_idle()
        .await
        .map_err(|e| format!("recv done failed: {e}"))?;
    if done.is_some() {
        return Err("client issued a new blockfetch request instead of closing".to_string());
    }

    peer.abort().await;
    Ok(())
}
