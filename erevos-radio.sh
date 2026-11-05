#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Εκκίνηση EREVOS RADIO από τον φάκελο όπου βρίσκεται αυτό το script.
cd "$(dirname "$(readlink -f "$0")")" || exit 1
exec python3 erevos_gui.py "$@"
