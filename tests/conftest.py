# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Steff_SKG
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import sounddevice  # noqa: F401
except Exception:                                       # noqa: BLE001
    # Χωρίς PortAudio (κοντέινερ, CI χωρίς ήχο). Κανένα test δεν ανοίγει
    # πραγματική συσκευή ήχου - ο "αέρας" προσομοιώνεται στο simair.py.
    stub = types.ModuleType('sounddevice')

    def _no_audio(*a, **k):
        raise RuntimeError('no audio device in tests')

    stub.query_devices = lambda *a, **k: []
    stub.default = types.SimpleNamespace(device=(None, None))
    stub.InputStream = stub.play = stub.rec = stub.playrec = _no_audio
    sys.modules['sounddevice'] = stub

import pytest  # noqa: E402

import erevos_link  # noqa: E402
import erevos_modem  # noqa: E402


@pytest.fixture(autouse=True)
def _defaults():
    """Κάθε test ξεκινά με τις προεπιλογές του modem και της ζεύξης."""
    erevos_modem.reset_defaults()
    erevos_link.reset_defaults()
    yield
    erevos_modem.reset_defaults()
    erevos_link.reset_defaults()
