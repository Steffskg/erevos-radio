#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Steff_SKG
"""
EREVOS RADIO - μέτρηση απόδοσης modem   /   modem benchmark
===========================================================
Οι αριθμοί του README βγαίνουν από αυτό το script.
The numbers in the README come from this script.

Λευκός θόρυβος σε ΟΛΟ το εύρος 0-24 kHz (δειγματοληψία 48 kHz).
Σε κανάλι φωνής ~3 kHz το ίδιο σήμα έχει ~9 dB καλύτερο SNR.

White noise across the FULL 0-24 kHz band (48 kHz sampling).
In a ~3 kHz voice channel the same signal has ~9 dB better SNR.

    python3 tools/benchmark.py            # 40 δοκιμές ανά σημείο
    python3 tools/benchmark.py --n 100
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import erevos_modem as m  # noqa: E402

BAND_GAIN_DB = 10 * np.log10(24000 / 3000)          # ~9.0 dB


def run(n=40, payload_len=100, snrs=(6, 3, 0, -3, -6, -9), seed=100):
    text = (b'EREVOS benchmark 0123456789 ' * 10)[:payload_len]
    pad = np.zeros(m.SAMPLE_RATE // 4)
    print(f'{n} trials per point, {payload_len}-byte payload, '
          f'seed {seed}\n')
    print(f'{"SNR 0-24 kHz":>13} {"≈ SNR 3 kHz":>12} {"FEC":>8} '
          f'{"no FEC":>8}')
    rows = []
    for snr in snrs:
        res = []
        for fec in (True, False):
            m.reset_defaults()
            m.configure(USE_FEC=fec)
            sig = m.modulate(m.build_frame(m.TYPE_TEXT, 'N0CALL', 'N1CALL',
                                           text)).astype(np.float64)
            rng = np.random.default_rng(seed + snr)
            ok = 0
            for _ in range(n):
                p = np.mean(sig ** 2) / 10 ** (snr / 10)
                rx = sig + rng.normal(0, np.sqrt(p), len(sig))
                got = m.receive(np.concatenate([pad, rx, pad]))
                ok += bool(got) and got[0]['payload'] == text
            res.append(ok)
        rows.append((snr, *res))
        print(f'{snr:>10} dB {snr + BAND_GAIN_DB:>+9.0f} dB '
              f'{res[0]:>5}/{n} {res[1]:>5}/{n}')
    m.reset_defaults()
    print(f'\nthroughput: {m.throughput_bps():.0f} bps with FEC, ', end='')
    m.configure(USE_FEC=False)
    print(f'{m.throughput_bps():.0f} bps without')
    m.reset_defaults()
    return rows


if __name__ == '__main__':
    n = int(sys.argv[sys.argv.index('--n') + 1]) if '--n' in sys.argv else 40
    run(n)
