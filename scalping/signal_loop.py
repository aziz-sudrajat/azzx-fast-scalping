# Copyright (c) 2026 aziz-sudrajat <azizsudrajat.stb4@gmail.com>
# https://github.com/aziz-sudrajat
#
# All rights reserved. This file is part of the azzx-fast-scalping project.
# Unauthorized copying, modification, distribution, or false claim of
# authorship is prohibited.

"""SignalTrader: live trading berbasis sinyal ScalpEngine (fleksibel).

Berbeda dengan AlternatingTrader (paksa gantian LONG/SHORT), trader ini:
- evaluasi ScalpEngine (bias 15m -> trigger 1m -> konfirmasi 5m -> filter)
  setiap poll;
- hanya entry bila ada sinyal DAN sedang flat (tidak ada posisi);
- setelah TP/SL/time-stop: evaluasi ulang — bila sinyal masih searah boleh
  re-entry, bila berlawanan ikut arah baru, bila tidak ada sinyal tunggu;
- TIDAK PERNAH memaksa arah berlawanan.

Proteksi yang dipakai ulang dari live_loop: rekonsiliasi start, stop-loss
di exchange (K1), retry close 3x, fallback PnL, risk guard.
"""

from __future__ import annotations

import time

from .broker import PaperBroker
from .live_loop import _now_wib, _fmt_pnl, fmt_price, paint
from .risk import RiskGuard
from .signal import ScalpEngine


class SignalTrader:
    """Eksekutor live berbasis sinyal MTF. Fleksibel, tidak dipaksa gantian."""

    def __init__(self, broker, data, cfg_mod, risk: RiskGuard,
                 on_event=None):
        self.broker = broker
        self.data = data
        self.cfg_mod = cfg_mod  # modul config (getattr), untuk ScalpEngine
        self.engine = ScalpEngine(cfg_mod)
        self.risk = risk
        self.on_event = on_event or (lambda e: None)
        self._stop = False
        self.trades_done = 0
        self.total_pnl = 0.0
        self.wins = 0

    def stop(self):
        self._stop = True

    def _live_price(self, symbol: str) -> float | None:
        getter = getattr(self.data, "get_ticker_price", None)
        if callable(getter):
            try:
                px = getter(symbol)
                if px and px > 0:
                    return float(px)
            except Exception:
                pass
        try:
            kl = self.data.get_klines(symbol, "1m", limit=2)
            if kl:
                return float(kl[-1]["close"])
        except Exception:
            pass
        return None

    def _klines(self, symbol: str):
        """Ambil klines 1m/5m/15m. Return None bila gagal."""
        try:
            k1m = self.data.get_klines(symbol, "1m", limit=200)
            k5m = self.data.get_klines(symbol, "5m", limit=200)
            k15m = self.data.get_klines(symbol, "15m", limit=200)
            return k1m, k5m, k15m
        except Exception:
            return None

    def _emit(self, **kw):
        try:
            self.on_event(kw)
        except Exception:
            pass

    # ---------------------------------------------------------- main loop
    def run(self, symbol: str, qty: float, max_trades: int = 0,
            poll_sec: float = 15.0, monitor_sec: float = 3.0) -> int:
        """Jalankan loop signal-driven. Return jumlah trade selesai."""
        symbol = symbol.upper()
        fee_rate = 0.0005

        print(paint(f"[{_now_wib()}] mulai SIGNAL trader {symbol} "
                    f"qty={qty} mode={getattr(self.cfg_mod, 'MODE', 'paper')}",
                    "cyan", "bold"), flush=True)
        self._emit(type="started", symbol=symbol, qty=qty, strategy="signal")

        # --- rekonsiliasi: tolak start bila ada posisi nyangkut ---
        try:
            existing = self.broker.get_position(symbol)
        except Exception:
            existing = None
        if existing is not None:
            msg = (f"posisi terbuka terdeteksi saat start: {existing} — "
                   f"tutup manual dulu sebelum run baru")
            print(f"[{_now_wib()}] {msg}", flush=True)
            self._emit(type="stopped", reason=msg)
            return 0

        while not self._stop:
            if max_trades and self.trades_done >= max_trades:
                break

            ok, reason = self.risk.check(symbol, 0)
            if not ok:
                print(f"[{_now_wib()}] risk guard menolak: {reason} — berhenti.",
                      flush=True)
                self._emit(type="stopped", reason=reason)
                break

            # --- evaluasi sinyal ---
            kl = self._klines(symbol)
            if kl is None:
                print(f"[{_now_wib()}] data gagal (fail-closed), coba lagi "
                      f"{poll_sec}s...", flush=True)
                time.sleep(poll_sec)
                continue
            k1m, k5m, k15m = kl
            sig = self.engine.evaluate(k1m, k5m, k15m)
            if sig is None:
                print(f"[{_now_wib()}] tidak ada sinyal — menunggu "
                      f"{poll_sec}s...", flush=True)
                self._emit(type="waiting", symbol=symbol)
                time.sleep(poll_sec)
                continue

            # --- ada sinyal: entry ---
            entry = self._live_price(symbol) or sig.price
            is_long = (sig.side == "long")
            label = "LONG" if is_long else "SHORT"
            side = "BUY" if is_long else "SELL"
            # TP/SL dari sinyal; sesuaikan terhadap harga entry aktual
            atr = sig.atr
            tp_mult = float(getattr(self.cfg_mod, "TP_ATR_MULT", 1.5))
            sl_mult = float(getattr(self.cfg_mod, "SL_ATR_MULT", 1.0))
            tp = entry + tp_mult * atr if is_long else entry - tp_mult * atr
            sl = entry - sl_mult * atr if is_long else entry + sl_mult * atr
            tstop = float(sig.time_stop_min) * 60
            t0 = time.time()

            try:
                self.broker.market_open(symbol, side, qty, entry)
            except Exception as e:
                print(f"[{_now_wib()}] open gagal: {e} — skip sinyal.",
                      flush=True)
                time.sleep(poll_sec)
                continue

            if hasattr(self.broker, "place_stop_loss"):
                try:
                    self.broker.place_stop_loss(symbol, side, qty, sl)
                except Exception as e_sl:
                    print(f"[{_now_wib()}] stop-loss exchange gagal: {e_sl} "
                          f"— lanjut tanpa proteksi exchange!", flush=True)
            self._emit(type="trade_open", side=label, entry=entry, tp=tp,
                       sl=sl, strength=sig.strength, reason=sig.reason,
                       time=_now_wib())
            ocol = "green" if label == "LONG" else "red"
            print(paint(f"[{_now_wib()}] OPEN {label} {symbol} @ {fmt_price(entry)} "
                        f"(TP {fmt_price(tp)} / SL {fmt_price(sl)}) "
                        f"[strength {sig.strength}/3: {sig.reason}]",
                        ocol, "bold"), flush=True)

            # --- monitor TP/SL/time-stop ---
            exit_price, exit_why = entry, "time-stop"
            while True:
                if self._stop:
                    exit_why = "manual-stop"
                    break
                px = self._live_price(symbol)
                if px is None:
                    time.sleep(monitor_sec)
                    continue
                self._emit(type="tick", price=px, side=label, entry=entry,
                           tp=tp, sl=sl)
                hit_tp = px >= tp if is_long else px <= tp
                hit_sl = px <= sl if is_long else px >= sl
                if hit_tp:
                    exit_price, exit_why = px, "TP"
                    break
                if hit_sl:
                    exit_price, exit_why = px, "SL"
                    break
                if time.time() - t0 >= tstop:
                    exit_price, exit_why = px, "time-stop"
                    break
                time.sleep(monitor_sec)

            # --- close: cancel stop + retry 3x ---
            if hasattr(self.broker, "cancel_stop_loss"):
                try:
                    self.broker.cancel_stop_loss(symbol)
                except Exception:
                    pass
            res, pnl, close_ok = None, 0.0, False
            for attempt in range(3):
                try:
                    res = self.broker.market_close(symbol, exit_price)
                    pnl = float(res.get("pnl_usd", 0.0))
                    close_ok = True
                    break
                except Exception as e2:
                    print(f"[{_now_wib()}] close gagal (percobaan "
                          f"{attempt + 1}/3): {e2}", flush=True)
                    time.sleep(2)
            if not close_ok:
                print(f"[{_now_wib()}] close GAGAL 3x — DARURAT: posisi "
                      f"mungkin masih terbuka, cek manual SEGERA!", flush=True)
                self._emit(type="error", error="close gagal 3x")
                break
            if isinstance(self.broker, PaperBroker):
                pnl -= (entry + exit_price) * qty * fee_rate

            self.trades_done += 1
            self.total_pnl += pnl
            if pnl > 0:
                self.wins += 1
            self.risk.record_trade(pnl)

            line = (f"{_now_wib()} | {label} | Entry: {fmt_price(entry)} | "
                    f"TP: {fmt_price(tp)} | PnL: {_fmt_pnl(pnl)} [{exit_why}]")
            line = paint(line, "green" if pnl >= 0 else "red", "bold")
            print(line, flush=True)
            self._emit(type="trade_close", side=label, entry=entry, tp=tp,
                       pnl=pnl, why=exit_why, time=_now_wib(),
                       total_pnl=self.total_pnl, trades=self.trades_done,
                       wins=self.wins)
            # TIDAK alternating — loop kembali ke evaluasi sinyal

        print(f"[{_now_wib()}] selesai: {self.trades_done} trade, "
              f"total PnL {_fmt_pnl(self.total_pnl)}", flush=True)
        self._emit(type="finished", trades=self.trades_done,
                   total_pnl=self.total_pnl)
        return self.trades_done
