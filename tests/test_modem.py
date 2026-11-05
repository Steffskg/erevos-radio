# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Steff_SKG
"""Modem: πλαισίωση, CRC, διαμόρφωση/αποδιαμόρφωση με θόρυβο."""

import numpy as np
import pytest

import erevos_modem as m

PAD = np.zeros(m.SAMPLE_RATE // 4)


def roundtrip(payload, snr_db=None, rng=None, ftype=m.TYPE_TEXT):
    bits = m.build_frame(ftype, 'N0CALL', 'N1CALL', payload, msg_id=42)
    sig = m.modulate(bits).astype(np.float64)
    if snr_db is not None:
        p = np.mean(sig ** 2) / 10 ** (snr_db / 10)
        sig = sig + rng.normal(0, np.sqrt(p), len(sig))
    return m.receive(np.concatenate([PAD, sig, PAD]))


@pytest.mark.parametrize('fec', [True, False])
def test_clean_roundtrip(fec):
    m.configure(USE_FEC=fec)
    got = roundtrip(b'EREVOS test 123')
    assert len(got) == 1
    f = got[0]
    assert f['payload'] == b'EREVOS test 123'
    assert (f['src'], f['dst'], f['msg_id']) == ('N0CALL', 'N1CALL', 42)


def test_full_frame_200_bytes():
    payload = bytes(np.random.default_rng(3).integers(0, 256, 200,
                                                      dtype=np.uint8))
    got = roundtrip(payload)
    assert got and got[0]['payload'] == payload


def test_noise_with_fec():
    # Θόρυβος σε όλο το 0-24 kHz. Σε κανάλι φωνής 3 kHz το ίδιο σήμα
    # αντιστοιχεί σε ~9 dB καλύτερο SNR (εδώ: ~+6 dB).
    rng = np.random.default_rng(7)
    ok = sum(bool(g) and g[0]['payload'] == b'noise test ' * 5
             for g in (roundtrip(b'noise test ' * 5, -3, rng)
                       for _ in range(20)))
    assert ok >= 19


def test_selftest_all_pass(capsys):
    np.random.seed(0)
    passed, total = m._selftest()
    assert (passed, total) == (14, 14)


def test_crc_rejects_corruption():
    body = bytearray(m.build_body(m.TYPE_TEXT, 'N0CALL', 'CQ', b'hello'))
    assert m.parse_body(bytes(body))['payload'] == b'hello'
    body[-5] ^= 0x01
    assert m.parse_body(bytes(body)) is None


def test_payload_limit():
    with pytest.raises(ValueError):
        m.build_body(m.TYPE_TEXT, 'N0CALL', 'CQ', b'x' * (m.MAX_PAYLOAD + 1))


def test_zlib_flag_survives_framing():
    f = m.parse_body(m.build_body(m.TYPE_TEXT | m.FLAG_ZLIB, 'N0CALL', 'CQ',
                                  b'abc'))
    assert f['type'] == m.TYPE_TEXT and f['zlib'] is True


def test_two_frames_in_one_buffer():
    a = m.modulate(m.build_frame(m.TYPE_TEXT, 'N0CALL', 'CQ', b'one'))
    b = m.modulate(m.build_frame(m.TYPE_TEXT, 'N0CALL', 'CQ', b'two',
                                 msg_id=1))
    got = m.receive(np.concatenate([PAD, a, PAD, b, PAD]).astype(np.float64))
    assert [f['payload'] for f in got] == [b'one', b'two']


def test_throughput():
    assert 470 < m.throughput_bps() < 485
    m.configure(USE_FEC=False)
    assert 880 < m.throughput_bps() < 900


def test_removed_parameters_are_ignored():
    # Παλιές ρυθμίσεις (PLL_GAIN κ.λπ.) δεν πρέπει να σπάνε το configure.
    cur = m.configure(PLL_GAIN=0.5, TRANS_HYST=0.3, DECIM=4)
    assert 'PLL_GAIN' not in cur and 'DECIM' not in cur
    assert roundtrip(b'still works')[0]['payload'] == b'still works'
