#!/usr/bin/env python3
"""
Cross-chain settlement saga manager.

Closes the HIGH-severity "no atomic cross-chain payments" finding: a two-leg
transfer (lock on chain A -> mint on chain B) is not atomic, so a failed second
leg previously left funds stranded pending manual Senatus action.

This implements a SAGA with explicit compensation. Every forward step has a
compensating inverse. A stalled saga auto-compensates after a timeout WITHOUT
governance intervention; only genuinely ambiguous states (both legs partially
observed) escalate to Senatus.

State machine:
    INIT -> LOCKED -> CONSENSUS -> MINTED -> DONE
              |           |           |
              v           v           v
          (timeout)   (timeout)   (verify fail)
              |           |           |
              +----> COMPENSATING -> REFUNDED
                          |
                          v
                    (ambiguous) -> ESCALATED

Author: Gregory L. (codephreak) | License: Apache-2.0
"""

from __future__ import annotations
import time
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Callable


class Saga(Enum):
    INIT = "init"
    LOCKED = "locked"            # funds escrowed on source
    CONSENSUS = "consensus"      # 1R+3V signed the transfer
    MINTED = "minted"            # destination minted xERC20
    DONE = "done"
    COMPENSATING = "compensating"
    REFUNDED = "refunded"        # source unlocked -> user made whole
    ESCALATED = "escalated"      # ambiguous; Senatus review


@dataclass
class TransferSaga:
    transfer_id: str
    amount: int
    src_chain: int
    dst_chain: int
    recipient: str
    # timeouts (seconds) tuned per source-chain finality
    lock_timeout: int = 900          # 15 min: consensus must form
    mint_timeout: int = 1800         # 30 min: destination must mint
    state: Saga = Saga.INIT
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    src_locked: bool = False
    dst_minted: bool = False
    history: list = field(default_factory=list)

    def _to(self, s: Saga):
        self.history.append((self.state.value, s.value, time.time()))
        self.state = s
        self.updated_at = time.time()


class SagaCoordinator:
    """
    Drives transfers to a terminal state. Forward progress OR compensation —
    never an indefinite stranded balance. Injected callbacks perform the actual
    chain ops (lock/unlock/mint) and are assumed idempotent.
    """

    def __init__(
        self,
        do_lock: Callable[[TransferSaga], bool],
        do_unlock: Callable[[TransferSaga], bool],
        do_mint: Callable[[TransferSaga], bool],
        has_consensus: Callable[[TransferSaga], bool],
        mint_confirmed: Callable[[TransferSaga], bool],
    ):
        self.do_lock = do_lock
        self.do_unlock = do_unlock
        self.do_mint = do_mint
        self.has_consensus = has_consensus
        self.mint_confirmed = mint_confirmed

    def step(self, sg: TransferSaga) -> Saga:
        now = time.time()

        if sg.state == Saga.INIT:
            if self.do_lock(sg):
                sg.src_locked = True
                sg._to(Saga.LOCKED)

        elif sg.state == Saga.LOCKED:
            if self.has_consensus(sg):
                sg._to(Saga.CONSENSUS)
            elif now - sg.created_at > sg.lock_timeout:
                # consensus never formed -> safe to unwind, only source touched
                sg._to(Saga.COMPENSATING)

        elif sg.state == Saga.CONSENSUS:
            if self.do_mint(sg):
                sg.dst_minted = True
                sg._to(Saga.MINTED)
            elif now - sg.updated_at > sg.mint_timeout:
                sg._to(Saga.COMPENSATING)

        elif sg.state == Saga.MINTED:
            if self.mint_confirmed(sg):
                sg._to(Saga.DONE)
            elif now - sg.updated_at > sg.mint_timeout:
                # mint submitted but unconfirmed past window -> AMBIGUOUS.
                # We must not unlock source and risk double-spend. Escalate.
                sg._to(Saga.ESCALATED)

        elif sg.state == Saga.COMPENSATING:
            # Only reachable when destination has NOT minted. Unlock is safe.
            if not sg.dst_minted and self.do_unlock(sg):
                sg.src_locked = False
                sg._to(Saga.REFUNDED)
            else:
                sg._to(Saga.ESCALATED)

        return sg.state

    def drive(self, sg: TransferSaga, max_steps: int = 10) -> Saga:
        for _ in range(max_steps):
            prev = sg.state
            self.step(sg)
            if sg.state in (Saga.DONE, Saga.REFUNDED, Saga.ESCALATED):
                break
            if sg.state == prev:
                break
        return sg.state


# ---- demonstration with simulated chain ops ----

if __name__ == "__main__":
    # Scenario A: happy path
    ok_env = dict(consensus=True, minted=True, confirmed=True)
    # Scenario B: consensus never forms -> auto-refund, no governance
    fail_consensus = dict(consensus=False, minted=False, confirmed=False)

    def make(env):
        return SagaCoordinator(
            do_lock=lambda s: True,
            do_unlock=lambda s: True,
            do_mint=lambda s: env["minted"],
            has_consensus=lambda s: env["consensus"],
            mint_confirmed=lambda s: env["confirmed"],
        )

    a = TransferSaga("tx-A", 1_000_000, 1, 16661, "0xrec")
    print("Scenario A (happy):", make(ok_env).drive(a).value)
    print("  path:", " -> ".join(h[1] for h in a.history))

    b = TransferSaga("tx-B", 1_000_000, 1, 16661, "0xrec", lock_timeout=0)
    print("Scenario B (no consensus):", make(fail_consensus).drive(b).value)
    print("  path:", " -> ".join(h[1] for h in b.history))
    print("\nNeither scenario leaves funds stranded pending manual action.")
