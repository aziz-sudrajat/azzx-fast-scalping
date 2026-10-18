# Copyright (c) 2026 aziz-sudrajat <azizsudrajat.stb4@gmail.com>
# https://github.com/aziz-sudrajat
#
# All rights reserved. This file is part of the azzx-fast-scalping project.
# Unauthorized copying, modification, distribution, or false claim of
# authorship is prohibited.

"""Entry point: paper-mode dry-run sinyal scalping (tanpa order beneran).

Usage:
    python -m scalping.run [SYMBOL] [--chart out.svg]

Mengambil klines 1m/5m/15m asli dari Binance futures, menjalankan
ScalpEngine.evaluate(), mencetak sinyal (jika ada). TIDAK PERNAH
menempatkan order — murni dry-run.
"""
from __future__ import annotations

import sys

from . import config
from .data import BinanceData
from .indicators import ema
from .signal import ScalpEngine


def main(argv: list[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])
    symbol = "BTCUSDT"
    chart_path = None
    it = iter(args)
    for a in it:
        if a == "--chart":
            chart_path = next(it, "chart.svg")
        elif not a.startswith("-"):
            symbol = a.upper()
    cfg = config  # modul: ScalpEngine memakai getattr(cfg, ...)
    mode = config.load().get("MODE", "paper")
    if mode != "paper":
        print(f"MODE={mode}: run.py hanya untuk paper dry-run. "
              "Set SCALP_MODE=paper.", file=sys.stderr)
        return 2
    data = BinanceData(testnet=False)
    try:
        k1m = data.get_klines(symbol, "1m", limit=200)
        k5m = data.get_klines(symbol, "5m", limit=200)
        k15m = data.get_klines(symbol, "15m", limit=200)
    except RuntimeError as e:
        print(f"data gagal (fail-closed): {e}", file=sys.stderr)
        return 1
    engine = ScalpEngine(cfg)
    sig = engine.evaluate(k1m, k5m, k15m)
    last = k1m[-1]["close"]
    print(f"{symbol} 1m close: {last}")
    if sig is None:
        print("tidak ada sinyal (filter/bias tidak terpenuhi) — tidak ada trade.")
    else:
        print(f"SINYAL {sig.side.upper()} @ {sig.price} "
              f"(strength {sig.strength}/3)")
        print(f"  TP {sig.tp:.2f} | SL {sig.sl:.2f} | time-stop {sig.time_stop_min} mnt")
        print(f"  alasan: {sig.reason}")
    if chart_path:
        from .chart import render_chart
        closes = [c["close"] for c in k1m]
        e9 = ema(closes, cfg.EMA_FAST)
        e21 = ema(closes, cfg.EMA_SLOW)
        sigs = []
        if sig is not None:
            sigs.append({"index": len(k1m) - 1, "side": sig.side,
                         "strength": sig.strength})
        render_chart(k1m, e9, e21, sigs, chart_path,
                     title=f"{symbol} 1m — paper dry-run")
        print(f"chart: {chart_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
