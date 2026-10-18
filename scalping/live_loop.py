"""Alternating long/short executor — langsung eksekusi tanpa tunggu sinyal.

Strategi: buka LONG → TP/SL/time-stop → tutup → buka SHORT → tutup → ...
Setiap trade selalu punya TP (1.5x ATR), SL (1x ATR), dan time-stop.

Usage:
    python -m scalping.live_loop BTCUSDT --mode paper --qty 0.001
    python -m scalping.live_loop BTCUSDT --mode testnet --qty 0.001 --max-trades 5
    python -m scalping.live_loop BTCUSDT --mode live --qty 0.001 --i-understand

    Mode juga bisa via env SCALP_MODE. API key via env (tidak pernah di kode):
      testnet: BINANCE_TESTNET_KEY / BINANCE_TESTNET_SECRET
      live:    BINANCE_LIVE_KEY / BINANCE_LIVE_SECRET

Output per trade tertutup:
    2026-10-18 07:05:12 | LONG | Entry: 81225.90 | TP: 81300.00 | PnL: +12.50

Fail-closed: data gagal / risk guard menolak / kill-switch -> berhenti aman.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone, timedelta

from . import config as cfg_mod
from .data import BinanceData
from . import indicators as ind
from .broker import PaperBroker, BinanceBroker
from .risk import RiskGuard


def _now_wib() -> str:
    wib = timezone(timedelta(hours=7))
    return datetime.now(wib).strftime("%Y-%m-%d %H:%M:%S")


def _fmt_pnl(pnl: float) -> str:
    return f"{pnl:+.2f}"


# --- warna terminal (ANSI) ---
_USE_COLOR = sys.stdout.isatty()
_C = {
    "green": "\033[92m",   # hijau terang
    "red": "\033[91m",     # merah terang
    "cyan": "\033[96m",
    "yellow": "\033[93m",
    "dim": "\033[2m",
    "bold": "\033[1m",
    "reset": "\033[0m",
}


def paint(text: str, *colors: str) -> str:
    """Bungkus teks dengan kode ANSI. Tanpa warna bila bukan tty."""
    if not _USE_COLOR or not colors:
        return text
    return "".join(_C[c] for c in colors) + text + _C["reset"]


def fmt_price(px: float) -> str:
    """Format harga dengan desimal adaptif berdasar magnitud.

    BTC 81225.9 -> '81225.90'; HBAR 0.085234 -> '0.085234'.
    Jangan pernah tampilkan 0.09 untuk harga 0.085xxx.
    """
    px = float(px)
    a = abs(px)
    if a >= 1000:
        return f"{px:.2f}"
    if a >= 100:
        return f"{px:.2f}"
    if a >= 10:
        return f"{px:.3f}"
    if a >= 1:
        return f"{px:.4f}"
    if a >= 0.1:
        return f"{px:.5f}"
    if a >= 0.01:
        return f"{px:.6f}"
    return f"{px:.8f}"


class AlternatingTrader:
    """Eksekutor alternating LONG/SHORT dengan TP/SL/time-stop."""

    def __init__(self, broker, data: BinanceData, cfg, risk: RiskGuard,
                 on_event=None):
        self.broker = broker
        self.data = data
        self.cfg = cfg
        self.risk = risk
        self.on_event = on_event or (lambda e: None)
        self._stop = False
        self.trades_done = 0
        self.total_pnl = 0.0
        self.wins = 0

    def stop(self):
        self._stop = True

    # ---------------------------------------------------------- helpers
    def _g(self, name, default):
        return getattr(self.cfg, name, default)

    def _live_price(self, symbol: str) -> float | None:
        try:
            kl = self.data.get_klines(symbol, "1m", limit=2)
            if kl:
                return float(kl[-1]["close"])
        except Exception:
            pass
        return None

    def _atr(self, symbol: str) -> float | None:
        try:
            kl = self.data.get_klines(symbol, "1m", limit=60)
            a = ind.atr(kl, int(self._g("ATR_PERIOD", 14)))
            v = a[-1] if a else None
            return float(v) if v and v > 0 else None
        except Exception:
            return None

    def _emit(self, **kw):
        try:
            self.on_event(kw)
        except Exception:
            pass

    # ---------------------------------------------------------- main loop
    def run(self, symbol: str, qty: float, max_trades: int = 0,
            poll_sec: float = 3.0) -> int:
        """Jalankan loop alternating. Return jumlah trade selesai."""
        symbol = symbol.upper()
        side = "BUY"  # mulai LONG, lalu alternating
        tp_mult = float(self._g("TP_ATR_MULT", 1.5))
        sl_mult = float(self._g("SL_ATR_MULT", 1.0))
        time_stop = float(self._g("TIME_STOP_MIN", 15)) * 60
        fee_rate = 0.0005  # taker 0.05% per side

        print(paint(f"[{_now_wib()}] mulai alternating {symbol} "
                        f"qty={qty} mode={self._g('MODE', 'paper')}",
                        "cyan", "bold"), flush=True)
        self._emit(type="started", symbol=symbol, qty=qty)

        while not self._stop:
            if max_trades and self.trades_done >= max_trades:
                break

            ok, reason = self.risk.check(symbol, 0)
            if not ok:
                print(f"[{_now_wib()}] risk guard menolak: {reason} — berhenti.",
                      flush=True)
                self._emit(type="stopped", reason=reason)
                break

            entry = self._live_price(symbol)
            atr = self._atr(symbol)
            if entry is None or atr is None:
                print(f"[{_now_wib()}] data gagal (fail-closed), coba lagi "
                      f"{poll_sec}s...", flush=True)
                time.sleep(poll_sec)
                continue

            is_long = (side == "BUY")
            tp = entry + tp_mult * atr if is_long else entry - tp_mult * atr
            sl = entry - sl_mult * atr if is_long else entry + sl_mult * atr
            t0 = time.time()
            label = "LONG" if is_long else "SHORT"

            # --- open ---
            try:
                self.broker.market_open(symbol, side, qty, entry)
            except Exception as e:
                # BinanceBroker pakai place_market
                try:
                    self.broker.place_market(symbol, side, qty)
                except Exception as e2:
                    print(f"[{_now_wib()}] open gagal: {e2} — skip trade.",
                          flush=True)
                    time.sleep(poll_sec)
                    continue
            self._emit(type="trade_open", side=label, entry=entry, tp=tp,
                       sl=sl, time=_now_wib())
            ocol = "green" if label == "LONG" else "red"
            print(paint(f"[{_now_wib()}] OPEN {label} {symbol} @ {fmt_price(entry)} "
                        f"(TP {fmt_price(tp)} / SL {fmt_price(sl)})",
                        ocol, "bold"), flush=True)

            # --- monitor ---
            exit_price, exit_why = entry, "time-stop"
            while True:
                if self._stop:
                    exit_why = "manual-stop"
                    break
                px = self._live_price(symbol)
                if px is None:
                    time.sleep(poll_sec)
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
                if time.time() - t0 >= time_stop:
                    exit_price, exit_why = px, "time-stop"
                    break
                time.sleep(poll_sec)

            # --- close ---
            try:
                res = self.broker.market_close(symbol, exit_price)
                pnl = float(res.get("pnl_usd", 0.0))
            except Exception:
                try:
                    close_side = "SELL" if is_long else "BUY"
                    self.broker.place_market(symbol, close_side, qty)
                    gross = (exit_price - entry) * qty if is_long \
                        else (entry - exit_price) * qty
                    fees = (entry + exit_price) * qty * fee_rate
                    pnl = gross - fees
                except Exception as e2:
                    print(f"[{_now_wib()}] close gagal: {e2} — DARURAT: "
                          f"cek posisi manual!", flush=True)
                    self._emit(type="error", error=str(e2))
                    break
            # fee untuk paper juga (realistis)
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

            side = "SELL" if side == "BUY" else "BUY"  # alternating

        print(f"[{_now_wib()}] selesai: {self.trades_done} trade, "
              f"total PnL {_fmt_pnl(self.total_pnl)}", flush=True)
        self._emit(type="finished", trades=self.trades_done,
                   total_pnl=self.total_pnl)
        return self.trades_done


def _build_broker(mode: str):
    if mode == "paper":
        return PaperBroker()
    testnet = (mode == "testnet")
    if mode == "live":
        key = os.environ.get("BINANCE_LIVE_KEY", "")
        sec = os.environ.get("BINANCE_LIVE_SECRET", "")
    else:
        key = os.environ.get("BINANCE_TESTNET_KEY", "")
        sec = os.environ.get("BINANCE_TESTNET_SECRET", "")
    if not key or not sec:
        name = "BINANCE_LIVE_KEY/SECRET" if mode == "live" \
            else "BINANCE_TESTNET_KEY/SECRET"
        raise SystemExit(f"mode {mode} butuh {name} di environment.")
    return BinanceBroker(api_key=key, api_secret=sec, testnet=testnet)


def main(argv=None) -> int:
    args = list(argv or sys.argv[1:])
    symbol, mode, qty = "BTCUSDT", None, 0.001
    max_trades, i_understand = 0, False
    it = iter(args)
    for a in it:
        if a == "--mode":
            mode = next(it, None)
        elif a == "--qty":
            qty = float(next(it, "0.001"))
        elif a == "--max-trades":
            max_trades = int(next(it, "0"))
        elif a == "--i-understand":
            i_understand = True
        elif not a.startswith("-"):
            symbol = a.upper()

    cfg = cfg_mod.load()
    if mode:
        if mode not in ("paper", "testnet", "live"):
            raise SystemExit("--mode harus paper|testnet|live")
        cfg["MODE"] = mode
    mode = cfg["MODE"]

    if mode == "live" and not i_understand:
        raise SystemExit("mode live butuh flag --i-understand. Uang asli, sayang — pikir dua kali.")

    broker = _build_broker(mode)
    data = BinanceData(testnet=(mode == "testnet"))
    risk = RiskGuard()
    trader = AlternatingTrader(broker, data, cfg, risk)
    try:
        trader.run(symbol, qty, max_trades=max_trades)
    except KeyboardInterrupt:
        print("\ndihentikan user.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
