# SPDX-License-Identifier: GPL-3.0-or-later
# bankon-vault — the PROGRAMMABLE policy engine (Step 3). A signing "firewall": each candidate
# signature is evaluated against a set of rules, every rule fail-closed, before any key is touched.
#
# Rules (all optional, all AND-ed — every one must allow):
#   • max_fee_sats / max_output_sats / max_total_out_sats   spend limits
#   • allowlist / denylist                                   which output addresses are permitted
#   • cooldown_sec                                           minimum time between signatures (rate limit)
#   • not_before_epoch / not_before_height                   timelock (wall-clock / block height)
#   • quorum {n, approvers:[pubkey…]}                        N-of-M signatures over the PSBT payload
#   • require_approval                                       still show a human the decoded tx
#
# Config + cooldown state persist next to the vault (0600). Decisions are appended to an audit log.
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Callable, Optional

from .core import _secure_write


@dataclass
class PolicyConfig:
    max_fee_sats: Optional[int] = None
    max_output_sats: Optional[int] = None            # cap on any single output
    max_total_out_sats: Optional[int] = None         # cap on the sum of outputs
    allowlist: list[str] = field(default_factory=list)   # if set, every output address must be in it
    denylist: list[str] = field(default_factory=list)
    cooldown_sec: int = 0
    not_before_epoch: Optional[int] = None
    not_before_height: Optional[int] = None
    quorum_n: int = 0                                 # 0 = no quorum; else N signatures required
    quorum_approvers: list[str] = field(default_factory=list)   # hex pubkeys allowed to vote
    require_approval: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PolicyConfig":
        return cls(**{k: d[k] for k in d if k in cls.__dataclass_fields__})


@dataclass
class Decision:
    allowed: bool
    reasons: list[str] = field(default_factory=list)   # every failing rule (or "ok")

    def __bool__(self):
        return self.allowed


class PolicyEngine:
    """Evaluate a signing request against PolicyConfig. Composes as a Gate (has .approve()).

    Extra inputs the vault-agnostic rules can't get themselves are injected:
      • current_height()      -> int|None   (for the height timelock)
      • verify_sig(msg, sig, pubkey) -> address|None   (for quorum — the BTC adapter's verify_message)
      • approver(summary) -> bool           (human confirmation, if require_approval)
      • quorum_votes: list[(pubkey, signature)]  collected out-of-band over the payload message
    """
    def __init__(self, config: PolicyConfig, state_dir: str,
                 current_height: Optional[Callable[[], Optional[int]]] = None,
                 verify_sig: Optional[Callable] = None,
                 approver: Optional[Callable[[dict], bool]] = None):
        self.cfg = config
        self.state_dir = state_dir
        self._height = current_height
        self._verify = verify_sig
        self._approver = approver
        self._state_file = os.path.join(state_dir, ".policy_state.json")
        self._audit = os.path.join(state_dir, ".policy_audit.jsonl")

    # ---- persistence ----
    @staticmethod
    def load_config(path: str) -> PolicyConfig:
        try:
            with open(os.path.join(path, ".policy.json")) as f:
                return PolicyConfig.from_dict(json.load(f))
        except Exception:
            return PolicyConfig()

    def save_config(self) -> None:
        _secure_write(os.path.join(self.state_dir, ".policy.json"),
                      json.dumps(self.cfg.to_dict(), indent=2).encode())

    def _load_state(self) -> dict:
        try:
            with open(self._state_file) as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_state(self, st: dict) -> None:
        _secure_write(self._state_file, json.dumps(st).encode())

    # ---- evaluation ----
    def evaluate(self, req, quorum_votes: Optional[list] = None) -> Decision:
        s = req.summary
        fails: list[str] = []
        now = int(time.time())

        # spend limits — fail CLOSED on an unknown fee (a PSBT without input amounts can't be capped)
        if self.cfg.max_fee_sats is not None:
            fee = s.get("fee_sats")
            if fee is None:
                fails.append("fee unknown (PSBT lacks input amounts) — cannot enforce max_fee")
            elif fee > self.cfg.max_fee_sats:
                fails.append(f"fee {fee} > max_fee {self.cfg.max_fee_sats}")
        outs = s.get("outputs", [])
        if self.cfg.max_output_sats is not None:
            for o in outs:
                if (o.get("sats") or 0) > self.cfg.max_output_sats:
                    fails.append(f"output {o.get('sats')} > max_output {self.cfg.max_output_sats}")
                    break
        if self.cfg.max_total_out_sats is not None and (s.get("out_sats") or 0) > self.cfg.max_total_out_sats:
            fails.append(f"total out {s.get('out_sats')} > max_total {self.cfg.max_total_out_sats}")

        # address allow/deny
        addrs = [o.get("address") for o in outs if o.get("address")]
        if self.cfg.allowlist:
            bad = [a for a in addrs if a not in self.cfg.allowlist]
            if bad:
                fails.append(f"output(s) not in allowlist: {bad[:3]}")
        if self.cfg.denylist:
            hit = [a for a in addrs if a in self.cfg.denylist]
            if hit:
                fails.append(f"output(s) on denylist: {hit[:3]}")

        # timelocks
        if self.cfg.not_before_epoch is not None and now < self.cfg.not_before_epoch:
            fails.append(f"timelock: not before epoch {self.cfg.not_before_epoch} (now {now})")
        if self.cfg.not_before_height is not None:
            h = self._height() if self._height else None
            if h is None:
                fails.append("timelock: block height unknown (no height source)")
            elif h < self.cfg.not_before_height:
                fails.append(f"timelock: height {h} < {self.cfg.not_before_height}")

        # cooldown (rate limit) — read last-sign time
        if self.cfg.cooldown_sec > 0:
            last = self._load_state().get("last_sign_ts", 0)
            if now - last < self.cfg.cooldown_sec:
                fails.append(f"cooldown: {self.cfg.cooldown_sec - (now - last)}s remaining")

        # N-of-M quorum over the payload message
        if self.cfg.quorum_n > 0:
            fails += self._check_quorum(req, quorum_votes or [])

        # human approval
        if self.cfg.require_approval:
            try:
                if not (self._approver and self._approver(s)):
                    fails.append("human approval not granted")
            except Exception:
                fails.append("human approval errored")

        dec = Decision(allowed=not fails, reasons=fails or ["ok"])
        self._audit_write(req, dec)
        return dec

    def quorum_message(self, req) -> str:
        """The exact message quorum approvers must sign (binds the PSBT payload)."""
        import hashlib
        ph = hashlib.sha256(req.psbt_b64.encode()).hexdigest()
        return f"BANKON-VAULT quorum approve entry={req.entry_id} payload={ph}"

    def _check_quorum(self, req, votes: list) -> list[str]:
        if not self._verify:
            return ["quorum: no signature verifier wired"]
        msg = self.quorum_message(req)
        allowed = set(self.cfg.quorum_approvers)
        seen, valid = set(), 0
        for pubkey, sig in votes:
            if pubkey not in allowed or pubkey in seen:
                continue
            try:
                if self._verify(msg, sig, pubkey):
                    seen.add(pubkey)
                    valid += 1
            except Exception:
                continue
        if valid < self.cfg.quorum_n:
            return [f"quorum: {valid}/{self.cfg.quorum_n} valid approver signatures"]
        return []

    def record_signed(self) -> None:
        """Call AFTER a successful sign to arm the cooldown."""
        st = self._load_state()
        st["last_sign_ts"] = int(time.time())
        self._save_state(st)

    def _audit_write(self, req, dec: Decision) -> None:
        try:
            row = {"ts": int(time.time()), "entry": req.entry_id, "requester": getattr(req, "requester", "?"),
                   "allowed": dec.allowed, "reasons": dec.reasons, "fee": req.summary.get("fee_sats")}
            with open(self._audit, "a") as f:
                f.write(json.dumps(row) + "\n")
            os.chmod(self._audit, 0o600)
        except Exception:
            pass

    # ---- Gate protocol (drop-in for gated_sign_psbt) ----
    def approve(self, req) -> bool:
        return bool(self.evaluate(req, getattr(req, "quorum_votes", None)))
