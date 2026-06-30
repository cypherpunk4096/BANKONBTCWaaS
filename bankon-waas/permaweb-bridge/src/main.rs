//! permaweb-bridge — BANKON's web2 → web3 bridge.
//!
//! Pipeline:
//!   ragest (web2 ingest URL) → PostgreSQL pgvectorscale → [THIS] → Arweave permaweb
//!
//! pgvectorscale is the bridge source: this reads the `bitcoin_nodes` table (geo + network
//! profile + vector(8) embedding), builds a content-addressed JSON snapshot, and publishes it
//! to the permaweb so the dataset RAGE retrieves over also has an immutable, permanent record.
//!
//! Config (env):
//!   DATABASE_URL          postgres://…              (the pgvectorscale store; required)
//!   PERMAWEB_GATEWAY      https://arweave.net        (gateway/bundler upload endpoint; default arweave.net)
//!   PERMAWEB_KEY_FILE     /path/to/arweave.json      (JWK wallet that signs the data item; user-supplied)
//!   BRIDGE_LIMIT          5000                       (max rows per snapshot)
//!   BRIDGE_INTERVAL_SECS  (unset = one-shot; set = loop every N seconds)
//!
//! Build & run:
//!   cargo run --release       # one-shot snapshot → permaweb
//!
//! NOTE: Arweave data-item SIGNING requires the JWK wallet and the bundler signing scheme; that
//! step is the proprietary/key-bearing part and is isolated in `publish()` behind PERMAWEB_KEY_FILE.
//! Without a key this runs as a dry bridge: it builds + content-addresses the snapshot and reports
//! what WOULD be published, so the rest of the pipeline is verifiable end-to-end without a wallet.

mod arweave;

use anyhow::{Context, Result};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::env;
use std::time::Duration;

#[derive(Serialize)]
struct NodeRow {
    address: String,
    port: i32,
    network: Option<String>,
    country_code: Option<String>,
    asn: Option<i64>,
    asn_org: Option<String>,
    user_agent: Option<String>,
    services: Option<i64>,
    reachable_pct: Option<f64>,
    embedding: Option<String>, // pgvector text form, e.g. "[0.1,0.2,…]"
}

#[derive(Serialize)]
struct Snapshot {
    source: &'static str,   // "bankon"
    kind: &'static str,     // "btc-nodes"
    taken_at: String,       // RFC3339
    count: usize,
    nodes: Vec<NodeRow>,
}

#[tokio::main]
async fn main() -> Result<()> {
    let verify_only = env::args().any(|a| a == "--verify");
    let db_url = env::var("DATABASE_URL").context("DATABASE_URL not set (the pgvectorscale store)")?;
    let limit: i64 = env::var("BRIDGE_LIMIT").ok().and_then(|v| v.parse().ok()).unwrap_or(5000);
    let interval = env::var("BRIDGE_INTERVAL_SECS").ok().and_then(|v| v.parse::<u64>().ok());

    // --verify: gather a snapshot, sign it, verify the signature LOCALLY — never uploads.
    if verify_only {
        let key = env::var("PERMAWEB_KEY_FILE")
            .context("--verify needs PERMAWEB_KEY_FILE (the JWK to sign+verify with)")?;
        let (body, content, count) = gather(&db_url, limit).await?;
        let signer = arweave::Signer::from_jwk_file(&key)?;
        let (item, item_id) = arweave::build_verified_data_item(&signer, &body, &snapshot_tags(&content))?;
        println!("✓ VERIFY PASS — RSA-PSS signature valid, ANS-104 item well-formed");
        println!("  snapshot : {count} nodes, {} bytes, content-id {content}", body.len());
        println!("  data item: id {item_id}, {} bytes (NOT uploaded)", item.len());
        return Ok(());
    }

    loop {
        match run_once(&db_url, limit).await {
            Ok((id, n)) => println!("bridge: snapshot {id} ({n} nodes) ready/published"),
            Err(e) => eprintln!("bridge error: {e:#}"),
        }
        match interval {
            Some(secs) => tokio::time::sleep(Duration::from_secs(secs)).await,
            None => break,
        }
    }
    Ok(())
}

/// Tags applied to the permaweb data item (make the snapshot discoverable).
fn snapshot_tags(content_id: &str) -> Vec<(String, String)> {
    vec![
        ("App-Name".to_string(), "BANKON".to_string()),
        ("App-Version".to_string(), env!("CARGO_PKG_VERSION").to_string()),
        ("Content-Type".to_string(), "application/json".to_string()),
        ("Content-Id".to_string(), content_id.to_string()), // sha256 of the snapshot body
        ("Kind".to_string(), "btc-nodes".to_string()),
    ]
}

/// Gather a snapshot from pgvectorscale. Returns (json body, content-id, node count).
async fn gather(db_url: &str, limit: i64) -> Result<(Vec<u8>, String, usize)> {
    let nodes = read_pgvectorscale(db_url, limit).await?;
    let count = nodes.len();
    let snapshot = Snapshot {
        source: "bankon",
        kind: "btc-nodes",
        taken_at: chrono::Utc::now().to_rfc3339(),
        count,
        nodes,
    };
    let body = serde_json::to_vec(&snapshot)?;
    let id = content_id(&body); // content-addressed (sha256) — the permaweb record key
    Ok((body, id, count))
}

async fn run_once(db_url: &str, limit: i64) -> Result<(String, usize)> {
    let (body, id, count) = gather(db_url, limit).await?;
    publish(&id, &body).await?;
    Ok((id, count))
}

/// Read the node-intelligence rows from pgvectorscale (the bridge source).
async fn read_pgvectorscale(db_url: &str, limit: i64) -> Result<Vec<NodeRow>> {
    let (client, conn) = tokio_postgres::connect(db_url, tokio_postgres::NoTls)
        .await
        .context("connect to pgvectorscale Postgres")?;
    tokio::spawn(async move {
        if let Err(e) = conn.await {
            eprintln!("pg connection error: {e}");
        }
    });
    // Join the uptime view for reachable_pct; embedding cast to text for portable serialization.
    let rows = client
        .query(
            "SELECT n.address, n.port, n.network, n.country_code, n.asn, n.asn_org, \
                    n.user_agent, n.services, u.reachable_pct, n.embedding::text \
             FROM bitcoin_nodes n \
             LEFT JOIN bitcoin_node_uptime u ON u.address = n.address AND u.port = n.port \
             ORDER BY n.last_seen DESC NULLS LAST LIMIT $1",
            &[&limit],
        )
        .await
        .context("query bitcoin_nodes")?;
    Ok(rows
        .iter()
        .map(|r| NodeRow {
            address: r.get(0),
            port: r.get(1),
            network: r.get(2),
            country_code: r.get(3),
            asn: r.get(4),
            asn_org: r.get(5),
            user_agent: r.get(6),
            services: r.get(7),
            reachable_pct: r.get::<_, Option<f64>>(8),
            embedding: r.get(9),
        })
        .collect())
}

/// sha256 content address — deterministic key so a snapshot is its own permaweb identifier.
fn content_id(body: &[u8]) -> String {
    let mut h = Sha256::new();
    h.update(body);
    hex::encode(h.finalize())
}

/// Publish to the permaweb. With a JWK wallet (PERMAWEB_KEY_FILE) this signs an ANS-104 data item
/// (RSA-PSS) and uploads it to a bundler; without one it is a dry bridge (content-addresses +
/// reports), so the pipeline is verifiable end-to-end before a wallet is wired in.
async fn publish(id: &str, body: &[u8]) -> Result<()> {
    let gateway = env::var("PERMAWEB_GATEWAY").unwrap_or_else(|_| "https://arweave.net".into());
    match env::var("PERMAWEB_KEY_FILE") {
        Err(_) => {
            println!(
                "bridge(dry): {} bytes, content-id {id} → would publish to {gateway} \
                 (set PERMAWEB_KEY_FILE to sign + upload an Arweave data item)",
                body.len()
            );
            Ok(())
        }
        Ok(key_file) => {
            // Sign an ANS-104 data item (RSA-PSS), VERIFY it locally, then POST to the bundler.
            // Key-bearing step, isolated behind PERMAWEB_KEY_FILE.
            let signer = arweave::Signer::from_jwk_file(&key_file)?;
            let (item, item_id) = arweave::build_verified_data_item(&signer, body, &snapshot_tags(id))?;
            let resp = reqwest::Client::new()
                .post(format!("{gateway}/tx"))
                .header("content-type", "application/octet-stream")
                .body(item)
                .send()
                .await
                .context("bundler upload")?;
            let status = resp.status();
            println!("bridge: signed data item {item_id} ({} bytes) → {gateway}/tx (HTTP {status})", body.len());
            if !status.is_success() {
                anyhow::bail!("bundler rejected: HTTP {status} — {}", resp.text().await.unwrap_or_default());
            }
            Ok(())
        }
    }
}
