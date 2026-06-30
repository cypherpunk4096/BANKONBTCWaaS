// test-sign.mjs — proves the non-custodial signing roundtrip offline.
// Mints a wallet, builds a PSBT spending a synthetic UTXO it owns, signs with
// the mnemonic, finalizes (which validates the signature), and checks the tx.
import { generateWallet } from './keygen.mjs';
import { signPsbt } from './sign.mjs';
import * as btc from '@scure/btc-signer';
import { base64, hex } from '@scure/base';
import { HDKey } from '@scure/bip32';
import * as bip39 from '@scure/bip39';

function buildUnsignedPsbt(mnemonic, purpose, prefix) {
  const seed = bip39.mnemonicToSeedSync(mnemonic);
  const acct = HDKey.fromMasterSeed(seed).derive(`m/${purpose}'/0'/0'`);
  const child = acct.deriveChild(0).deriveChild(0);            // receive index 0
  const pay = prefix === 'tr'
    ? btc.p2tr(child.publicKey.subarray(1), undefined, btc.NETWORK)
    : prefix === 'pkh' ? btc.p2pkh(child.publicKey, btc.NETWORK)
    : btc.p2wpkh(child.publicKey, btc.NETWORK);
  const tx = new btc.Transaction();
  const input = { txid: hex.decode('11'.repeat(32)), index: 0 };
  if (prefix === 'pkh') {
    // legacy needs the full prev tx; skip legacy in this offline test
    throw new Error('legacy needs nonWitnessUtxo — skipped in offline test');
  } else if (prefix === 'tr') {
    input.witnessUtxo = { script: pay.script, amount: 100000n };
    input.tapInternalKey = pay.tapInternalKey;
  } else {
    input.witnessUtxo = { script: pay.script, amount: 100000n };
  }
  tx.addInput(input);
  tx.addOutputAddress(btc.p2wpkh(child.publicKey, btc.NETWORK).address, 90000n, btc.NETWORK);
  return base64.encode(tx.toPSBT());
}

let pass = 0, fail = 0;
for (const [type, prefix] of [['native-segwit','wpkh'], ['taproot','tr']]) {
  try {
    const w = generateWallet(type, 128);
    const purpose = type === 'taproot' ? 86 : 84;
    const psbt = buildUnsignedPsbt(w.private.mnemonic, purpose, prefix);
    const { signedTxHex, inputsSigned } = signPsbt(w.private.mnemonic, type, psbt, { gap: 5 });
    const decoded = btc.Transaction.fromRaw(hex.decode(signedTxHex), { allowUnknownOutputs: true });
    const okHex = signedTxHex.length > 100 && inputsSigned === 1 && decoded.inputsLength === 1;
    console.log(`  ${okHex ? 'PASS' : 'FAIL'}  ${type}: signed ${inputsSigned} input(s), tx ${signedTxHex.length/2} bytes`);
    okHex ? pass++ : fail++;

    // Negative test: a wrong mnemonic must NOT be able to sign.
    const wrong = generateWallet(type, 128).private.mnemonic;
    let rejected = false;
    try { signPsbt(wrong, type, psbt, { gap: 5 }); } catch { rejected = true; }
    console.log(`  ${rejected ? 'PASS' : 'FAIL'}  ${type}: wrong phrase correctly cannot sign`);
    rejected ? pass++ : fail++;
  } catch (e) { console.log(`  FAIL  ${type}: ${e.message}`); fail++; }
}
console.log(`\n${fail === 0 ? '✓ ALL PASS' : '✗ FAILURES'} — ${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
