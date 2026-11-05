# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Steff_SKG
"""FEC: συνελικτικός κώδικας 1/2 K=7, Viterbi ήπιας απόφασης, διεμπλοκή."""

import numpy as np

import erevos_fec as fec

RNG = np.random.default_rng(1)
DATA = bytes(RNG.integers(0, 256, 60, dtype=np.uint8))
CODED = fec.encode(DATA)


def soft(bits):
    return 2.0 * bits.astype(np.float64) - 1.0


def test_coded_length():
    # Ο κώδικας δίνει (8n + 6) * 2 bits. Η διεμπλοκή συμπληρώνει μέχρι
    # πολλαπλάσιο του πλάτους της (16).
    n = fec.coded_length(len(DATA))
    assert n == (60 * 8 + 6) * 2
    assert len(CODED) == -(-n // fec.INTERLEAVE_WIDTH) * fec.INTERLEAVE_WIDTH


def test_no_errors():
    assert fec.decode(soft(CODED), len(DATA)) == DATA


def test_random_errors_2_percent():
    rng = np.random.default_rng(2)
    ok = 0
    for _ in range(20):
        s = soft(CODED)
        s[rng.random(len(s)) < 0.02] *= -1
        s += rng.normal(0, 0.3, len(s))
        ok += fec.decode(s, len(DATA)) == DATA
    assert ok >= 19


def test_burst_60_bits():
    rng = np.random.default_rng(3)
    for _ in range(20):
        s = soft(CODED)
        start = int(rng.integers(0, len(s) - 60))
        s[start:start + 60] = rng.normal(0, 0.2, 60)
        assert fec.decode(s, len(DATA)) == DATA


def test_length_field():
    rng = np.random.default_rng(4)
    for value in (0, 1, 27, 200, 255):
        v = soft(fec.encode_length(value))
        assert fec.decode_length(v) == value
    ok = 0
    for _ in range(50):
        v = soft(fec.encode_length(200))
        v[rng.random(len(v)) < 0.05] *= -1
        ok += fec.decode_length(v) == 200
    assert ok >= 48
