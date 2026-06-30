// SPDX-License-Identifier: GPL-3.0-or-later
// BANKON client-facing encryption software — GPLv3 (see POLICY.md). Like GnuPG,
// code that handles users' keys is copyleft so derivatives stay free & auditable.
//
// sign.mjs — BANKON WaaS CLIENT-SIDE PSBT signing (non-custodial).
//
// The server builds an UNSIGNED PSBT from the watch-only wallet; this module
// re-derives the private keys from the user's mnemonic and signs locally. The
// mnemonic/xprv never leave the client. Mirrors keygen.mjs derivation exactly.
//
import * as bip39 from '@scure/bip39';
import { HDKey } from '@scure/bip32';
import * as btc from '@scure/btc-signer';
import { base64, hex } from '@scure/base';

const PURPOSE = { 'native-segwit': 84, 'taproot': 86, 'legacy': 44 };

// Sign every input this wallet owns, finalize, and return the broadcastable hex.
// gap = how many child indices (per branch) to try when matching inputs.
export function signPsbt(mnemonic, type, psbtBase64, { gap = 50, account = 0 } = {}) {
  const purpose = PURPOSE[type];
  if (!purpose) throw new Error(`unknown wallet type: ${type}`);
  if (!bip39.validateMnemonic(mnemonic.trim(), bip39.wordlists ? bip39.wordlists.english : undefined)
      && !looksLikeWords(mnemonic)) { /* validateMnemonic needs wordlist; fall through */ }

  const seed = bip39.mnemonicToSeedSync(mnemonic.trim());
  const root = HDKey.fromMasterSeed(seed);
  const acct = root.derive(`m/${purpose}'/0'/${account}'`);

  const tx = btc.Transaction.fromPSBT(base64.decode(psbtBase64));

  let signed = 0;
  for (const branch of [0, 1]) {           // 0 = receive, 1 = change
    for (let i = 0; i <= gap; i++) {
      const child = acct.deriveChild(branch).deriveChild(i);
      if (!child.privateKey) continue;
      try { signed += tx.sign(child.privateKey); } catch (_) { /* key didn't match this input */ }
    }
  }
  if (signed === 0) throw new Error('No inputs matched this wallet — check the recovery phrase / wallet type, or the PSBT is empty.');

  tx.finalize();
  return { signedTxHex: hex.encode(tx.extract()), inputsSigned: signed };
}

function looksLikeWords(m) { return String(m).trim().split(/\s+/).length >= 12; }
