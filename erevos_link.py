#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Steff_SKG
"""
EREVOS LINK - επίπεδο ζεύξης   [v3]
====================================
Πάνω από το modem: αξιόπιστη παράδοση μηνυμάτων και αρχείων.

  * Επιβεβαίωση παραλαβής (ACK) και επαναποστολή
  * Τεμαχισμός/επανασύνθεση αρχείων
  * Αποφυγή συγκρούσεων (ακούει πριν εκπέμψει)
  * Ένα αμφίδρομο ρεύμα ήχου - το μικρόφωνο δεν κλείνει ποτέ
  * Αυτόματη επανεκκίνηση αν πέσει το ρεύμα ήχου
  * Μεγάλα μηνύματα χωρίζονται αυτόματα σε πολλά πλαίσια
  * Όριο στην αποσυμπίεση: ένα μικρό πλαίσιο δεν μπορεί να γεμίσει
    τη μνήμη ή τον δίσκο του παραλήπτη ("zip bomb")

    python3 erevos_link.py --chat --call N0CALL
"""

import contextlib
import os
import zlib
import queue
import random
import struct
import sys
import threading
import time

import erevos_audio as audio
import erevos_modem as modem
from erevos_modem import (MAX_PAYLOAD, build_frame, TYPE_TEXT,
                          TYPE_FILE_META, TYPE_FILE_CHUNK, TYPE_ACK,
                          FLAG_ZLIB)

# ---------------------------------------------------------------------------

ACK_TIMEOUT = 9.0
ACK_DELAY   = (0.45, 0.85)   # παύση πριν την επιβεβαίωση
ACK_REPEAT  = 2              # πόσες φορές στέλνεται η επιβεβαίωση
SEQ_META    = 0xFFFF         # η κεφαλίδα αρχείου έχει δικό της αριθμό

# Μετά από επιτυχή επιβεβαίωση περιμένουμε λίγο πριν στείλουμε το
# επόμενο πλαίσιο: ο παραλήπτης μπορεί να στέλνει ακόμα τις
# επαναλήψεις της επιβεβαίωσης, και θα ήταν κουφός στη νέα εκπομπή.
# Ημιαμφίδρομη ζεύξη - δεν μπορούμε να μιλάμε ταυτόχρονα.
def _post_ack_guard() -> float:
    return 0.4 + max(0, ACK_REPEAT - 1) * 1.9
VERBOSE     = False          # καταγραφή κάθε πλαισίου που φτάνει
COMPRESS    = True           # συμπίεση ωφέλιμου φορτίου πριν την εκπομπή
COMPRESS_MIN = 24            # κάτω από τόσα bytes δεν αξίζει


def _pack(data: bytes):
    """
    Συμπιέζει αν συμφέρει. Επιστρέφει (bytes, σημαία).

    Το κείμενο συμπιέζεται τυπικά 2-4 φορές. Στα 477 bps αυτό
    σημαίνει ότι ένα μήνυμα φεύγει σε ένα τρίτο του χρόνου -
    το φθηνότερο κέρδος ταχύτητας σε ολόκληρη τη στοίβα.
    """
    if not COMPRESS or len(data) < COMPRESS_MIN:
        return data, 0
    try:
        z = zlib.compress(data, 9)
    except Exception:                                   # noqa: BLE001
        return data, 0
    return (z, FLAG_ZLIB) if len(z) < len(data) else (data, 0)


def _unpack(data: bytes, flag: bool, limit: int = 0):
    """
    Αποσυμπίεση με ΟΡΙΟ μεγέθους.

    Το zlib μπορεί να βγάλει ~1000 φορές περισσότερα bytes από όσα
    μπαίνουν. Χωρίς όριο, κάποιος στη συχνότητα μπορεί να στείλει
    λίγα KB που γίνονται εκατοντάδες MB στη μνήμη του παραλήπτη.

    Επιστρέφει τα δεδομένα, ή None αν ξεπεράστηκε το όριο / είναι
    χαλασμένα.
    """
    if not flag:
        return data
    limit = int(limit) if limit else MAX_TEXT_BYTES
    try:
        d = zlib.decompressobj()
        out = d.decompress(data, limit + 1)
    except Exception:                                   # noqa: BLE001
        return None
    if len(out) > limit or d.unconsumed_tail or not d.eof:
        return None
    return out


def split_text(text: str):
    """
    Χωρίζει ένα μήνυμα σε κομμάτια που χωράνε το καθένα σε ένα πλαίσιο
    (μετά τη συμπίεση). Κόβει σε όρια χαρακτήρων - ποτέ στη μέση ενός
    ελληνικού χαρακτήρα - και προτιμά τα κενά, ώστε να μην σπάνε λέξεις.

    Επιστρέφει λίστα από (bytes, σημαία_συμπίεσης).
    """
    parts, rest = [], text
    while rest:
        lo, hi, best = 1, len(rest), None
        while lo <= hi:                      # μεγαλύτερο πρόθεμα που χωράει
            mid = (lo + hi) // 2
            body, flag = _pack(rest[:mid].encode())
            if len(body) <= MAX_PAYLOAD:
                best, lo = mid, mid + 1
            else:
                hi = mid - 1
        n = best or 1
        if n < len(rest):                    # προτίμησε κόψιμο σε κενό
            cut = rest.rfind(' ', 0, n)
            if cut >= int(n * 0.6):
                n = cut + 1
        body, flag = _pack(rest[:n].encode())
        parts.append((body, flag))
        rest = rest[n:]
    return parts or [(b'', 0)]


MAX_RETRIES = 3
CHUNK_SIZE  = MAX_PAYLOAD
BUSY_RMS    = 0.02
BUSY_WAIT   = (0.5, 2.0)
BUSY_GIVEUP = 10.0
INBOX_DIR   = os.path.join(os.path.expanduser('~'), 'erevos_inbox')

# Ανώτατα όρια παραλαβής. Στα ~477 bps ένα αρχείο 16 MB θέλει μέρες
# εκπομπής, οπότε κανένα νόμιμο αρχείο δεν τα φτάνει.
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TEXT_BYTES = 64 * 1024

DEFAULTS = dict(ACK_TIMEOUT=9.0, MAX_RETRIES=3, CHUNK_SIZE=MAX_PAYLOAD,
                BUSY_RMS=0.02, BUSY_GIVEUP=10.0, ACK_REPEAT=2)


def set_verbose(on: bool):
    globals()['VERBOSE'] = bool(on)


def configure(**kw):
    g = globals()
    for k, v in kw.items():
        if k in DEFAULTS:
            g[k] = v
    g['CHUNK_SIZE'] = int(min(g['CHUNK_SIZE'], MAX_PAYLOAD))
    return {k: g[k] for k in DEFAULTS}


def reset_defaults():
    return configure(**DEFAULTS)


# ---------------------------------------------------------------------------

class Station:
    """Ένας κόμβος: εκπέμπει, λαμβάνει, επιβεβαιώνει."""

    def __init__(self, callsign, in_dev=None, out_dev=None, amplitude=0.5,
                 vox=True, ptt=None, on_event=None, prefer_duplex=False):
        self.call = callsign.upper()[:9]
        self.in_dev, self.out_dev = in_dev, out_dev
        self.amplitude, self.vox, self.ptt = amplitude, vox, ptt
        self.on_event = on_event or (lambda kind, **kw: None)

        self.rx = audio.AudioEngine(in_dev, out_dev,
                                    prefer_duplex=prefer_duplex)
        self._acks = queue.Queue()
        self._running = False
        self._thread = None
        self._tx_lock = threading.Lock()
        self._last_check = 0.0

        self.peers = {}
        self._files = {}
        self._delivered = {}     # μηνύματα που ήδη δείξαμε -> πότε
        self.stats = dict(tx_frames=0, rx_frames=0, retries=0, failed=0,
                          tx_bytes=0, rx_bytes=0, saved_bytes=0,
                          started=None)

    # -- ζωή ----------------------------------------------------------------

    def start(self):
        self.rx.start()
        # μέτρηση του θορύβου του χώρου πριν ξεκινήσουμε
        time.sleep(0.4)
        nf = self.rx.calibrate(1.6)
        self.on_event('calibrated', noise=nf,
                      threshold=self.rx.busy_threshold(floor=BUSY_RMS * 0.3))
        self._running = True
        self.stats['started'] = time.time()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        self.rx.stop()
        if self.ptt:
            self.ptt.close()

    @property
    def duplex(self):
        return self.rx.duplex

    def transmitting(self):
        return self._tx_lock.locked()

    def _loop(self):
        while self._running:
            try:
                for f in self.rx.poll(timeout=0.4):
                    self._handle(f)

                # φύλακας: αν πέσει το ρεύμα ήχου, ξανασήκωσέ το
                now = time.time()
                if now - self._last_check > 3.0:
                    self._last_check = now
                    if not self.rx.tx_active and not self.rx.alive():
                        self.on_event('warn',
                                      message='Το ρεύμα ήχου έπεσε — '
                                              'επανεκκίνηση')
                        with contextlib.suppress(Exception):
                            self.rx.restart()
            except Exception as e:                       # noqa: BLE001
                self.on_event('error', message=str(e))
                time.sleep(0.3)

    # -- λήψη ---------------------------------------------------------------

    def _handle(self, f):
        if VERBOSE:
            self.on_event('frame', src=f['src'], dst=f['dst'],
                          type=f['type'], seq=f['seq'],
                          size=len(f['payload']),
                          mine=(f['dst'] in (self.call, 'CQ', '')))
        if f['src'] == self.call:
            return
        if f['dst'] not in (self.call, 'CQ', ''):
            return

        self.peers[f['src']] = time.time()
        self.stats['rx_frames'] += 1
        self.stats['rx_bytes'] += len(f['payload'])

        if f['type'] == TYPE_ACK:
            self._acks.put((f['src'], f['msg_id'], f['seq']))
            self.on_event('ack_recv', src=f['src'], msg_id=f['msg_id'],
                          seq=f['seq'])
            return

        if f['type'] == TYPE_TEXT:
            # Επαναποστολή μηνύματος που ήδη δείξαμε (χάθηκε η δική μας
            # επιβεβαίωση): επιβεβαιώνουμε ξανά, αλλά δεν το δείχνουμε
            # δεύτερη φορά.
            key = (f['src'], f['msg_id'], f['seq'])
            now = time.time()
            for k in [k for k, t in self._delivered.items() if now - t > 600]:
                del self._delivered[k]
            if key in self._delivered:
                self._ack(f)
                return
            self._delivered[key] = now
            data = _unpack(f['payload'], f.get('zlib'), MAX_TEXT_BYTES)
            if data is None:
                self.on_event('warn', message=f'{f["src"]}: μήνυμα '
                              f'απορρίφθηκε / message rejected '
                              f'(αποσυμπίεση / decompression)')
            else:
                self.on_event('text', src=f['src'],
                              text=data.decode(errors='replace'))
            self._ack(f)

        elif f['type'] == TYPE_FILE_META:
            if len(f['payload']) < 4:
                return
            size = struct.unpack('!I', f['payload'][:4])[0]
            name = os.path.basename(
                f['payload'][4:].decode(errors='replace')) or 'unnamed'
            if size > MAX_FILE_BYTES:
                # Δεν επιβεβαιώνουμε: ο αποστολέας θα δει "χωρίς
                # επιβεβαίωση" και θα σταματήσει.
                self.on_event('warn', message=f'{f["src"]}: αρχείο '
                              f'"{name}" απορρίφθηκε / file rejected '
                              f'({size} > {MAX_FILE_BYTES} bytes)')
                return
            key = (f['src'], f['msg_id'])
            if key not in self._files:       # όχι επαναποστολή κεφαλίδας
                self._files[key] = {
                    'name': name, 'size': size, 'total': f['total'],
                    'chunks': {}, 'zlib': bool(f.get('zlib'))}
                self.on_event('file_start', src=f['src'], name=name,
                              size=size, total=f['total'])
            self._ack(f)

        elif f['type'] == TYPE_FILE_CHUNK:
            key = (f['src'], f['msg_id'])
            st = self._files.get(key)
            self._ack(f)
            if not st or f['seq'] >= st['total']:
                return
            st['chunks'][f['seq']] = f['payload']
            self.on_event('file_progress', src=f['src'], name=st['name'],
                          have=len(st['chunks']), total=st['total'])
            if len(st['chunks']) >= st['total']:
                self._finish(key)

    def _finish(self, key):
        st = self._files.pop(key)
        data = b''.join(st['chunks'][i] for i in sorted(st['chunks']))
        if st.get('zlib'):
            # Όριο = το μέγεθος που δήλωσε ο ίδιος ο αποστολέας.
            data = _unpack(data, True, limit=max(1, st['size']))
            if data is None:
                self.on_event('warn', message=f'{key[0]}: αρχείο '
                              f'"{st["name"]}" απορρίφθηκε / file '
                              f'rejected (αποσυμπίεση / decompression)')
                return
        os.makedirs(INBOX_DIR, exist_ok=True)
        path = os.path.join(INBOX_DIR, st['name'])
        base, ext = os.path.splitext(path)
        n = 1
        while os.path.exists(path):
            path = f'{base}_{n}{ext}'
            n += 1
        with open(path, 'wb') as fh:
            fh.write(data[:st['size']] if st['size'] else data)
        self.on_event('file_done', src=key[0], path=path, size=len(data))

    def _ack(self, f):
        """Στέλνεται περισσότερες από μία φορές: είναι μικρό πλαίσιο και
        η απώλειά του κοστίζει ολόκληρη επαναποστολή δεδομένων."""
        """
        Μικρή παύση πριν την επιβεβαίωση.

        Ο αποστολέας μόλις τελείωσε τη δική του εκπομπή και χρειάζεται
        λίγο για να σταθεροποιηθεί στη λήψη (καθαρισμός ενταμιευτή,
        απελευθέρωση VOX). Αν απαντήσουμε ακαριαία, η επιβεβαίωση
        πέφτει ακριβώς στο κενό και χάνεται - τα δεδομένα φτάνουν
        αλλά ο αποστολέας ξαναστέλνει άσκοπα.

        Η τυχαιότητα αποτρέπει δύο σταθμούς να απαντήσουν ταυτόχρονα.
        """
        bits = build_frame(TYPE_ACK, self.call, f['src'], b'',
                           msg_id=f['msg_id'], seq=f['seq'], total=1)
        time.sleep(random.uniform(*ACK_DELAY))
        for i in range(max(1, ACK_REPEAT)):
            if i:
                time.sleep(random.uniform(0.25, 0.5))
            self._raw_send(bits)
            self.on_event('ack_sent', dst=f['src'], msg_id=f['msg_id'],
                          seq=f['seq'], n=i + 1)

    # -- εκπομπή ------------------------------------------------------------

    def channel_busy(self) -> bool:
        """
        Κατειλημμένο = η στάθμη είναι αισθητά πάνω από τον θόρυβο του χώρου.
        Το BUSY_RMS λειτουργεί ως απόλυτο κατώτατο όριο, ώστε σε ήσυχο
        περιβάλλον να μην γίνεται υπερβολικά ευαίσθητο.
        """
        return self.rx.level() > self.rx.busy_threshold(floor=BUSY_RMS * 0.3)

    def _raw_send(self, bits):
        with self._tx_lock:
            self.on_event('tx_start')
            try:
                self.rx.transmit(bits, amplitude=self.amplitude,
                                 vox=self.vox, ptt=self.ptt)
                self.stats['tx_frames'] += 1
            finally:
                self.on_event('tx_end')

    def _send_reliable(self, ftype, dst, payload, msg_id, seq, total) -> bool:
        bits = build_frame(ftype, self.call, dst, payload,
                           msg_id=msg_id, seq=seq, total=total)

        for attempt in range(1, MAX_RETRIES + 1):
            waited = 0.0
            while self.channel_busy() and waited < BUSY_GIVEUP:
                d = random.uniform(*BUSY_WAIT)
                self.on_event('waiting', seconds=d)
                time.sleep(d)
                waited += d

            while not self._acks.empty():
                with contextlib.suppress(queue.Empty):
                    self._acks.get_nowait()

            self._raw_send(bits)
            self.stats['tx_bytes'] += len(payload)

            if dst == 'CQ':
                return True

            end = time.time() + ACK_TIMEOUT
            while time.time() < end:
                try:
                    src, mid, sq = self._acks.get(timeout=0.3)
                except queue.Empty:
                    continue
                if src == dst and mid == msg_id and sq == seq:
                    time.sleep(_post_ack_guard())
                    return True
                # Έφτασε επιβεβαίωση αλλά δεν ταιριάζει - αυτό είναι
                # εντελώς διαφορετικό πρόβλημα από το να μην φτάνει.
                self.on_event('ack_bad', got=(src, mid, sq),
                              want=(dst, msg_id, seq))

            self.stats['retries'] += 1
            self.on_event('retry', attempt=attempt, seq=seq, total=total)

        self.stats['failed'] += 1
        return False

    # -- API ----------------------------------------------------------------

    def send_text(self, dst, text) -> bool:
        """
        Μήνυμα που δεν χωράει σε ένα πλαίσιο (200 bytes μετά τη
        συμπίεση - περίπου 100 ελληνικοί χαρακτήρες χωρίς συμπίεση)
        στέλνεται σε διαδοχικά πλαίσια. Πριν κοβόταν σιωπηλά.

        Κάθε κομμάτι είναι κανονικό πλαίσιο κειμένου, οπότε και οι
        παλιότεροι σταθμοί το λαμβάνουν - απλώς ως ξεχωριστά μηνύματα.
        """
        parts = split_text(text)
        dst = dst.upper()
        msg_id = random.randrange(65536)
        ok = True
        for i, (body, flag) in enumerate(parts):
            raw_len = len(body) if not flag else len(
                _unpack(body, True, MAX_TEXT_BYTES) or b'')
            self.stats['saved_bytes'] += max(0, raw_len - len(body))
            if not self._send_reliable(TYPE_TEXT | flag, dst, body,
                                       msg_id, i, len(parts)):
                ok = False
                break
        return ok

    def send_file(self, dst, path, cancel=None) -> bool:
        with open(path, 'rb') as fh:
            raw = fh.read()

        # Συμπιέζουμε ΟΛΟΚΛΗΡΟ το αρχείο πριν τον τεμαχισμό - πολύ
        # καλύτερη αναλογία από το να συμπιέζαμε κάθε τμήμα χωριστά.
        data, flag = _pack(raw)
        self.stats['saved_bytes'] += max(0, len(raw) - len(data))
        name = os.path.basename(path)
        cs = int(max(16, min(CHUNK_SIZE, MAX_PAYLOAD)))
        chunks = [data[i:i + cs] for i in range(0, len(data), cs)] or [b'']
        msg_id = random.randrange(65536)
        dst = dst.upper()

        meta = struct.pack('!I', len(raw)) + name.encode()[:MAX_PAYLOAD - 4]
        if not self._send_reliable(TYPE_FILE_META | flag, dst, meta,
                                   msg_id, SEQ_META, len(chunks)):
            return False

        for i, ch in enumerate(chunks):
            if cancel and cancel():
                self.on_event('cancelled')
                return False
            self.on_event('sending', seq=i + 1, total=len(chunks))
            if not self._send_reliable(TYPE_FILE_CHUNK, dst, ch,
                                       msg_id, i, len(chunks)):
                return False
        return True

    def estimate_seconds(self, nbytes: int, ratio: float = 1.0) -> float:
        nbytes = int(nbytes * ratio)
        cs = int(max(16, min(CHUNK_SIZE, MAX_PAYLOAD)))
        n = max(1, (nbytes + cs - 1) // cs)
        per = (modem.frame_seconds(cs) + audio.VOX_LEAD_SEC +
               audio.TX_SETTLE_SEC + modem.frame_seconds(0) + 0.5)
        return (n + 1) * per


# ---------------------------------------------------------------------------

def _chat(call, in_dev, out_dev, amp, vox):
    dst = ['CQ']

    def ev(kind, **kw):
        if kind == 'text':
            print(f'\n[{kw["src"]}] {kw["text"]}')
        elif kind == 'file_done':
            print(f'\n[{kw["src"]}] αποθηκεύτηκε: {kw["path"]}')
        elif kind == 'retry':
            print(f'  ...επαναποστολή {kw["attempt"]}')
        elif kind in ('error', 'warn'):
            print(f'\n[{kind}] {kw["message"]}')

    st = Station(call, in_dev, out_dev, amplitude=amp, vox=vox, on_event=ev)
    st.start()
    print(f'EREVOS {call}  ·  ρεύμα '
          f'{"αμφίδρομο" if st.duplex else "ξεχωριστά"}  ·  '
          f'/to /file /peers /stats /quit\n')
    try:
        while True:
            line = input(f'{call} -> {dst[0]} > ').strip()
            if not line:
                continue
            if line == '/quit':
                break
            elif line.startswith('/to '):
                dst[0] = line[4:].strip().upper() or 'CQ'
            elif line == '/peers':
                for p, t in sorted(st.peers.items()):
                    print(f'  {p:<10} πριν {time.time()-t:.0f}s')
            elif line == '/stats':
                for k, v in st.stats.items():
                    print(f'  {k}: {v}')
            elif line.startswith('/file '):
                p = os.path.expanduser(line[6:].strip())
                print('  ok' if os.path.isfile(p) and st.send_file(dst[0], p)
                      else '  απέτυχε')
            else:
                print('  ok' if st.send_text(dst[0], line)
                      else '  χωρίς επιβεβαίωση')
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        st.stop()
        print('\nτέλος.')


if __name__ == '__main__':
    def _a(n, c, d=None):
        return c(sys.argv[sys.argv.index(n) + 1]) if n in sys.argv else d

    if '--chat' in sys.argv and _a('--call', str):
        _chat(_a('--call', str), _a('--in', int), _a('--out', int),
              _a('--amp', float, 0.5), '--novox' not in sys.argv)
    else:
        print(__doc__)
