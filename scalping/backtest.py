# Copyright (c) 2026 aziz-sudrajat <azizsudrajat.stb4@gmail.com>
# https://github.com/aziz-sudrajat
#
# All rights reserved. This file is part of the azzx-fast-scalping project.
# Unauthorized copying, modification, distribution, or false claim of
# authorship is prohibited.

"""Backtest walk-forward untuk ScalpEngine.

Asumsi:
- 1 posisi per arah per waktu (tidak ada pyramiding); long dan short boleh
  terbuka bersamaan karena arahnya berlawanan.
- Entry di close bar sinyal (asumsi taker): harga entry diperburuk sebesar
  SLIPPAGE, fee TAKER_FEE x notional per side (qty = 1 unit).
- Exit: TP/SL tercapai (dicek dari high/low tiap bar; bila TP dan SL tersentuh
  di bar yang sama, SL dianggap lebih dulu = skenario konservatif), atau
  time stop TIME_STOP_MIN bar 1m (keluar di close).
- Posisi yang masih terbuka di akhir data ditutup paksa di close terakhir
  (reason "eod").
- Time stop dihitung dari selisih indeks bar 1m (1 bar 1m ~= 1 menit).
"""

from scalping.signal import ScalpEngine

_TIME_KEYS = ("time", "timestamp", "ts", "datetime", "open_time")


def _time(candle):
    for k in _TIME_KEYS:
        v = candle.get(k)
        if v is not None:
            return v
    return None


def _window_upto(candles, t):
    """Window histori candle dengan waktu <= t (best-effort bila tanpa waktu)."""
    if t is None:
        return list(candles)
    return [c for c in candles if _time(c) is not None and _time(c) <= t]


def run_backtest(k1m, k5m, k15m, cfg=None) -> dict:
    """Jalankan backtest. Kembalikan dict metrik + daftar trade."""
    if cfg is None:
        from scalping import config as cfg_mod
        cfg = cfg_mod
    fee_rate = float(getattr(cfg, "TAKER_FEE", 0.0005))
    slip = float(getattr(cfg, "SLIPPAGE", 0.0001))
    tstop = int(getattr(cfg, "TIME_STOP_MIN", 15))

    engine = ScalpEngine(cfg)
    positions = {}  # side -> dict posisi terbuka
    trades = []

    def open_position(side, sig, idx):
        close = float(k1m[idx]["close"])
        if side == "long":
            entry_eff = close * (1.0 + slip)
        else:
            entry_eff = close * (1.0 - slip)
        positions[side] = {
            "side": side,
            "entry_index": idx,
            "entry_time": _time(k1m[idx]),
            "entry_close": close,
            "entry_eff": entry_eff,
            "tp": float(sig.tp),
            "sl": float(sig.sl),
        }

    def close_position(side, idx, exit_raw, reason):
        pos = positions.pop(side)
        if side == "long":
            exit_eff = exit_raw * (1.0 - slip)
            pnl_gross = exit_eff - pos["entry_eff"]
        else:
            exit_eff = exit_raw * (1.0 + slip)
            pnl_gross = pos["entry_eff"] - exit_eff
        fee_total = fee_rate * pos["entry_close"] + fee_rate * exit_raw
        pnl_net = pnl_gross - fee_total
        trades.append({
            "side": side,
            "entry_index": pos["entry_index"],
            "exit_index": idx,
            "entry_time": pos["entry_time"],
            "exit_time": _time(k1m[idx]),
            "entry_price": pos["entry_eff"],
            "exit_price": exit_eff,
            "pnl_net": pnl_net,
            "fee_total": fee_total,
            "reason": reason,
        })

    n = len(k1m)
    for i in range(n):
        bar = k1m[i]
        t = _time(bar)
        w1 = k1m[: i + 1]
        w5 = _window_upto(k5m, t)
        w15 = _window_upto(k15m, t)

        # 1. Kelola posisi terbuka pada bar ini (bar entry sendiri dilewati:
        #    TP/SL/time-stop baru dicek mulai bar berikutnya).
        for side in ("long", "short"):
            pos = positions.get(side)
            if pos is None or pos["entry_index"] >= i:
                continue
            high = float(bar["high"])
            low = float(bar["low"])
            close = float(bar["close"])
            tp, sl = pos["tp"], pos["sl"]
            if side == "long":
                sl_hit = low <= sl
                tp_hit = high >= tp
            else:
                sl_hit = high >= sl
                tp_hit = low <= tp
            if sl_hit:
                # SL didahulukan bila TP & SL tersentuh di bar yang sama.
                close_position(side, i, sl, "sl")
            elif tp_hit:
                close_position(side, i, tp, "tp")
            elif i - pos["entry_index"] >= tstop:
                close_position(side, i, close, "time_stop")

        # 2. Cari sinyal baru untuk sisi yang belum punya posisi.
        sig = engine.evaluate(w1, w5, w15)
        if sig is not None and sig.side not in positions:
            open_position(sig.side, sig, i)

    # 3. Tutup paksa posisi yang masih terbuka di akhir data.
    if n > 0:
        last = n - 1
        for side in ("long", "short"):
            if side in positions:
                close_position(side, last, float(k1m[last]["close"]), "eod")

    # 4. Metrik.
    n_trades = len(trades)
    if n_trades == 0:
        return {
            "trades": [],
            "winrate": None,
            "profit_factor": None,
            "max_drawdown": 0.0,
            "total_pnl": 0.0,
            "expectancy": 0.0,
            "n_trades": 0,
        }

    pnls = [t["pnl_net"] for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = -sum(p for p in pnls if p < 0)
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = float("inf")
    else:
        profit_factor = None

    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    total = sum(pnls)
    return {
        "trades": trades,
        "winrate": wins / n_trades,
        "profit_factor": profit_factor,
        "max_drawdown": max_dd,
        "total_pnl": total,
        "expectancy": total / n_trades,
        "n_trades": n_trades,
    }
