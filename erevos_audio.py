#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Steff_SKG
"""
EREVOS AUDIO - επίπεδο ήχου   [v6]
===================================
Η λήψη γίνεται σε αποκλειστικό νήμα με stream.read(), ΟΧΙ με callback.
Το read() αφήνει το GIL όσο περιμένει δείγματα, οπότε το μικρόφωνο
διαβάζεται πάντα - ακόμα κι όταν ο αποδιαμορφωτής ή το γραφικό
περιβάλλον είναι απασχολημένα.

Αυτόματη εφεδρεία συσκευής: οι φυσικές κάρτες ALSA (hw:0,0) είναι
ΑΠΟΚΛΕΙΣΤΙΚΕΣ - μόνο ένα πρόγραμμα τη φορά. Αν η επιλεγμένη συσκευή
είναι πιασμένη, δοκιμάζουμε αυτόματα την προεπιλεγμένη (PulseAudio),
που μοιράζεται. Έτσι δουλεύουν δύο σταθμοί στον ίδιο υπολογιστή.

    python3 erevos_audio.py --devices
    python3 erevos_audio.py --monitor      # ζωντανή στάθμη + SNR
    python3 erevos_audio.py --loopback
"""

import contextlib
import queue
import sys
import threading
import time

import numpy as np
import sounddevice as sd

import erevos_modem as modem
from erevos_modem import SAMPLE_RATE, modulate, receive, build_frame, TYPE_TEXT

# ---------------------------------------------------------------------------

VOX_LEAD_SEC  = 0.30
VOX_TAIL_SEC  = 0.12
TX_SETTLE_SEC = 0.25

RX_WINDOW_SEC = 3.5
RX_GATE_RMS   = 0.004
TONE_GATE     = 3.2
READ_BLOCK    = 4096
MAX_QUEUE     = 24
MIN_DECODE_GAP = 0.55   # δευτ. - ελάχιστο διάστημα μεταξύ αποκωδικοποιήσεων

DEVICE_BUSY = ('unavailable', '-9985', '-9996', 'busy', 'in use')


def _is_busy_error(e) -> bool:
    t = str(e).lower()
    return any(k in t for k in DEVICE_BUSY)


# ---------------------------------------------------------------------------
# Συσκευές
# ---------------------------------------------------------------------------

def device_list(kind: str):
    out = []
    with contextlib.suppress(Exception):
        for i, d in enumerate(sd.query_devices()):
            n = (d['max_input_channels'] if kind == 'in'
                 else d['max_output_channels'])
            if n > 0:
                out.append((i, d['name']))
    return out


def device_name(idx):
    if idx is None:
        return 'default'
    with contextlib.suppress(Exception):
        return sd.query_devices()[idx]['name']
    return str(idx)


def shared_device(kind: str):
    """
    Συσκευή που μπορούν να μοιραστούν πολλά προγράμματα.
    Προτιμάμε το pulse/default αντί για φυσική κάρτα.
    """
    for i, name in device_list(kind):
        if name.strip().lower() in ('pulse', 'default', 'sysdefault'):
            return i
    return None


def list_devices():
    print('Audio devices:\n')
    din, dout = sd.default.device
    for i, d in enumerate(sd.query_devices()):
        m = []
        if d['max_input_channels']:
            m.append(f"in:{d['max_input_channels']}")
        if d['max_output_channels']:
            m.append(f"out:{d['max_output_channels']}")
        flag = (' <- default IN' if i == din else '') + \
               (' <- default OUT' if i == dout else '')
        print(f"  {i:>3}  {d['name']:<45} {'/'.join(m):<12}{flag}")
    print('\nΓια ΔΥΟ σταθμούς στον ίδιο υπολογιστή χρησιμοποίησε')
    print('(default) ή "pulse" - οι φυσικές κάρτες είναι αποκλειστικές.')


def measure_input(seconds=2.0, device=None):
    a = sd.rec(int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE,
               channels=1, device=device, blocking=True)[:, 0]
    return (float(np.sqrt(np.mean(a ** 2))), float(np.max(np.abs(a))),
            float(np.mean(np.abs(a) > 0.99) * 100))


def tone(seconds: float, amplitude: float, freq=None) -> np.ndarray:
    n = int(seconds * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    x = amplitude * np.sin(2 * np.pi * (freq or modem.F_MARK) * t)
    fade = int(0.005 * SAMPLE_RATE)
    if n > 2 * fade:
        r = np.linspace(0, 1, fade)
        x[:fade] *= r
        x[-fade:] *= r[::-1]
    return x.astype(np.float32)


def play(signal, device=None, blocking=True):
    try:
        sd.play(signal, samplerate=SAMPLE_RATE, device=device,
                blocking=blocking)
    except Exception as e:                              # noqa: BLE001
        if device is not None and _is_busy_error(e):
            sd.play(signal, samplerate=SAMPLE_RATE, device=None,
                    blocking=blocking)
        else:
            raise


def build_tx_signal(frame_bits, amplitude=0.5, vox=True) -> np.ndarray:
    parts = []
    if vox:
        parts.append(tone(VOX_LEAD_SEC, amplitude))
    parts.append(modulate(frame_bits, amplitude=amplitude))
    parts.append(np.zeros(int(VOX_TAIL_SEC * SAMPLE_RATE), dtype=np.float32))
    return np.concatenate(parts).astype(np.float32)


# ---------------------------------------------------------------------------
# Μηχανή ήχου
# ---------------------------------------------------------------------------

class AudioEngine:

    def __init__(self, in_dev=None, out_dev=None, on_frame=None,
                 prefer_duplex=False):
        self.in_dev, self.out_dev = in_dev, out_dev
        self.in_actual, self.out_actual = in_dev, out_dev
        self.on_frame = on_frame
        self.prefer_duplex = prefer_duplex

        self._q = queue.Queue()
        self._buffer = np.zeros(0, dtype=np.float32)
        self._stream = None
        self._reader = None
        self._running = False
        self._seen = {}          # πλαίσιο -> πότε αποκωδικοποιήθηκε
        self._tx_gate = threading.Event()

        self.duplex = False
        self.fallback = False
        self.out_switched = False
        self.last_stats = {}
        self.error = None

        self.blocks_in = 0
        self.overflows = 0
        self.dropped = 0
        self.decode_attempts = 0
        self.decode_ms = 0.0
        self.last_block_at = 0.0
        self.reopens = 0

        self._levels = []
        self.noise_floor = 0.004
        self.calibrated = False
        self._last_decode = 0.0
        self.skipped = 0
        self.rejected_level = 0
        self.rejected_tones = 0
        self.last_level = 0.0

    @property
    def tx_active(self):
        return self._tx_gate.is_set()

    # -- άνοιγμα με εφεδρεία ------------------------------------------------

    def _candidates(self):
        """Σειρά δοκιμής συσκευών εισόδου."""
        seen, out = set(), []
        for d in (self.in_dev, shared_device('in'), None):
            key = 'none' if d is None else d
            if key not in seen:
                seen.add(key)
                out.append(d)
        return out

    def _open_input(self):
        last = None
        for dev in self._candidates():
            try:
                st = sd.InputStream(
                    samplerate=SAMPLE_RATE, blocksize=READ_BLOCK,
                    dtype='float32', channels=1, device=dev, latency='high')
                st.start()
                self._stream = st
                self.in_actual = dev
                self.fallback = (dev != self.in_dev)
                if self.fallback:
                    self.error = (f'η συσκευή {device_name(self.in_dev)} '
                                  f'ήταν πιασμένη — χρήση '
                                  f'{device_name(dev)}')
                return
            except Exception as e:                      # noqa: BLE001
                last = e
                if not _is_busy_error(e):
                    break
        raise last if last else RuntimeError('no input device')

    # -- νήμα ανάγνωσης -----------------------------------------------------

    def _read_loop(self):
        while self._running:
            if self._stream is None:
                try:
                    self._open_input()
                    self.reopens += 1
                except Exception as e:                  # noqa: BLE001
                    self.error = str(e)
                    time.sleep(0.5)
                    continue
            try:
                data, over = self._stream.read(READ_BLOCK)
            except Exception as e:                      # noqa: BLE001
                self.error = str(e)
                with contextlib.suppress(Exception):
                    self._stream.stop()
                    self._stream.close()
                self._stream = None
                time.sleep(0.2)
                continue

            self.blocks_in += 1
            self.last_block_at = time.time()
            if over:
                self.overflows += 1
            if self._tx_gate.is_set():
                continue
            if self._q.qsize() >= MAX_QUEUE:
                with contextlib.suppress(queue.Empty):
                    self._q.get_nowait()
                    self.dropped += 1
            self._q.put(np.array(data[:, 0], dtype=np.float32, copy=True))

    def start(self):
        self._open_input()                # σφάλμα εδώ = ενημερώνεται ο χρήστης
        self._running = True
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def stop(self):
        self._running = False
        if self._reader:
            self._reader.join(timeout=1.5)
            self._reader = None
        if self._stream:
            with contextlib.suppress(Exception):
                self._stream.stop()
                self._stream.close()
            self._stream = None

    def alive(self) -> bool:
        if not self._running or self._stream is None:
            return False
        if self.last_block_at and time.time() - self.last_block_at > 2.5:
            return False
        return True

    def restart(self):
        was = self._running
        self.stop()
        time.sleep(0.2)
        self.blocks_in = 0
        self.last_block_at = 0.0
        if was:
            self.start()

    # -- εκπομπή ------------------------------------------------------------

    def _safe_out(self):
        """
        Οι φυσικές κάρτες ALSA είναι αποκλειστικές. Αν κρατάμε ήδη τη
        συσκευή ανοιχτή για ΕΙΣΟΔΟ, δεν μπορούμε να παίξουμε στην ίδια -
        η αναπαραγωγή αποτυγχάνει ή διώχνει το μικρόφωνο.
        Σε αυτή την περίπτωση στέλνουμε στην κοινόχρηστη έξοδο.
        """
        if self.out_dev is not None and self.out_dev == self.in_actual:
            alt = shared_device('out')
            self.out_actual = alt
            self.out_switched = True
            return alt
        self.out_actual = self.out_dev
        return self.out_dev

    def transmit(self, frame_bits, amplitude=0.5, vox=True, ptt=None) -> float:
        sig = build_tx_signal(frame_bits, amplitude, vox)
        dur = len(sig) / SAMPLE_RATE
        if ptt:
            ptt.key()
        self._tx_gate.set()
        try:
            play(sig, device=self._safe_out(), blocking=True)
            time.sleep(TX_SETTLE_SEC)
        except Exception as e:                          # noqa: BLE001
            self.error = str(e)
            raise
        finally:
            self._tx_gate.clear()
            if ptt:
                ptt.unkey()
            self._flush()
        return dur

    def _flush(self):
        while not self._q.empty():
            with contextlib.suppress(queue.Empty):
                self._q.get_nowait()
        self._buffer = np.zeros(0, dtype=np.float32)
        # Ο παλιός ήχος μόλις πετάχτηκε, οπότε ό,τι αποκωδικοποιηθεί από
        # εδώ και πέρα είναι νέα εκπομπή - ακόμα κι αν είναι ίδιο πλαίσιο
        # (επαναποστολή επειδή χάθηκε η επιβεβαίωσή μας).
        self._seen.clear()

    # -- λήψη ---------------------------------------------------------------

    def level(self) -> float:
        if len(self._buffer) < 2400:
            return 0.0
        return float(np.sqrt(np.mean(self._buffer[-4800:] ** 2)))

    def buffer(self) -> np.ndarray:
        return self._buffer

    def _track_noise(self, lvl: float):
        """
        Παρακολούθηση του θορύβου βάθους.

        ΚΡΙΣΙΜΟ: δεν μετράμε στιγμές που προφανώς περιέχουν σήμα.
        Αλλιώς κάθε εκπομπή που ακούμε ανεβάζει τον «θόρυβο», το
        κατώφλι ακολουθεί, και ο σταθμός κουφαίνεται μόνος του όσο
        περισσότερο δουλεύει το δίκτυο.
        """
        if self._levels and lvl > self.noise_floor * 2.5:
            return                       # πολύ ψηλά για θόρυβος - σήμα
        self._levels.append(lvl)
        if len(self._levels) > 80:
            self._levels.pop(0)
        if len(self._levels) >= 8:
            self.noise_floor = float(np.percentile(self._levels, 20))

    def calibrate(self, seconds: float = 2.0) -> float:
        samples = []
        end = time.time() + seconds
        while time.time() < end:
            self.poll(timeout=0.3)
            lv = self.level()
            if lv > 0:
                samples.append(lv)
        if samples:
            self.noise_floor = float(np.percentile(samples, 50))
            self._levels = list(samples[-40:])
            self.calibrated = True
        return self.noise_floor

    def busy_threshold(self, factor: float = 1.8, floor: float = 0.006):
        return max(floor, self.noise_floor * factor)

    def snr_db(self) -> float:
        return float(20.0 * np.log10(max(self.level(), 1e-6) /
                                     max(self.noise_floor, 1e-6)))

    def _is_new(self, f):
        """
        Το ίδιο πλαίσιο μένει μέσα στο κυλιόμενο παράθυρο για έως
        RX_WINDOW_SEC και αποκωδικοποιείται ξανά σε κάθε πέρασμα - αυτό
        το φιλτράρουμε.

        ΔΕΝ πρέπει όμως να φιλτράρουμε μια πραγματική επαναποστολή: αν
        χαθεί η επιβεβαίωσή μας, ο αποστολέας ξαναστέλνει το ίδιο ακριβώς
        πλαίσιο και περιμένει να το ξαναεπιβεβαιώσουμε. Γι' αυτό η μνήμη
        λήγει μόλις ο ήχος βγει από το παράθυρο, και αδειάζει σε κάθε
        δική μας εκπομπή (_flush).
        """
        now = time.time()
        ttl = RX_WINDOW_SEC + 1.5
        for k in [k for k, t in self._seen.items() if now - t > ttl]:
            del self._seen[k]
        k = (f['src'], f['msg_id'], f['seq'], f['type'], f['payload'])
        if k in self._seen:
            return False
        self._seen[k] = now
        return True

    def _tones_present(self, buf) -> bool:
        """
        Υπάρχει ενέργεια στους δύο τόνους ΚΑΠΟΥ μέσα στο παράθυρο;

        Εξετάζουμε τέσσερα σημεία σε όλο το μήκος - όχι μόνο το τέλος.
        Ένα μήνυμα μπορεί να βρίσκεται οπουδήποτε μέσα στα 3,5
        δευτερόλεπτα που κρατάμε.
        """
        n = len(buf)
        if n < 4096:
            return False

        seg_len = 8192
        starts = [0] if n <= seg_len else [
            int(p) for p in np.linspace(0, n - seg_len, 4)]

        for st in starts:
            seg = buf[st:st + seg_len]
            if len(seg) < 2048:
                continue
            mag = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
            fr = np.fft.rfftfreq(len(seg), 1.0 / SAMPLE_RATE)
            band = (fr > 200) & (fr < 4000)
            if not band.any():
                continue
            base = float(np.mean(mag[band])) + 1e-9

            def peak(f, w=140):
                m = (fr > f - w) & (fr < f + w)
                return float(np.max(mag[m])) if m.any() else 0.0

            if (peak(modem.F_MARK) > base * TONE_GATE or
                    peak(modem.F_SPACE) > base * TONE_GATE):
                return True
        return False

    def poll(self, timeout=0.4):
        window = int(RX_WINDOW_SEC * SAMPLE_RATE)
        chunks = []
        try:
            chunks.append(self._q.get(timeout=timeout))
        except queue.Empty:
            return []
        while not self._q.empty():
            with contextlib.suppress(queue.Empty):
                chunks.append(self._q.get_nowait())

        self._buffer = np.concatenate([self._buffer] + chunks)[-window:]

        lvl = float(np.sqrt(np.mean(self._buffer ** 2)))
        self.last_level = lvl
        self._track_noise(lvl)

        # Τρία φίλτρα πριν τον ακριβό αποκωδικοποιητή. Χωρίς αυτά, σε
        # αργό μηχάνημα ο επεξεργαστής κορένεται αποκωδικοποιώντας
        # θόρυβο δωματίου και το ίδιο το μικρόφωνο μένει πίσω (overflow).
        #   1. απόλυτη σιωπή
        #   2. τίποτα αξιόλογο πάνω από τον θόρυβο του χώρου
        #   3. ρυθμός: όχι πιο συχνά από MIN_DECODE_GAP
        if lvl < max(RX_GATE_RMS, self.noise_floor * 1.25):
            self.rejected_level += 1
            return []

        now = time.time()
        if now - self._last_decode < MIN_DECODE_GAP:
            self.skipped += 1
            return []

        if not self._tones_present(self._buffer):
            self.rejected_tones += 1
            return []

        self._last_decode = now
        t0 = time.time()
        stats, new = {}, []
        for f in receive(self._buffer, stats=stats):
            if self._is_new(f):
                new.append(f)
                if self.on_frame:
                    self.on_frame(f)
        self.decode_attempts += 1
        self.decode_ms = (time.time() - t0) * 1000.0
        self.last_stats = stats
        return new

    def health(self) -> str:
        return (f"{'●' if self.alive() else '○'} "
                f"blk {self.blocks_in}  ovf {self.overflows}  "
                f"dec {self.decode_attempts} {self.decode_ms:.0f}ms  "
                f"nf {self.noise_floor:.4f}  lvl {self.last_level:.4f}  "
                f"rej {self.rejected_level}/{self.rejected_tones}")

    def cpu_hint(self) -> str:
        if self.overflows > 5:
            return 'υπερφόρτωση επεξεργαστή'
        return 'ok'


Receiver = AudioEngine


def transmit(frame_bits, device=None, amplitude=0.5, vox=True,
             blocking=True, ptt=None) -> float:
    sig = build_tx_signal(frame_bits, amplitude, vox)
    if ptt:
        ptt.key()
    try:
        play(sig, device=device, blocking=blocking)
    finally:
        if ptt:
            ptt.unkey()
    if blocking:
        time.sleep(TX_SETTLE_SEC)
    return len(sig) / SAMPLE_RATE


# ---------------------------------------------------------------------------

def _monitor(in_dev=None):
    eng = AudioEngine(in_dev)
    eng.start()
    print(f'input: {device_name(eng.in_actual)}'
          f'{"  (εφεδρεία)" if eng.fallback else ""}')
    print('Ctrl+C για έξοδο\n')
    try:
        while True:
            eng.poll(timeout=0.3)
            lvl = eng.level()
            bar = '█' * int(min(1.0, lvl * 6) * 32)
            snr = eng.snr_db()
            v = ('ΑΡΙΣΤΟ ' if snr > 9 else 'ΚΑΛΟ   ' if snr > 5
                 else 'ΟΡΙΑΚΟ ' if snr > 2 else 'ΘΟΡΥΒΟΣ')
            print(f'\r  {lvl:.4f} |{bar:<32}| θόρ {eng.noise_floor:.4f} '
                  f' SNR {snr:+5.1f} dB  {v}  blk {eng.blocks_in} '
                  f' dec {eng.decode_attempts}   ', end='', flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        eng.stop()
        print()


def _test_loopback(in_dev=None, out_dev=None):
    bits = build_frame(TYPE_TEXT, 'N0CALL', 'CQ', b'EREVOS loopback test')
    eng = AudioEngine(in_dev, out_dev)
    eng.start()
    time.sleep(0.6)
    print(f'in : {device_name(eng.in_actual)}')
    print(f'TX {eng.transmit(bits, vox=False):.2f} s')
    end, got = time.time() + 6, []
    while time.time() < end and not got:
        got.extend(eng.poll(timeout=0.5))
    print(eng.health())
    eng.stop()
    print(f'[OK] "{got[0]["payload"].decode(errors="replace")}"' if got
          else '[FAIL] nothing received')


if __name__ == '__main__':
    def _a(n, c):
        return c(sys.argv[sys.argv.index(n) + 1]) if n in sys.argv else None

    if '--devices' in sys.argv:
        list_devices()
    elif '--tone' in sys.argv:
        print('tone 2 s…')
        play(tone(2.0, 0.5), device=_a('--out', int))
    elif '--monitor' in sys.argv:
        _monitor(_a('--in', int))
    elif '--loopback' in sys.argv:
        _test_loopback(_a('--in', int), _a('--out', int))
    else:
        print(__doc__)
