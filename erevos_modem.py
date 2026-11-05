#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Steff_SKG
"""
EREVOS MODEM - AFSK (Bell 202 συμβατό)   [v5]
==============================================
Πυρήνας: bytes <-> ηχητικό σήμα. Καμία εξάρτηση από FLDigi/VARA.

Δομή πλαισίου στον αέρα:

  [PREAMBLE]  32 bytes 0x55 - ανάκτηση ρολογιού
  [SYNC]       4 bytes      - εντοπισμός αρχής (πάντα ακωδικοποίητο)
  [LEN]        μήκος ωφέλιμου φορτίου
  [BODY]       header + payload + CRC
  [TRAILER]    4 bytes 0x55 - να μην κοπεί το τελευταίο bit

Με FEC (προεπιλογή) το LEN και το BODY περνούν από συνελικτικό
κώδικα ρυθμού 1/2 - διπλάσια bits, αλλά διορθώνει τυχαία σφάλματα
έως ~2-5% και ριπές έως 60 bits (βλ. erevos_fec.py --selftest).
Χωρίς FEC είναι διπλάσια γρήγορο αλλά θέλει καθαρή γραμμή.

Όλες οι παράμετροι αλλάζουν από την εφαρμογή κατά την εκτέλεση
μέσω της configure().

    python3 erevos_modem.py --selftest
"""

import struct
import numpy as np

import erevos_fec as fec

# ---------------------------------------------------------------------------
# Παράμετροι (μεταβλητές - αλλάζουν με configure())
# ---------------------------------------------------------------------------

SAMPLE_RATE = 48000
BAUD        = 1200
F_MARK      = 1200
F_SPACE     = 2200
SPS         = SAMPLE_RATE // BAUD

USE_FEC     = True

PREAMBLE_BYTES = 32
SYNC     = bytes([0x2D, 0xD4, 0x5A, 0xE3])
TRAILER  = b'\x55' * 4

PROTOCOL_VERSION = 1

TYPE_TEXT       = 0
TYPE_FILE_META  = 1
TYPE_FILE_CHUNK = 2
TYPE_ACK        = 3

TYPE_NAMES = {0: 'κείμενο', 1: 'αρχείο-κεφαλίδα',
              2: 'αρχείο-τμήμα', 3: 'επιβεβαίωση'}

# Το πάνω bit του τύπου δηλώνει συμπιεσμένο ωφέλιμο φορτίο.
# Έτσι δεν αλλάζει καθόλου η δομή του πλαισίου - παλιοί και νέοι
# σταθμοί συνεννοούνται, αρκεί να μην στέλνουν συμπιεσμένα.
TYPE_MASK = 0x7F
FLAG_ZLIB = 0x80

CALLSIGN_LEN = 9
MAX_PAYLOAD  = 200

HEADER_FMT  = '!BB{0}s{0}sHHHB'.format(CALLSIGN_LEN)
HEADER_SIZE = struct.calcsize(HEADER_FMT)

# --- αποδιαμορφωτής ---
# (Οι παλιές παράμετροι DECIM / PLL_GAIN / TRANS_HYST ανήκαν στον
#  αποδιαμορφωτή με PLL, που αντικαταστάθηκε από τον Oerder-Meyr.
#  Δεν επηρέαζαν τίποτα και αφαιρέθηκαν.)
SYNC_MAX_ERR   = 2
MAX_CANDIDATES = 12
AGC_WIN_BITS   = 48
LOWPASS_DIV    = 3       # μήκος χαμηλοπερατού = SPS / αυτό

DEFAULTS = dict(BAUD=1200, F_MARK=1200, F_SPACE=2200, USE_FEC=True,
                SYNC_MAX_ERR=2, MAX_CANDIDATES=12, AGC_WIN_BITS=48,
                LOWPASS_DIV=3, PREAMBLE_BYTES=32)


def configure(**kw):
    """Αλλάζει παραμέτρους κατά την εκτέλεση. Επιστρέφει τι ισχύει."""
    g = globals()
    for k, v in kw.items():
        if k in DEFAULTS:
            g[k] = v
    g['SPS'] = max(4, SAMPLE_RATE // g['BAUD'])
    return current()


def current():
    g = globals()
    d = {k: g[k] for k in DEFAULTS}
    d['SPS'] = g['SPS']
    return d


def reset_defaults():
    return configure(**DEFAULTS)


def preamble() -> bytes:
    return b'\x55' * PREAMBLE_BYTES


# ---------------------------------------------------------------------------
# CRC-16/CCITT-FALSE
# ---------------------------------------------------------------------------

def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 \
                else (crc << 1) & 0xFFFF
    return crc


def bytes_to_bits(data: bytes) -> np.ndarray:
    return np.unpackbits(np.frombuffer(data, dtype=np.uint8))


def bits_to_bytes(bits) -> bytes:
    bits = np.asarray(bits, dtype=np.uint8)
    bits = bits[:len(bits) - (len(bits) % 8)]
    return np.packbits(bits).tobytes()


# ---------------------------------------------------------------------------
# Τυχαιοποίηση (scrambler)
# ---------------------------------------------------------------------------
# Χωρίς αυτό, ένα ωφέλιμο φορτίο με πολλά μηδενικά (π.χ. διακριτικά
# συμπληρωμένα με 0x00) δίνει μεγάλες σειρές από ίδιο τόνο. Τότε:
#   - το ρολόι δεν έχει μεταβάσεις για να κλειδώσει
#   - η αυτόματη εξισορρόπηση νομίζει ότι η σειρά είναι "μεροληψία"
#     και την αντιστρέφει
# Η XOR με μια ψευδοτυχαία ακολουθία (ίδια και στις δύο πλευρές)
# εγγυάται ισορροπημένη ροή ό,τι κι αν στέλνουμε.

_PRBS_CACHE = None


def _prbs(n: int) -> np.ndarray:
    """Ακολουθία PRBS-9 (x^9 + x^5 + 1), ντετερμινιστική."""
    global _PRBS_CACHE
    if _PRBS_CACHE is None or len(_PRBS_CACHE) < n:
        size = max(n, 8192)
        reg = 0x1FF
        out = np.empty(size, dtype=np.uint8)
        for i in range(size):
            bit = ((reg >> 8) ^ (reg >> 4)) & 1
            out[i] = bit
            reg = ((reg << 1) | bit) & 0x1FF
        _PRBS_CACHE = out
    return _PRBS_CACHE[:n]


def scramble(bits: np.ndarray) -> np.ndarray:
    return np.bitwise_xor(np.asarray(bits, dtype=np.uint8), _prbs(len(bits)))


def descramble_soft(soft: np.ndarray) -> np.ndarray:
    """Στις ήπιες τιμές, το XOR με 1 σημαίνει αλλαγή προσήμου."""
    return np.asarray(soft, dtype=np.float64) * (1.0 - 2.0 * _prbs(len(soft)))


# ---------------------------------------------------------------------------
# Πλαισίωση
# ---------------------------------------------------------------------------

def build_body(ftype, src, dst, payload, msg_id=0, seq=0, total=1) -> bytes:
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f'payload > {MAX_PAYLOAD} bytes')
    header = struct.pack(
        HEADER_FMT, PROTOCOL_VERSION, ftype,
        src.upper().encode()[:CALLSIGN_LEN].ljust(CALLSIGN_LEN, b'\x00'),
        dst.upper().encode()[:CALLSIGN_LEN].ljust(CALLSIGN_LEN, b'\x00'),
        msg_id, seq, total, len(payload))
    body = header + payload
    return body + struct.pack('!H', crc16(body))


def build_frame(ftype, src, dst, payload, msg_id=0, seq=0,
                total=1) -> np.ndarray:
    """Πλήρες πλαίσιο ως ροή bits."""
    body = build_body(ftype, src, dst, payload, msg_id, seq, total)
    head = bytes_to_bits(preamble() + SYNC)

    if USE_FEC:
        mid = np.concatenate([fec.encode_length(len(payload)),
                              fec.encode(body)])
    else:
        mid = np.concatenate([bytes_to_bits(bytes([len(payload)])),
                              bytes_to_bits(body)])

    return np.concatenate([head, scramble(mid),
                           bytes_to_bits(TRAILER)]).astype(np.uint8)


def parse_body(body: bytes):
    if len(body) < HEADER_SIZE + 2:
        return None
    (ver, ftype, src, dst, msg_id, seq, total, plen) = struct.unpack(
        HEADER_FMT, body[:HEADER_SIZE])
    if ver != PROTOCOL_VERSION or plen > MAX_PAYLOAD:
        return None
    end = HEADER_SIZE + plen
    if len(body) < end + 2:
        return None
    if crc16(body[:end]) != struct.unpack('!H', body[end:end + 2])[0]:
        return None
    return {'type': ftype & TYPE_MASK,
            'zlib': bool(ftype & FLAG_ZLIB),
            'src': src.rstrip(b'\x00').decode(errors='replace'),
            'dst': dst.rstrip(b'\x00').decode(errors='replace'),
            'msg_id': msg_id, 'seq': seq, 'total': total,
            'payload': body[HEADER_SIZE:end]}


def frame_bits(payload_len: int) -> int:
    """Πόσα bits πιάνει ένα πλαίσιο με τόσο ωφέλιμο φορτίο."""
    nbytes = HEADER_SIZE + payload_len + 2
    head = (PREAMBLE_BYTES + len(SYNC) + len(TRAILER)) * 8
    if USE_FEC:
        return head + fec.LEN_BITS + fec.coded_length(nbytes)
    return head + 8 + nbytes * 8


def frame_seconds(payload_len: int) -> float:
    return frame_bits(payload_len) / BAUD


def throughput_bps() -> float:
    """Ωφέλιμος ρυθμός με πλήρη πλαίσια."""
    return MAX_PAYLOAD * 8 / frame_seconds(MAX_PAYLOAD)


# ---------------------------------------------------------------------------
# Διαμόρφωση
# ---------------------------------------------------------------------------

def modulate(data, amplitude: float = 0.5) -> np.ndarray:
    bits = bytes_to_bits(data) if isinstance(data, (bytes, bytearray)) \
        else np.asarray(data, dtype=np.uint8)
    freqs = np.where(bits == 1, F_MARK, F_SPACE).astype(np.float64)
    phase = np.cumsum(np.repeat(freqs, SPS) * (2 * np.pi / SAMPLE_RATE))
    signal = amplitude * np.sin(phase)
    fade = int(0.003 * SAMPLE_RATE)
    if len(signal) > 2 * fade:
        ramp = np.linspace(0, 1, fade)
        signal[:fade] *= ramp
        signal[-fade:] *= ramp[::-1]
    return signal.astype(np.float32)


# ---------------------------------------------------------------------------
# Αποδιαμόρφωση
# ---------------------------------------------------------------------------

# --- ταχύτητα: όλα διανυσματοποιημένα ---------------------------------
# Ο παλιός βρόχος Python κρατούσε το GIL για >1 δευτ. σε αργό μηχάνημα,
# με αποτέλεσμα το callback της κάρτας ήχου να μην προλαβαίνει να τρέξει
# και το ρεύμα εισόδου να πεθαίνει από overflow. Τώρα δεν υπάρχει
# καθόλου βρόχος ανά δείγμα.

_OSC_CACHE = {}


def _osc(n: int, freq: float):
    """Ταλαντωτής cos/sin - υπολογίζεται μία φορά ανά μήκος/συχνότητα."""
    key = (n, float(freq), SAMPLE_RATE)
    hit = _OSC_CACHE.get(key)
    if hit is None:
        if len(_OSC_CACHE) > 12:
            _OSC_CACHE.clear()
        w = 2 * np.pi * freq * np.arange(n) / SAMPLE_RATE
        hit = (np.cos(w), np.sin(w))
        _OSC_CACHE[key] = hit
    return hit


def _boxcar(x: np.ndarray, n: int) -> np.ndarray:
    """Κυλιόμενος μέσος με cumsum: O(N) αντί για O(N*n)."""
    n = max(1, min(int(n), len(x)))
    if n <= 1:
        return x.astype(np.float64, copy=True)
    c = np.concatenate(([0.0], np.cumsum(x, dtype=np.float64)))
    run = (c[n:] - c[:-n]) / n
    left = (n - 1) // 2
    right = len(x) - len(run) - left
    return np.concatenate([np.full(left, run[0]), run,
                           np.full(max(0, right), run[-1])])[:len(x)]


def _correlate(signal: np.ndarray, freq: float) -> np.ndarray:
    c, s = _osc(len(signal), freq)
    return np.hypot(_boxcar(signal * c, SPS), _boxcar(signal * s, SPS))


def tone_levels(signal):
    """Μέσα πλάτη mark και space - για τη ρύθμιση στάθμης."""
    s = np.asarray(signal, dtype=np.float64)
    s = s - np.mean(s)
    return (float(np.mean(_correlate(s, F_MARK))),
            float(np.mean(_correlate(s, F_SPACE))))


def discriminator(signal):
    """
    Ήχος -> σήμα διάκρισης γύρω από το 0 (+ = mark, - = space).
      1. λόγος (mark-space)/(mark+space)  -> ανεξαρτησία στάθμης
      2. χαμηλοπερατό                     -> καθαρίζει ψευδείς μεταβάσεις
      3. αφαίρεση μεροληψίας + κανονικοποίηση κέρδους
         -> διορθώνει άνιση απόκριση ηχείων/μικροφώνου/πομποδέκτη
    """
    s = np.asarray(signal, dtype=np.float64)
    s = s - np.mean(s)

    mark, space = _correlate(s, F_MARK), _correlate(s, F_SPACE)
    total = mark + space
    total[total < 1e-9] = 1e-9
    diff = (mark - space) / total

    diff = _boxcar(diff, max(3, SPS // max(1, LOWPASS_DIV)))

    win = AGC_WIN_BITS * SPS
    diff = diff - _boxcar(diff, win)
    scale = np.maximum(_boxcar(np.abs(diff), win), 1e-6)
    return np.clip(diff / scale, -2.0, 2.0)


def _timing(diff: np.ndarray, sps: int):
    """
    Ανάκτηση ρολογιού Oerder-Meyr, χωρίς βρόχο ανά δείγμα.

    Υψώνουμε το σήμα στο τετράγωνο: εμφανίζεται καθαρή φασματική
    γραμμή ακριβώς στον ρυθμό των bits. Η ΦΑΣΗ αυτής της γραμμής
    είναι η στιγμή δειγματοληψίας. Επειδή προκύπτει από άθροισμα
    πάνω σε χιλιάδες δείγματα, ο θόρυβος αλληλοαναιρείται.

    Το κάνουμε σε μπλοκ και προσαρμόζουμε ευθεία, ώστε να
    παρακολουθείται και η απόκλιση ρολογιού μεταξύ των δύο καρτών.
    """
    n = len(diff)
    if n < sps * 8:
        return 0.0, 0.0

    x = diff * diff
    x = x - x.mean()
    e = np.exp(-2j * np.pi * np.arange(n) / sps)
    xe = x * e

    blk = int(48 * sps)
    nb = max(1, n // blk)
    if nb < 2:
        tau = -np.angle(np.sum(xe)) / (2 * np.pi) * sps
        return float(tau % sps), 0.0

    edges = np.linspace(0, n, nb + 1).astype(int)
    sums = np.add.reduceat(xe, edges[:-1])
    mag = np.abs(sums)

    # ΚΡΙΣΙΜΟ: τα μπλοκ που περιέχουν μόνο σιωπή δίνουν angle(0)=0,
    # δηλαδή μια εντελώς ψεύτικη ένδειξη φάσης. Αν μπουν στην
    # προσαρμογή, τραβάνε τον χρονισμό εκτός και χάνεται ο
    # συγχρονισμός - ανάλογα με το πού τυχαίνει να πέσουν, δηλαδή
    # ανάλογα με το μήκος του πλαισίου. Κρατάμε μόνο τα μπλοκ με
    # πραγματική ενέργεια, και σταθμίζουμε με αυτήν.
    if mag.max() <= 0:
        return 0.0, 0.0
    keep = mag > max(mag.max() * 0.20, 1e-12)
    if keep.sum() < 2:
        tau = -np.angle(sums[int(np.argmax(mag))]) / (2 * np.pi) * sps
        return float(tau % sps), 0.0

    centers = ((edges[:-1] + edges[1:]) / 2.0)[keep]
    taus = -np.angle(sums[keep]) / (2 * np.pi) * sps
    weights = mag[keep]

    # ξετύλιγμα της φάσης ανά μπλοκ
    taus = np.unwrap(taus / sps * 2 * np.pi) / (2 * np.pi) * sps

    a, b = float(taus[0]), 0.0
    if len(centers) >= 3:
        w = np.sqrt(weights / weights.max())
        A = np.vstack([np.ones_like(centers), centers]).T * w[:, None]
        try:
            coef = np.linalg.lstsq(A, taus * w, rcond=None)[0]
            a, b = float(coef[0]), float(coef[1])
        except Exception:                                # noqa: BLE001
            a, b = float(np.median(taus)), 0.0

    if not np.isfinite(a) or not np.isfinite(b) or abs(b) > 0.03:
        a, b = float(np.median(taus)), 0.0
    return a, b


def demodulate_soft(signal: np.ndarray) -> np.ndarray:
    """
    Ήχος -> ΗΠΙΕΣ τιμές ανά bit (θετικό = '1', μέτρο = εμπιστοσύνη).
    Πλήρως διανυσματοποιημένο: δεν κρατάει το GIL, δεν ρίχνει τον ήχο.
    """
    if len(signal) < SPS * 4:
        return np.array([], dtype=np.float64)

    diff = discriminator(signal)
    sps = float(SPS)
    a, b = _timing(diff, SPS)

    denom = 1.0 - b
    if abs(denom) < 1e-6:
        denom = 1.0

    nbits = int((len(diff) * denom - a) / sps) + 2
    if nbits < 8:
        return np.array([], dtype=np.float64)

    k = np.arange(nbits, dtype=np.float64)
    idx = np.rint((k * sps + a) / denom).astype(np.int64)
    idx = idx[(idx >= 0) & (idx < len(diff))]
    return diff[idx]


def demodulate_bits(signal):
    return (demodulate_soft(signal) > 0).astype(np.uint8)


def _find_sync(bits):
    sb = bytes_to_bits(SYNC)
    n = len(sb)
    if len(bits) < n:
        return np.array([], dtype=np.int64)
    win = np.lib.stride_tricks.sliding_window_view(bits, n)
    return np.flatnonzero(np.sum(win != sb, axis=1) <= SYNC_MAX_ERR) + n


def receive(signal, stats=None):
    """Ήχος -> λίστα πλαισίων που πέρασαν CRC."""
    soft = demodulate_soft(signal)
    if len(soft) == 0:
        return []

    hard = (soft > 0).astype(np.uint8)
    syncs = _find_sync(hard)
    if stats is not None:
        stats['bits'] = len(hard)
        stats['syncs'] = len(syncs)
        stats['confidence'] = float(np.mean(np.abs(soft))) if len(soft) else 0

    frames, seen, tried = [], set(), 0

    for start in syncs:
        if tried >= MAX_CANDIDATES:
            break

        seg_soft = descramble_soft(soft[start:])
        seg_hard = (seg_soft > 0).astype(np.uint8)

        if USE_FEC:
            if len(seg_soft) < fec.LEN_BITS:
                continue
            plen = fec.decode_length(seg_soft[:fec.LEN_BITS])
            off = fec.LEN_BITS
        else:
            if len(seg_hard) < 8:
                continue
            plen = int(np.packbits(seg_hard[:8])[0])
            off = 8

        if plen < 0 or plen > MAX_PAYLOAD:
            continue

        tried += 1
        nbytes = HEADER_SIZE + plen + 2

        if USE_FEC:
            need = fec.coded_length(nbytes)
            chunk = seg_soft[off:off + need]
            if len(chunk) < need:
                continue
            body = fec.decode(chunk, nbytes)
        else:
            need = nbytes * 8
            chunk = seg_hard[off:off + need]
            if len(chunk) < need:
                continue
            body = bits_to_bytes(chunk)

        frame = parse_body(body)
        if frame:
            key = (frame['src'], frame['msg_id'], frame['seq'],
                   frame['type'], frame['payload'])
            if key not in seen:
                seen.add(key)
                frames.append(frame)

    return frames


# ---------------------------------------------------------------------------
# Αυτοδιαγνωστικό
# ---------------------------------------------------------------------------

def _selftest():
    print(f'EREVOS MODEM v5   ({BAUD} baud, {F_MARK}/{F_SPACE} Hz, '
          f'FEC={"ναι" if USE_FEC else "όχι"})')
    print(f'  Ωφέλιμος ρυθμός: {throughput_bps():.0f} bps')
    print(f'  Πλαίσιο 200 bytes: {frame_seconds(200):.2f} s\n')

    text = 'Δοκιμη EREVOS - test 123 !@#'
    bits = build_frame(TYPE_TEXT, 'N0CALL', 'N1CALL', text.encode())
    audio = modulate(bits).astype(np.float64)
    pad = np.zeros(SAMPLE_RATE // 4)

    def check(rx, label):
        got = receive(np.concatenate([pad, rx, pad]))
        ok = bool(got) and got[0]['payload'].decode(errors='replace') == text
        print(f'  [{"OK  " if ok else "FAIL"}] {label}')
        return 1 if ok else 0

    def tilt(x, mg, sg):
        n = len(x)
        f = np.fft.rfftfreq(n, 1 / SAMPLE_RATE)
        g = np.ones(len(f))
        g[(f > F_MARK - 300) & (f < F_MARK + 300)] = mg
        g[(f > F_SPACE - 300) & (f < F_SPACE + 300)] = sg
        return np.fft.irfft(np.fft.rfft(x) * g, n)

    p = t = 0
    for snr in (None, 20, 10, 6, 3, 0, -3):
        if snr is None:
            rx, lab = audio, 'καθαρό'
        else:
            nz = np.random.normal(0, 1, len(audio))
            nz *= np.sqrt(np.mean(audio ** 2) / (10 ** (snr / 10)))
            rx, lab = audio + nz, f'SNR {snr:>3} dB'
        t += 1; p += check(rx, lab)

    echo = np.concatenate([np.zeros(600), audio[:-600]]) * 0.4
    t += 1; p += check(audio + echo, 'αντήχηση δωματίου')
    t += 1; p += check(audio * 0.02, 'στάθμη 2%')
    t += 1; p += check(np.clip(audio * 4, -1, 1), 'ψαλίδισμα x4')
    idx = np.arange(0, len(audio) - 1, 1.005)
    t += 1; p += check(np.interp(idx, np.arange(len(audio)), audio),
                       'απόκλιση ρολογιού 0.5%')
    for mg, sg in ((1.0, 0.35), (0.35, 1.0), (1.0, 0.15)):
        t += 1; p += check(tilt(audio, mg, sg),
                           f'άνιση απόκριση {mg}/{sg}')

    print(f'\nΑποτέλεσμα: {p}/{t}')
    return p, t


if __name__ == '__main__':
    import sys
    if '--selftest' in sys.argv:
        _selftest()
    else:
        print(__doc__)
