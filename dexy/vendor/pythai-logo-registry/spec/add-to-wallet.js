/**
 * PLR add-to-wallet helper (EIP-747 wallet_watchAsset)
 *
 * One click in AgenticPlace / bankon.pythai.net adds the token to the user's
 * wallet with the PLR-verified logo. The logo URL is served by mindX through
 * rage.pythai.net, which resolves the on-chain canonical CID — so the image a
 * wallet caches is always the registry's current validated revision.
 *
 * Apache-2.0 — PYTHAI
 */

const PLR_ADDRESS = "0x..."; // set post-deploy
const PLR_ABI = [
  "function canonicalLogo(uint64 chainId, address token) view returns (bool verified, bytes32 logoHash, string logoCID, uint32 revision)",
];

// rage.pythai.net renders the CID as a wallet-friendly PNG and pins the
// revision in the path so wallet-side caches bust automatically on evolve().
function plrImageURL(chainId, token, revision) {
  return `https://rage.pythai.net/plr/img/${chainId}/${token.toLowerCase()}/${revision}.png`;
}

export async function addTokenToWallet({ chainId, address, symbol, decimals, provider }) {
  // 1. Resolve the verified logo from the registry (any provider; read-only).
  const { Contract, JsonRpcProvider } = await import("ethers");
  const read = new Contract(PLR_ADDRESS, PLR_ABI, provider ?? new JsonRpcProvider());
  const [verified, , , revision] = await read.canonicalLogo(chainId, address);

  const image = verified
    ? plrImageURL(chainId, address, revision)
    : undefined; // unverified tokens get no image — verification IS the product

  // 2. EIP-747: works in MetaMask, Rabby, Trust Wallet in-app browser, etc.
  return window.ethereum.request({
    method: "wallet_watchAsset",
    params: {
      type: "ERC20",
      options: { address, symbol, decimals, image },
    },
  });
}

/*
Drop-in button:

  <button onclick="addTokenToWallet({
    chainId: 1,
    address: '0xPAI...',
    symbol: 'PAI',
    decimals: 18,
  })">Add PAI to wallet</button>
*/
