# Security

## Non-custodial guarantees (enforced, not just promised)
1. **Keys are minted client-side.** `keygen.mjs` runs in the browser/offline file; the
   mnemonic and xprv never leave the device.
2. **The server rejects secrets.** Any request body containing
   `mnemonic|xprv|seed|privkey|wif|passphrase` → HTTP 400 (`server.mjs` `rejectPrivate`).
3. **The node cannot spend.** Wallets are imported with `disable_private_keys=true`
   (watch-only). The node builds **unsigned** PSBTs only.
4. **Signing is client-side.** `sign.mjs` / the in-page signer / the offline client
   produce the signed tx; the server only broadcasts the finalized hex.
5. **No secrets are logged.** The API servers log boot banners only — never request
   bodies. (Audited; the one mnemonic `console.log` is in keygen.mjs's local CLI demo,
   guarded by `import.meta.url`, never run by the server.)

## API authentication
Off by default for local use. To require auth, set a token:
```bash
BANKON_API_TOKEN=your-long-random-token node server.mjs
```
Then `/api/*` requires `Authorization: Bearer your-long-random-token` (constant-time
compared). Static UI assets remain public. Configured via `shared/security.mjs`.

## Rate limiting
`shared/security.mjs` applies a generous in-memory sliding-window limit (default 120
req/min per IP) to backstop abuse. Tune in code if exposing beyond localhost.

## Encrypted backups
```bash
bankon-backup.sh create   # AES-256 + PBKDF2 (200k iters) of the wallet registry
bankon-backup.sh restore <file.enc>
```
The registry holds **public** watch-only descriptors + metadata (no private keys);
encryption prevents a backup from leaking which addresses you own. **Your private
recovery phrases are not in scope here** — back those up yourself (write them down).

## Client-code integrity
The browser keygen/signer is auditable. For the strongest assurance use
`offline-client.html` — a single self-contained file you can inspect and run offline, so a
server can't ship tampered signing code. (For full air-gap, vendor the `@scure` libs
locally instead of the CDN imports.)

## Exposure notes
Everything binds to `127.0.0.1`. If you put BANKON behind a reverse proxy, enable
`BANKON_API_TOKEN`, terminate TLS, and keep the node's RPC bound to localhost.
