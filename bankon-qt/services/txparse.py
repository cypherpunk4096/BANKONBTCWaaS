"""Exact Bitcoin transaction parser for the blackICE monitor — pure integer math.

Parses a raw (possibly segwit) transaction from the ZMQ `rawtx` feed without any RPC
round-trip: txid (double-SHA256 of the witness-stripped serialization, displayed
big-endian), weight/vsize per BIP 141, and the SUM OF OUTPUTS in integer satoshis —
which stays an int end-to-end so btc18() can render it exactly. Output totals are
what the wire carries; fees need the spent inputs' values and are therefore not
claimed here (accuracy over completeness).
"""
from hashlib import sha256


def _varint(b, o):
    v = b[o]
    if v < 0xFD:
        return v, o + 1
    if v == 0xFD:
        return int.from_bytes(b[o + 1:o + 3], "little"), o + 3
    if v == 0xFE:
        return int.from_bytes(b[o + 1:o + 5], "little"), o + 5
    return int.from_bytes(b[o + 1:o + 9], "little"), o + 9


def parse_tx(raw: bytes):
    """raw tx bytes → dict(txid, size, vsize, weight, nin, nout, out_sats) or None."""
    try:
        o = 4                                            # version
        segwit = raw[o] == 0 and raw[o + 1] == 1         # BIP 144 marker + flag
        if segwit:
            o += 2
        in_start = o
        nin, o = _varint(raw, o)
        for _ in range(nin):
            o += 36                                      # outpoint
            sl, o = _varint(raw, o)
            o += sl + 4                                  # scriptSig + sequence
        nout, o = _varint(raw, o)
        out_sats = 0
        for _ in range(nout):
            out_sats += int.from_bytes(raw[o:o + 8], "little")
            o += 8
            sl, o = _varint(raw, o)
            o += sl
        wit_start = o                                    # end of outputs
        if segwit:
            for _ in range(nin):
                items, o = _varint(raw, o)
                for _ in range(items):
                    ln, o = _varint(raw, o)
                    o += ln
        o += 4                                           # locktime
        total_size = o
        # txid hashes the LEGACY serialization: strip marker/flag + witness data
        base = raw[0:4] + raw[in_start:wit_start] + raw[o - 4:o] if segwit else raw[:o]
        txid = sha256(sha256(base).digest()).digest()[::-1].hex()
        weight = len(base) * 3 + total_size              # BIP 141
        return {"txid": txid, "size": total_size, "vsize": (weight + 3) // 4,
                "weight": weight, "nin": nin, "nout": nout, "out_sats": out_sats}
    except (IndexError, ValueError):
        return None
