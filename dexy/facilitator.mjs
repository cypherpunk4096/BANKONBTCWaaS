// facilitator.mjs — off-chain mirror of BankonToll's golden-ratio math (18-dp, exact).
// Lets DEXY quote the BANKON toll before a client sends a facilitation/bridge/mint tx.
// Keep this in lockstep with contracts/BankonToll.sol (PHI_E18, PHI_DIV).

export const PHI_E18 = 1_618_033_988_749_894_848n; // φ (golden ratio) × 1e18, exact to 18 dp
export const PHI_DIV = 10n * 10n ** 18n;           // φ/10 as an 18-dp fraction ⇒ divide by 1e19

/** BANKON toll = golden ratio of a gas fee (wei), to 18 dp: toll = gasFee × φ/10. */
export function tollOnGasFee(gasFeeWei) {
  return (BigInt(gasFeeWei) * PHI_E18) / PHI_DIV;
}

/** Toll for a gas-unit count at a given gas price (wei). */
export function tollOnGas(gasUnits, gasPriceWei) {
  return tollOnGasFee(BigInt(gasUnits) * BigInt(gasPriceWei));
}

/** One-call quote: the toll, plus the total the client pays (gas fee + toll). */
export function quoteToll({ gasUnits, gasPriceWei, gasFeeWei } = {}) {
  const fee = gasFeeWei != null ? BigInt(gasFeeWei) : BigInt(gasUnits) * BigInt(gasPriceWei);
  const toll = tollOnGasFee(fee);
  return {
    gasFeeWei: fee.toString(),
    bankonTollWei: toll.toString(),
    totalWei: (fee + toll).toString(),
    phi: '1.618033988749894848',
    ratio: 'φ/10 (0.1618033988749894848) of the gas fee, to 18 dp',
    heldIn: 'bankonTreasury (bankon.eth)',
    note: 'toll is native, charged on top of gas, pegged to the transaction gas fee — never touches the moved asset or a private key',
  };
}
