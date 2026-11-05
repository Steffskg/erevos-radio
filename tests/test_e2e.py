# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Steff_SKG
"""
Δύο σταθμοί από άκρη σε άκρη, σε πραγματικό χρόνο, μέσα από
προσομοιωμένο αέρα (simair.py): ολόκληρη η στοίβα - ζεύξη, ACK,
επαναποστολές, ήχος (χωρίς κάρτα), modem, FEC.

Αργό (~1,5 λεπτό) γιατί ο χρόνος στον αέρα είναι ο πραγματικός.
"""

import time

import numpy as np
import pytest

import erevos_link as link
from simair import Air, drain, make_pair


@pytest.fixture
def pair(request, tmp_path, monkeypatch):
    monkeypatch.setattr(link, 'INBOX_DIR', str(tmp_path / 'inbox'))
    air = Air(**getattr(request, 'param', {}))
    a, b, ea, eb = make_pair(link, air)
    yield air, a, b, ea, eb
    a.stop()
    b.stop()


def wait_for(q, kind, n=1, timeout=10.0):
    got, end = [], time.time() + timeout
    while len(got) < n and time.time() < end:
        got += drain(q, kind)
        time.sleep(0.1)
    return got


def test_text(pair):
    air, a, b, ea, eb = pair
    assert a.send_text('N1CALL', 'Ola kala edo. QRV.')
    assert [kw['text'] for _, kw in wait_for(eb, 'text')] == \
        ['Ola kala edo. QRV.']
    assert a.stats['retries'] == 0


@pytest.mark.parametrize('pair', [{'snr_db': 0}], indirect=True)
def test_text_in_noise(pair):
    air, a, b, ea, eb = pair
    assert a.send_text('N1CALL', 'noise test 0 dB')
    assert wait_for(eb, 'text')[0][1]['text'] == 'noise test 0 dB'


@pytest.mark.parametrize('pair', [{'drop': {1, 2}}], indirect=True)
def test_lost_ack_is_recovered(pair):
    """Χάνονται και οι δύο επιβεβαιώσεις του B. Ο A ξαναστέλνει, ο B
    πρέπει να ξαναεπιβεβαιώσει - και να μην δείξει το μήνυμα δύο φορές."""
    air, a, b, ea, eb = pair
    assert a.send_text('N1CALL', 'hello')
    assert a.stats['retries'] == 1
    time.sleep(1.0)
    assert [kw['text'] for _, kw in drain(eb, 'text')] == ['hello']


def test_long_greek_text_arrives_complete(pair):
    air, a, b, ea, eb = pair
    rng = np.random.default_rng(11)
    text = ''.join(rng.choice(list('αβγδεζηθικλμνξοπρστυφχψωάέήίόύώ .,'),
                              260))
    assert a.send_text('N1CALL', text)
    parts = link.split_text(text)
    got = wait_for(eb, 'text', n=len(parts))
    assert len(parts) >= 2
    assert ''.join(kw['text'] for _, kw in got) == text


def test_file_transfer(pair, tmp_path):
    air, a, b, ea, eb = pair
    rng = np.random.default_rng(12)
    raw = bytes(rng.integers(0, 256, 450, dtype=np.uint8))   # 3 τμήματα
    src = tmp_path / 'data.bin'
    src.write_bytes(raw)
    assert a.send_file('N1CALL', str(src))
    done = wait_for(eb, 'file_done')
    assert done, 'file not received'
    with open(done[0][1]['path'], 'rb') as fh:
        assert fh.read() == raw
