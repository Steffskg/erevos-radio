# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Steff_SKG
"""
Προσομοιωμένος "αέρας" για δοκιμές χωρίς κάρτα ήχου.

Το SimEngine κληρονομεί το ΠΡΑΓΜΑΤΙΚΟ erevos_audio.AudioEngine και
αντικαθιστά μόνο το υλικό: αντί για μικρόφωνο, ένα νήμα δίνει μπλοκ
ήχου με τον ρυθμό της κάρτας ήχου (4096 δείγματα κάθε ~85 ms), και
αντί για ηχεία, η εκπομπή γράφεται στην "είσοδο" των άλλων σταθμών.
Η αποκωδικοποίηση, τα φίλτρα, ο χρονισμός και η αποφυγή διπλοτύπων
είναι ο κανονικός κώδικας.
"""

import collections
import threading
import time

import numpy as np

import erevos_audio as audio
from erevos_modem import SAMPLE_RATE


class Air:
    """Κοινό κανάλι. snr_db=None -> χωρίς θόρυβο πάνω στο σήμα."""

    def __init__(self, snr_db=None, noise_rms=0.002, seed=1, drop=()):
        self.engines = []
        self.snr_db = snr_db
        self.noise_rms = noise_rms
        self.rng = np.random.default_rng(seed)
        self.lock = threading.Lock()
        self.drop = set(drop)        # αριθμοί εκπομπών που "χάνονται"
        self.tx_count = 0
        self.log = []

    def engine(self, *a, **k):
        e = SimEngine(self, *a, **k)
        self.engines.append(e)
        return e


class SimEngine(audio.AudioEngine):

    def __init__(self, air, *a, **k):
        super().__init__(*a, **k)
        self.air = air
        self._pending = collections.deque()
        self._plock = threading.Lock()

    # -- "υλικό" ------------------------------------------------------------

    def start(self):
        self._running = True
        self.in_actual = self.in_dev
        self._reader = threading.Thread(target=self._feed, daemon=True)
        self._reader.start()

    def stop(self):
        self._running = False
        if self._reader:
            self._reader.join(timeout=1.5)
            self._reader = None

    def restart(self):
        pass

    def calibrate(self, seconds=2.0):
        self.noise_floor = self.air.noise_rms
        self._levels = [self.noise_floor] * 8
        self.calibrated = True
        return self.noise_floor

    def _feed(self):
        blk = audio.READ_BLOCK
        dt = blk / SAMPLE_RATE
        nxt = time.time()
        rng = np.random.default_rng(id(self) & 0xFFFF)
        while self._running:
            out = rng.normal(0, self.air.noise_rms, blk).astype(np.float32)
            with self._plock:
                need, pos = blk, 0
                while need and self._pending:
                    head = self._pending[0]
                    take = min(need, len(head))
                    out[pos:pos + take] += head[:take]
                    if take == len(head):
                        self._pending.popleft()
                    else:
                        self._pending[0] = head[take:]
                    pos += take
                    need -= take
            self.blocks_in += 1
            self.last_block_at = time.time()
            if not self._tx_gate.is_set():
                if self._q.qsize() >= audio.MAX_QUEUE:
                    self._q.get_nowait()
                    self.dropped += 1
                self._q.put(out)
            nxt += dt
            time.sleep(max(0.0, nxt - time.time()))

    def hear(self, sig):
        with self._plock:
            self._pending.append(sig.astype(np.float32))

    def transmit(self, frame_bits, amplitude=0.5, vox=True, ptt=None):
        sig = audio.build_tx_signal(frame_bits, amplitude, vox)
        dur = len(sig) / SAMPLE_RATE
        with self.air.lock:
            n = self.air.tx_count
            self.air.tx_count += 1
        lost = n in self.air.drop
        self.air.log.append((n, len(frame_bits), lost))
        self._tx_gate.set()
        try:
            if not lost:
                for e in self.air.engines:
                    if e is self:
                        continue
                    rx = sig.astype(np.float64)
                    if self.air.snr_db is not None:
                        p = np.mean(rx ** 2) / 10 ** (self.air.snr_db / 10)
                        rx = rx + self.air.rng.normal(0, np.sqrt(p), len(rx))
                    e.hear(rx)
            time.sleep(dur + audio.TX_SETTLE_SEC)    # το κανάλι είναι πιασμένο
        finally:
            self._tx_gate.clear()
            self._flush()
        return dur


def make_pair(link, air, call_a='N0CALL', call_b='N1CALL'):
    """Δύο σταθμοί στον ίδιο αέρα. Επιστρέφει (A, B, γεγονότα_A, γεγονότα_B)."""
    import queue
    orig = link.audio.AudioEngine
    link.audio.AudioEngine = lambda *a, **k: air.engine(*a, **k)
    try:
        ev_a, ev_b = queue.Queue(), queue.Queue()
        a = link.Station(call_a, on_event=lambda kind, **kw: ev_a.put((kind, kw)))
        b = link.Station(call_b, on_event=lambda kind, **kw: ev_b.put((kind, kw)))
    finally:
        link.audio.AudioEngine = orig
    a.start()
    b.start()
    return a, b, ev_a, ev_b


def drain(q, kind=None):
    out = []
    while not q.empty():
        k, kw = q.get_nowait()
        if kind is None or k == kind:
            out.append((k, kw))
    return out
