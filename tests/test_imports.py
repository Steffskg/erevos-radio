# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Steff_SKG
"""Κάθε αρχείο φορτώνει. (Έτσι πιάστηκε το PREAMBLE στο erevos_debug.py.)"""

import importlib
import os
import py_compile

import pytest

from conftest import ROOT

MODULES = ['erevos_modem', 'erevos_fec', 'erevos_audio', 'erevos_link',
           'erevos_ptt', 'erevos_debug']


@pytest.mark.parametrize('name', MODULES)
def test_import(name):
    importlib.import_module(name)


def test_gui_compiles():
    py_compile.compile(os.path.join(ROOT, 'erevos_gui.py'), doraise=True)


def test_gui_imports_when_tk_available():
    pytest.importorskip('tkinter')
    importlib.import_module('erevos_gui')
