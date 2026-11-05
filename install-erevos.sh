#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Steff_SKG
# =====================================================================
#  EREVOS RADIO  ·  εγκατάσταση
#  Μενού HamRadio + rofi + συντόμευση + έλεγχος συστήματος
# =====================================================================

# Ο φάκελος όπου βρίσκεται αυτό το script - όπου κι αν έγινε το clone.
APP="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HOME/bin"
DESK="$HOME/.local/share/applications"
DFILE="$DESK/erevos-radio.desktop"
LAUNCHER="$BIN/access-launcher.sh"

ok()   { echo "  [ok]   $1"; }
warn() { echo "  [!]    $1"; }
info() { echo "  [--]   $1"; }

echo
echo "  EREVOS RADIO — εγκατάσταση"
echo "  ────────────────────────────────────────────"

mkdir -p "$BIN" "$DESK" 2>/dev/null
ok "φάκελοι"

cat > "$BIN/erevos-radio.sh" <<EOF
#!/bin/bash
cd "$APP" || exit 1
exec python3 erevos_gui.py "\$@"
EOF
chmod +x "$BIN/erevos-radio.sh"
ok "εκκινητής"

cat > "$DFILE" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=EREVOS Radio
GenericName=Radio Messenger
Comment=Messages and files over radio, no internet
Comment[el]=Μηνύματα και αρχεία μέσω πομποδέκτη, χωρίς internet
Exec=$BIN/erevos-radio.sh
Path=$APP
Icon=$APP/erevos.svg
Terminal=false
StartupNotify=true
NoDisplay=false
Categories=HamRadio;Network;
Keywords=radio;ham;afsk;packet;erevos;messenger;
EOF
chmod 644 "$DFILE"
ok "μενού HamRadio"

if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate "$DFILE" >/dev/null 2>&1 \
        && ok "έγκυρη καταχώρηση" || warn "προειδοποιήσεις επικύρωσης"
fi

update-desktop-database "$DESK" >/dev/null 2>&1 && ok "βάση .desktop" \
    || info "update-desktop-database δεν υπάρχει (αβλαβές)"
rm -f "$HOME/.cache/menus/"* 2>/dev/null
xfce4-panel -r >/dev/null 2>&1 && ok "ανανέωση panel" \
    || info "το panel θα ανανεωθεί στην επόμενη σύνδεση"

# --- rofi -------------------------------------------------------------
echo
echo "  rofi"
echo "  ────"
CLEARED=0
for c in "$HOME/.cache/rofi3.druncache" "$HOME/.cache/rofi-drun.cache" \
         "$HOME/.cache/rofi/drun" "$HOME/.cache/rofi"; do
    if [ -e "$c" ]; then rm -rf "$c" 2>/dev/null && CLEARED=1; fi
done
if [ $CLEARED -eq 1 ]; then ok "καθαρίστηκε η μνήμη του rofi"
else info "δεν βρέθηκε μνήμη rofi προς καθαρισμό"; fi

if command -v rofi >/dev/null 2>&1; then
    ok "rofi -show drun  →  θα βρίσκει το EREVOS Radio"
else
    warn "το rofi δεν βρέθηκε στο PATH"
fi

if [ -f "$LAUNCHER" ]; then
    if grep -qi "erevos" "$LAUNCHER"; then
        ok "ο access-launcher το έχει ήδη"
    else
        cp "$LAUNCHER" "$LAUNCHER.bak.$(date +%H%M%S)"
        python3 - "$LAUNCHER" "$BIN/erevos-radio.sh" <<'PY'
import re, sys
path, exe = sys.argv[1], sys.argv[2]
src = orig = open(path).read()
name = 'EREVOS Radio'
plist = pcase = False

m = re.search(r'^(\s*\w+=)"([^"]*\\n[^"]*)"', src, re.M)
if m:
    src = src[:m.start(2)] + name + r'\n' + src[m.start(2):]
    plist = True

if not plist:
    m = re.search(r'^(\s*\w+=\()', src, re.M)
    if m:
        src = src[:m.end(1)] + f'\n    "{name}"' + src[m.end(1):]
        plist = True

m = re.search(r'^(\s*)case\s+"?\$\w+"?\s+in\s*$', src, re.M)
if m:
    src = src[:m.end(0)] + f'\n{m.group(1)}    "{name}") {exe} & ;;' \
        + src[m.end(0):]
    pcase = True

if src != orig:
    open(path, 'w').write(src)

print(f'       λίστα  : {"προστέθηκε" if plist else "ΔΕΝ βρέθηκε μοτίβο"}')
print(f'       εντολή : {"προστέθηκε" if pcase else "ΔΕΝ βρέθηκε μοτίβο"}')
if not (plist and pcase):
    print()
    print('       Πρόσθεσε χειροκίνητα:')
    print(f'         στη λίστα :  {name}')
    print(f'         στο case  :  "{name}") {exe} & ;;')
PY
        ok "αντίγραφο: $LAUNCHER.bak.*"
    fi
else
    info "δεν βρέθηκε $LAUNCHER"
fi

# --- έλεγχος ----------------------------------------------------------
echo
echo "  Έλεγχος"
echo "  ───────"
python3 - <<'PY'
import importlib
for m in ('numpy', 'sounddevice', 'tkinter'):
    try:
        mod = importlib.import_module(m)
        print(f"  [ok]   {m} {getattr(mod, '__version__', '')}")
    except Exception as e:
        print(f'  [!]    {m}: {e}')
try:
    import serial
    print('  [ok]   pyserial (σειριακό PTT διαθέσιμο)')
except Exception:
    print('  [--]   pyserial λείπει — μόνο VOX')
PY

for f in erevos_gui.py erevos_modem.py erevos_fec.py erevos_audio.py \
         erevos_link.py erevos_ptt.py erevos.svg; do
    if [ -f "$APP/$f" ]; then ok "$f"; else warn "ΛΕΙΠΕΙ: $f"; fi
done

echo
echo "  Δοκιμή αλυσίδας (χωρίς ήχο)"
echo "  ───────────────────────────"
( cd "$APP" && python3 - <<'PY'
import numpy as np, erevos_modem as m
m.reset_defaults()
ok = 0
for t in ['Ola kala. QRV.', 'Test 123 !@#', 'X'*180]:
    b = m.build_frame(m.TYPE_TEXT, 'A1', 'B2', t.encode())
    s = m.modulate(b).astype(float)
    n = np.random.normal(0, 1, len(s)); n *= np.sqrt(np.mean(s**2)/10**0.6)
    p = np.zeros(12000)
    g = m.receive(np.concatenate([p, s+n, p]))
    ok += bool(g) and g[0]['payload'].decode(errors='replace') == t
mark = 'ok' if ok == 3 else '!'
print(f'  [{mark}]    {ok}/3 μηνύματα με θόρυβο 6 dB')
print(f'  [ok]   ρυθμός {m.throughput_bps():.0f} bps')
PY
)

echo
echo "  ────────────────────────────────────────────"
echo "  Έτοιμο."
echo "    Μενού     : HamRadio → EREVOS Radio"
echo "    rofi      : rofi -show drun → EREVOS"
echo "    Τερματικό : $BIN/erevos-radio.sh"
echo
echo "  Έλεγχος μικροφώνου χωρίς γραφικό περιβάλλον:"
echo "    cd $APP && python3 erevos_audio.py --monitor"
echo
