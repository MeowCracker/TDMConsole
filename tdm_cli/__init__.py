"""TDM-CLI — a headless / terminal front-end for DevilXD's TwitchDropsMiner.

All code in this package lives *outside* the upstream ``TwitchDropsMiner`` git
submodule. The submodule is used pristine; we swap its tkinter ``GUIManager``
for a terminal implementation at import time (see :mod:`tdm_cli.bootstrap`).
"""
