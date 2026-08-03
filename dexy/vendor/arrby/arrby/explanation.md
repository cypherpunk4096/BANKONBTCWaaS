# ARRBY — explanation.md

## What this is

ARRBY is a smart contract plus a small dashboard that does one thing: borrow
a large amount of a token for the length of a single transaction, use it to
buy something cheap on one exchange and sell it for more on another, repay
the loan, and keep the difference — all atomically, so if the trade wasn't
actually profitable the entire thing undoes itself as if it never happened.

That's a flash loan. It's not "a loan" in the normal sense — there's no
collateral, no credit check, no repayment schedule. It's a property of how
Ethereum-style transactions work: everything inside one transaction either
all happens or none of it happens. Aave (a lending protocol) will hand you
however many millions of dollars of a token you ask for, on the condition
that by the end of that same transaction you've given it back plus a small
fee. If you can't, the transaction reverts, the loan never "happened" from
the chain's point of view, and you've lost nothing but the gas you spent
trying.

## Why "agnostic"

Two things make ARRBY portable instead of chain-specific:

1. **Aave V3 is the same contract shape everywhere.** Ethereum, Arbitrum,
   Base, Optimism, Polygon, Avalanche — wherever Aave V3 is deployed, its
   `Pool.flashLoanSimple` function takes the same arguments and behaves the
   same way. ARRBY only needs one address to change per chain: the Pool's
   address. Nothing else about the contract changes.

2. **Uniswap V2 forks are the same contract shape everywhere.** Sushiswap,
   Quickswap, Camelot, PancakeSwap V2 — dozens of DEXes across dozens of
   chains all copied the same `swapExactTokensForTokens` / `getAmountsOut`
   interface. ARRBY takes the router address and the token path as
   *parameters to the function call*, not as something baked into the
   contract. So the same deployed contract can arbitrage USDC/WETH on
   Sushiswap vs. Camelot today, and DAI/USDT on Quickswap vs. Sushiswap
   tomorrow, without redeploying anything.

That combination is what "write once, run on any EVM chain with a flash
loan provider" actually means in practice — it's not a marketing claim,
it's just: two widely-copied interfaces, two configuration values.

## The shape of one run

1. You tell the contract: borrow this much of this token.
2. You tell it: swap that token for X on Router A, then swap X back to the
   original token on Router B.
3. You tell it: don't bother if profit would be less than this minimum.
4. The contract asks Aave for the loan.
5. Aave hands over the tokens and calls back into the contract mid-transaction.
6. The contract does the two swaps.
7. The contract checks: did I get back more than I owe (principal + Aave's
   fee) plus my minimum? If yes, it approves Aave to take what it's owed and
   keeps the rest. If no, the whole transaction reverts — nothing moves,
   nothing is lost except the gas already spent trying.
8. Aave takes its principal + fee back out of the contract's balance.

Steps 4 through 8 happen inside a single transaction. There is no window
where the contract is holding an unrepaid loan that someone else could
interfere with — either the whole sequence completes, or none of it did.

## Why the UI matters

Smart contract calls are opaque by default — you submit a transaction and
wait. The console (`ui/index.html`) exists so you can watch the sequence
above unfold in something closer to real time: it shows when the loan was
requested, when each swap settled, when repayment happened, and — critically
— it decodes the contract's own event log to tell you the *actual* profit in
plain units, not just "transaction succeeded." It also has a **Quote** button
that runs the exact same math as a free, no-gas read call, so you can see
whether a trade would be profitable before you ever risk a real transaction.

## What this is not

- It's not a strategy. ARRBY doesn't find arbitrage opportunities for you —
  it executes one you've already identified (two specific routers, a
  specific pair, a specific size). Finding opportunities is a separate,
  ongoing problem (price monitoring, mempool watching, etc.).
- It's not risk-free. Gas costs money whether or not the trade is
  profitable, and if someone else takes the same opportunity first (or
  front-runs your transaction), your trade simply reverts and you're out the
  gas. The contract protects your capital, not your gas spend.
- It's not custody-free forever unless you renounce ownership. Until you
  call `renounceOwnership()`, the deployer's key can call `initiateArbitrage`
  and `withdraw` — by design, so you can operate it — but that also means
  the key is a real dependency until you choose to give that up.
