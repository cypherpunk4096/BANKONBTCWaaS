// e2e-helper.mjs — bridges keygen.mjs / sign.mjs to the regtest shell harness.
//   node e2e-helper.mjs keygen [type]      -> JSON {mnemonic,type,external,internal,firstAddress}
//   echo '{"mnemonic","type","psbt"}' | node e2e-helper.mjs sign   -> signed tx hex
import { generateWallet } from './keygen.mjs';
import { signPsbt } from './sign.mjs';
import { HDKey } from '@scure/bip32';
import * as bip39 from '@scure/bip39';
import { wordlist } from '@scure/bip39/wordlists/english';

// Regtest/testnet use tpub-versioned extended keys. BIP32 child PRIVATE keys are
// version-independent, so sign.mjs (mainnet versions) signs these correctly.
const TESTNET_VER = { private: 0x04358394, public: 0x043587cf };
const PREFIX = { 'native-segwit': 'wpkh', taproot: 'tr', legacy: 'pkh' };
const PURPOSE = { 'native-segwit': 84, taproot: 86, legacy: 44 };

const cmd = process.argv[2];
if (cmd === 'keygen' || cmd === 'keygen-regtest') {
  const type = process.argv[3] || 'native-segwit';
  if (cmd === 'keygen-regtest') {
    const mnemonic = bip39.generateMnemonic(wordlist, 128);
    const seed = bip39.mnemonicToSeedSync(mnemonic);
    const root = HDKey.fromMasterSeed(seed, TESTNET_VER);
    const fp = root.fingerprint.toString(16).padStart(8, '0');
    const acct = root.derive(`m/${PURPOSE[type]}'/0'/0'`);
    const tpub = acct.publicExtendedKey;            // tpub-versioned
    const origin = `[${fp}/${PURPOSE[type]}'/0'/0']`;
    process.stdout.write(JSON.stringify({
      mnemonic, type,
      external: `${PREFIX[type]}(${origin}${tpub}/0/*)`,
      internal: `${PREFIX[type]}(${origin}${tpub}/1/*)`,
    }));
  } else {
    const w = generateWallet(type, 128);
    process.stdout.write(JSON.stringify({
      mnemonic: w.private.mnemonic, type,
      external: w.publicRegistration.external, internal: w.publicRegistration.internal,
      firstAddress: w.firstAddress,
    }));
  }
} else if (cmd === 'keygen-regtest-ms') {
  // Emit one multisig cosigner: { mnemonic, fingerprint, path, xpub(tpub) } at 48'/0'/0'/2'.
  const mnemonic = bip39.generateMnemonic(wordlist, 128);
  const root = HDKey.fromMasterSeed(bip39.mnemonicToSeedSync(mnemonic), TESTNET_VER);
  const path = "48'/0'/0'/2'";
  const acct = root.derive('m/' + path);
  process.stdout.write(JSON.stringify({
    mnemonic, fingerprint: root.fingerprint.toString(16).padStart(8, '0'), path, xpub: acct.publicExtendedKey,
  }));
} else if (cmd === 'sign') {
  let buf = ''; process.stdin.on('data', d => buf += d);
  process.stdin.on('end', () => {
    const { mnemonic, type, psbt } = JSON.parse(buf);
    process.stdout.write(signPsbt(mnemonic, type, psbt, { gap: 60 }).signedTxHex);
  });
} else {
  console.error('usage: keygen [type] | sign (<stdin JSON>)'); process.exit(1);
}
