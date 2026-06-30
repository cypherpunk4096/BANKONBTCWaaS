// test-anchor-helper.mjs — thin CLI around anchor.mjs for the regtest test.
// Honors BITCOIN_RPC_URL / BITCOIN_COOKIE / BANKON_ANCHOR_WALLET from the environment.
import { anchorHash, verifyAnchor } from './anchor.mjs';

const [cmd, a, b] = process.argv.slice(2);
try {
  if (cmd === 'anchor')      console.log(JSON.stringify(await anchorHash(a)));
  else if (cmd === 'verify') console.log(JSON.stringify(await verifyAnchor(a, b)));
  else { console.error('usage: anchor <data> | verify <txid> <data>'); process.exit(2); }
} catch (e) {
  console.error('ERR ' + (e.message || e));
  process.exit(1);
}
