#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Steff_SKG
"""
EREVOS FEC - διόρθωση σφαλμάτων   [v2]
=======================================
Συνελικτικός κώδικας ρυθμού 1/2, μήκος περιορισμού K=7
(πολυώνυμα 0o171 / 0o133 - τα ίδια που χρησιμοποιεί η NASA/Voyager
και τα περισσότερα δορυφορικά συστήματα), με:

  * Διεμπλοκή (interleaving) - σκορπίζει τις ριπές σφαλμάτων
  * Αποκωδικοποίηση Viterbi ΗΠΙΑΣ απόφασης - αξιοποιεί το "πόσο
    σίγουρο" είναι κάθε bit, όχι μόνο το 0/1. Κερδίζει ~2 dB.

v2: το πεδίο μήκους περνάει κι αυτό από τον συνελικτικό κώδικα
    αντί για απλή επανάληψη - πιο ανθεκτικό ΚΑΙ πιο σύντομο
    (28 bits αντί για 40).

Κόστος: διπλασιάζει τα bits (1200 baud -> 600 bps ωφέλιμα).
Όφελος (μετρημένο με το --selftest, 60 bytes):
  τυχαία λάθη 2% -> 20/20 σωστά, 5% -> ~17/20, 8% -> ~2/20
  ριπές έως 60 συνεχόμενα bits -> 20/20

    python3 erevos_fec.py --selftest
"""

import numpy as np

K      = 7                    # μήκος περιορισμού
G1     = 0o171                # πολυώνυμο 1
G2     = 0o133                # πολυώνυμο 2
NSTATE = 1 << (K - 1)         # 64 καταστάσεις
INTERLEAVE_WIDTH = 16


# ---------------------------------------------------------------------------
# Πίνακες κώδικα (υπολογίζονται μία φορά)
# ---------------------------------------------------------------------------

def _parity(x: int) -> int:
    x ^= x >> 4
    x ^= x >> 2
    x ^= x >> 1
    return x & 1


_OUT  = np.zeros((NSTATE, 2, 2), dtype=np.int8)    # [state][bit][έξοδος]
_NEXT = np.zeros((NSTATE, 2), dtype=np.int64)      # [state][bit] -> state

for _s in range(NSTATE):
    for _b in (0, 1):
        _full = (_s << 1) | _b
        _OUT[_s, _b, 0] = _parity(_full & G1)
        _OUT[_s, _b, 1] = _parity(_full & G2)
        _NEXT[_s, _b]   = _full & (NSTATE - 1)

# Προκάτοχοι κάθε κατάστασης (ακριβώς 2)
_PRED = np.zeros((NSTATE, 2, 2), dtype=np.int64)   # [next][k] = (state, bit)
_cnt = np.zeros(NSTATE, dtype=np.int64)
for _s in range(NSTATE):
    for _b in (0, 1):
        _ns = _NEXT[_s, _b]
        _PRED[_ns, _cnt[_ns]] = (_s, _b)
        _cnt[_ns] += 1

# Προϋπολογισμένα πρόσημα εξόδου: +1 για bit 1, -1 για bit 0
_SIGN = (2 * _OUT.astype(np.float64) - 1)          # (64, 2, 2)

_P0S, _P0B = _PRED[:, 0, 0], _PRED[:, 0, 1]
_P1S, _P1B = _PRED[:, 1, 0], _PRED[:, 1, 1]


# ---------------------------------------------------------------------------
# Βοηθητικά
# ---------------------------------------------------------------------------

def _to_bits(data: bytes) -> np.ndarray:
    return np.unpackbits(np.frombuffer(data, dtype=np.uint8))


def _to_bytes(bits: np.ndarray) -> bytes:
    bits = np.asarray(bits, dtype=np.uint8)
    bits = bits[:len(bits) - (len(bits) % 8)]
    return np.packbits(bits).tobytes()


def coded_length(nbytes: int) -> int:
    """Πόσα bits στον αέρα για nbytes ωφέλιμων δεδομένων."""
    return (nbytes * 8 + K - 1) * 2


# ---------------------------------------------------------------------------
# Διεμπλοκή
# ---------------------------------------------------------------------------

def interleave(bits: np.ndarray, width: int = INTERLEAVE_WIDTH) -> np.ndarray:
    n = len(bits)
    rows = int(np.ceil(n / width))
    buf = np.zeros(rows * width, dtype=bits.dtype)
    buf[:n] = bits
    return buf.reshape(rows, width).T.reshape(-1)


def deinterleave(vals: np.ndarray, n: int,
                 width: int = INTERLEAVE_WIDTH) -> np.ndarray:
    rows = int(np.ceil(n / width))
    need = rows * width
    v = np.asarray(vals, dtype=np.float64)
    if len(v) < need:
        v = np.concatenate([v, np.zeros(need - len(v))])
    return v[:need].reshape(width, rows).T.reshape(-1)[:n]


# ---------------------------------------------------------------------------
# Κωδικοποίηση
# ---------------------------------------------------------------------------

def conv_encode(bits: np.ndarray) -> np.ndarray:
    """Συνελικτική κωδικοποίηση με τερματισμό στην κατάσταση 0."""
    padded = np.concatenate([np.asarray(bits, dtype=np.uint8),
                             np.zeros(K - 1, dtype=np.uint8)])
    out = np.empty(len(padded) * 2, dtype=np.uint8)
    s = 0
    for i, b in enumerate(padded):
        b = int(b)
        out[2 * i]     = _OUT[s, b, 0]
        out[2 * i + 1] = _OUT[s, b, 1]
        s = int(_NEXT[s, b])
    return out


def encode(data: bytes) -> np.ndarray:
    """bytes -> bits έτοιμα για διαμόρφωση (κωδικοποιημένα + διεμπλεγμένα)."""
    return interleave(conv_encode(_to_bits(data)))


# ---------------------------------------------------------------------------
# Αποκωδικοποίηση (Viterbi, ήπια απόφαση)
# ---------------------------------------------------------------------------

def viterbi(soft: np.ndarray) -> np.ndarray:
    """
    soft: τιμές όπου θετικό = '1', αρνητικό = '0', μέτρο = εμπιστοσύνη.
    Επιστρέφει τα αποκωδικοποιημένα bits (χωρίς τα bits τερματισμού).
    """
    T = len(soft) // 2
    if T <= K:
        return np.array([], dtype=np.uint8)

    soft = np.asarray(soft, dtype=np.float64)
    metrics = np.full(NSTATE, -1e18)
    metrics[0] = 0.0
    dec = np.zeros((T, NSTATE), dtype=np.uint8)

    for t in range(T):
        bm = soft[2 * t] * _SIGN[:, :, 0] + soft[2 * t + 1] * _SIGN[:, :, 1]
        cand = metrics[:, None] + bm

        c0 = cand[_P0S, _P0B]
        c1 = cand[_P1S, _P1B]
        take1 = c1 > c0
        metrics = np.where(take1, c1, c0)
        dec[t] = take1

        m = metrics.max()
        if m > 1e12:
            metrics -= m

    # Ανίχνευση προς τα πίσω από την κατάσταση 0 (τερματισμένη)
    s = 0
    bits = np.zeros(T, dtype=np.uint8)
    for t in range(T - 1, -1, -1):
        k = dec[t, s]
        bits[t] = _PRED[s, k, 1]
        s = int(_PRED[s, k, 0])

    return bits[:T - (K - 1)]


def decode(soft: np.ndarray, nbytes: int) -> bytes:
    """Ήπιες τιμές -> bytes. Χρειάζεται το αναμενόμενο μήκος."""
    n = coded_length(nbytes)
    return _to_bytes(viterbi(deinterleave(soft, n)))


# ---------------------------------------------------------------------------
# Πεδίο μήκους - μικρό, σταθερού μεγέθους, κωδικοποιημένο
# ---------------------------------------------------------------------------

LEN_BITS = coded_length(1)        # 28 bits


def encode_length(value: int) -> np.ndarray:
    """Το μήκος πρέπει να επιβιώσει ό,τι κι αν γίνει."""
    return interleave(conv_encode(_to_bits(bytes([value & 0xFF]))), width=4)


def decode_length(soft: np.ndarray) -> int:
    bits = viterbi(deinterleave(soft[:LEN_BITS], LEN_BITS, width=4))
    if len(bits) < 8:
        return -1
    return int(np.packbits(bits[:8])[0])


# ---------------------------------------------------------------------------
# Αυτοδιαγνωστικό
# ---------------------------------------------------------------------------

def _selftest():
    rng = np.random.default_rng(1)
    print('EREVOS FEC v2 - αυτοδιαγνωστικό')
    print(f'  Ρυθμός 1/2, K={K}, καταστάσεις={NSTATE}')
    print(f'  Διεμπλοκή πλάτους {INTERLEAVE_WIDTH}')
    print(f'  Πεδίο μήκους: {LEN_BITS} bits\n')

    data = bytes(rng.integers(0, 256, 60, dtype=np.uint8))
    coded = encode(data)
    print(f'  {len(data)} bytes -> {len(coded)} bits στον αέρα')
    print(f'  (χωρίς FEC θα ήταν {len(data)*8})\n')

    print('  Τυχαία σφάλματα:')
    for ber in (0.00, 0.02, 0.05, 0.08, 0.10):
        okc = 0
        for _ in range(20):
            soft = (2.0 * coded - 1.0)
            flip = rng.random(len(soft)) < ber
            soft[flip] *= -1
            soft += rng.normal(0, 0.3, len(soft))
            okc += (decode(soft, len(data)) == data)
        print(f'    BER {ber*100:>5.1f}%  ->  {okc:>2}/20 σωστά')

    print('\n  Ριπές σφαλμάτων (συνεχόμενα bits):')
    for burst in (10, 20, 40, 60):
        okc = 0
        for _ in range(20):
            soft = (2.0 * coded - 1.0)
            start = int(rng.integers(0, len(soft) - burst))
            soft[start:start + burst] = rng.normal(0, 0.2, burst)
            okc += (decode(soft, len(data)) == data)
        print(f'    ριπή {burst:>3} bits ->  {okc:>2}/20 σωστά')

    print('\n  Πεδίο μήκους:')
    for ber in (0.05, 0.10, 0.20, 0.30):
        okc = 0
        for _ in range(50):
            v = 2.0 * encode_length(200).astype(np.float64) - 1.0
            flip = rng.random(len(v)) < ber
            v[flip] *= -1
            okc += (decode_length(v) == 200)
        print(f'    BER {ber*100:>5.1f}%  ->  {okc:>2}/50 σωστά')


if __name__ == '__main__':
    import sys
    if '--selftest' in sys.argv:
        _selftest()
    else:
        print(__doc__)
