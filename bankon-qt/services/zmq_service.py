"""ZMQ push subscriber — real-time block events from bitcoind, no polling.

The node publishes on tcp://127.0.0.1:283xx (configured in bitcoin.conf:
zmqpubhashblock=28332, zmqpubrawtx=28333, zmqpubsequence=28335). This runs on a
QThread so the UI thread never blocks; it emits Qt signals the Main window connects
to. During IBD `hashblock` fires on each connected block, so the UI updates push-driven.

We subscribe to `hashblock` only (one message per block — the right cadence for a
refresh trigger). `rawtx` is intentionally NOT subscribed during IBD because it would
fire for every transaction in every connected block and flood the UI; it can be added
post-sync for live mempool. Per the Qt guide, ZMQ block hashes arrive in little-endian
(internal) byte order — we reverse them for display/correlation with RPC.
"""
import struct
from PySide6 import QtCore

try:
    import zmq
except Exception:
    zmq = None

ZMQ_BLOCK = "tcp://127.0.0.1:28332"   # hashblock
ZMQ_TX    = "tcp://127.0.0.1:28333"   # rawtx (not subscribed during IBD)
ZMQ_SEQ   = "tcp://127.0.0.1:28335"   # sequence


class ZmqService(QtCore.QThread):
    block  = QtCore.Signal(str, int)    # block hash (big-endian display hex), sequence
    tx     = QtCore.Signal(str)         # raw-tx activity marker (size); off by default
    txraw  = QtCore.Signal(object)      # full raw tx bytes (with_tx only) — for exact parsing
    status = QtCore.Signal(bool, str)   # connected?, message

    def __init__(self, parent=None, with_tx=False):
        super().__init__(parent)
        self._stop = False
        self._with_tx = with_tx

    def run(self):
        if zmq is None:
            self.status.emit(False, "pyzmq not installed")
            return
        ctx = zmq.Context.instance()
        sock = ctx.socket(zmq.SUB)
        sock.setsockopt(zmq.RCVTIMEO, 500)    # 0.5s so stop() is acted on quickly
        sock.setsockopt(zmq.LINGER, 0)
        try:
            sock.connect(ZMQ_BLOCK)
            sock.setsockopt_string(zmq.SUBSCRIBE, "hashblock")
            if self._with_tx:
                sock.connect(ZMQ_TX)
                sock.setsockopt_string(zmq.SUBSCRIBE, "rawtx")
        except Exception as e:
            self.status.emit(False, f"zmq connect failed: {e}")
            try: sock.close(0)
            except Exception: pass
            return
        self.status.emit(True, "subscribed hashblock + rawtx" if self._with_tx else "subscribed hashblock")
        while not self._stop:
            try:
                parts = sock.recv_multipart()
            except Exception:               # zmq.Again (timeout) or shutdown
                continue
            if len(parts) < 2:
                continue
            topic, body = parts[0], parts[1]
            seq = struct.unpack("<I", parts[2])[0] if (len(parts) > 2 and len(parts[2]) == 4) else -1
            if topic == b"hashblock":
                self.block.emit(body[::-1].hex(), seq)   # LE → BE for display
            elif topic == b"rawtx":
                self.tx.emit(str(len(body)))
                self.txraw.emit(bytes(body))
        try: sock.close(0)
        except Exception: pass
        self.status.emit(False, "stopped")

    def stop(self):
        self._stop = True
        self.wait(1200)
