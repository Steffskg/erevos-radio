#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Steff_SKG
"""
EREVOS DEBUG - διαγνωστικό ακουστικής διαδρομής   [v3]
======================================================
Παίζει ένα πλαίσιο και το ηχογραφεί ΤΑΥΤΟΧΡΟΝΑ (sample-synchronized),
μετά αναλύει κάθε στάδιο της αλυσίδας λήψης για να δούμε πού σπάει.

    python3 erevos_debug.py                    # προεπιλεγμένες συσκευές
    python3 erevos_debug.py --in 0 --out 0     # ρητή επιλογή
    python3 erevos_debug.py --amp 0.3          # χαμηλότερη ένταση εκπομπής
"""

import sys
import numpy as np
import sounddevice as sd

import erevos_fec as fec
from erevos_modem import (SAMPLE_RATE, F_MARK, F_SPACE, SYNC, TYPE_TEXT,
                          preamble, modulate, build_frame, receive,
                          demodulate_soft, bytes_to_bits, _find_sync)


def analyse(rx: np.ndarray, tx_bits: np.ndarray, label: str):
    print(f'\n--- {label} ---')

    peak = float(np.max(np.abs(rx)))
    rms  = float(np.sqrt(np.mean(rx ** 2)))
    clipped = float(np.mean(np.abs(rx) > 0.99) * 100)

    print(f'  Peak {peak:.3f} | RMS {rms:.4f} | clipping {clipped:.2f}%')
    if peak < 0.01:
        print('  !! Σχεδόν σιωπή - δεν έφτασε ήχος στο μικρόφωνο')
        return
    if clipped > 1.0:
        print('  !! Ψαλίδισμα - κατέβασε ένταση ηχείων ή ευαισθησία μικροφώνου')

    spec = np.abs(np.fft.rfft(rx * np.hanning(len(rx))))
    freqs = np.fft.rfftfreq(len(rx), 1 / SAMPLE_RATE)

    def band(f, w=60):
        m = (freqs > f - w) & (freqs < f + w)
        return float(np.max(spec[m])) if m.any() else 0.0

    mark, space, noise = band(F_MARK), band(F_SPACE), float(np.median(spec))
    print(f'  Φάσμα: mark {mark:.0f} | space {space:.0f} | θόρυβος {noise:.0f}')
    if mark < noise * 5 or space < noise * 5:
        print('  !! Οι τόνοι δεν ξεχωρίζουν από τον θόρυβο')

    soft = demodulate_soft(rx)
    hard = (soft > 0).astype(np.uint8)
    print(f'  Bits: {len(hard)} (αναμενόμενα {len(tx_bits)})')
    print(f'  Μέση εμπιστοσύνη: {float(np.mean(np.abs(soft))):.3f}')

    syncs = _find_sync(hard)
    print(f'  Sync words βρέθηκαν: {len(syncs)}')

    if len(syncs) == 0:
        sb = bytes_to_bits(SYNC)
        if len(hard) > len(sb):
            win = np.lib.stride_tricks.sliding_window_view(hard, len(sb))
            print(f'  Καλύτερο ταίριασμα sync: '
                  f'{int(np.max(np.sum(win == sb, axis=1)))}/{len(sb)} bits')
        print('  -> Το ρολόι/συγχρονισμός δεν κλείδωσε')
    else:
        # BER πριν το FEC, στο σημείο του πρώτου sync
        start = int(syncs[0])
        offset = len(bytes_to_bits(preamble() + SYNC))
        exp = tx_bits[offset:]
        got = hard[start:start + len(exp)]
        if len(got) == len(exp):
            errs = int(np.sum(got != exp))
            print(f'  BER πριν το FEC: {errs}/{len(exp)} '
                  f'= {100*errs/len(exp):.2f}%')

        plen = fec.decode_length(soft[start:start + fec.LEN_BITS])
        print(f'  Πεδίο μήκους: {plen}')

    frames = receive(rx)
    print(f'  Έγκυρα πλαίσια (μετά FEC + CRC): {len(frames)}')
    for f in frames:
        print(f'  >> "{f["payload"].decode(errors="replace")}"'
              f'  ({f["src"]} -> {f["dst"]})')


def main():
    def arg(name, cast, default=None):
        if name in sys.argv:
            return cast(sys.argv[sys.argv.index(name) + 1])
        return default

    in_dev  = arg('--in', int)
    out_dev = arg('--out', int)
    amp     = arg('--amp', float, 0.5)

    text = 'EREVOS debug'
    tx_bits = build_frame(TYPE_TEXT, 'N0CALL', 'CQ', text.encode())
    tx = modulate(tx_bits, amplitude=amp).astype(np.float64)

    pad = np.zeros(int(0.5 * SAMPLE_RATE))
    signal = np.concatenate([pad, tx, pad]).astype(np.float32)

    print(f'Εκπομπή {len(signal)/SAMPLE_RATE:.2f} s σε ένταση {amp}')
    print('(ταυτόχρονη ηχογράφηση)')

    device = (in_dev, out_dev) if (in_dev is not None
                                   or out_dev is not None) else None
    rec = sd.playrec(signal, samplerate=SAMPLE_RATE, channels=1,
                     device=device, blocking=True)
    rx = rec[:, 0].astype(np.float64)

    analyse(signal.astype(np.float64), tx_bits, 'ΑΝΑΦΟΡΑ (ψηφιακό, χωρίς ήχο)')
    analyse(rx, tx_bits, 'ΗΧΟΓΡΑΦΗΜΕΝΟ (ηχείο -> μικρόφωνο)')

    if np.max(np.abs(rx)) > 0:
        analyse(rx / np.max(np.abs(rx)) * 0.5, tx_bits,
                'ΗΧΟΓΡΑΦΗΜΕΝΟ + κανονικοποίηση στάθμης')

    np.save('erevos_debug_capture.npy', rx)
    print('\nΗ ηχογράφηση αποθηκεύτηκε: erevos_debug_capture.npy')


if __name__ == '__main__':
    main()
