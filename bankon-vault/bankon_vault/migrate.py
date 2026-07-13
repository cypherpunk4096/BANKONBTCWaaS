# SPDX-License-Identifier: GPL-3.0-or-later
# bankon-vault — legacy migration importer. Bring secrets out of an OLD store (a mounted legacy Tomb,
# a JSON dump, a KEY=VALUE .env) INTO a bankon-vault, with round-trip verification. Generic by design:
# it never hard-codes any secret file path or value — you point it at a source you control.
#
# Safety: after import, every value is read back and byte-compared; a manifest records WHAT was moved
# (ids only, never values). Nothing is deleted from the source — you verify, then remove the legacy
# store yourself (e.g. `tomb slam` + shred).
"""
    report = migrate_json("legacy.json", vault, context="imported")     # {"id": "secret", ...}
    report = migrate_env("legacy.env",  vault)                          # ID=secret per line
    report = migrate_mapping({"btc.seed": mnemonic}, vault)             # from memory
    # report = {"imported": [...], "verified": [...], "failed": [...]}
"""
from __future__ import annotations

import json
import os
from typing import Dict, Optional


def _import_mapping(mapping: Dict[str, str], vault, context: str) -> dict:
    imported, verified, failed = [], [], []
    for key, value in mapping.items():
        if not key or value is None:
            continue
        try:
            vault.store(key, value, context=context)
            back = vault.retrieve_str(key)
            if back == (value if isinstance(value, str) else value.decode()):
                imported.append(key)
                verified.append(key)                      # round-trip proven
            else:
                failed.append(key)                        # stored but read-back mismatch — do not trust
        except Exception:
            failed.append(key)
    return {"imported": imported, "verified": verified, "failed": failed, "count": len(imported)}


def migrate_mapping(mapping: Dict[str, str], vault, context: str = "imported") -> dict:
    """Import an in-memory {id: secret} mapping (the vault must already be unlocked)."""
    return _import_mapping(mapping, vault, context)


def migrate_json(path: str, vault, context: str = "imported") -> dict:
    """Import a flat JSON object {id: secret}. Values that are dicts/lists are JSON-encoded as the secret."""
    with open(os.path.expanduser(path)) as f:
        obj = json.load(f)
    if not isinstance(obj, dict):
        raise ValueError("legacy JSON must be a flat object {id: secret}")
    mapping = {k: (v if isinstance(v, str) else json.dumps(v)) for k, v in obj.items()}
    return _import_mapping(mapping, vault, context)


def migrate_env(path: str, vault, context: str = "imported") -> dict:
    """Import a KEY=VALUE file (shell/.env style; blank lines and #comments skipped)."""
    mapping: Dict[str, str] = {}
    with open(os.path.expanduser(path)) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            mapping[k.strip()] = v.strip().strip('"').strip("'")
    return _import_mapping(mapping, vault, context)


def write_manifest(report: dict, path: str) -> None:
    """Record what was migrated — IDS ONLY, never secret values."""
    safe = {"count": report["count"], "imported_ids": report["imported"],
            "verified_ids": report["verified"], "failed_ids": report["failed"]}
    with open(os.path.expanduser(path), "w") as f:
        json.dump(safe, f, indent=2)
    os.chmod(os.path.expanduser(path), 0o600)
