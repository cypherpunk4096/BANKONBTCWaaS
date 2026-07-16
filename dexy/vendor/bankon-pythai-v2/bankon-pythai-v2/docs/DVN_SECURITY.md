# DVN / Security Stack Notes — BANKON PYTHAI v2

Not scripted here (DVN/library selection is done via `EndpointV2.setConfig`, typically
through the LayerZero CLI or a dedicated `SetConfig.s.sol` you should add per-deployment
once your DVN choices are final), but required before `TransferToDAIO.s.sol`:

- **Use ≥2 independent required DVNs per pathway.** Never ship a single-DVN production
  config. This was the root cause of a large 2026 cross-chain bridge exploit attributed to
  a 1-of-1 verifier single point of failure; LayerZero mainnet now effectively refuses
  single-DVN send configs for production apps.
- **Config symmetry:** Send config on Chain A must match Receive config on Chain B for the
  A→B pathway, and vice versa for B→A. Verify both directions independently after wiring.
- **Enforced gas:** `WireOApp.s.sol` sets a default 80,000 gas floor for `lzReceive`. Raise
  this per-chain for gas-expensive execution environments (e.g. chains with repriced
  SSTORE/cold-access opcodes or non-standard gas metering) before wiring that chain.
- **Owner == delegate:** `TransferToDAIO.s.sol` sets both to the same DAIO address in the
  same script run — do not let them drift apart. Only the owner can call `setDelegate`, so
  reassign delegate BEFORE or IN THE SAME transaction batch as ownership transfer.
- **Pin libraries** (send/receive message libraries) explicitly once your security stack is
  finalized, so a LayerZero default-library change never silently alters your app's trust
  assumptions.
- **Do all wiring before handoff.** Once `TransferToDAIO.s.sol` runs, every further change
  to peers, enforced options, delegate, or DVN config requires a DAIO governance action
  (per your Boardroom/WarCouncil process), not a single signer.
