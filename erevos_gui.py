#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Steff_SKG
"""
EREVOS RADIO
============
Μηνύματα και αρχεία μέσω πομποδέκτη. Χωρίς internet, χωρίς διακομιστές.
Messages and files over radio. No internet, no servers.

    python3 erevos_gui.py
"""

import contextlib
import io
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np

import erevos_audio as audio
import erevos_fec as fec
import erevos_link as link
import erevos_modem as modem
import erevos_ptt as pttmod

CONFIG_PATH = os.path.expanduser('~/.erevos-radio.json')
VERSION = '1.0'

VOID, BG, PANEL, CARD, EDGE = '#040a07', '#06110c', '#0a1a12', '#0d2318', '#163d29'
GREEN, GLOW, MID, DIM, GHOST = '#3ff08a', '#8effc0', '#2a9c63', '#1e6b45', '#134a30'
AMBER, RED, CYAN = '#ffb02e', '#ff5f56', '#3fe0f0'

# Η γραμματοσειρά επιλέγεται στην εκκίνηση: το DejaVu υπάρχει σε Linux,
# το Consolas σε Windows. Χωρίς αυτό η διεπαφή διαλύεται εκτός Linux.
MONO = ('DejaVu Sans Mono', 10)
MONO_S = ('DejaVu Sans Mono', 9)
MONO_B = ('DejaVu Sans Mono', 10, 'bold')
MONO_L = ('DejaVu Sans Mono', 15, 'bold')
MONO_XS = ('DejaVu Sans Mono', 8)

FONT_CANDIDATES = ('DejaVu Sans Mono', 'Consolas', 'Liberation Mono',
                   'Lucida Console', 'Courier New', 'Courier')


def pick_font(root):
    global MONO, MONO_S, MONO_B, MONO_L, MONO_XS
    fam = 'Courier'
    try:
        import tkinter.font as tkfont
        available = set(tkfont.families(root))
        for name in FONT_CANDIDATES:
            if name in available:
                fam = name
                break
    except Exception:                                   # noqa: BLE001
        pass
    MONO = (fam, 10)
    MONO_S = (fam, 9)
    MONO_B = (fam, 10, 'bold')
    MONO_L = (fam, 15, 'bold')
    MONO_XS = (fam, 8)
    return fam

# ---------------------------------------------------------------------------
# Κείμενα  /  Strings
# ---------------------------------------------------------------------------

S = {
 'call': ('ΔΙΑΚΡΙΤΙΚΟ', 'CALLSIGN'),
 'start': ('▶  ΕΝΑΡΞΗ', '▶  START'),
 'stop': ('■  ΔΙΑΚΟΠΗ', '■  STOP'),
 'idle': ('ΑΝΕΝΕΡΓΟ', 'OFFLINE'),
 'listen': ('ΑΚΡΟΑΣΗ', 'LISTENING'),
 'tx': ('ΕΚΠΟΜΠΗ', 'TRANSMIT'),
 't_main': ('  ΚΕΝΤΡΟ  ', '  CONSOLE  '),
 't_conn': ('  ΣΥΝΔΕΣΗ  ', '  CONNECTION  '),
 't_modem': ('  ΜΟΝΤΕΜ  ', '  MODEM  '),
 't_net': ('  ΔΙΚΤΥΟ  ', '  NETWORK  '),
 't_spec': ('  ΦΑΣΜΑ  ', '  SPECTRUM  '),
 't_diag': ('  ΔΙΑΓΝΩΣΤΙΚΑ  ', '  DIAGNOSTICS  '),
 't_help': ('  ΟΔΗΓΟΣ  ', '  GUIDE  '),
 'm_file': ('Αρχείο', 'File'),
 'm_radio': ('Ασύρματος', 'Radio'),
 'm_view': ('Προβολή', 'View'),
 'm_tools': ('Εργαλεία', 'Tools'),
 'm_lang': ('Γλώσσα', 'Language'),
 'm_help': ('Βοήθεια', 'Help'),
 'mi_send_file': ('Αποστολή αρχείου…', 'Send file…'),
 'mi_inbox': ('Άνοιγμα εισερχομένων', 'Open inbox'),
 'mi_savelog': ('Αποθήκευση συνομιλίας…', 'Save conversation…'),
 'mi_quit': ('Έξοδος', 'Quit'),
 'mi_start': ('Έναρξη / Διακοπή σταθμού', 'Start / Stop station'),
 'mi_tone': ('Δοκιμή εξόδου (τόνος)', 'Test output (tone)'),
 'mi_mic': ('Μέτρηση εισόδου', 'Measure input'),
 'mi_loop': ('Πλήρης δοκιμή βρόχου', 'Full loopback test'),
 'mi_demo': ('Επίδειξη χωρίς ήχο', 'Silent demo'),
 'mi_clearlog': ('Καθαρισμός συνομιλίας', 'Clear conversation'),
 'mi_quick': ('Γρήγορη εκκίνηση', 'Quick start panel'),
 'mi_wf': ('Ζωντανό φάσμα', 'Live spectrum'),
 'mi_defmodem': ('Προεπιλογές μόντεμ', 'Modem defaults'),
 'mi_defnet': ('Προεπιλογές δικτύου', 'Network defaults'),
 'mi_export': ('Εξαγωγή ρυθμίσεων…', 'Export settings…'),
 'mi_import': ('Εισαγωγή ρυθμίσεων…', 'Import settings…'),
 'mi_guide': ('Οδηγός χρήσης', 'User guide'),
 'mi_keys': ('Συντομεύσεις πληκτρολογίου', 'Keyboard shortcuts'),
 'mi_about': ('Περί EREVOS RADIO', 'About EREVOS RADIO'),
 'qs_title': ('▐ ΓΡΗΓΟΡΗ ΕΚΚΙΝΗΣΗ', '▐ QUICK START'),
 'qs_body': (
   '1.  Γράψε το διακριτικό σου πάνω δεξιά\n'
   '2.  Πάτα ΕΝΑΡΞΗ  —  δουλεύει αμέσως με τις προεπιλογές\n'
   '3.  Γράψε μήνυμα στο κάτω πλαίσιο και πάτα ΑΠΟΣΤΟΛΗ\n\n'
   'Προεπιλογή: ακουστική λειτουργία. Τα ηχεία μιλάνε στο μικρόφωνο\n'
   'του πομποδέκτη. Δεν χρειάζεται καλώδιο.',
   '1.  Type your callsign at the top right\n'
   '2.  Press START  —  works out of the box\n'
   '3.  Type a message in the box below and press SEND\n\n'
   'Default: acoustic mode. Your speakers talk to the radio\'s\n'
   'microphone. No cable needed.'),
 'qs_demo': ('ΔΟΚΙΜΗ ΧΩΡΙΣ ΗΧΟ', 'SILENT TEST'),
 'qs_hide': ('ΑΠΟΚΡΥΨΗ', 'HIDE'),
 'stations': ('ΣΤΑΘΜΟΙ', 'STATIONS'),
 'rxlevel': ('ΣΤΑΘΜΗ ΛΗΨΗΣ', 'RX LEVEL'),
 'linkinfo': ('ΖΕΥΞΗ', 'LINK'),
 'to': ('ΠΡΟΣ', 'TO'),
 'msg_ph': ('γράψε μήνυμα και πάτα Enter…', 'type a message and press Enter…'),
 'b_file': ('ΑΡΧΕΙΟ', 'FILE'),
 'b_send': ('ΑΠΟΣΤΟΛΗ', 'SEND'),
 'cq_hint': ('CQ = προς όλους, χωρίς επιβεβαίωση.  '
             'Για αρχείο χρειάζεται συγκεκριμένος παραλήπτης.',
             'CQ = broadcast, unacknowledged.  '
             'File transfer needs a specific recipient.'),
 'c_mode': ('ΤΡΟΠΟΣ ΣΥΝΔΕΣΗΣ', 'CONNECTION MODE'),
 'mode_ac': ('ΑΚΟΥΣΤΙΚΟΣ  ·  χωρίς καλώδιο',
             'ACOUSTIC  ·  no cable'),
 'mode_ac_d': ('Τα ηχεία του υπολογιστή μιλάνε στο μικρόφωνο του '
               'πομποδέκτη και το ηχείο του πομποδέκτη στο μικρόφωνο του '
               'υπολογιστή. Δουλεύει με radio οποιασδήποτε μάρκας.',
               'Your speakers talk to the radio microphone and the radio '
               'speaker talks to your microphone. Works with any brand.'),
 'mode_cb': ('ΕΝΣΥΡΜΑΤΟΣ  ·  audio interface',
             'WIRED  ·  audio interface'),
 'mode_cb_d': ('Digirig ή κάρτα ήχου USB με καλώδιο K1. Πολύ καθαρότερο '
               'σήμα, χωρίς θόρυβο δωματίου, με σειριακό PTT.',
               'Digirig or USB sound card with a K1 cable. Much cleaner '
               'signal, no room noise, with serial PTT.'),
 'c_dev': ('ΣΥΣΚΕΥΕΣ ΗΧΟΥ', 'AUDIO DEVICES'),
 'f_in': ('Είσοδος  (ακούμε)', 'Input  (we listen)'),
 'f_out': ('Έξοδος  (εκπέμπουμε)', 'Output  (we transmit)'),
 'dev_hint': ('Προτίμησε τη φυσική κάρτα (hw:0,0) ή το interface — '
              'όχι τα εικονικά (dmix, upmix, surround).',
              'Prefer the physical card (hw:0,0) or the interface — '
              'not the virtual ones (dmix, upmix, surround).'),
 'b_testout': ('Δοκιμή εξόδου', 'Test output'),
 'b_testin': ('Μέτρηση εισόδου', 'Measure input'),
 'b_testloop': ('Πλήρης δοκιμή βρόχου', 'Full loopback test'),
 'c_level': ('ΣΤΑΘΜΗ ΕΚΠΟΜΠΗΣ', 'TRANSMIT LEVEL'),
 'f_amp': ('Ένταση εξόδου', 'Output volume'),
 'amp_hint': ('Πολύ χαμηλή → δεν ανοίγει το VOX.  '
              'Πολύ ψηλή → ψαλίδισμα και παραμόρφωση.  Ξεκίνα από 0.50.',
              'Too low → VOX will not trigger.  '
              'Too high → clipping and distortion.  Start at 0.50.'),
 'c_ptt': ('ΕΝΕΡΓΟΠΟΙΗΣΗ ΕΚΠΟΜΠΗΣ  (PTT)', 'TRANSMIT KEYING  (PTT)'),
 'f_pttm': ('Μέθοδος', 'Method'),
 'f_pttm_d': ('vox = το radio ανοίγει μόνο του από τον ήχο',
              'vox = the radio keys itself from the audio'),
 'f_pttp': ('Σειριακή θύρα', 'Serial port'),
 'f_pttp_d': ('μόνο για μέθοδο serial', 'only for serial method'),
 'f_pttl': ('Γραμμή ελέγχου', 'Control line'),
 'f_pttl_d': ('το Digirig χρησιμοποιεί RTS', 'Digirig uses RTS'),
 'f_vox': ('Τόνος πριν τα δεδομένα', 'Tone before data'),
 'f_vox_d': ('δίνει χρόνο στο VOX να ανοίξει', 'gives VOX time to open'),
 'warn_same': ('⚠  ΟΛΟΙ οι σταθμοί πρέπει να έχουν ΤΙΣ ΙΔΙΕΣ τιμές '
               'για να μιλήσουν μεταξύ τους.',
               '⚠  ALL stations must use THE SAME values to talk '
               'to each other.'),
 'c_mod': ('ΔΙΑΜΟΡΦΩΣΗ', 'MODULATION'),
 'c_demod': ('ΑΠΟΔΙΑΜΟΡΦΩΤΗΣ', 'DEMODULATOR'),
 'f_baud': ('Ταχύτητα (baud)', 'Speed (baud)'),
 'f_baud_d': ('χαμηλότερη = πιο αργή αλλά πιο ανθεκτική',
              'lower = slower but more robust'),
 'f_mark': ('Τόνος mark  (bit 1)', 'Mark tone  (bit 1)'),
 'f_space': ('Τόνος space (bit 0)', 'Space tone (bit 0)'),
 'f_tone_d': ('Hz — 1200/2200 είναι το πρότυπο Bell 202',
              'Hz — 1200/2200 is the Bell 202 standard'),
 'f_fec': ('Διόρθωση σφαλμάτων', 'Error correction'),
 'f_fec_d': ('διπλασιάζει τα bits αλλά σώζει το σήμα σε θόρυβο',
             'doubles the bits but saves the signal in noise'),
 'f_pre': ('Προοίμιο (bytes)', 'Preamble (bytes)'),
 'f_pre_d': ('περισσότερο = σιγουρότερο κλείδωμα, αργότερη εκπομπή',
             'more = safer lock, slower transmission'),
 'f_agc': ('Παράθυρο εξισορρόπησης', 'Balancing window'),
 'f_agc_d': ('bits — διορθώνει άνιση απόκριση ηχείων/μικροφώνου',
             'bits — corrects uneven speaker/mic response'),
 'f_sync': ('Ανοχή συγχρονισμού', 'Sync tolerance'),
 'f_sync_d': ('πόσα λάθος bits δεχόμαστε στη λέξη έναρξης',
              'how many wrong bits we accept in the start word'),
 'b_apply': ('ΕΦΑΡΜΟΓΗ', 'APPLY'),
 'b_defaults': ('Επαναφορά προεπιλογών', 'Restore defaults'),
 'c_rel': ('ΑΞΙΟΠΙΣΤΙΑ', 'RELIABILITY'),
 'c_rel_s': ('κάθε πλαίσιο επιβεβαιώνεται', 'every frame is acknowledged'),
 'f_ack': ('Αναμονή επιβεβαίωσης', 'Acknowledgement timeout'),
 'f_ack_d': ('δευτερόλεπτα — αύξησέ το σε αργές ταχύτητες',
             'seconds — increase it at slow speeds'),
 'f_retry': ('Προσπάθειες', 'Retries'),
 'f_retry_d': ('πόσες φορές ξαναστέλνει πριν τα παρατήσει',
               'how many times it resends before giving up'),
 'f_ackrep': ('Επαναλήψεις επιβεβαίωσης', 'Acknowledgement repeats'),
 'f_ackrep_d': ('1 = γρηγορότερα αρχεία, 2 = πιο σίγουρη ζεύξη',
                '1 = faster files, 2 = more reliable link'),
 'f_chunk': ('Μέγεθος τμήματος', 'Chunk size'),
 'f_chunk_d': ('bytes ανά πλαίσιο αρχείου', 'bytes per file frame'),
 'c_csma': ('ΑΠΟΦΥΓΗ ΣΥΓΚΡΟΥΣΕΩΝ', 'COLLISION AVOIDANCE'),
 'c_csma_s': ('άκου πριν μιλήσεις', 'listen before you talk'),
 'f_busy': ('Κατώφλι κατειλημμένου', 'Busy threshold'),
 'f_busy_d': ('πάνω από αυτή τη στάθμη περιμένει',
              'above this level it waits'),
 'busy_hint': ('Φαίνεται ως λευκή γραμμή στη μπάρα στάθμης λήψης.',
               'Shown as a white line on the RX level bar.'),
 'c_files': ('ΑΡΧΕΙΑ', 'FILES'),
 'f_inbox': ('Φάκελος εισερχομένων', 'Inbox folder'),
 'b_openinbox': ('Άνοιγμα φακέλου', 'Open folder'),
 'wf_title': ('ΚΑΤΑΡΡΑΚΤΗΣ  ·  τι ακούει η είσοδος',
              'WATERFALL  ·  what the input hears'),
 'wf_live': ('ενεργή απεικόνιση', 'live display'),
 'sp_title': ('ΣΤΙΓΜΙΑΙΟ ΦΑΣΜΑ', 'INSTANT SPECTRUM'),
 'balanced': ('ισορροπημένο', 'balanced'),
 'unbalanced': ('ανισορροπία — το AGC το διορθώνει',
                'imbalance — the AGC corrects it'),
 'b_demo': ('ΕΠΙΔΕΙΞΗ  (χωρίς ήχο)', 'DEMO  (no sound)'),
 'b_dmodem': ('Δοκιμή μόντεμ', 'Modem test'),
 'b_dfec': ('Δοκιμή διόρθωσης σφαλμάτων', 'Error correction test'),
 'b_clear': ('Καθαρισμός', 'Clear'),
 'ready': ('EREVOS RADIO έτοιμο.', 'EREVOS RADIO ready.'),
 'ready2': ('Γράψε διακριτικό πάνω δεξιά και πάτα ΕΝΑΡΞΗ.',
            'Type your callsign at the top right and press START.'),
 'ready3': ('Για να δεις ότι δουλεύει χωρίς ήχο: ΔΙΑΓΝΩΣΤΙΚΑ → ΕΠΙΔΕΙΞΗ.',
            'To verify it works with no sound: DIAGNOSTICS → DEMO.'),
 'st_ready': ('έτοιμο', 'ready'),
 'st_listen': ('ακρόαση', 'listening'),
 'st_busy': ('κανάλι κατειλημμένο — αναμονή', 'channel busy — waiting'),
 'st_rx': ('λήψη', 'receiving'),
 'st_tx': ('αποστολή', 'sending'),
 'need_start': ('Πάτα πρώτα ΕΝΑΡΞΗ', 'Press START first'),
 'tx_running': ('Εκπομπή σε εξέλιξη', 'Transmission in progress'),
 'need_dst': ('Για αρχείο χρειάζεται συγκεκριμένος παραλήπτης, όχι CQ.',
              'File transfer needs a specific recipient, not CQ.'),
 'est': ('Εκτιμώμενος χρόνος', 'Estimated time'),
 'minutes': ('λεπτά', 'minutes'),
 'proceed': ('Συνέχεια;', 'Proceed?'),
 'no_ack': ('  χωρίς επιβεβαίωση παραλαβής', '  no acknowledgement'),
 'stopped': ('Σταθμός εκτός λειτουργίας.', 'Station stopped.'),
 'nocall': ('Συμπλήρωσε διακριτικό', 'Enter a callsign'),
 'fail_audio': ('Αποτυχία εκκίνησης ήχου', 'Audio start failed'),
 'try_other': ('Δοκίμασε άλλη συσκευή εισόδου στην καρτέλα ΣΥΝΔΕΣΗ.',
               'Try another input device in the CONNECTION tab.'),
 'duplex_on': ('αμφίδρομο ρεύμα — το μικρόφωνο μένει ανοιχτό',
               'full duplex — microphone stays open'),
 'duplex_off': ('ξεχωριστά ρεύματα — η λήψη παύει κατά την εκπομπή',
                'separate streams — RX pauses during TX'),
 'ack_sent': ('  → ACK προς {d} (μην {m}/{s}) #{n}',
              '  → ACK to {d} (msg {m}/{s}) #{n}'),
 'ack_recv': ('  ← ACK από {s0} (μην {m}/{s})',
              '  ← ACK from {s0} (msg {m}/{s})'),
 'ack_bad': ('  ! ACK δεν ταιριάζει: ήρθε {g} — περίμενα {w}',
             '  ! ACK mismatch: got {g} — expected {w}'),
 'mi_verbose': ('Λεπτομερής καταγραφή', 'Verbose log'),
 'copy': ('Αντιγραφή', 'Copy'),
 'paste': ('Επικόλληση', 'Paste'),
 'cut': ('Αποκοπή', 'Cut'),
 'selall': ('Επιλογή όλων', 'Select all'),
 'clear_f': ('Καθαρισμός', 'Clear'),
 'busy_dev': ('Η συσκευή ήχου χρησιμοποιείται ήδη από άλλο πρόγραμμα.\n\n'
              'Οι φυσικές κάρτες (hw:0,0) δέχονται ΕΝΑ πρόγραμμα τη φορά.\n'
              'Για δύο σταθμούς στον ίδιο υπολογιστή διάλεξε\n'
              '(default) ή "pulse" στην καρτέλα ΣΥΝΔΕΣΗ.',
              'The audio device is already in use by another program.\n\n'
              'Physical cards (hw:0,0) accept ONE program at a time.\n'
              'For two stations on the same computer choose\n'
              '(default) or "pulse" in the CONNECTION tab.'),
 'same_dev': ('⚠ Ίδια φυσική κάρτα για είσοδο και έξοδο. Οι κάρτες ALSA '
              'είναι αποκλειστικές — η εκπομπή θα σταλεί στην '
              'κοινόχρηστη έξοδο.',
              '⚠ Same physical card for input and output. ALSA cards are '
              'exclusive — transmission will use the shared output.'),
 'b_auto': ('Αυτόματη ρύθμιση', 'Auto configure'),
 'auto_done': ('Ρυθμίστηκε: συσκευές σε (default), ένταση 0.60. '
               'Δουλεύει και με δύο σταθμούς στον ίδιο υπολογιστή.',
               'Configured: devices set to (default), volume 0.60. '
               'Works with two stations on the same computer.'),
 'fallback': ('Η επιλεγμένη συσκευή ήταν πιασμένη — χρήση εφεδρικής.',
              'Selected device was busy — using fallback.'),
 'calib': ('Θόρυβος χώρου {n:.4f} · κατώφλι καναλιού {t:.4f}',
           'Room noise {n:.4f} · channel threshold {t:.4f}'),
 'b_calib': ('Μέτρηση θορύβου', 'Measure noise'),
 'noisy': ('⚠ Ο θόρυβος είναι υψηλός. Κατέβασε την ευαισθησία του '
           'μικροφώνου (alsamixer → F4 → Capture).',
           '⚠ Room noise is high. Lower the microphone sensitivity '
           '(alsamixer → F4 → Capture).'),
 'tone_gap': ('⚠ Η απόσταση των δύο τόνων είναι μικρότερη από την '
              'ταχύτητα. Η λήψη θα είναι αναξιόπιστη — αύξησε την '
              'απόσταση ή κατέβασε την ταχύτητα.',
              '⚠ Tone spacing is smaller than the baud rate. Reception '
              'will be unreliable — widen the spacing or lower the '
              'speed.'),
 'c_engine': ('ΜΗΧΑΝΗ ΗΧΟΥ', 'AUDIO ENGINE'),
 'f_engine': ('Τρόπος', 'Mode'),
 'f_engine_d': ('split = μόνιμη είσοδος + ξεχωριστή έξοδος (δοκιμασμένο)\n'
                'duplex = ένα αμφίδρομο ρεύμα (δεν το σηκώνουν όλες οι κάρτες)',
                'split = permanent input + separate output (proven)\n'
                'duplex = one bidirectional stream (not all cards support it)'),
 'audio_live': ('ΗΧΟΣ', 'AUDIO'),
 'mic_dead': ('Το μικρόφωνο σταμάτησε — επανεκκίνηση', 
              'Microphone stalled — restarting'),
 'keys_body': (
   'Enter           αποστολή μηνύματος\n'
   'Ctrl+O          αποστολή αρχείου\n'
   'Ctrl+R          έναρξη / διακοπή σταθμού\n'
   'Ctrl+D          επίδειξη χωρίς ήχο\n'
   'Ctrl+L          καθαρισμός συνομιλίας\n'
   'F1              οδηγός χρήσης\n'
   'Ctrl+Q          έξοδος',
   'Enter           send message\n'
   'Ctrl+O          send file\n'
   'Ctrl+R          start / stop station\n'
   'Ctrl+D          silent demo\n'
   'Ctrl+L          clear conversation\n'
   'F1              user guide\n'
   'Ctrl+Q          quit'),
}

HELP = {
 'el': [
  ('ΤΙ ΚΑΝΕΙ ΑΥΤΟ ΤΟ ΠΡΟΓΡΑΜΜΑ',
   'Μετατρέπει τα μηνύματα και τα αρχεία σου σε ήχο, τον στέλνει στον '
   'πομποδέκτη, και στην άλλη άκρη κάνει το αντίστροφο.\n\n'
   'Δεν χρειάζεται internet, ούτε κεραία παρόχου, ούτε διακομιστής. '
   'Δουλεύει όπου φτάνει το radio.'),
  ('ΠΩΣ ΣΤΗΝΕΤΑΙ Η ΑΛΛΗ ΑΚΡΗ',
   'Ο συνομιλητής σου τρέχει το ίδιο πρόγραμμα, βάζει το διακριτικό σου '
   'στο πεδίο ΠΡΟΣ, και έχει τις ίδιες ρυθμίσεις στην καρτέλα ΜΟΝΤΕΜ.\n\n'
   'Τρέχει σε Linux και Windows — χρειάζεται μόνο Python, numpy και '
   'sounddevice.'),
  ('ΑΚΟΥΣΤΙΚΑ Ή ΜΕ ΚΑΛΩΔΙΟ',
   'ΑΚΟΥΣΤΙΚΑ: ηχεία κοντά στο μικρόφωνο του radio, ηχείο του radio κοντά '
   'στο μικρόφωνο του υπολογιστή. Λειτουργεί, αλλά πιάνει και τον θόρυβο '
   'του δωματίου.\n\n'
   'ΜΕ ΚΑΛΩΔΙΟ: audio interface (Digirig) — πολύ καθαρότερο, και μπορείς '
   'να χρησιμοποιήσεις σειριακό PTT αντί για VOX.'),
  ('ΑΝ ΔΕΝ ΠΕΡΝΑΕΙ ΤΙΠΟΤΑ',
   '1.  ΔΙΑΓΝΩΣΤΙΚΑ → ΕΠΙΔΕΙΞΗ. Αν αποτύχει, φταίει το λογισμικό, όχι '
   'ο ήχος.\n'
   '2.  ΣΥΝΔΕΣΗ → Μέτρηση εισόδου. Η κορυφή πρέπει να είναι 0.2 έως 0.9.\n'
   '    Ρύθμιση ευαισθησίας: Linux alsamixer (F4 → Capture), '
   'Windows Ρυθμίσεις → Ήχος → Είσοδος.\n'
   '3.  ΣΥΝΔΕΣΗ → Πλήρης δοκιμή βρόχου. Στέλνει και ακούει τον εαυτό του.\n'
   '4.  ΦΑΣΜΑ. Το σήμα πρέπει να πέφτει πάνω στις δύο διακεκομμένες '
   'γραμμές.'),
  ('ΤΑΧΥΤΗΤΑ',
   'Στις προεπιλογές περνάνε περίπου 60 bytes το δευτερόλεπτο. Ένα μήνυμα '
   'φεύγει σε 1-3 δευτερόλεπτα, ένα αρχείο 10 KB θέλει γύρω στα 3 λεπτά.\n\n'
   'Με καθαρή γραμμή (καλώδιο) κλείσε τη διόρθωση σφαλμάτων και '
   'διπλασιάζεις την ταχύτητα.'),
  ('ΝΟΜΙΜΟΤΗΤΑ',
   'Εκπέμπεις μόνο στις ζώνες που επιτρέπει η άδειά σου, με το διακριτικό '
   'σου. Η κρυπτογράφηση περιεχομένου δεν επιτρέπεται στις ερασιτεχνικές '
   'ζώνες.')],
 'en': [
  ('WHAT THIS PROGRAM DOES',
   'It turns your messages and files into audio, sends that audio through '
   'the radio, and does the reverse at the other end.\n\n'
   'No internet, no carrier, no server. It works wherever the radio '
   'reaches.'),
  ('SETTING UP THE OTHER END',
   'Your correspondent runs the same program, puts your callsign in the TO '
   'field, and uses the same settings in the MODEM tab.\n\n'
   'Runs on Linux and Windows — needs only Python, numpy and sounddevice.'),
  ('ACOUSTIC OR CABLE',
   'ACOUSTIC: speakers near the radio microphone, radio speaker near the '
   'computer microphone. It works, but it also picks up room noise.\n\n'
   'CABLE: an audio interface (Digirig) — much cleaner, and you can use '
   'serial PTT instead of VOX.'),
  ('IF NOTHING GETS THROUGH',
   '1.  DIAGNOSTICS → DEMO. If that fails, the problem is software, not '
   'audio.\n'
   '2.  CONNECTION → Measure input. Peak should be between 0.2 and 0.9.\n'
   '    Sensitivity: Linux alsamixer (F4 → Capture), '
   'Windows Settings → Sound → Input.\n'
   '3.  CONNECTION → Full loopback test. It transmits and hears itself.\n'
   '4.  SPECTRUM. The signal must land on the two dashed lines.'),
  ('SPEED',
   'At defaults roughly 60 bytes per second get through. A message takes '
   '1-3 seconds, a 10 KB file about 3 minutes.\n\n'
   'On a clean line (cable) turn off error correction and you double the '
   'speed.'),
  ('LEGALITY',
   'Transmit only in the bands your licence allows, using your callsign. '
   'Content encryption is not permitted on amateur bands.')],
}


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ===========================================================================

class ErevosApp:

    def __init__(self, root):
        self.root = root
        self.cfg = self._load()
        self.lang = self.cfg.get('lang', 'en')

        self.station = None
        self.ptt = None
        self.events = queue.Queue()
        self.busy = False
        self.cancel_flag = False
        self.tx_active = False
        self._wf_img = None
        self._wf_row = 0
        self._blink = 0

        root.title('EREVOS RADIO')
        root.configure(bg=VOID)
        root.geometry('1080x760')
        root.minsize(940, 660)

        self.font_family = pick_font(root)
        self._keymap = self._keycodes()
        self._init_vars()
        self._style()
        self._build_all()
        self._bind_keys()

        self._apply_modem(silent=True)
        self._apply_link(silent=True)
        link.set_verbose(self.v_verbose.get())

        root.protocol('WM_DELETE_WINDOW', self._close)
        root.after(150, self._pump)
        root.after(250, self._paint)
        root.after(500, self._startup_check)

    def t(self, key):
        v = S.get(key, (key, key))
        return v[0 if self.lang == 'el' else 1]

    # ------------------------------------------------------------ μεταβλητές

    def _init_vars(self):
        c = self.cfg
        self.v_call = tk.StringVar(value=c.get('call', ''))
        self.v_dst = tk.StringVar(value=c.get('dst', 'CQ'))
        self.v_msg = tk.StringVar()
        self.v_state = tk.StringVar(value=self.t('idle'))
        self.v_status = tk.StringVar(value=self.t('st_ready'))
        self.v_stats = tk.StringVar(value='')
        self.v_linkinfo = tk.StringVar(value='—')
        self.v_modeminfo = tk.StringVar(value='')
        self.v_tone = tk.StringVar(value='—')
        self.v_wfon = tk.BooleanVar(value=True)
        self.v_verbose = tk.BooleanVar(value=c.get('verbose', False))
        self.v_quick = tk.BooleanVar(value=c.get('quick', True))

        self.v_mode = tk.StringVar(value=c.get('mode', 'acoustic'))
        ins = ['(default)'] + [f'{i}: {n[:38]}'
                               for i, n in audio.device_list('in')]
        outs = ['(default)'] + [f'{i}: {n[:38]}'
                                for i, n in audio.device_list('out')]
        self.dev_in, self.dev_out = ins, outs
        self.v_in = tk.StringVar(value=c.get('in') if c.get('in') in ins
                                 else ins[0])
        self.v_out = tk.StringVar(value=c.get('out') if c.get('out') in outs
                                  else outs[0])
        self.v_amp = tk.DoubleVar(value=c.get('amp', 0.5))
        self.v_ptt = tk.StringVar(value=c.get('ptt', 'vox'))
        self.ports = [d for d, _ in pttmod.list_ports()] or ['—']
        self.v_pttport = tk.StringVar(
            value=c.get('pttport') if c.get('pttport') in self.ports
            else self.ports[0])
        self.v_pttline = tk.StringVar(value=c.get('pttline', 'RTS'))
        self.v_vox = tk.BooleanVar(value=c.get('vox', True))
        self.v_engine = tk.StringVar(value=c.get('engine', 'split'))
        self.v_audio = tk.StringVar(value='—')

        self.m_baud = tk.IntVar(value=c.get('baud', 1200))
        self.m_mark = tk.IntVar(value=c.get('mark', 1200))
        self.m_space = tk.IntVar(value=c.get('space', 2200))
        self.m_fec = tk.BooleanVar(value=c.get('fec', True))
        self.m_pre = tk.IntVar(value=c.get('preamble', 32))
        self.m_agc = tk.IntVar(value=c.get('agc', 48))
        self.m_sync = tk.IntVar(value=c.get('syncerr', 2))

        _ack = float(c.get('ack', 9.0))
        self.n_ack = tk.DoubleVar(value=9.0 if _ack <= 6.0 else _ack)
        self.n_retry = tk.IntVar(value=c.get('retries', 3))
        self.n_ackrep = tk.IntVar(value=c.get('ackrep', 2))
        self.n_chunk = tk.IntVar(value=c.get('chunk', 200))
        self.n_busy = tk.DoubleVar(value=c.get('busy', 0.02))
        self.v_inbox = tk.StringVar(value=c.get('inbox', link.INBOX_DIR))

    # ----------------------------------------------------------------- στυλ

    def _style(self):
        s = ttk.Style()
        with contextlib.suppress(tk.TclError):
            s.theme_use('clam')
        s.configure('.', background=BG, foreground=GREEN, fieldbackground=CARD,
                    bordercolor=EDGE, lightcolor=EDGE, darkcolor=VOID)
        s.configure('TNotebook', background=VOID, borderwidth=0, tabmargins=0)
        s.configure('TNotebook.Tab', background=VOID, foreground=DIM,
                    padding=(18, 10), font=MONO_S, borderwidth=0)
        s.map('TNotebook.Tab', background=[('selected', BG)],
              foreground=[('selected', GLOW)])
        s.configure('TCombobox', fieldbackground=CARD, background=CARD,
                    foreground=GREEN, arrowcolor=MID, borderwidth=0,
                    selectbackground=CARD, selectforeground=GLOW)
        s.map('TCombobox', fieldbackground=[('readonly', CARD)])
        s.configure('Vertical.TScrollbar', background=CARD, troughcolor=BG,
                    bordercolor=BG, arrowcolor=DIM, borderwidth=0)
        s.configure('Horizontal.TProgressbar', background=GREEN,
                    troughcolor=CARD, borderwidth=0, thickness=6)
        self.root.option_add('*TCombobox*Listbox.background', CARD)
        self.root.option_add('*TCombobox*Listbox.foreground', GREEN)
        self.root.option_add('*TCombobox*Listbox.selectBackground', DIM)
        self.root.option_add('*TCombobox*Listbox.font', MONO_S)

    def _btn(self, p, text, cmd, kind='ghost', width=None):
        pal = {'ghost': (CARD, GREEN, EDGE), 'solid': (GREEN, VOID, GREEN),
               'amber': (CARD, AMBER, AMBER), 'danger': (CARD, RED, RED)}
        bg, fg, bd = pal[kind]
        b = tk.Button(p, text=text, command=cmd, bg=bg, fg=fg,
                      activebackground=bg, activeforeground=GLOW, bd=0,
                      font=MONO_S, padx=14, pady=7, cursor='hand2',
                      highlightthickness=1, highlightbackground=bd,
                      highlightcolor=bd)
        if width:
            b.configure(width=width)
        return b

    def _hint(self, p, text, pady=(2, 0), wrap=820):
        w = tk.Label(p, text=text, bg=p['bg'], fg=GHOST, font=MONO_XS,
                     anchor='w', justify='left', wraplength=wrap)
        w.pack(anchor='w', pady=pady)
        return w

    def _card(self, p, title, sub=''):
        wrap = tk.Frame(p, bg=EDGE, padx=1, pady=1)
        wrap.pack(fill='x', pady=(0, 14))
        c = tk.Frame(wrap, bg=CARD, padx=16, pady=13)
        c.pack(fill='both', expand=True)
        bar = tk.Frame(c, bg=CARD)
        bar.pack(fill='x', pady=(0, 8))
        tk.Label(bar, text='▐ ' + title, bg=CARD, fg=GLOW,
                 font=MONO_B).pack(side='left')
        if sub:
            tk.Label(bar, text=sub, bg=CARD, fg=GHOST,
                     font=MONO_XS).pack(side='right')
        return c

    def _field(self, p, label, factory, explain=''):
        row = tk.Frame(p, bg=p['bg'])
        row.pack(fill='x', pady=4)
        tk.Label(row, text=label, bg=p['bg'], fg=GREEN, font=MONO_S,
                 width=26, anchor='w').pack(side='left')
        factory(row).pack(side='left')
        if explain:
            tk.Label(row, text=explain, bg=p['bg'], fg=GHOST, font=MONO_XS,
                     anchor='w', justify='left',
                     wraplength=420).pack(side='left', padx=(14, 0))

    def _spin(self, p, var, lo, hi, step=1, width=8):
        return tk.Spinbox(p, from_=lo, to=hi, increment=step, textvariable=var,
                          width=width, bg=CARD, fg=GREEN, bd=0, font=MONO_S,
                          justify='center', buttonbackground=PANEL,
                          insertbackground=GREEN, highlightthickness=1,
                          highlightbackground=EDGE, readonlybackground=CARD)

    def _scale(self, p, var, lo, hi, step, length=220):
        return tk.Scale(p, from_=lo, to=hi, resolution=step,
                        orient='horizontal', variable=var, length=length,
                        bg=CARD, fg=GREEN, troughcolor=BG,
                        highlightthickness=0, bd=0, font=MONO_XS,
                        activebackground=GLOW, sliderrelief='flat',
                        sliderlength=18, width=10)

    def _check(self, p, var):
        return tk.Checkbutton(p, variable=var, bg=p['bg'], fg=GREEN,
                              selectcolor=VOID, activebackground=p['bg'],
                              highlightthickness=0, bd=0)

    def _entry(self, p, var, width=20, font=MONO_S, justify='left'):
        w = tk.Entry(p, textvariable=var, bg=CARD, fg=GREEN, bd=0,
                     width=width, font=font, justify=justify,
                     insertbackground=GREEN, highlightthickness=1,
                     highlightbackground=EDGE, highlightcolor=MID,
                     selectbackground=DIM, selectforeground=GLOW)
        self._editable(w)
        return w

    # ---- αντιγραφή / επικόλληση -------------------------------------
    # Με ελληνική διάταξη πληκτρολογίου το Ctrl+C δεν στέλνει "c" αλλά
    # "ψ", οπότε οι προεπιλεγμένες συντομεύσεις του Tk δεν πιάνουν.
    # Δένουμε με ΚΩΔΙΚΟ πλήκτρου (ανεξάρτητος διάταξης) και προσθέτουμε
    # και μενού δεξιού κλικ.
    #
    # Οι κωδικοί διαφέρουν ανά σύστημα παραθύρων:
    #   x11   (Linux)   φυσικοί κωδικοί πλήκτρων
    #   win32 (Windows) virtual-key codes - κι αυτοί ανεξάρτητοι διάταξης
    #   aqua  (macOS)   η αντιγραφή γίνεται με Cmd και το Tk τη χειρίζεται
    #                   μόνο του, οπότε δεν δένουμε τίποτα

    KEYCODES_X11 = {54: 'copy', 55: 'paste', 53: 'cut', 38: 'all'}
    KEYCODES_WIN = {67: 'copy', 86: 'paste', 88: 'cut', 65: 'all'}

    def _keycodes(self):
        try:
            ws = str(self.root.tk.call('tk', 'windowingsystem'))
        except Exception:                               # noqa: BLE001
            ws = 'x11'
        return {'x11': self.KEYCODES_X11,
                'win32': self.KEYCODES_WIN}.get(ws, {})

    def _editable(self, w, readonly=False):
        menu = tk.Menu(w, tearoff=0, bg=PANEL, fg=GREEN,
                       activebackground=DIM, activeforeground=GLOW,
                       bd=0, font=MONO_S)
        menu.add_command(label=self.t('copy'),
                         command=lambda: self._edit(w, 'copy'))
        if not readonly:
            menu.add_command(label=self.t('paste'),
                             command=lambda: self._edit(w, 'paste'))
            menu.add_command(label=self.t('cut'),
                             command=lambda: self._edit(w, 'cut'))
        menu.add_separator()
        menu.add_command(label=self.t('selall'),
                         command=lambda: self._edit(w, 'all'))

        def popup(e):
            with contextlib.suppress(Exception):
                menu.tk_popup(e.x_root, e.y_root)
            return 'break'

        def onkey(e):
            if not (e.state & 0x4):          # χωρίς Control
                return None
            act = self._keymap.get(e.keycode)
            if act and (not readonly or act in ('copy', 'all')):
                self._edit(w, act)
                return 'break'
            return None

        w.bind('<Button-3>', popup)
        w.bind('<Control-KeyPress>', onkey)
        return w

    def _edit(self, w, action):
        try:
            is_text = isinstance(w, tk.Text)
            if action == 'all':
                if is_text:
                    w.tag_add('sel', '1.0', 'end')
                else:
                    w.select_range(0, 'end')
                return
            if action in ('copy', 'cut'):
                try:
                    sel = (w.get('sel.first', 'sel.last') if is_text
                           else w.selection_get())
                except Exception:                       # noqa: BLE001
                    sel = (w.get('1.0', 'end-1c') if is_text else w.get())
                if not sel:
                    return
                self.root.clipboard_clear()
                self.root.clipboard_append(sel)
                if action == 'cut' and str(w.cget('state')) != 'disabled':
                    if is_text:
                        with contextlib.suppress(Exception):
                            w.delete('sel.first', 'sel.last')
                    else:
                        with contextlib.suppress(Exception):
                            w.delete('sel.first', 'sel.last')
            elif action == 'paste':
                if str(w.cget('state')) == 'disabled':
                    return
                data = self.root.clipboard_get()
                if is_text:
                    w.insert('insert', data)
                else:
                    with contextlib.suppress(Exception):
                        w.delete('sel.first', 'sel.last')
                    w.insert('insert', data)
        except Exception:                               # noqa: BLE001
            pass

    # ================================================================ ΜΕΝΟΥ

    def _menu(self):
        mb = tk.Menu(self.root, bg=PANEL, fg=GREEN, activebackground=DIM,
                     activeforeground=GLOW, bd=0, font=MONO_S)

        def sub():
            return tk.Menu(mb, tearoff=0, bg=PANEL, fg=GREEN,
                           activebackground=DIM, activeforeground=GLOW,
                           bd=0, font=MONO_S, selectcolor=GREEN)

        m = sub()
        m.add_command(label=self.t('mi_send_file'), accelerator='Ctrl+O',
                      command=self._send_file)
        m.add_command(label=self.t('mi_inbox'), command=self._open_inbox)
        m.add_separator()
        m.add_command(label=self.t('mi_savelog'), command=self._save_log)
        m.add_separator()
        m.add_command(label=self.t('mi_quit'), accelerator='Ctrl+Q',
                      command=self._close)
        mb.add_cascade(label=self.t('m_file'), menu=m)

        m = sub()
        m.add_command(label=self.t('mi_start'), accelerator='Ctrl+R',
                      command=self._toggle)
        m.add_separator()
        m.add_command(label=self.t('mi_tone'), command=self._test_out)
        m.add_command(label=self.t('mi_mic'), command=self._test_in)
        m.add_command(label=self.t('mi_loop'), command=self._test_loop)
        m.add_separator()
        m.add_command(label=self.t('mi_demo'), accelerator='Ctrl+D',
                      command=self._demo)
        mb.add_cascade(label=self.t('m_radio'), menu=m)

        m = sub()
        m.add_checkbutton(label=self.t('mi_quick'), variable=self.v_quick,
                          command=self._toggle_quick)
        m.add_checkbutton(label=self.t('mi_wf'), variable=self.v_wfon)
        m.add_checkbutton(label=self.t('mi_verbose'), variable=self.v_verbose,
                          command=lambda: link.set_verbose(
                              self.v_verbose.get()))
        m.add_separator()
        m.add_command(label=self.t('mi_clearlog'), accelerator='Ctrl+L',
                      command=self._clear_log)
        mb.add_cascade(label=self.t('m_view'), menu=m)

        m = sub()
        m.add_command(label=self.t('mi_defmodem'), command=self._reset_modem)
        m.add_command(label=self.t('mi_defnet'), command=self._reset_link)
        m.add_separator()
        m.add_command(label=self.t('mi_export'), command=self._export_cfg)
        m.add_command(label=self.t('mi_import'), command=self._import_cfg)
        mb.add_cascade(label=self.t('m_tools'), menu=m)

        m = sub()
        self.v_lang = tk.StringVar(value=self.lang)
        m.add_radiobutton(label='Ελληνικά', variable=self.v_lang, value='el',
                          command=lambda: self._set_lang('el'))
        m.add_radiobutton(label='English', variable=self.v_lang, value='en',
                          command=lambda: self._set_lang('en'))
        mb.add_cascade(label=self.t('m_lang'), menu=m)

        m = sub()
        m.add_command(label=self.t('mi_guide'), accelerator='F1',
                      command=lambda: self.nb.select(6))
        m.add_command(label=self.t('mi_keys'), command=self._show_keys)
        m.add_separator()
        m.add_command(label=self.t('mi_about'), command=self._show_about)
        mb.add_cascade(label=self.t('m_help'), menu=m)

        self.root.config(menu=mb)

    def _bind_keys(self):
        r = self.root
        r.bind('<Control-o>', lambda e: self._send_file())
        r.bind('<Control-r>', lambda e: self._toggle())
        r.bind('<Control-d>', lambda e: self._demo())
        r.bind('<Control-l>', lambda e: self._clear_log())
        r.bind('<Control-q>', lambda e: self._close())
        r.bind('<F1>', lambda e: self.nb.select(6))

    def _set_lang(self, lang):
        if lang == self.lang:
            return
        self.lang = lang
        self._save()
        self._wf_img = None
        for w in self.root.winfo_children():
            with contextlib.suppress(Exception):
                w.destroy()
        self.root.config(menu=tk.Menu(self.root))
        self._build_all()
        self._apply_modem(silent=True)
        self._say(self.t('ready'), 'ok')

    # =============================================================== ΔΟΜΗ

    def _build_all(self):
        self._menu()
        self._header()
        self._statusbar()
        self._tabs()
        if not self.v_quick.get():
            self.quick_wrap.pack_forget()

    def _header(self):
        h = tk.Frame(self.root, bg=PANEL)
        h.pack(fill='x')
        tk.Frame(h, bg=GREEN, height=2).pack(fill='x')
        inner = tk.Frame(h, bg=PANEL, padx=18, pady=12)
        inner.pack(fill='x')

        logo = tk.Frame(inner, bg=PANEL)
        logo.pack(side='left')
        tk.Label(logo, text='EREVOS', bg=PANEL, fg=GREEN,
                 font=MONO_L).pack(side='left')
        tk.Label(logo, text='RADIO', bg=PANEL, fg=DIM,
                 font=MONO_L).pack(side='left', padx=(8, 0))
        tk.Label(logo, text='v' + VERSION, bg=PANEL, fg=GHOST,
                 font=MONO_XS).pack(side='left', padx=(8, 0), pady=(6, 0))

        self.lamp = tk.Canvas(inner, width=16, height=16, bg=PANEL,
                              highlightthickness=0)
        self.lamp.pack(side='left', padx=(28, 8))
        tk.Label(inner, textvariable=self.v_state, bg=PANEL, fg=DIM,
                 font=MONO_B).pack(side='left')

        tk.Label(inner, text=self.t('audio_live'), bg=PANEL, fg=GHOST,
                 font=MONO_XS).pack(side='left', padx=(28, 6))
        tk.Label(inner, textvariable=self.v_audio, bg=PANEL, fg=MID,
                 font=MONO_XS).pack(side='left')

        self.btn_start = self._btn(inner, self.t('start'), self._toggle,
                                   'solid', width=13)
        self.btn_start.pack(side='right')
        self._entry(inner, self.v_call, 10, MONO_B, 'center').pack(
            side='right', padx=(0, 14), ipady=7)
        tk.Label(inner, text=self.t('call'), bg=PANEL, fg=GHOST,
                 font=MONO_XS).pack(side='right', padx=(0, 8))
        tk.Frame(h, bg=EDGE, height=1).pack(fill='x')
        self._set_lamp(GHOST)

    def _set_lamp(self, color, ring=False):
        self.lamp.delete('all')
        if ring:
            self.lamp.create_oval(1, 1, 15, 15, outline=color, width=1)
        self.lamp.create_oval(4, 4, 12, 12, fill=color, width=0)

    def _statusbar(self):
        bar = tk.Frame(self.root, bg=PANEL)
        bar.pack(fill='x', side='bottom')
        tk.Frame(bar, bg=EDGE, height=1).pack(fill='x')
        f = tk.Frame(bar, bg=PANEL, padx=16, pady=9)
        f.pack(fill='x')
        tk.Label(f, textvariable=self.v_status, bg=PANEL, fg=MID,
                 font=MONO_S).pack(side='left')
        self.prog = ttk.Progressbar(f, length=230, mode='determinate')
        self.prog.pack(side='right')
        tk.Label(f, textvariable=self.v_stats, bg=PANEL, fg=GHOST,
                 font=MONO_XS).pack(side='right', padx=(0, 18))

    def _tabs(self):
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill='both', expand=True, padx=14, pady=(12, 8))

        self.tab_main = tk.Frame(self.nb, bg=BG)
        h_conn, self.tab_conn = self._scroll_tab()
        h_modem, self.tab_modem = self._scroll_tab()
        h_net, self.tab_net = self._scroll_tab()
        self.tab_spec = tk.Frame(self.nb, bg=BG)
        self.tab_diag = tk.Frame(self.nb, bg=BG)
        h_help, self.tab_help = self._scroll_tab()

        for w, k in ((self.tab_main, 't_main'), (h_conn, 't_conn'),
                     (h_modem, 't_modem'), (h_net, 't_net'),
                     (self.tab_spec, 't_spec'), (self.tab_diag, 't_diag'),
                     (h_help, 't_help')):
            self.nb.add(w, text=self.t(k))

        self._build_main()
        self._build_conn()
        self._build_modem()
        self._build_net()
        self._build_spec()
        self._build_diag()
        self._build_help()

    def _scroll_tab(self):
        holder = tk.Frame(self.nb, bg=BG)
        canvas = tk.Canvas(holder, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(holder, orient='vertical', command=canvas.yview)
        inner = tk.Frame(canvas, bg=BG, padx=18, pady=16)
        inner.bind('<Configure>',
                   lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        win = canvas.create_window((0, 0), window=inner, anchor='nw')
        canvas.bind('<Configure>',
                    lambda e: canvas.itemconfig(win, width=e.width))
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')

        # X11 στέλνει Button-4/5, τα Windows και ο macOS στέλνουν
        # MouseWheel με πρόσημο στο delta. Δένουμε και τα δύο.
        def on_enter(_):
            canvas.bind_all('<Button-4>',
                            lambda e: canvas.yview_scroll(-3, 'units'))
            canvas.bind_all('<Button-5>',
                            lambda e: canvas.yview_scroll(3, 'units'))
            canvas.bind_all('<MouseWheel>',
                            lambda e: canvas.yview_scroll(
                                -3 if e.delta > 0 else 3, 'units'))

        def on_leave(_):
            for seq in ('<Button-4>', '<Button-5>', '<MouseWheel>'):
                with contextlib.suppress(Exception):
                    canvas.unbind_all(seq)

        canvas.bind('<Enter>', on_enter)
        canvas.bind('<Leave>', on_leave)
        return holder, inner

    # ------------------------------------------------------------- ΚΕΝΤΡΟ

    def _build_main(self):
        f = self.tab_main

        # --- Η ΓΡΑΜΜΗ ΑΠΟΣΤΟΛΗΣ ΚΑΡΦΩΝΕΤΑΙ ΚΑΤΩ ΠΡΩΤΗ ---
        tk.Label(f, text=self.t('cq_hint'), bg=BG, fg=GHOST, font=MONO_XS,
                 anchor='w').pack(side='bottom', fill='x', pady=(6, 2))

        cwrap = tk.Frame(f, bg=EDGE, padx=1, pady=1)
        cwrap.pack(side='bottom', fill='x', pady=(10, 0))
        c = tk.Frame(cwrap, bg=PANEL, padx=10, pady=10)
        c.pack(fill='x')

        tk.Label(c, text=self.t('to'), bg=PANEL, fg=GHOST,
                 font=MONO_XS).pack(side='left', padx=(0, 6))
        self._entry(c, self.v_dst, 10, MONO_S, 'center').pack(side='left',
                                                              ipady=7)
        self.entry_msg = self._entry(c, self.v_msg, 20, MONO)
        self.entry_msg.pack(side='left', fill='x', expand=True, padx=10,
                            ipady=8)
        self.entry_msg.bind('<Return>', lambda _: self._send_text())
        self._btn(c, self.t('b_file'), self._send_file).pack(side='left',
                                                             padx=(0, 8))
        self._btn(c, self.t('b_send'), self._send_text,
                  'solid').pack(side='left')

        # --- γρήγορη εκκίνηση ---
        self.quick_wrap = tk.Frame(f, bg=EDGE, padx=1, pady=1)
        self.quick_wrap.pack(fill='x', pady=(8, 10))
        q = tk.Frame(self.quick_wrap, bg=CARD, padx=16, pady=12)
        q.pack(fill='x')
        tk.Label(q, text=self.t('qs_title'), bg=CARD, fg=GLOW,
                 font=MONO_B).pack(anchor='w')
        tk.Label(q, text=self.t('qs_body'), bg=CARD, fg=MID, font=MONO_S,
                 justify='left').pack(anchor='w', pady=(6, 0))
        r = tk.Frame(q, bg=CARD)
        r.pack(anchor='w', pady=(10, 0))
        self._btn(r, self.t('qs_demo'), self._demo).pack(side='left')
        self._btn(r, self.t('qs_hide'),
                  lambda: (self.v_quick.set(False),
                           self.quick_wrap.pack_forget())).pack(side='left',
                                                                padx=8)

        # --- σώμα ---
        body = tk.Frame(f, bg=BG)
        body.pack(fill='both', expand=True)

        left = tk.Frame(body, bg=EDGE, padx=1, pady=1)
        left.pack(side='left', fill='both', expand=True)
        lin = tk.Frame(left, bg=PANEL)
        lin.pack(fill='both', expand=True)
        self.log = tk.Text(lin, bg=PANEL, fg=GREEN, bd=0, wrap='word',
                           font=MONO, padx=14, pady=12, state='disabled',
                           insertbackground=GREEN, spacing1=1, spacing3=4,
                           selectbackground=DIM, height=8)
        self.log.pack(side='left', fill='both', expand=True)
        sb = ttk.Scrollbar(lin, command=self.log.yview)
        sb.pack(side='right', fill='y')
        self.log.configure(yscrollcommand=sb.set)
        for tag, col in (('sys', DIM), ('me', CYAN), ('them', GLOW),
                         ('warn', AMBER), ('err', RED), ('time', GHOST),
                         ('ok', GREEN)):
            self.log.tag_configure(tag, foreground=col)
        self._editable(self.log, readonly=True)

        right = tk.Frame(body, bg=BG, width=228)
        right.pack(side='right', fill='y', padx=(14, 0))
        right.pack_propagate(False)

        tk.Label(right, text=self.t('stations'), bg=BG, fg=GHOST,
                 font=MONO_XS, anchor='w').pack(fill='x')
        pf = tk.Frame(right, bg=EDGE, padx=1, pady=1)
        pf.pack(fill='both', expand=True, pady=(4, 12))
        self.peers = tk.Listbox(pf, bg=PANEL, fg=GREEN, bd=0,
                                highlightthickness=0, selectbackground=DIM,
                                selectforeground=GLOW, font=MONO_S,
                                activestyle='none')
        self.peers.pack(fill='both', expand=True)
        self.peers.bind('<<ListboxSelect>>', self._pick_peer)

        tk.Label(right, text=self.t('rxlevel'), bg=BG, fg=GHOST,
                 font=MONO_XS, anchor='w').pack(fill='x')
        self.meter = tk.Canvas(right, height=18, bg=PANEL,
                               highlightthickness=1, highlightbackground=EDGE)
        self.meter.pack(fill='x', pady=(4, 12))

        tk.Label(right, text=self.t('linkinfo'), bg=BG, fg=GHOST,
                 font=MONO_XS, anchor='w').pack(fill='x')
        tk.Label(right, textvariable=self.v_linkinfo, bg=BG, fg=MID,
                 font=MONO_XS, justify='left', anchor='w').pack(fill='x',
                                                                pady=(4, 0))

    def _toggle_quick(self):
        if self.v_quick.get():
            self.quick_wrap.pack(fill='x', pady=(8, 10), before=None)
            self.quick_wrap.pack_configure(after=None)
        else:
            self.quick_wrap.pack_forget()

    # ------------------------------------------------------------ ΣΥΝΔΕΣΗ

    def _build_conn(self):
        f = self.tab_conn
        c = self._card(f, self.t('c_mode'))
        for val, tk_, dk in (('acoustic', 'mode_ac', 'mode_ac_d'),
                             ('cable', 'mode_cb', 'mode_cb_d')):
            row = tk.Frame(c, bg=CARD)
            row.pack(fill='x', pady=(6, 2))
            tk.Radiobutton(row, text=self.t(tk_), variable=self.v_mode,
                           value=val, bg=CARD, fg=GREEN, selectcolor=VOID,
                           font=MONO_S, activebackground=CARD,
                           activeforeground=GLOW, highlightthickness=0, bd=0,
                           command=self._mode_changed).pack(anchor='w')
            tk.Label(row, text=self.t(dk), bg=CARD, fg=GHOST, font=MONO_XS,
                     wraplength=740, justify='left').pack(anchor='w',
                                                          padx=(26, 0))

        c = self._card(f, self.t('c_dev'))
        self._field(c, self.t('f_in'),
                    lambda p: ttk.Combobox(p, textvariable=self.v_in,
                                           values=self.dev_in, width=40,
                                           state='readonly', font=MONO_S))
        self._field(c, self.t('f_out'),
                    lambda p: ttk.Combobox(p, textvariable=self.v_out,
                                           values=self.dev_out, width=40,
                                           state='readonly', font=MONO_S))
        self._hint(c, self.t('dev_hint'), (8, 0))
        b = tk.Frame(c, bg=CARD)
        b.pack(anchor='w', pady=(12, 0))
        self._btn(b, self.t('b_testout'), self._test_out).pack(side='left')
        self._btn(b, self.t('b_testin'), self._test_in).pack(side='left',
                                                             padx=8)
        self._btn(b, self.t('b_testloop'), self._test_loop,
                  'amber').pack(side='left')
        self._btn(b, self.t('b_calib'), self._calibrate).pack(side='left',
                                                              padx=8)
        self._btn(b, self.t('b_auto'), self._auto_config,
                  'solid').pack(side='left')

        c = self._card(f, self.t('c_level'))
        self._field(c, self.t('f_amp'),
                    lambda p: self._scale(p, self.v_amp, 0.05, 1.0, 0.05, 260))
        self._hint(c, self.t('amp_hint'))

        c = self._card(f, self.t('c_ptt'))
        self._field(c, self.t('f_pttm'),
                    lambda p: ttk.Combobox(p, textvariable=self.v_ptt,
                                           values=['vox', 'serial'], width=14,
                                           state='readonly', font=MONO_S),
                    self.t('f_pttm_d'))
        self._field(c, self.t('f_pttp'),
                    lambda p: ttk.Combobox(p, textvariable=self.v_pttport,
                                           values=self.ports, width=22,
                                           font=MONO_S), self.t('f_pttp_d'))
        self._field(c, self.t('f_pttl'),
                    lambda p: ttk.Combobox(p, textvariable=self.v_pttline,
                                           values=['RTS', 'DTR'], width=8,
                                           state='readonly', font=MONO_S),
                    self.t('f_pttl_d'))
        self._field(c, self.t('f_vox'),
                    lambda p: self._check(p, self.v_vox), self.t('f_vox_d'))
        if not pttmod.HAVE_SERIAL:
            self._hint(c, 'pip3 install pyserial --break-system-packages',
                       (10, 0))

    def _mode_changed(self):
        if self.v_mode.get() == 'cable':
            self.v_vox.set(False)
        else:
            self.v_vox.set(True)
            self.v_ptt.set('vox')

    # ------------------------------------------------------------- ΜΟΝΤΕΜ

    def _build_modem(self):
        f = self.tab_modem
        w = tk.Frame(f, bg=CARD, highlightthickness=1,
                     highlightbackground=AMBER)
        w.pack(fill='x', pady=(0, 14))
        tk.Label(w, text=self.t('warn_same'), bg=CARD, fg=AMBER, font=MONO_S,
                 padx=14, pady=10, wraplength=820,
                 justify='left').pack(anchor='w')

        c = self._card(f, self.t('c_mod'))
        self._field(c, self.t('f_baud'),
                    lambda p: ttk.Combobox(p, textvariable=self.m_baud,
                                           values=[300, 600, 1200, 2400],
                                           width=10, state='readonly',
                                           font=MONO_S), self.t('f_baud_d'))
        self._field(c, self.t('f_mark'),
                    lambda p: self._spin(p, self.m_mark, 300, 3000, 50))
        self._field(c, self.t('f_space'),
                    lambda p: self._spin(p, self.m_space, 300, 3000, 50),
                    self.t('f_tone_d'))
        self._field(c, self.t('f_fec'),
                    lambda p: self._check(p, self.m_fec), self.t('f_fec_d'))
        self._field(c, self.t('f_pre'),
                    lambda p: self._spin(p, self.m_pre, 8, 128, 8),
                    self.t('f_pre_d'))

        c = self._card(f, self.t('c_demod'))
        self._field(c, self.t('f_agc'),
                    lambda p: self._spin(p, self.m_agc, 8, 200, 8),
                    self.t('f_agc_d'))
        self._field(c, self.t('f_sync'),
                    lambda p: self._spin(p, self.m_sync, 0, 6),
                    self.t('f_sync_d'))

        tk.Label(f, textvariable=self.v_modeminfo, bg=BG, fg=CYAN,
                 font=MONO_S, justify='left', anchor='w').pack(fill='x',
                                                               pady=(0, 10))
        b = tk.Frame(f, bg=BG)
        b.pack(anchor='w', pady=(0, 18))
        self._btn(b, self.t('b_apply'), self._apply_modem,
                  'solid').pack(side='left')
        self._btn(b, self.t('b_defaults'),
                  self._reset_modem).pack(side='left', padx=8)

    def _apply_modem(self, silent=False):
        try:
            if int(self.m_mark.get()) == int(self.m_space.get()):
                raise ValueError('mark == space')
            modem.configure(BAUD=int(self.m_baud.get()),
                            F_MARK=int(self.m_mark.get()),
                            F_SPACE=int(self.m_space.get()),
                            USE_FEC=bool(self.m_fec.get()),
                            PREAMBLE_BYTES=int(self.m_pre.get()),
                            AGC_WIN_BITS=int(self.m_agc.get()),
                            SYNC_MAX_ERR=int(self.m_sync.get()))
        except Exception as e:                          # noqa: BLE001
            messagebox.showerror('EREVOS', str(e))
            return
        bps = modem.throughput_bps()
        # Το Bell 202 (1200/2200 στα 1200 baud) έχει απόσταση 1000 και
        # είναι το πρότυπο - δεν προειδοποιούμε γι' αυτό.
        gap = abs(int(self.m_space.get()) - int(self.m_mark.get()))
        if gap < int(self.m_baud.get()) * 0.8:
            self._say(self.t('tone_gap'), 'warn')
        self.v_modeminfo.set(
            f'  {bps:>4.0f} bps  ·  200 bytes → {modem.frame_seconds(200):.1f} s'
            f'  ·  {modem.SPS} samples/bit  ·  10 KB → '
            f'{10240*8/max(1,bps)/60:.1f} min')
        if not silent:
            self._say(self.t('warn_same'), 'warn')

    def _reset_modem(self):
        d = modem.DEFAULTS
        self.m_baud.set(d['BAUD']); self.m_mark.set(d['F_MARK'])
        self.m_space.set(d['F_SPACE']); self.m_fec.set(d['USE_FEC'])
        self.m_pre.set(d['PREAMBLE_BYTES']); self.m_agc.set(d['AGC_WIN_BITS'])
        self.m_sync.set(d['SYNC_MAX_ERR'])
        self._apply_modem()

    # ------------------------------------------------------------- ΔΙΚΤΥΟ

    def _build_net(self):
        f = self.tab_net
        c = self._card(f, self.t('c_rel'), self.t('c_rel_s'))
        self._field(c, self.t('f_ack'),
                    lambda p: self._spin(p, self.n_ack, 2, 40, 1),
                    self.t('f_ack_d'))
        self._field(c, self.t('f_retry'),
                    lambda p: self._spin(p, self.n_retry, 1, 10),
                    self.t('f_retry_d'))
        self._field(c, self.t('f_ackrep'),
                    lambda p: self._spin(p, self.n_ackrep, 1, 3),
                    self.t('f_ackrep_d'))
        self._field(c, self.t('f_chunk'),
                    lambda p: self._spin(p, self.n_chunk, 32, 200, 8),
                    self.t('f_chunk_d'))

        c = self._card(f, self.t('c_csma'), self.t('c_csma_s'))
        self._field(c, self.t('f_busy'),
                    lambda p: self._scale(p, self.n_busy, 0.0, 0.2, 0.005,
                                          230), self.t('f_busy_d'))
        self._hint(c, self.t('busy_hint'))

        c = self._card(f, self.t('c_files'))
        self._field(c, self.t('f_inbox'),
                    lambda p: self._entry(p, self.v_inbox, 44))
        self._btn(c, self.t('b_openinbox'),
                  self._open_inbox).pack(anchor='w', pady=(10, 0))

        b = tk.Frame(f, bg=BG)
        b.pack(anchor='w', pady=(4, 18))
        self._btn(b, self.t('b_apply'), self._apply_link,
                  'solid').pack(side='left')
        self._btn(b, self.t('b_defaults'),
                  self._reset_link).pack(side='left', padx=8)

    def _apply_link(self, silent=False):
        with contextlib.suppress(Exception):
            link.configure(ACK_TIMEOUT=float(self.n_ack.get()),
                           MAX_RETRIES=int(self.n_retry.get()),
                           CHUNK_SIZE=int(self.n_chunk.get()),
                           ACK_REPEAT=int(self.n_ackrep.get()),
                           BUSY_RMS=float(self.n_busy.get()))
            link.INBOX_DIR = os.path.expanduser(self.v_inbox.get())

    def _reset_link(self):
        d = link.DEFAULTS
        self.n_ack.set(d['ACK_TIMEOUT']); self.n_retry.set(d['MAX_RETRIES'])
        self.n_chunk.set(d['CHUNK_SIZE']); self.n_busy.set(d['BUSY_RMS'])
        self.n_ackrep.set(d['ACK_REPEAT'])
        self._apply_link()

    def _open_inbox(self):
        p = os.path.expanduser(self.v_inbox.get())
        os.makedirs(p, exist_ok=True)
        # Χωρίς shell: η διαδρομή περνάει ως όρισμα, οπότε ένα όνομα
        # φακέλου με εισαγωγικά ή ; δεν μπορεί να εκτελέσει εντολές.
        with contextlib.suppress(Exception):
            if os.name == 'nt':
                os.startfile(p)                          # noqa: S606
            else:
                opener = 'open' if sys.platform == 'darwin' else 'xdg-open'
                subprocess.Popen([opener, p], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)

    # -------------------------------------------------------------- ΦΑΣΜΑ

    def _build_spec(self):
        f = self.tab_spec
        top = tk.Frame(f, bg=BG)
        top.pack(fill='x', pady=(10, 6))
        tk.Label(top, text=self.t('wf_title'), bg=BG, fg=GHOST,
                 font=MONO_XS).pack(side='left')
        tk.Checkbutton(top, text=self.t('wf_live'), variable=self.v_wfon,
                       bg=BG, fg=DIM, selectcolor=VOID, activebackground=BG,
                       activeforeground=GREEN, font=MONO_XS,
                       highlightthickness=0, bd=0).pack(side='right')

        wrap = tk.Frame(f, bg=EDGE, padx=1, pady=1)
        wrap.pack(fill='both', expand=True)
        self.wf = tk.Canvas(wrap, bg=VOID, highlightthickness=0)
        self.wf.pack(fill='both', expand=True)

        tk.Label(f, text=self.t('sp_title'), bg=BG, fg=GHOST, font=MONO_XS,
                 anchor='w').pack(fill='x', pady=(10, 4))
        w2 = tk.Frame(f, bg=EDGE, padx=1, pady=1)
        w2.pack(fill='x')
        self.sp = tk.Canvas(w2, bg=PANEL, highlightthickness=0, height=110)
        self.sp.pack(fill='x')
        tk.Label(f, textvariable=self.v_tone, bg=BG, fg=MID, font=MONO_S,
                 anchor='w').pack(fill='x', pady=(8, 10))

    # -------------------------------------------------------- ΔΙΑΓΝΩΣΤΙΚΑ

    def _build_diag(self):
        f = self.tab_diag
        b = tk.Frame(f, bg=BG)
        b.pack(fill='x', pady=(10, 8))
        self._btn(b, self.t('b_demo'), self._demo, 'solid').pack(side='left')
        self._btn(b, self.t('b_dmodem'),
                  self._diag_modem).pack(side='left', padx=8)
        self._btn(b, self.t('b_dfec'), self._diag_fec).pack(side='left')
        self._btn(b, self.t('b_clear'),
                  self._diag_clear).pack(side='right')

        wrap = tk.Frame(f, bg=EDGE, padx=1, pady=1)
        wrap.pack(fill='both', expand=True, pady=(0, 10))
        self.diag = tk.Text(wrap, bg=VOID, fg=GREEN, bd=0, wrap='none',
                            font=MONO_S, padx=14, pady=12, state='disabled',
                            selectbackground=DIM)
        self.diag.pack(side='left', fill='both', expand=True)
        sb = ttk.Scrollbar(wrap, command=self.diag.yview)
        sb.pack(side='right', fill='y')
        self.diag.configure(yscrollcommand=sb.set)
        self.diag.tag_configure('hd', foreground=GLOW)
        self.diag.tag_configure('bad', foreground=RED)
        self._editable(self.diag, readonly=True)

    def _diag_clear(self):
        self.diag.configure(state='normal')
        self.diag.delete('1.0', 'end')
        self.diag.configure(state='disabled')

    def _diag_out(self, text):
        self.diag.configure(state='normal')
        for line in text.splitlines(True):
            tag = ('hd' if line.strip().startswith('=') else
                   'bad' if 'FAIL' in line or 'ΣΦΑΛΜΑ' in line else None)
            self.diag.insert('end', line, tag)
        self.diag.see('end')
        self.diag.configure(state='disabled')

    def _diag_run(self, fn, title):
        self._diag_out(f'\n=== {title} ===\n')

        def run():
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    fn()
            except Exception as e:                      # noqa: BLE001
                buf.write(f'\nΣΦΑΛΜΑ: {e}\n')
            self.events.put(('diag', {'text': buf.getvalue()}))
        threading.Thread(target=run, daemon=True).start()

    def _diag_modem(self):
        self._apply_modem(silent=True)
        self._diag_run(modem._selftest, 'MODEM')

    def _diag_fec(self):
        self._diag_run(fec._selftest, 'FEC')

    def _demo(self):
        self.nb.select(5)
        self._apply_modem(silent=True)
        el = self.lang == 'el'

        def test():
            print('Ολόκληρη η αλυσίδα, χωρίς κάρτα ήχου και χωρίς radio.\n'
                  if el else
                  'The whole chain, with no sound card and no radio.\n')
            cases = ([('σύντομο μήνυμα', 'Ολα καλα εδω. QRV.'),
                      ('με σύμβολα', 'Test 123 !@#$% ABCabc'),
                      ('γεμάτο πλαίσιο', 'E' * 190)] if el else
                     [('short message', 'All good here. QRV.'),
                      ('with symbols', 'Test 123 !@#$% ABCabc'),
                      ('full frame', 'E' * 190)])
            ok = 0
            for name, txt in cases:
                bits = modem.build_frame(modem.TYPE_TEXT, 'EREVOS1',
                                         'EREVOS2', txt.encode())
                sig = modem.modulate(bits).astype(np.float64)
                nz = np.random.normal(0, 1, len(sig))
                nz *= np.sqrt(np.mean(sig ** 2) / 10 ** 0.6)
                pad = np.zeros(modem.SAMPLE_RATE // 4)
                got = modem.receive(np.concatenate([pad, sig + nz, pad]))
                good = bool(got) and got[0]['payload'].decode(
                    errors='replace') == txt
                ok += good
                print(f'  [{"OK  " if good else "FAIL"}]  {name:<18} '
                      f'{len(txt):>3} ch  '
                      f'{len(sig)/modem.SAMPLE_RATE:>5.2f} s on air')
            print(f'\n  {ok}/{len(cases)} '
                  + ('πέρασαν με θόρυβο 6 dB κάτω από το σήμα\n' if el else
                     'passed with noise 6 dB below the signal\n'))
            print('  ΤΕΧΝΙΚΑ ΣΤΟΙΧΕΙΑ' if el else '  TECHNICAL DATA')
            print(f'    modulation   AFSK {modem.BAUD} baud '
                  f'({modem.F_MARK}/{modem.F_SPACE} Hz)')
            print(f'    coding       convolutional 1/2, K={fec.K}, '
                  f'soft Viterbi')
            print('    integrity    CRC-16 per frame')
            print(f'    throughput   {modem.throughput_bps():.0f} bps')
            print('    dependencies numpy, sounddevice — nothing else')

        self._diag_run(test, 'DEMO')

    # -------------------------------------------------------------- ΟΔΗΓΟΣ

    def _build_help(self):
        for title, text in HELP[self.lang]:
            c = self._card(self.tab_help, title)
            tk.Label(c, text=text, bg=CARD, fg=MID, font=MONO_S,
                     justify='left', anchor='w',
                     wraplength=800).pack(anchor='w')

    # ============================================================ ΕΝΕΡΓΕΙΕΣ

    def _dev(self, var):
        v = var.get()
        return None if v.startswith('(') else int(v.split(':')[0])

    def _toggle(self):
        self._stop() if self.station else self._start()

    def _start(self):
        call = self.v_call.get().strip().upper()
        if not call:
            messagebox.showwarning('EREVOS', self.t('nocall'))
            return
        self._apply_modem(silent=True)
        self._apply_link(silent=True)
        try:
            self.ptt = pttmod.make(
                self.v_ptt.get(),
                port=self.v_pttport.get() if self.v_pttport.get() != '—'
                else '', line=self.v_pttline.get())
        except Exception as e:                          # noqa: BLE001
            messagebox.showerror('EREVOS', f'PTT:\n{e}')
            return
        try:
            self.station = link.Station(
                call, in_dev=self._dev(self.v_in),
                out_dev=self._dev(self.v_out), amplitude=self.v_amp.get(),
                vox=self.v_vox.get(), ptt=self.ptt,
                prefer_duplex=(self.v_engine.get() == 'duplex'),
                on_event=lambda kind, **kw: self.events.put((kind, kw)))
            self.station.start()
        except Exception as e:                          # noqa: BLE001
            txt = str(e).lower()
            if any(k in txt for k in ('unavailable', '-9985', '-9996',
                                      'busy', 'in use')):
                messagebox.showerror('EREVOS', self.t('busy_dev'))
            else:
                messagebox.showerror(
                    'EREVOS', f'{self.t("fail_audio")}:\n{e}\n\n'
                              f'{self.t("try_other")}')
            self.station = None
            return

        self.btn_start.configure(text=self.t('stop'), bg=CARD, fg=RED,
                                 activebackground=CARD, highlightbackground=RED)
        self.v_state.set(f'{call}  ·  {self.t("listen")}')
        self._set_lamp(GREEN, ring=True)
        self._say(f'{call}  ·  PTT: {self.ptt.name}', 'ok')
        self._say(self.t('duplex_on') if self.station.duplex
                  else self.t('duplex_off'),
                  'ok' if self.station.duplex else 'warn')
        if (self._dev(self.v_in) is not None and
                self._dev(self.v_in) == self._dev(self.v_out)):
            self._say(self.t('same_dev'), 'warn')
        if self.station.rx.fallback:
            self._say(self.t('fallback'), 'warn')
            self._say(f'  → {audio.device_name(self.station.rx.in_actual)}',
                      'warn')
        self._say(f'inbox: {link.INBOX_DIR}', 'sys')
        self._save()

    def _stop(self):
        if self.station:
            with contextlib.suppress(Exception):
                self.station.stop()
            self.station = None
        self.ptt = None
        self.btn_start.configure(text=self.t('start'), bg=GREEN, fg=VOID,
                                 activebackground=GREEN,
                                 highlightbackground=GREEN)
        self.v_state.set(self.t('idle'))
        self._set_lamp(GHOST)
        self._say(self.t('stopped'), 'sys')

    def _guard(self):
        if not self.station:
            messagebox.showinfo('EREVOS', self.t('need_start'))
            return False
        if self.busy:
            messagebox.showinfo('EREVOS', self.t('tx_running'))
            return False
        return True

    def _work(self, fn):
        def run():
            self.busy = True
            try:
                ok = fn()
            except Exception as e:                      # noqa: BLE001
                self.events.put(('error', {'message': str(e)}))
                ok = False
            self.busy = False
            self.events.put(('done', {'ok': ok}))
        threading.Thread(target=run, daemon=True).start()

    def _send_text(self):
        if not self._guard():
            return
        text = self.v_msg.get().strip()
        if not text:
            return
        dst = self.v_dst.get().strip().upper() or 'CQ'
        self.v_msg.set('')
        self._say(f'► {dst:<9} {text}', 'me')
        self.prog.configure(mode='indeterminate')
        self.prog.start(45)
        self._work(lambda: self.station.send_text(dst, text))

    def _send_file(self):
        if not self._guard():
            return
        path = filedialog.askopenfilename()
        if not path:
            return
        dst = self.v_dst.get().strip().upper() or 'CQ'
        if dst == 'CQ':
            messagebox.showinfo('EREVOS', self.t('need_dst'))
            return
        size = os.path.getsize(path)
        ratio = 1.0
        with contextlib.suppress(Exception):
            import zlib
            with open(path, 'rb') as fh:
                sample = fh.read(65536)
            if len(sample) >= 64:
                ratio = min(1.0, len(zlib.compress(sample, 9)) / len(sample))
        secs = self.station.estimate_seconds(size, ratio)
        if not messagebox.askyesno(
                'EREVOS', f'{os.path.basename(path)}\n{size} bytes\n\n'
                          f'{self.t("est")}: {secs/60:.1f} '
                          f'{self.t("minutes")}\n'
                          f'{modem.throughput_bps():.0f} bps\n\n'
                          f'{self.t("proceed")}'):
            return
        self.cancel_flag = False
        self._say(f'► {dst:<9} file "{os.path.basename(path)}" '
                  f'({size} bytes)', 'me')
        self.prog.configure(mode='determinate', value=0)
        self._work(lambda: self.station.send_file(
            dst, path, cancel=lambda: self.cancel_flag))

    def _pick_peer(self, _):
        sel = self.peers.curselection()
        if sel:
            self.v_dst.set(self.peers.get(sel[0]).split()[0])

    # -- δοκιμές ------------------------------------------------------------

    def _test_out(self):
        self._say('tone 2 s →', 'sys')

        def run():
            with contextlib.suppress(Exception):
                audio.play(audio.tone(2.0, self.v_amp.get()),
                           device=self._dev(self.v_out))
        threading.Thread(target=run, daemon=True).start()

    def _test_in(self):
        if self.station:
            messagebox.showinfo('EREVOS', self.t('tx_running'))
            return
        self._say('mic 3 s …', 'sys')

        def run():
            try:
                rms, peak, clip = audio.measure_input(
                    3.0, device=self._dev(self.v_in))
                v = ('too low' if peak < 0.05 else
                     'clipping' if clip > 0.5 else 'OK')
                self.events.put(('info', {'text':
                    f'RMS {rms:.3f}   peak {peak:.3f}   '
                    f'clip {clip:.2f}%   →  {v}'}))
            except Exception as e:                      # noqa: BLE001
                self.events.put(('error', {'message': str(e)}))
        threading.Thread(target=run, daemon=True).start()

    def _test_loop(self):
        if self.station:
            messagebox.showinfo('EREVOS', self.t('tx_running'))
            return
        self._say('loopback …', 'sys')
        self._apply_modem(silent=True)

        def run():
            try:
                bits = modem.build_frame(modem.TYPE_TEXT, 'LOOP', 'CQ',
                                         b'EREVOS loopback')
                din, dout = self._dev(self.v_in), self._dev(self.v_out)
                if din is not None and din == dout:
                    dout = audio.shared_device('out')
                eng = audio.AudioEngine(din, dout)
                eng.start()
                time.sleep(0.4)
                eng.transmit(bits, amplitude=self.v_amp.get(), vox=False)
                end, got = time.time() + 7, []
                while time.time() < end and not got:
                    got.extend(eng.poll(timeout=0.5))
                duplex = eng.duplex
                eng.stop()
                self.events.put(('info', {'text':
                    f'✔ loopback OK  ({"duplex" if duplex else "split"})'
                    if got else '✘ loopback FAILED — check levels/devices'}))
            except Exception as e:                      # noqa: BLE001
                self.events.put(('error', {'message': str(e)}))
        threading.Thread(target=run, daemon=True).start()

    def _auto_config(self):
        """Ρυθμίσεις που δουλεύουν παντού, χωρίς αποκλειστικές συσκευές."""
        self.v_in.set(self.dev_in[0])
        self.v_out.set(self.dev_out[0])
        self.v_amp.set(0.60)
        self.v_mode.set('acoustic')
        self.v_vox.set(True)
        self.v_ptt.set('vox')
        self._reset_modem()
        self._reset_link()
        self._save()
        self._say(self.t('auto_done'), 'ok')
        self.nb.select(0)

    def _calibrate(self):
        if not self.station:
            messagebox.showinfo('EREVOS', self.t('need_start'))
            return

        def run():
            nf = self.station.rx.calibrate(2.0)
            th = self.station.rx.busy_threshold(floor=link.BUSY_RMS * 0.3)
            self.events.put(('calibrated', {'noise': nf, 'threshold': th}))
        threading.Thread(target=run, daemon=True).start()

    def _startup_check(self):
        try:
            ni, no = len(audio.device_list('in')), len(audio.device_list('out'))
            self._say(f'audio: {ni} in / {no} out', 'sys')
            if not ni or not no:
                self._say('no audio device found', 'err')
        except Exception as e:                          # noqa: BLE001
            self._say(str(e), 'err')

    # -- εργαλεία -----------------------------------------------------------

    def _clear_log(self):
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')

    def _save_log(self):
        p = filedialog.asksaveasfilename(defaultextension='.txt',
                                         initialfile='erevos_log.txt')
        if not p:
            return
        with contextlib.suppress(Exception):
            with open(p, 'w') as fh:
                fh.write(self.log.get('1.0', 'end'))
            self._say(f'saved: {p}', 'ok')

    def _export_cfg(self):
        p = filedialog.asksaveasfilename(defaultextension='.json',
                                         initialfile='erevos-settings.json')
        if not p:
            return
        self._save()
        with contextlib.suppress(Exception):
            with open(CONFIG_PATH) as a, open(p, 'w') as b:
                b.write(a.read())
            self._say(f'exported: {p}', 'ok')

    def _import_cfg(self):
        p = filedialog.askopenfilename(filetypes=[('JSON', '*.json')])
        if not p:
            return
        with contextlib.suppress(Exception):
            with open(p) as fh:
                self.cfg = json.load(fh)
            with open(CONFIG_PATH, 'w') as fh:
                json.dump(self.cfg, fh, indent=2, ensure_ascii=False)
            messagebox.showinfo('EREVOS', 'OK — restart the application')

    def _show_keys(self):
        messagebox.showinfo(self.t('mi_keys'), self.t('keys_body'))

    def _show_about(self):
        messagebox.showinfo(
            self.t('mi_about'),
            f'EREVOS RADIO  v{VERSION}\n\n'
            f'AFSK {modem.BAUD} baud ({modem.F_MARK}/{modem.F_SPACE} Hz)\n'
            f'convolutional 1/2  K={fec.K}  soft-decision Viterbi\n'
            f'CRC-16 · PRBS scrambler · block interleaver\n'
            f'stop-and-wait ARQ · CSMA\n\n'
            f'{modem.throughput_bps():.0f} bps\n\n'
            'Part of the Erevos project.')

    # ============================================================== ΒΡΟΧΟΙ

    def _say(self, text, tag='sys'):
        self.log.configure(state='normal')
        self.log.insert('end', time.strftime('%H:%M:%S  '), 'time')
        self.log.insert('end', text + '\n', tag)
        self.log.see('end')
        self.log.configure(state='disabled')

    def _pump(self):
        try:
            while True:
                try:
                    kind, kw = self.events.get_nowait()
                except queue.Empty:
                    break
                self._event(kind, kw)
            self._meter()
            self._peers_refresh()
            self._stats_refresh()
        except Exception:                               # noqa: BLE001
            pass
        self.root.after(150, self._pump)

    def _event(self, kind, kw):
        if kind == 'text':
            self._say(f'◄ {kw["src"]:<9} {kw["text"]}', 'them')
        elif kind == 'file_start':
            self._say(f'◄ {kw["src"]:<9} file "{kw["name"]}" '
                      f'({kw["size"]} bytes)', 'them')
            self.prog.configure(mode='determinate',
                                maximum=max(1, kw['total']), value=0)
        elif kind == 'file_progress':
            self.prog.configure(value=kw['have'])
            self.v_status.set(f'{self.t("st_rx")}  {kw["have"]}/{kw["total"]}')
        elif kind == 'file_done':
            self._say(f'◄ {kw["src"]:<9} → {kw["path"]}', 'ok')
            self.v_status.set(self.t('st_listen'))
            self.prog.configure(value=0)
        elif kind == 'sending':
            self.prog.configure(mode='determinate',
                                maximum=max(1, kw['total']), value=kw['seq'])
            self.v_status.set(f'{self.t("st_tx")}  {kw["seq"]}/{kw["total"]}')
        elif kind == 'ack_sent':
            self._say(self.t('ack_sent').format(
                d=kw['dst'], m=kw['msg_id'], s=kw['seq'], n=kw['n']), 'sys')
        elif kind == 'ack_recv':
            self._say(self.t('ack_recv').format(
                s0=kw['src'], m=kw['msg_id'], s=kw['seq']), 'ok')
        elif kind == 'ack_bad':
            self._say(self.t('ack_bad').format(
                g=kw['got'], w=kw['want']), 'err')
        elif kind == 'frame':
            names = {0: 'TEXT', 1: 'FILE-META', 2: 'FILE-CHUNK', 3: 'ACK'}
            self._say(f'  ⟨{names.get(kw["type"], "?")}⟩ '
                      f'{kw["src"]}→{kw["dst"]} seq {kw["seq"]} '
                      f'{kw["size"]}B '
                      f'{"" if kw["mine"] else "(όχι για εμάς)"}', 'sys')
        elif kind == 'calibrated':
            self._say(self.t('calib').format(n=kw['noise'],
                                             t=kw['threshold']), 'ok')
            if kw['noise'] > 0.05:
                self._say(self.t('noisy'), 'warn')
        elif kind == 'waiting':
            self.v_status.set(self.t('st_busy'))
        elif kind == 'tx_start':
            self.tx_active = True
        elif kind == 'tx_end':
            self.tx_active = False
        elif kind == 'retry':
            self._say(f'  retry {kw["attempt"]} '
                      f'({kw["seq"]+1}/{kw["total"]})', 'warn')
        elif kind == 'cancelled':
            self._say('cancelled', 'warn')
        elif kind == 'done':
            self.prog.stop()
            self.prog.configure(mode='determinate', value=0)
            self.v_status.set(self.t('st_listen') if self.station
                              else self.t('st_ready'))
            if not kw.get('ok'):
                self._say(self.t('no_ack'), 'warn')
        elif kind == 'info':
            self._say(kw['text'], 'ok')
        elif kind == 'diag':
            self._diag_out(kw['text'])
        elif kind == 'warn':
            self._say(kw['message'], 'warn')
        elif kind == 'error':
            self._say(kw['message'], 'err')

    def _meter(self):
        w = max(1, self.meter.winfo_width())
        self.meter.delete('all')
        lvl = self.station.rx.level() if self.station else 0.0
        frac = clamp(lvl * 6, 0, 1)
        segs = 26
        sw = w / segs
        lit = int(frac * segs)
        for i in range(segs):
            col = (GREEN if i < segs * 0.7 else
                   AMBER if i < segs * 0.88 else RED)
            self.meter.create_rectangle(i * sw + 1, 3, (i + 1) * sw - 1, 15,
                                        fill=col if i < lit else GHOST,
                                        width=0)
        if self.station:
            t = self.station.rx.busy_threshold(floor=link.BUSY_RMS * 0.3)
        else:
            t = link.BUSY_RMS
        thr = clamp(t * 6, 0, 1) * w
        self.meter.create_line(thr, 0, thr, 18, fill=GLOW, width=1)

    def _peers_refresh(self):
        if not self.station:
            return
        now = time.time()
        items = [f'{p:<9} {int(now - t):>4}s'
                 for p, t in sorted(self.station.peers.items())]
        if list(self.peers.get(0, 'end')) != items:
            self.peers.delete(0, 'end')
            for it in items:
                self.peers.insert('end', it)

    def _stats_refresh(self):
        if not self.station:
            self.v_stats.set('')
            self.v_linkinfo.set('—')
            self.v_audio.set('—')
            return

        rx = self.station.rx
        self.v_audio.set(rx.health())
        s = self.station.stats
        extra = (f"  ZIP {s['saved_bytes']}B" if s.get('saved_bytes') else '')
        self.v_stats.set(f"TX {s['tx_frames']}  RX {s['rx_frames']}  "
                         f"RTY {s['retries']}  ERR {s['failed']}{extra}")
        st = rx.last_stats or {}
        snr = rx.snr_db()
        mark = '++' if snr > 9 else '+ ' if snr > 5 else '~ ' if snr > 2 else '! '
        self.v_linkinfo.set(f"SNR   {snr:+5.1f} dB {mark}\n"
                            f"noise {rx.noise_floor:.3f}\n"
                            f"bits  {st.get('bits', 0)}\n"
                            f"sync  {st.get('syncs', 0)}\n"
                            f"qual  {st.get('confidence', 0):.2f}\n"
                            f"rate  {modem.throughput_bps():.0f} bps")

    # -- ζωγραφική ----------------------------------------------------------

    _PAL = None

    def _palette(self):
        if ErevosApp._PAL is None:
            cols = []
            for i in range(64):
                t = i / 63.0
                if t < 0.25:
                    u = t / 0.25
                    r, g, b = 2, int(12 + 26 * u), int(8 + 14 * u)
                elif t < 0.6:
                    u = (t - 0.25) / 0.35
                    r, g, b = int(6 + 20 * u), int(38 + 150 * u), int(22 + 60 * u)
                elif t < 0.85:
                    u = (t - 0.6) / 0.25
                    r, g, b = int(26 + 190 * u), 188 + int(50 * u), int(82 + 60 * u)
                else:
                    u = (t - 0.85) / 0.15
                    r, g, b = 216 + int(39 * u), int(238 - 60 * u), int(142 - 80 * u)
                cols.append(f'#{clamp(r,0,255):02x}{clamp(g,0,255):02x}'
                            f'{clamp(b,0,255):02x}')
            ErevosApp._PAL = cols
        return ErevosApp._PAL

    def _paint(self):
        try:
            self._paint_lamp()
            if (self.station and self.v_wfon.get() and
                    self.nb.index(self.nb.select()) == 4):
                self._paint_spectrum()
        except Exception:                               # noqa: BLE001
            pass
        self.root.after(220, self._paint)

    def _paint_lamp(self):
        if not self.station:
            return
        self._blink = (self._blink + 1) % 4
        if self.tx_active:
            self._set_lamp(AMBER if self._blink < 2 else CARD, ring=True)
            self.v_state.set(f'{self.station.call}  ·  {self.t("tx")}')
        else:
            self._set_lamp(GREEN, ring=True)
            self.v_state.set(f'{self.station.call}  ·  {self.t("listen")}')

    def _paint_spectrum(self):
        buf = self.station.rx.buffer()
        if len(buf) < 2048:
            return
        fmax = 4000.0
        seg = buf[-8192:] if len(buf) >= 8192 else buf
        mag = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
        freqs = np.fft.rfftfreq(len(seg), 1.0 / modem.SAMPLE_RATE)
        mag = mag[freqs <= fmax]
        if len(mag) < 8:
            return

        bins = int(min(max(120, self.wf.winfo_width()), 640))
        idx = np.linspace(0, len(mag), bins + 1).astype(int)
        vals = np.maximum.reduceat(mag, np.clip(idx[:-1], 0, len(mag) - 1))
        vals = np.log1p(vals * 40)
        pk = vals.max()
        vals = vals / pk if pk > 0 else vals * 0

        h, w = 110, max(1, self.sp.winfo_width())
        self.sp.delete('all')
        bw = w / bins
        pts = []
        for i, v in enumerate(vals):
            x = i * bw
            y = h - v * (h - 8)
            pts.extend([x, y])
            self.sp.create_rectangle(x, y, x + bw + 0.6, h, fill=DIM, width=0)
        if len(pts) >= 4:
            self.sp.create_line(*pts, fill=GLOW, width=1)
        for fr, col, lab in ((modem.F_MARK, GREEN, 'mark'),
                             (modem.F_SPACE, AMBER, 'space')):
            x = fr / fmax * w
            self.sp.create_line(x, 0, x, h, fill=col, dash=(2, 4))
            self.sp.create_text(x + 4, 10, text=lab, fill=col, anchor='w',
                                font=MONO_XS)

        rows = int(clamp(self.wf.winfo_height(), 40, 200))
        if (self._wf_img is None or self._wf_img.width() != bins
                or self._wf_img.height() != rows):
            self._wf_img = tk.PhotoImage(width=bins, height=rows)
            self._wf_img.put(VOID, to=(0, 0, bins, rows))
            self._wf_row = 0
            self.wf.delete('all')
            self.wf.create_image(0, 0, image=self._wf_img, anchor='nw',
                                 tags='a')
            self.wf.create_image(0, 0, image=self._wf_img, anchor='nw',
                                 tags='b')

        pal = self._palette()
        row = '{' + ' '.join(pal[int(clamp(v, 0, 1) * 63)] for v in vals) + '}'
        self._wf_img.put(row, to=(0, self._wf_row))
        self._wf_row = (self._wf_row + 1) % rows
        self.wf.coords('a', 0, -self._wf_row)
        self.wf.coords('b', 0, rows - self._wf_row)

        m, s = (modem.tone_levels(seg[-4800:]) if len(seg) > 4800 else (0, 0))
        ratio = m / (s + 1e-9)
        self.v_tone.set(f'  mark {m:.4f}    space {s:.4f}    '
                        f'ratio {ratio:5.2f}    '
                        f'{self.t("balanced") if 0.5 < ratio < 2 else self.t("unbalanced")}')

    # -- ρυθμίσεις ----------------------------------------------------------

    def _load(self):
        try:
            with open(CONFIG_PATH) as fh:
                return json.load(fh)
        except Exception:                               # noqa: BLE001
            return {}

    def _save(self):
        d = {'lang': self.lang, 'call': self.v_call.get(),
             'dst': self.v_dst.get(), 'mode': self.v_mode.get(),
             'in': self.v_in.get(), 'out': self.v_out.get(),
             'amp': self.v_amp.get(), 'vox': self.v_vox.get(),
             'ptt': self.v_ptt.get(), 'pttport': self.v_pttport.get(),
             'engine': self.v_engine.get(),
             'pttline': self.v_pttline.get(), 'quick': self.v_quick.get(),
             'baud': self.m_baud.get(), 'mark': self.m_mark.get(),
             'space': self.m_space.get(), 'fec': self.m_fec.get(),
             'preamble': self.m_pre.get(), 'agc': self.m_agc.get(),
             'syncerr': self.m_sync.get(),
             'ack': self.n_ack.get(), 'retries': self.n_retry.get(),
             'chunk': self.n_chunk.get(), 'busy': self.n_busy.get(),
             'ackrep': self.n_ackrep.get(),
             'inbox': self.v_inbox.get(),
             'verbose': self.v_verbose.get()}
        with contextlib.suppress(Exception):
            with open(CONFIG_PATH, 'w') as fh:
                json.dump(d, fh, indent=2, ensure_ascii=False)

    def _close(self):
        self._save()
        if self.station:
            with contextlib.suppress(Exception):
                self.station.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = ErevosApp(root)
    app._say(app.t('ready'), 'ok')
    app._say(app.t('ready2'), 'sys')
    app._say(app.t('ready3'), 'sys')
    root.mainloop()


if __name__ == '__main__':
    main()
