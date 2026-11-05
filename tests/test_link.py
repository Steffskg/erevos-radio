# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Steff_SKG
"""
Επίπεδο ζεύξης χωρίς ήχο: τα πλαίσια περνάνε κατευθείαν στο _handle.
Ελέγχει συμπίεση, όρια αποσυμπίεσης, χωρισμό μηνυμάτων, παραλαβή
αρχείων και ασφάλεια ονομάτων αρχείων.
"""

import os
import struct
import zlib

import numpy as np
import pytest

import erevos_link as link
import erevos_modem as m


# -- συμπίεση ---------------------------------------------------------------

def test_pack_unpack_roundtrip():
    data = ('EREVOS ' * 40).encode()
    body, flag = link._pack(data)
    assert flag == m.FLAG_ZLIB and len(body) < len(data)
    assert link._unpack(body, True, 10_000) == data


def test_small_payload_not_compressed():
    assert link._pack(b'short') == (b'short', 0)


def test_unpack_refuses_zip_bomb():
    bomb = zlib.compress(b'\0' * 5_000_000, 9)          # ~5 KB -> 5 MB
    assert len(bomb) < 10_000
    assert link._unpack(bomb, True, limit=100_000) is None


def test_unpack_refuses_garbage():
    assert link._unpack(b'not zlib at all', True, 1000) is None


# -- χωρισμός μηνυμάτων ----------------------------------------------------

def _join(parts):
    return ''.join((link._unpack(b, True, 10_000) if f else b).decode()
                   for b, f in parts)


def test_short_text_is_one_frame():
    parts = link.split_text('hello')
    assert parts == [(b'hello', 0)]


@pytest.mark.parametrize('text', [
    ' '.join(np.random.default_rng(3).choice(                     # συμπιέζεται
        ['radio', 'erevos', 'mesh', 'node', 'signal', 'φάσμα', 'κεραία',
         'σταθμός', 'QRV', '73'], 400)),
    ''.join(np.random.default_rng(1).choice(list('αβγδεζηθικλμνξοπρστυφχψω'
                                                 'ΑΒΓΔΕΖΗΘ .,;'), 700)),
    ''.join(chr(c) for c in
            np.random.default_rng(2).integers(0x4E00, 0x9FFF, 300)),  # 3 bytes
], ids=['compressible', 'greek', 'cjk-3-bytes'])
def test_long_text_split_without_loss(text):
    parts = link.split_text(text)
    assert len(parts) > 1
    assert all(len(b) <= m.MAX_PAYLOAD for b, _ in parts)
    for b, f in parts:                     # κάθε κομμάτι είναι έγκυρο UTF-8
        (link._unpack(b, True, 10_000) if f else b).decode('utf-8')
    assert _join(parts) == text


def test_split_prefers_spaces():
    text = ' '.join(f'λέξη{i:03d}' for i in range(150))
    for b, f in link.split_text(text)[:-1]:
        chunk = (link._unpack(b, True, 10_000) if f else b).decode()
        assert chunk.endswith(' ')


# -- παραλαβή (χωρίς ήχο) --------------------------------------------------

@pytest.fixture
def station(tmp_path, monkeypatch):
    monkeypatch.setattr(link, 'INBOX_DIR', str(tmp_path / 'inbox'))
    events = []
    st = link.Station('N1CALL',
                      on_event=lambda kind, **kw: events.append((kind, kw)))
    st.acked = []
    st._ack = lambda f: st.acked.append((f['type'], f['seq']))
    st.events = events
    return st


def frame(ftype, payload, msg_id=1, seq=0, total=1, src='N0CALL',
          dst='N1CALL'):
    return m.parse_body(m.build_body(ftype, src, dst, payload, msg_id, seq,
                                     total))


def kinds(st, kind):
    return [kw for k, kw in st.events if k == kind]


def test_text_received_and_acked(station):
    station._handle(frame(m.TYPE_TEXT, 'Γεια σου'.encode()))
    assert kinds(station, 'text')[0]['text'] == 'Γεια σου'
    assert station.acked == [(m.TYPE_TEXT, 0)]


def test_retransmitted_text_shown_once_but_acked_again(station):
    f = frame(m.TYPE_TEXT, b'once')
    station._handle(f)
    station._handle(f)                 # επαναποστολή: χάθηκε το ACK μας
    assert len(kinds(station, 'text')) == 1
    assert len(station.acked) == 2


def test_frames_for_others_ignored(station):
    station._handle(frame(m.TYPE_TEXT, b'not mine', dst='N9XYZ'))
    assert not kinds(station, 'text') and not station.acked


def send_file(st, name, raw, compress=True, msg_id=5):
    data, flag = link._pack(raw) if compress else (raw, 0)
    chunks = [data[i:i + 200] for i in range(0, len(data), 200)] or [b'']
    meta = struct.pack('!I', len(raw)) + name.encode()
    st._handle(frame(m.TYPE_FILE_META | flag, meta, msg_id, link.SEQ_META,
                     len(chunks)))
    for i, ch in enumerate(chunks):
        st._handle(frame(m.TYPE_FILE_CHUNK, ch, msg_id, i, len(chunks)))
    return chunks


@pytest.mark.parametrize('compress', [True, False])
def test_file_reassembly(station, compress):
    raw = ('EREVOS line\n' * 100).encode() + bytes(range(256))
    send_file(station, 'notes.txt', raw, compress)
    done = kinds(station, 'file_done')
    assert len(done) == 1
    with open(done[0]['path'], 'rb') as fh:
        assert fh.read() == raw


def test_file_name_cannot_escape_inbox(station):
    send_file(station, '../../../etc/evil.txt', b'payload' * 10)
    path = kinds(station, 'file_done')[0]['path']
    assert os.path.dirname(path) == link.INBOX_DIR
    assert os.path.basename(path) == 'evil.txt'


def test_existing_file_not_overwritten(station):
    send_file(station, 'a.txt', b'first file content', msg_id=1)
    send_file(station, 'a.txt', b'second file content', msg_id=2)
    paths = [kw['path'] for kw in kinds(station, 'file_done')]
    assert len(set(paths)) == 2


def test_compressed_file_larger_than_declared_is_rejected(station):
    # Δηλώνει 100 bytes, αλλά το συμπιεσμένο περιεχόμενο είναι 2 MB.
    bomb = zlib.compress(b'\0' * 2_000_000, 9)
    chunks = [bomb[i:i + 200] for i in range(0, len(bomb), 200)]
    meta = struct.pack('!I', 100) + b'bomb.bin'
    station._handle(frame(m.TYPE_FILE_META | m.FLAG_ZLIB, meta, 9,
                          link.SEQ_META, len(chunks)))
    for i, ch in enumerate(chunks):
        station._handle(frame(m.TYPE_FILE_CHUNK, ch, 9, i, len(chunks)))
    assert not kinds(station, 'file_done')
    assert any('bomb.bin' in kw['message'] for kw in kinds(station, 'warn'))
    assert not os.path.exists(os.path.join(link.INBOX_DIR, 'bomb.bin'))


def test_oversized_file_refused_without_ack(station):
    meta = struct.pack('!I', link.MAX_FILE_BYTES + 1) + b'huge.bin'
    station._handle(frame(m.TYPE_FILE_META, meta, 3, link.SEQ_META, 10))
    assert not station.acked
    assert not station._files
    assert kinds(station, 'warn')


def test_chunk_outside_range_ignored(station):
    meta = struct.pack('!I', 4) + b'x.bin'
    station._handle(frame(m.TYPE_FILE_META, meta, 4, link.SEQ_META, 2))
    station._handle(frame(m.TYPE_FILE_CHUNK, b'zz', 4, 7, 2))   # seq 7 >= 2
    station._handle(frame(m.TYPE_FILE_CHUNK, b'ab', 4, 0, 2))
    assert not kinds(station, 'file_done')                      # λείπει το 1
    station._handle(frame(m.TYPE_FILE_CHUNK, b'cd', 4, 1, 2))
    with open(kinds(station, 'file_done')[0]['path'], 'rb') as fh:
        assert fh.read() == b'abcd'


def test_retransmitted_file_header_keeps_progress(station):
    meta = struct.pack('!I', 4) + b'y.bin'
    station._handle(frame(m.TYPE_FILE_META, meta, 6, link.SEQ_META, 2))
    station._handle(frame(m.TYPE_FILE_CHUNK, b'ab', 6, 0, 2))
    station._handle(frame(m.TYPE_FILE_META, meta, 6, link.SEQ_META, 2))
    station._handle(frame(m.TYPE_FILE_CHUNK, b'cd', 6, 1, 2))
    with open(kinds(station, 'file_done')[0]['path'], 'rb') as fh:
        assert fh.read() == b'abcd'
