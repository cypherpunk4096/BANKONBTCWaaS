// SPDX-License-Identifier: GPL-3.0-or-later
// BANKON client-facing encryption software — GPLv3 (see POLICY.md). Like GnuPG,
// code that handles users' keys is copyleft so derivatives stay free & auditable.
//
// keygen.mjs — BANKON WaaS client-side key generation (non-custodial core).
//
// EVERYTHING private in here is meant to live ONLY on the client. The server
// must never receive `mnemonic` or `xprv`. The only field that is safe to send
// to the BANKON API is the PUBLIC bundle returned by publicRegistration().
//
// Runs in Node (CLI/demo) and is mirrored 1:1 in the browser (public/index.html
// imports the same @scure libs from a CDN) so the exact same code path mints
// keys on the user's device.
//
import * as bip39 from '@scure/bip39';
import { wordlist } from '@scure/bip39/wordlists/english';
import { HDKey } from '@scure/bip32';
import * as btc from '@scure/btc-signer';

// BIP-purpose → script type. "Choose how you want your wallet."
export const WALLET_TYPES = {
  'native-segwit': { purpose: 84, coin: 0, descPrefix: 'wpkh', label: 'Native SegWit (bc1q…)' },
  'taproot':       { purpose: 86, coin: 0, descPrefix: 'tr',   label: 'Taproot (bc1p…)' },
  'legacy':        { purpose: 44, coin: 0, descPrefix: 'pkh',  label: 'Legacy (1…)' },
};

const NETWORK = btc.NETWORK; // mainnet

// Generate a fresh wallet entirely on the client.
// `strength` 128 = 12 words, 256 = 24 words.
export function generateWallet(type = 'native-segwit', strength = 256, account = 0) {
  const t = WALLET_TYPES[type];
  if (!t) throw new Error(`unknown wallet type: ${type}`);

  // ---- PRIVATE material — never leaves the client -------------------------
  const mnemonic = bip39.generateMnemonic(wordlist, strength); // == the user's passphrase/seed
  const seed = bip39.mnemonicToSeedSync(mnemonic);
  const root = HDKey.fromMasterSeed(seed);
  const fingerprint = root.fingerprint.toString(16).padStart(8, '0');
  const path = `m/${t.purpose}'/${t.coin}'/${account}'`;
  const account_node = root.derive(path);
  const xprv = account_node.privateExtendedKey; // PRIVATE

  // ---- PUBLIC material — safe to register with the API --------------------
  const xpub = account_node.publicExtendedKey; // account-level xpub (no private data)

  // Output descriptors Bitcoin Core will import watch-only.
  // origin = [fingerprint/purpose'/coin'/account'] so Core knows the key path.
  const origin = `[${fingerprint}/${t.purpose}'/${t.coin}'/${account}']`;
  const external = `${t.descPrefix}(${origin}${xpub}/0/*)`; // receive
  const internal = `${t.descPrefix}(${origin}${xpub}/1/*)`; // change

  // Derive the first receive address client-side (proves independence from server).
  const child0 = account_node.deriveChild(0).deriveChild(0).publicKey;
  let firstAddress;
  if (t.descPrefix === 'wpkh') firstAddress = btc.p2wpkh(child0, NETWORK).address;
  else if (t.descPrefix === 'tr') firstAddress = btc.p2tr(child0.subarray(1), undefined, NETWORK).address;
  else firstAddress = btc.p2pkh(child0, NETWORK).address;

  return {
    type, path, fingerprint,
    // PRIVATE — keep on client only:
    private: { mnemonic, xprv },
    // PUBLIC — this is the ONLY object you POST to /api/wallet:
    publicRegistration: { type, fingerprint, purpose: t.purpose, account, xpub, external, internal },
    firstAddress,
  };
}

// CLI demo (no node/server contact): node keygen.mjs [type] [12|24]
if (import.meta.url === `file://${process.argv[1]}`) {
  const type = process.argv[2] || 'native-segwit';
  const words = (process.argv[3] === '12') ? 128 : 256;
  const w = generateWallet(type, words);
  console.log('\n=== BANKON WaaS — wallet minted CLIENT-SIDE (this machine only) ===\n');
  console.log('  type ............', WALLET_TYPES[type].label);
  console.log('  derivation ......', w.path, `(fingerprint ${w.fingerprint})`);
  console.log('\n  🔐 PRIVATE — write down, NEVER send to BANKON / the API:');
  console.log('     mnemonic (passphrase):', w.private.mnemonic);
  console.log('     account xprv .........:', w.private.xprv.slice(0, 18) + '…(hidden)');
  console.log('\n  🌐 PUBLIC — this is all the API ever receives:');
  console.log('     xpub .........:', w.publicRegistration.xpub);
  console.log('     descriptor ...:', w.publicRegistration.external);
  console.log('\n  first receive address (derived client-side):', w.firstAddress);
  console.log('\n  NOTE: the API/node import the descriptor WATCH-ONLY. They can see');
  console.log('        balances and build UNSIGNED PSBTs, but cannot spend — only the');
  console.log('        holder of the mnemonic above can sign.\n');
}
