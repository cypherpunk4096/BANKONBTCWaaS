// custody.mjs — sovereign-destination checks. Every DEXY quote/plan targets a
// BTC address; this module proves it is (a) a valid address on this node's
// network and (b, optionally) an address of one of the user's OWN registered
// WaaS watch-only wallets — keys held client-side, never by any server.
//
// Default posture: warn on unverified destinations. DEXY_STRICT_CUSTODY=1
// refuses anything that isn't provably the user's own wallet.

import { rpc } from '../bankon-waas/rpc.mjs';
import { listWallets } from '../bankon-waas/registry.mjs';

export const strictCustody = () => process.env.DEXY_STRICT_CUSTODY === '1';

/** Public metadata of the user's registered WaaS watch-only wallets. */
export function listOwnWallets() {
  return listWallets().map(w => ({
    id: w.id, type: w.type, firstAddress: w.firstAddress || null, owner: w.owner || null,
  }));
}

/**
 * verifyDestination(address, { wallet }) →
 *   { valid, network, ownWallet: { name, watchOnly } | null, note }
 * `wallet` narrows the ownership scan to one registered wallet name.
 */
export async function verifyDestination(address, { wallet = null } = {}) {
  const v = await rpc('validateaddress', [address]);
  if (!v.isvalid) {
    return { valid: false, network: null, ownWallet: null, note: 'not a valid Bitcoin address on this network' };
  }

  const names = wallet ? [wallet] : listWallets().map(w => w.id);
  let ownWallet = null;
  for (const name of names) {
    try {
      const info = await rpc('getaddressinfo', [address], name);
      if (info.ismine || info.iswatchonly) {
        ownWallet = { name, watchOnly: !info.solvable || !!info.iswatchonly };
        break;
      }
    } catch { /* wallet not loaded or unknown — keep scanning */ }
  }

  return {
    valid: true,
    network: await rpc('getblockchaininfo').then(i => i.chain).catch(() => null),
    ownWallet,
    note: ownWallet
      ? `destination belongs to YOUR watch-only wallet "${ownWallet.name}" — sovereign custody confirmed`
      : 'destination is valid but not registered in your WaaS — verify you hold its keys before sending',
  };
}

/** Gate used by quote/plan: throws in strict mode when ownership is unproven. */
export async function requireSovereign(address, { wallet = null, strict = strictCustody() } = {}) {
  const check = await verifyDestination(address, { wallet });
  if (!check.valid) throw new Error(`invalid BTC destination: ${address}`);
  if (strict && !check.ownWallet) {
    throw new Error('DEXY_STRICT_CUSTODY: destination is not a registered WaaS watch-only wallet address');
  }
  return check;
}
