# Bitcoin Core Wallet — All Aspects, Categorized

Every Bitcoin Core v31 **wallet** RPC (55 commands from `bitcoin-cli help`, `== Wallet ==`)
grouped by function. ⭐ = used by BANKON's non-custodial watch-only flow.

## 1. Wallet lifecycle & management
Create, load, back up, migrate, and remove wallets.
- `createwallet` ⭐ · `loadwallet` · `unloadwallet` · `listwallets` ⭐ · `listwalletdir`
- `restorewallet` · `migratewallet` (legacy → descriptor) · `backupwallet`
- `setwalletflag` (e.g. avoid_reuse)

## 2. Keys, addresses & descriptors
Derivation, address generation, descriptor import/export.
- `getnewaddress` ⭐ · `getrawchangeaddress` · `getaddressinfo` ⭐ · `walletdisplayaddress` (hardware)
- `keypoolrefill` · `gethdkeys` · `createwalletdescriptor`
- `listdescriptors` ⭐ · `importdescriptors` ⭐ (how BANKON registers watch-only)

## 3. Labels
Human-readable tags for addresses.
- `setlabel` · `listlabels` · `getaddressesbylabel`

## 4. Balances & received amounts
Read-only views of funds.
- `getbalance` · `getbalances` ⭐ · `getreceivedbyaddress` · `getreceivedbylabel`
- `listreceivedbyaddress` · `listreceivedbylabel`

## 5. UTXOs, coin control & privacy
The unspent-output view and manual coin control.
- `listunspent` ⭐ · `listaddressgroupings` (privacy/clustering)
- `lockunspent` · `listlockunspent`

## 6. Sending & transaction construction
Build and fund transactions (incl. PSBT for non-custodial signing).
- `sendtoaddress` · `sendmany` · `send` · `sendall`
- `walletcreatefundedpsbt` ⭐ (BANKON builds the UNSIGNED PSBT here) · `walletprocesspsbt`
- `simulaterawtransaction` (preview balance effect)

## 7. Signing
- `signrawtransactionwithwallet` · `signmessage`
- (BANKON signs PSBTs **client-side** instead — the watch-only node holds no keys.)

## 8. Fee management & RBF
- `bumpfee` · `psbtbumpfee`

## 9. History, rescan & abandonment
- `gettransaction` ⭐ · `listtransactions` ⭐ · `listsinceblock` ⭐ (BANKON events)
- `rescanblockchain` · `abortrescan` · `abandontransaction`

## 10. Pruned-funds import/remove
- `importprunedfunds` · `removeprunedfunds`

## 11. Wallet security & encryption
Passphrase-encrypt the wallet file and (un)lock it for signing.
- `encryptwallet` · `walletpassphrase` · `walletpassphrasechange` · `walletlock`

## 12. Wallet info / status
- `getwalletinfo` ⭐ (balance, txcount, keypool, flags, watch-only)

---

### How BANKON maps onto these
BANKON uses category **2** (`importdescriptors`, watch-only) + **4/5/9** (read views) +
**6** (`walletcreatefundedpsbt`, unsigned) and deliberately **avoids 7 & 11** on the node:
the wallet is `disable_private_keys=true`, so signing and passphrase categories live on the
**client** instead (`sign.mjs` / offline client). See [wallets.md](wallets.md),
[security.md](security.md). Related util RPCs (not wallet-scoped): `getdescriptorinfo`,
`getnewaddress` address types — see [bitcoin-core-rpc.md](bitcoin-core-rpc.md).
