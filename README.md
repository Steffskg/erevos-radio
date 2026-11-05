# EREVOS RADIO

**Text messages and files over any radio transceiver — no internet, no
servers, no third-party digital-mode software.**

The whole chain is written from scratch in Python: modulation, timing
recovery, error correction, framing and the link protocol. The only
dependencies are `numpy` and `sounddevice`.

*Ελληνικά: [παρακάτω](#ελληνικά).*

---

## Status — read this first

| | |
|---|---|
| ✅ **Verified** | The modem, error correction and link layer, in software: built-in self-tests, the test suite (including two stations end-to-end over a simulated channel in real time), and two stations on one computer over acoustic coupling (speaker → microphone), July 2026. |
| ⚠️ **Not yet tested** | Through **real transceivers over the air**. Wired mode (audio interface + serial PTT). On a **real Windows machine** — CI runs the test suite on Windows, but not the GUI or real audio. |
| ⚠️ **Missing** | Automatic station identification. You must identify your station as your licence requires. |

The project is experimental and the on-air format may still change.
Reports from real radio tests are very welcome.

---

## How it works

| Layer | What it does |
|---|---|
| **Physical** | AFSK, 1200 baud, tones 1200/2200 Hz (Bell 202 compatible). Fits through the voice filter of any FM transceiver. Configurable 300–2400 baud. |
| **Timing** | Oerder–Meyr estimator (spectral line at the bit rate), energy-weighted, with linear tracking of the clock drift between the two sound cards. |
| **Equalisation** | Level-independent mark/space discriminator with rolling bias and gain correction — handles uneven speaker/microphone/radio response. |
| **Error correction** | Convolutional code rate 1/2, K=7 (polynomials 171/133 octal), soft-decision Viterbi, block interleaving. |
| **Integrity** | CRC-16/CCITT-FALSE per frame, PRBS-9 scrambler. |
| **Compression** | zlib before transmission when it helps (text typically 2–4×). Bounded decompression on receive. |
| **Link** | Stop-and-wait ARQ with acknowledgements and retransmission, file segmentation and reassembly, listen-before-talk (CSMA), long messages split automatically. |

Two ways to connect:

- **Acoustic** — the computer's speaker talks to the radio's microphone and
  the radio's speaker talks to the computer's microphone. No cable, no
  modification, any brand of radio. Picks up room noise.
- **Wired** — an audio interface (e.g. Digirig) with serial PTT (RTS/DTR).
  Much cleaner signal. *(Untested so far.)*

## Measured performance

Decoding a 100-byte message, 40 trials per point, white noise added
(`python3 tools/benchmark.py`):

| SNR, noise over 0–24 kHz | ≈ SNR in a 3 kHz voice channel | With FEC | Without FEC |
|---:|---:|:---:|:---:|
| 6 dB | +15 dB | 40/40 | 40/40 |
| 3 dB | +12 dB | 40/40 | 40/40 |
| 0 dB | +9 dB | 40/40 | 39/40 |
| −3 dB | +6 dB | 40/40 | 3/40 |
| −6 dB | +3 dB | 36/40 | 0/40 |
| −9 dB | 0 dB | 0/40 | 0/40 |

The noise is spread over the whole 0–24 kHz band of the 48 kHz sample rate.
A voice channel is about 3 kHz wide, so the SNR *inside the channel* is
about 9 dB better than the first column. In channel terms: reliable
down to about **+6 dB with FEC** and **+9 dB without**.

Also handled in the self-test: room echo, 2% input level, ×4 clipping,
0.5% clock drift between sound cards, and strongly uneven frequency
response.

Error correction alone (`python3 erevos_fec.py --selftest`, 60 bytes):
2% random bit errors → 20/20 recovered, 5% → ~17/20, 8% → ~2/20; error
bursts up to 60 consecutive bits → 20/20.

**Speed** (defaults, with FEC):

- 477 bps raw payload rate (889 without FEC); a full 200-byte frame takes
  3.35 s on air.
- With acknowledgements, retransmission guards and VOX lead-in, a full
  frame is delivered about every 8 s — roughly **200 bps effective**.
  A 10 KB file of incompressible data takes about 7 minutes; text
  compresses 2–4×.
- A short message goes out in 1–3 seconds.

## Install

Python 3.10 or newer.

**Linux (Debian / Ubuntu)**

```bash
sudo apt install python3 python3-tk python3-numpy libportaudio2 git
python3 -m pip install --user sounddevice     # Debian 12+: add --break-system-packages
git clone https://github.com/Steffskg/erevos-radio.git
cd erevos-radio
python3 erevos_gui.py
```

Optional: `./install-erevos.sh` adds a menu entry (category HamRadio) and
checks your system. For serial PTT: `python3 -m pip install --user pyserial`.

**Windows** *(untested on a real machine)*

Install Python from python.org (includes tkinter), then:

```bat
py -m pip install -r requirements.txt
py erevos_gui.py
```

## Use

1. Type your callsign at the top right.
2. Press **START** — the defaults work out of the box in acoustic mode.
3. Type a message and press **SEND**. `CQ` = broadcast, unacknowledged;
   a file needs a specific recipient.

Both ends must use **the same settings** in the MODEM tab.

To check the software without any sound: **DIAGNOSTICS → DEMO**. For two
stations on one computer, choose `(default)` or `pulse` as the audio device
— physical ALSA devices accept only one program at a time.

Command line:

```bash
python3 erevos_link.py --chat --call N0CALL      # text chat: /to /file /peers /stats /quit
python3 erevos_audio.py --devices               # list sound devices
python3 erevos_audio.py --monitor               # live input level and SNR
python3 erevos_audio.py --loopback              # transmit and hear yourself
python3 erevos_debug.py                         # stage-by-stage analysis of the audio path
python3 erevos_ptt.py --list                    # serial ports for PTT
python3 erevos_modem.py --selftest
python3 erevos_fec.py --selftest
```

Received files go to `~/erevos_inbox` (changeable in the NETWORK tab).
Settings are stored in `~/.erevos-radio.json`.

## Frame format

So that others can build compatible stations:

```
PREAMBLE  32 × 0x55
SYNC      2D D4 5A E3
LEN       payload length, 1 byte → FEC-coded (28 bits, interleaver width 4)
BODY      header + payload + CRC-16  → FEC-coded (interleaver width 16)
TRAILER   4 × 0x55
```

- Everything from `LEN` to the end of `BODY` is XORed with PRBS-9
  (x⁹ + x⁵ + 1, register seed 0x1FF) after coding.
- Header, 27 bytes, big-endian: `version u8 (=1)`, `type u8`,
  `src 9 bytes`, `dst 9 bytes`, `msg_id u16`, `seq u16`, `total u16`,
  `payload_len u8`. Callsigns are upper-case ASCII, zero-padded.
- Type: `0` text, `1` file header (`u32 size` + file name, `seq` = 0xFFFF,
  `total` = number of chunks), `2` file chunk, `3` acknowledgement (empty
  payload, echoes `msg_id` and `seq` of the frame it acknowledges). Bit
  `0x80` = payload is zlib-compressed.
- Payload ≤ 200 bytes. CRC-16/CCITT-FALSE over header + payload.
- FEC: convolutional, rate 1/2, K=7, polynomials 0o171 / 0o133,
  terminated in state 0. With FEC off, `LEN` and `BODY` are sent uncoded
  (still scrambled). Mark (1200 Hz) = bit 1.

## Tests

```bash
python3 -m pip install --user pytest
python3 -m pytest
```

The end-to-end tests run two complete stations through a simulated
channel in real time (about 1.5 minutes): text, text in noise, a long
Greek message split into several frames, a file transfer, and recovery
after both acknowledgements of a frame are lost. GitHub Actions runs
everything on Linux and Windows for every change.

## Legal

Transmit only on frequencies, power levels and modes your licence allows,
and identify your station as your rules require — this software does not
do it for you yet. It does not encrypt anything; the only transformation
is standard, publicly documented zlib compression and the scrambler
above. Check your local regulations.

The software is provided without any warranty (see the licence).

## License

**GPL-3.0-or-later** — see [LICENSE](LICENSE).
Copyright © 2026 Steff_SKG.

You may use, study, modify and share this software. If you distribute a
modified version, it must remain under the same licence with its source
code. For other licensing terms, contact the author.

Part of **Erevos**, a wider project for autonomous, resilient infrastructure.

---

## Ελληνικά

**Μηνύματα και αρχεία μέσω οποιουδήποτε πομποδέκτη — χωρίς internet,
χωρίς διακομιστές, χωρίς τρίτο λογισμικό ψηφιακών εκπομπών.** Ολόκληρη η
αλυσίδα (διαμόρφωση, χρονισμός, διόρθωση σφαλμάτων, πρωτόκολλο) είναι
γραμμένη από την αρχή σε Python. Εξαρτήσεις: μόνο `numpy` και `sounddevice`.

**Κατάσταση.** Επαληθευμένο σε λογισμικό (αυτοδιαγνωστικά, tests, δύο
σταθμοί από άκρη σε άκρη σε προσομοιωμένο κανάλι) και με δύο σταθμούς
στον ίδιο υπολογιστή, ηχείο → μικρόφωνο. **Δεν έχει δοκιμαστεί ακόμα μέσα
από πραγματικούς πομποδέκτες**, ούτε σε ενσύρματη λειτουργία, ούτε σε
πραγματικό υπολογιστή Windows. Δεν κάνει αυτόματη αναγνώριση σταθμού — τη
δηλώνεις εσύ, όπως ορίζει η άδειά σου.

**Απόδοση.** Με διόρθωση σφαλμάτων λειτουργεί αξιόπιστα μέχρι περίπου
+6 dB SNR μέσα σε κανάλι φωνής 3 kHz (πίνακας παραπάνω). Ωφέλιμος ρυθμός
477 bps· με τις επιβεβαιώσεις, περίπου 200 bps στην πράξη.

**Εγκατάσταση (Linux).**

```bash
sudo apt install python3 python3-tk python3-numpy libportaudio2 git
python3 -m pip install --user sounddevice     # Debian 12+: --break-system-packages
git clone https://github.com/Steffskg/erevos-radio.git
cd erevos-radio
python3 erevos_gui.py
```

**Χρήση.** Γράψε το διακριτικό σου πάνω δεξιά, πάτα **ΕΝΑΡΞΗ**, γράψε
μήνυμα και πάτα **ΑΠΟΣΤΟΛΗ**. Και οι δύο άκρες χρειάζονται ίδιες ρυθμίσεις
στην καρτέλα ΜΟΝΤΕΜ. Η γλώσσα αλλάζει από το μενού (Ελληνικά / English).

**Νομιμότητα.** Εκπέμπεις μόνο όπου και όπως επιτρέπει η άδειά σου, με το
διακριτικό σου. Το πρόγραμμα δεν κρυπτογραφεί.

**Άδεια.** GPL-3.0-or-later. Μπορείς να το χρησιμοποιήσεις, να το μελετήσεις,
να το αλλάξεις και να το μοιραστείς· αν διανείμεις τροποποιημένη έκδοση,
πρέπει να μείνει με την ίδια άδεια και με τον κώδικά της.
