# Copyright (c) 2026 aziz-sudrajat <azizsudrajat.stb4@gmail.com>
# https://github.com/aziz-sudrajat
#
# All rights reserved. This file is part of the azzx-fast-scalping project.
# Unauthorized copying, modification, distribution, or false claim of
# authorship is prohibited.

"""Scan coin terbaik untuk scalping cepat (short-TF 1m/5m/15m).

Jalankan langsung:  python scan.py

Output: tabel ranking coin yang cocok untuk scalping *cepat* (short-TF),
termasuk momentum 1m/5m, volume spike, candle-range (chop gate),
ATR percentile band, TP/SL dari ATR 1m, dan grade. Fungsi ``scan()``
(15m lama) tetap ada untuk backward compatibility.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scalping.scanner import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
