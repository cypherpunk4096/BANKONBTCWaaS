//! Minimal ANS-104 data-item signing for Arweave (signature type 1 = RSA-4096 PSS/SHA-256).
//!
//! Implements exactly what arweave-js / arbundles do:
//!   • load the JWK (RSA) wallet            → owner = raw modulus `n` (512 bytes)
//!   • signatureData = deepHash([...])      → Arweave's SHA-384 deep-hash over the item fields
//!   • signature = RSA-PSS(SHA-256, salt 32) over signatureData   (512 bytes)
//!   • id = base64url( SHA-256(signature) )
//!   • serialize the binary data item for upload to a bundler (POST /tx, octet-stream)

use anyhow::{Context, Result};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD as B64, Engine};
use rsa::pss::{Signature, SigningKey, VerifyingKey};
use rsa::signature::{RandomizedSigner, SignatureEncoding, Verifier};
use rsa::{BigUint, RsaPrivateKey};
use serde::Deserialize;
use sha2::{Digest, Sha256, Sha384};

#[derive(Deserialize)]
struct Jwk { n: String, e: String, d: String, p: String, q: String }

pub struct Signer {
    key: RsaPrivateKey,
    owner: Vec<u8>, // raw modulus n — the data-item "owner"
}

impl Signer {
    /// Load an Arweave JWK wallet (the RSA key that signs data items).
    pub fn from_jwk_file(path: &str) -> Result<Self> {
        let txt = std::fs::read_to_string(path).with_context(|| format!("read JWK {path}"))?;
        let j: Jwk = serde_json::from_str(&txt).context("parse JWK (need n,e,d,p,q)")?;
        let bn = |s: &str| -> Result<BigUint> { Ok(BigUint::from_bytes_be(&B64.decode(s)?)) };
        let key = RsaPrivateKey::from_components(bn(&j.n)?, bn(&j.e)?, bn(&j.d)?, vec![bn(&j.p)?, bn(&j.q)?])
            .context("build RSA key from JWK")?;
        Ok(Self { key, owner: B64.decode(&j.n)? })
    }

    /// RSA-PSS(SHA-256, salt 32) over `msg` — matches Arweave's WebCrypto signing.
    fn sign(&self, msg: &[u8]) -> Vec<u8> {
        let sk = SigningKey::<Sha256>::new(self.key.clone()); // default salt len = 32 (SHA-256 size)
        sk.sign_with_rng(&mut rand::thread_rng(), msg).to_vec()
    }

    /// Verify an RSA-PSS(SHA-256) signature over `msg` with this wallet's public key (offline check).
    fn verify(&self, msg: &[u8], sig: &[u8]) -> bool {
        let vk = VerifyingKey::<Sha256>::new(self.key.to_public_key());
        Signature::try_from(sig).map(|s| vk.verify(msg, &s).is_ok()).unwrap_or(false)
    }
}

fn sha384(b: &[u8]) -> [u8; 48] {
    let mut h = Sha384::new();
    h.update(b);
    h.finalize().into()
}

/// Arweave deep hash (SHA-384). Blob: H(H("blob"+len) + H(data)); List: fold H(acc + deepHash(item)).
enum Dh {
    Blob(Vec<u8>),
    List(Vec<Dh>),
}
fn deep_hash(item: &Dh) -> [u8; 48] {
    match item {
        Dh::Blob(data) => {
            let tagged = sha384(format!("blob{}", data.len()).as_bytes());
            let blob = sha384(data);
            sha384(&[tagged.as_slice(), blob.as_slice()].concat())
        }
        Dh::List(items) => {
            let mut acc = sha384(format!("list{}", items.len()).as_bytes());
            for it in items {
                let h = deep_hash(it);
                acc = sha384(&[acc.as_slice(), h.as_slice()].concat());
            }
            acc
        }
    }
}

/// Avro zig-zag varint (Avro `long`).
fn avro_long(n: i64, out: &mut Vec<u8>) {
    let mut zz = ((n << 1) ^ (n >> 63)) as u64;
    loop {
        let mut b = (zz & 0x7f) as u8;
        zz >>= 7;
        if zz != 0 { b |= 0x80; }
        out.push(b);
        if zz == 0 { break; }
    }
}
fn avro_string(s: &str, out: &mut Vec<u8>) {
    avro_long(s.len() as i64, out);
    out.extend_from_slice(s.as_bytes());
}
/// ANS-104 tag serialization (Avro array of {name,value}); empty → empty buffer.
fn serialize_tags(tags: &[(String, String)]) -> Vec<u8> {
    if tags.is_empty() { return Vec::new(); }
    let mut out = Vec::new();
    avro_long(tags.len() as i64, &mut out);
    for (k, v) in tags {
        avro_string(k, &mut out);
        avro_string(v, &mut out);
    }
    avro_long(0, &mut out); // terminating block
    out
}

/// The deep-hash signature target for an ANS-104 item (target & anchor absent → empty blobs).
fn item_deephash(owner: &[u8], raw_tags: &[u8], data: &[u8]) -> [u8; 48] {
    deep_hash(&Dh::List(vec![
        Dh::Blob(b"dataitem".to_vec()),
        Dh::Blob(b"1".to_vec()),            // data-item version
        Dh::Blob(b"1".to_vec()),            // signature type 1 (Arweave RSA)
        Dh::Blob(owner.to_vec()),
        Dh::Blob(Vec::new()),               // target (none)
        Dh::Blob(Vec::new()),               // anchor (none)
        Dh::Blob(raw_tags.to_vec()),
        Dh::Blob(data.to_vec()),
    ]))
}

/// Serialize the ANS-104 binary data item.
fn serialize_item(signature: &[u8], owner: &[u8], ntags: usize, raw_tags: &[u8], data: &[u8]) -> Vec<u8> {
    let mut buf = Vec::with_capacity(1100 + raw_tags.len() + data.len());
    buf.extend_from_slice(&1u16.to_le_bytes());                  // signature type
    buf.extend_from_slice(signature);                           // 512
    buf.extend_from_slice(owner);                              // 512
    buf.push(0);                                                // target presence = 0
    buf.push(0);                                                // anchor presence = 0
    buf.extend_from_slice(&(ntags as u64).to_le_bytes());       // tag count
    buf.extend_from_slice(&(raw_tags.len() as u64).to_le_bytes()); // tag bytes len
    buf.extend_from_slice(raw_tags);
    buf.extend_from_slice(data);
    buf
}

/// Build, sign, AND locally verify the data item before returning — fails if the RSA-PSS signature
/// does not verify against the deep hash. Used by `--verify` and before every real upload.
pub fn build_verified_data_item(signer: &Signer, data: &[u8], tags: &[(String, String)]) -> Result<(Vec<u8>, String)> {
    let raw_tags = serialize_tags(tags);
    let sigdata = item_deephash(&signer.owner, &raw_tags, data);
    let signature = signer.sign(&sigdata);
    if !signer.verify(&sigdata, &signature) {
        anyhow::bail!("data item failed local RSA-PSS verification — refusing to upload");
    }
    let id = B64.encode(Sha256::digest(&signature));
    Ok((serialize_item(&signature, &signer.owner, tags.len(), &raw_tags, data), id))
}

#[cfg(test)]
mod tests {
    use super::*;
    use rsa::RsaPrivateKey;

    fn sha384v(b: &[u8]) -> Vec<u8> {
        let mut h = Sha384::new();
        h.update(b);
        h.finalize().to_vec()
    }

    // deepHash(blob) = SHA384( SHA384("blob"+len) || SHA384(data) ) — re-derived from the Arweave spec.
    #[test]
    fn deephash_blob_matches_spec() {
        let data = b"BANKON";
        let tagged = sha384v(format!("blob{}", data.len()).as_bytes());
        let blob = sha384v(data);
        let expect = sha384v(&[tagged, blob].concat());
        assert_eq!(deep_hash(&Dh::Blob(data.to_vec())).to_vec(), expect);
    }

    // deepHash(list) folds: acc = SHA384("list"+n); acc = SHA384(acc || deepHash(item)).
    #[test]
    fn deephash_list_matches_spec() {
        let a = b"alpha".to_vec();
        let b = b"beta".to_vec();
        let mut acc = sha384v(b"list2");
        for d in [&a, &b] {
            let h = deep_hash(&Dh::Blob(d.clone()));
            acc = sha384v(&[acc.as_slice(), h.as_slice()].concat());
        }
        let got = deep_hash(&Dh::List(vec![Dh::Blob(a), Dh::Blob(b)]));
        assert_eq!(got.to_vec(), acc);
    }

    // Fixed regression vector — locks deep_hash output so a refactor can't silently change it.
    #[test]
    fn deephash_known_vector() {
        let got = hex::encode(deep_hash(&Dh::Blob(b"BANKON".to_vec())));
        assert_eq!(got, "069ca5c38c090530f12f9c415ac60f9e4f51924ff039d591552103c13bed668d1ff1d02b85579f2476a6e6549f78095e",
            "deep_hash(blob \"BANKON\") regression vector");
    }

    #[test]
    fn deephash_deterministic_and_48_bytes() {
        let h = deep_hash(&Dh::Blob(b"x".to_vec()));
        assert_eq!(h.len(), 48);
        assert_eq!(h, deep_hash(&Dh::Blob(b"x".to_vec())));
        assert_ne!(h, deep_hash(&Dh::Blob(b"y".to_vec())));
    }

    // Avro zig-zag varint (Avro `long`).
    #[test]
    fn avro_long_zigzag() {
        let f = |n| { let mut v = vec![]; avro_long(n, &mut v); v };
        assert_eq!(f(0), vec![0x00]);
        assert_eq!(f(1), vec![0x02]);
        assert_eq!(f(-1), vec![0x01]);
        assert_eq!(f(64), vec![0x80, 0x01]);
    }

    // ANS-104 tag block (Avro) — exact bytes for one tag.
    #[test]
    fn avro_tags_exact_bytes() {
        let got = serialize_tags(&[("App-Name".to_string(), "BANKON".to_string())]);
        let mut expect = vec![0x02u8]; // count 1 (zig-zag 2)
        expect.push(0x10); // "App-Name" len 8 (zig-zag 16)
        expect.extend_from_slice(b"App-Name");
        expect.push(0x0c); // "BANKON" len 6 (zig-zag 12)
        expect.extend_from_slice(b"BANKON");
        expect.push(0x00); // terminating block
        assert_eq!(got, expect);
        assert!(serialize_tags(&[]).is_empty());
    }

    // ANS-104 binary layout — fixed offsets for sig-type / signature / owner / presence / tags / data.
    #[test]
    fn item_layout_offsets() {
        let sig = vec![7u8; 512];
        let owner = vec![9u8; 512];
        let raw_tags = serialize_tags(&[("k".to_string(), "v".to_string())]);
        let data = b"hello";
        let item = serialize_item(&sig, &owner, 1, &raw_tags, data);
        assert_eq!(&item[0..2], &1u16.to_le_bytes());
        assert_eq!(&item[2..514], &sig[..]);
        assert_eq!(&item[514..1026], &owner[..]);
        assert_eq!(item[1026], 0); // target absent
        assert_eq!(item[1027], 0); // anchor absent
        assert_eq!(&item[1028..1036], &1u64.to_le_bytes());
        assert_eq!(&item[1036..1044], &(raw_tags.len() as u64).to_le_bytes());
        let t = 1044;
        assert_eq!(&item[t..t + raw_tags.len()], &raw_tags[..]);
        assert_eq!(&item[t + raw_tags.len()..], data);
    }

    // RSA-PSS sign → verify roundtrip (proves our sign/verify are inverse; tamper is rejected).
    #[test]
    fn sign_verify_roundtrip() {
        let key = RsaPrivateKey::new(&mut rand::thread_rng(), 2048).expect("gen key");
        let signer = Signer { key, owner: vec![] };
        let msg = b"deep-hash-stand-in";
        let sig = signer.sign(msg);
        assert!(signer.verify(msg, &sig));
        assert!(!signer.verify(b"tampered", &sig));
    }
}
