#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Steff_SKG
"""
EREVOS PTT - έλεγχος εκπομπής
==============================
Πώς λέμε στον πομποδέκτη "τώρα εκπέμπω".

  VOX     - το radio ανιχνεύει τον ήχο και ανοίγει μόνο του.
            Δεν χρειάζεται τίποτα άλλο. Λειτουργεί και ακουστικά.
  ΣΕΙΡΙΑΚΗ - ένα σήμα στη σειριακή θύρα (DTR ή RTS) πατάει το PTT.
            Το χρησιμοποιεί το Digirig και τα περισσότερα interfaces.
            Πιο αξιόπιστο από το VOX, χωρίς κομμένες αρχές λέξεων.

    python3 erevos_ptt.py --list      # διαθέσιμες σειριακές θύρες
"""

import time

try:
    import serial                    # pyserial
    HAVE_SERIAL = True
except Exception:                    # noqa: BLE001
    serial = None
    HAVE_SERIAL = False


class NullPTT:
    """Δεν κάνει τίποτα - για VOX ή ακουστική λειτουργία."""
    name = 'VOX / κανένα'

    def key(self):    pass
    def unkey(self):  pass
    def close(self):  pass
    def available(self): return True


class SerialPTT:
    """Πατάει το PTT μέσω DTR ή RTS σε σειριακή θύρα."""

    def __init__(self, port: str, line: str = 'RTS',
                 lead: float = 0.15, tail: float = 0.10):
        if not HAVE_SERIAL:
            raise RuntimeError('Λείπει το pyserial '
                               '(pip3 install pyserial --break-system-packages)')
        self.port = port
        self.line = line.upper()
        self.lead = lead
        self.tail = tail
        self.name = f'Σειριακή {port} / {self.line}'
        self._ser = serial.Serial(port)
        self.unkey()

    def _set(self, state: bool):
        if self.line == 'DTR':
            self._ser.dtr = state
        else:
            self._ser.rts = state

    def key(self):
        self._set(True)
        time.sleep(self.lead)        # να προλάβει να ανοίξει ο πομπός

    def unkey(self):
        time.sleep(self.tail)
        self._set(False)

    def close(self):
        try:
            self._set(False)
            self._ser.close()
        except Exception:            # noqa: BLE001
            pass

    def available(self):
        return self._ser.is_open


def list_ports():
    """Διαθέσιμες σειριακές θύρες."""
    if not HAVE_SERIAL:
        return []
    try:
        from serial.tools import list_ports as lp
        return [(p.device, p.description) for p in lp.comports()]
    except Exception:                # noqa: BLE001
        return []


def make(kind: str, port: str = '', line: str = 'RTS',
         lead: float = 0.15, tail: float = 0.10):
    """Εργοστάσιο: 'vox' ή 'serial'."""
    if kind == 'serial' and port:
        return SerialPTT(port, line, lead, tail)
    return NullPTT()


if __name__ == '__main__':
    import sys
    if '--list' in sys.argv:
        if not HAVE_SERIAL:
            print('Το pyserial δεν είναι εγκατεστημένο.')
            print('pip3 install pyserial --break-system-packages')
        else:
            ports = list_ports()
            if not ports:
                print('Καμία σειριακή θύρα.')
            for dev, desc in ports:
                print(f'  {dev:<20} {desc}')
    else:
        print(__doc__)
