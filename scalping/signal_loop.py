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
- entry boleh BERGANDA: tiap sinyal valid membuka posisi TERPISAH dengan
  TP/SL/time-stop sendiri (multi-position; dibatasi RiskGuard.max_positions,
  0 = tanpa batas);
- TIDAK PERNAH memaksa arah berlawanan; entry lawan arah di simbol yang sama
  ditolak (semantik one-way, seperti exchange real).

Proteksi yang dipakai ulang dari live_loop: rekonsiliasi start, stop-loss
di exchange (K1), retry close 3x, fallback PnL, risk guard.
"""

from __future__ import annotations

import time

from .broker import PaperBroker
from .live_loop import _now_wib, _fmt_pnl, fmt_price, paint
from .risk import RiskGuard
from .signal import ScalpEngine

_FEE_RATE = 0.0005  # taker fee paper (per sisi, dikali 2 sisi)


class SignalTrader:
    """Eksekutor live berbasis sinyal MTF. Fleksibel, tidak dipaksa gantian."""

    def __init__(self, broker, data, cfg_mod, risk: RiskGuard,
                 on_event=None, mode: str | None = None,
                 leverage: float = 10.0):
        self.broker = broker
        self.data = data
        self.cfg_mod = cfg_mod  # modul config (getattr), untuk ScalpEngine
        self._mode = mode or getattr(cfg_mod, "MODE", "paper")
        self.engine = ScalpEngine(cfg_mod)
        self.risk = risk
        self.on_event = on_event or (lambda e: None)
        self.leverage = float(leverage) if leverage else 10.0
        self._stop = False
        self.trades_done = 0
        self.total_pnl = 0.0
        self.wins = 0
        # --- multi-position: tiap entry = posisi terpisah ---
        self.open_trades: list[dict] = []
        self._trade_seq = 0
        self._last_reject_reason: str | None = None

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

    # ---------------------------------------------------------- entry/exit
    def _preflight_margin(self, symbol: str, qty: float,
                          entry: float) -> tuple[bool, str]:
        """Cek saldo cukup untuk margin 1 entry. Fail-closed bila saldo
        diketahui (broker live/testnet); paper selalu lolos."""
        try:
            lev = self.leverage if self.leverage > 0 else 1.0
            need = qty * entry / lev
            bal = None
            b = self.broker
            if hasattr(b, "balance") and callable(getattr(b, "balance")):
                bal = float(b.balance())
            elif hasattr(b, "get_balance") and callable(
                    getattr(b, "get_balance")):
                bal = float(b.get_balance())
            if bal is not None and bal < need:
                return (False,
                        f"saldo {bal:.2f} < margin butuh {need:.2f} USDT")
        except Exception as e:
            return False, f"pre-flight margin gagal: {e}"
        return True, "ok"

    def _open_trade(self, symbol: str, sig, qty: float) -> dict | None:
        """Buka 1 entry sebagai posisi TERPISAH (tidak di-merge)."""
        entry = self._live_price(symbol) or sig.price
        is_long = (sig.side == "long")
        label = "LONG" if is_long else "SHORT"
        side = "BUY" if is_long else "SELL"
        atr = sig.atr
        tp_mult = float(getattr(self.cfg_mod, "TP_ATR_MULT", 1.5))
        sl_mult = float(getattr(self.cfg_mod, "SL_ATR_MULT", 1.0))
        tp = entry + tp_mult * atr if is_long else entry - tp_mult * atr
        sl = entry - sl_mult * atr if is_long else entry + sl_mult * atr
        tstop = float(sig.time_stop_min) * 60

        ok, reason = self._preflight_margin(symbol, qty, entry)
        if not ok:
            print(f"[{_now_wib()}] pre-flight menolak entry: {reason}",
                  flush=True)
            self._emit(type="entry_rejected", reason=reason, symbol=symbol)
            return None

        try:
            self.broker.market_open(symbol, side, qty, entry)
        except Exception as e:
            print(f"[{_now_wib()}] open gagal: {e} — skip sinyal.", flush=True)
            return None

        if hasattr(self.broker, "place_stop_loss"):
            try:
                self.broker.place_stop_loss(symbol, side, qty, sl)
            except Exception as e_sl:
                print(f"[{_now_wib()}] stop-loss exchange gagal: {e_sl} "
                      f"— lanjut tanpa proteksi exchange!", flush=True)

        self._trade_seq += 1
        tr = {
            "id": self._trade_seq,
            "symbol": symbol,
            "side": label,          # LONG / SHORT (tampilan)
            "order_side": side,     # BUY / SELL (broker)
            "qty": qty,
            "entry": entry,
            "tp": tp,
            "sl": sl,
            "t0": time.time(),
            "tstop": tstop,
            "strength": getattr(sig, "strength", None),
            "reason": getattr(sig, "reason", ""),
        }
        self.open_trades.append(tr)
        self._emit(type="trade_open", trade_id=tr["id"], symbol=symbol,
                   side=label, entry=entry, tp=tp, sl=sl,
                   strength=tr["strength"], reason=tr["reason"],
                   time=_now_wib(), open_count=len(self.open_trades))
        ocol = "green" if label == "LONG" else "red"
        print(paint(
            f"[{_now_wib()}] OPEN #{tr['id']} {label} {symbol} @ "
            f"{fmt_price(entry)} (TP {fmt_price(tp)} / SL {fmt_price(sl)}) "
            f"[strength {tr['strength']}/3: {tr['reason']}] "
            f"(terbuka: {len(self.open_trades)})",
            ocol, "bold"), flush=True)
        return tr

    def _check_exit(self, tr: dict, px: float) -> tuple[float, str | None]:
        """Return (exit_price, alasan) bila TP/SL/time-stop kena."""
        is_long = (tr["side"] == "LONG")
        hit_tp = px >= tr["tp"] if is_long else px <= tr["tp"]
        hit_sl = px <= tr["sl"] if is_long else px >= tr["sl"]
        if hit_tp:
            return px, "TP"
        if hit_sl:
            return px, "SL"
        if time.time() - tr["t0"] >= tr["tstop"]:
            return px, "time-stop"
        return px, None

    def _broker_reduce(self, tr: dict,
                       exit_price: float) -> tuple[bool, float | str]:
        """Kurangi posisi broker sebesar qty 1 trade.

        Pakai close_qty bila broker mendukung (PaperBroker); fallback ke
        market_close penuh hanya bila ini satu-satunya trade di simbol itu
        (broker real tanpa partial-close).
        """
        symbol, qty = tr["symbol"], tr["qty"]
        close_qty = getattr(self.broker, "close_qty", None)
        if callable(close_qty):
            res = close_qty(symbol, qty, exit_price)
            return True, float(res.get("pnl_usd", 0.0))
        others = [t for t in self.open_trades
                  if t["symbol"] == symbol and t["id"] != tr["id"]]
        if others:
            return (False, "broker tak dukung partial close padahal ada "
                           f"{len(others)} posisi lain di {symbol}")
        res = self.broker.market_close(symbol, exit_price)
        return True, float(res.get("pnl_usd", 0.0))

    def _close_trade(self, tr: dict, exit_price: float, why: str) -> bool:
        """Tutup 1 trade. Return True bila sukses."""
        symbol = tr["symbol"]
        if hasattr(self.broker, "cancel_stop_loss"):
            try:
                self.broker.cancel_stop_loss(symbol)
            except Exception:
                pass
        ok, out = False, 0.0
        for attempt in range(3):
            try:
                ok, out = self._broker_reduce(tr, exit_price)
                if ok:
                    break
                # limitasi broker (bukan error jaringan): jangan retry sia-sia
                print(f"[{_now_wib()}] close #{tr['id']} ditolak: {out}",
                      flush=True)
                break
            except Exception as e2:
                out = str(e2)
                print(f"[{_now_wib()}] close #{tr['id']} gagal (percobaan "
                      f"{attempt + 1}/3): {e2}", flush=True)
                time.sleep(2)
        if not ok:
            print(f"[{_now_wib()}] close #{tr['id']} GAGAL — DARURAT: posisi "
                  f"mungkin masih terbuka, cek manual SEGERA! ({out})",
                  flush=True)
            self._emit(type="error", error=f"close #{tr['id']} gagal: {out}")
            return False
        pnl = float(out)
        if isinstance(self.broker, PaperBroker):
            pnl -= (tr["entry"] + exit_price) * tr["qty"] * _FEE_RATE
        self.open_trades = [t for t in self.open_trades
                            if t["id"] != tr["id"]]
        self.trades_done += 1
        self.total_pnl += pnl
        if pnl > 0:
            self.wins += 1
        self.risk.record_trade(pnl)

        line = (f"{_now_wib()} | #{tr['id']} {tr['side']} | Entry: "
                f"{fmt_price(tr['entry'])} | TP: {fmt_price(tr['tp'])} | "
                f"PnL: {_fmt_pnl(pnl)} [{why}]")
        print(paint(line, "green" if pnl >= 0 else "red", "bold"), flush=True)
        self._emit(type="trade_close", trade_id=tr["id"], symbol=symbol,
                   side=tr["side"], entry=tr["entry"], tp=tr["tp"],
                   pnl=pnl, why=why, time=_now_wib(),
                   total_pnl=self.total_pnl, trades=self.trades_done,
                   wins=self.wins, open_count=len(self.open_trades))
        return True

    def _maybe_open(self, symbol: str,
                    qty: float) -> tuple[dict | None, str | None]:
        """Coba buka 1 entry baru. Return (trade|None, reject_reason|None)."""
        ok, reason = self.risk.check(symbol, len(self.open_trades))
        if not ok:
            if reason != self._last_reject_reason:
                print(f"[{_now_wib()}] risk guard menolak entry baru: "
                      f"{reason}", flush=True)
                self._last_reject_reason = reason
            self._emit(type="waiting", symbol=symbol, reason=reason)
            return None, reason
        self._last_reject_reason = None

        kl = self._klines(symbol)
        if kl is None:
            print(f"[{_now_wib()}] data gagal (fail-closed), coba lagi...",
                  flush=True)
            return None, None
        k1m, k5m, k15m = kl
        sig = self.engine.evaluate(k1m, k5m, k15m)
        if sig is None:
            n = len(self.open_trades)
            print(f"[{_now_wib()}] tidak ada sinyal — menunggu... "
                  f"(terbuka: {n})", flush=True)
            self._emit(type="waiting", symbol=symbol)
            return None, None

        label = "LONG" if sig.side == "long" else "SHORT"
        for tr in self.open_trades:
            if tr["symbol"] == symbol and tr["side"] != label:
                print(f"[{_now_wib()}] skip sinyal {label}: ada posisi "
                      f"{tr['side']} terbuka di {symbol} (one-way)",
                      flush=True)
                return None, None
        tr = self._open_trade(symbol, sig, qty)
        return tr, None

    # ---------------------------------------------------------- main loop
    def run(self, symbol: str, qty: float, max_trades: int = 0,
            poll_sec: float = 15.0, monitor_sec: float = 3.0) -> int:
        """Jalankan loop signal-driven multi-position.

        Tiap iterasi: monitor semua posisi terbuka (TP/SL/time-stop), lalu
        evaluasi sinyal untuk entry baru (tiap poll_sec). Return jumlah
        trade selesai.
        """
        symbol = symbol.upper()

        print(paint(f"[{_now_wib()}] mulai SIGNAL trader {symbol} "
                    f"qty={qty} mode={self._mode} "
                    f"(max_posisi="
                    f"{self.risk.max_positions or 'tanpa batas'})",
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

        last_eval = 0.0
        while not self._stop:
            if max_trades and self.trades_done >= max_trades \
                    and not self.open_trades:
                break

            # --- 1. monitor semua posisi terbuka ---
            px = self._live_price(symbol)
            if px is not None:
                for tr in list(self.open_trades):
                    if self._stop:
                        break
                    self._emit(type="tick", trade_id=tr["id"], price=px,
                               side=tr["side"], entry=tr["entry"],
                               tp=tr["tp"], sl=tr["sl"])
                    exit_price, why = self._check_exit(tr, px)
                    if why:
                        # throttle retry bila close gagal persisten
                        if tr.get("close_fail_ts") and \
                                time.time() - tr["close_fail_ts"] < 30:
                            continue
                        if not self._close_trade(tr, exit_price, why):
                            tr["close_fail_ts"] = time.time()

            # --- 2. evaluasi sinyal untuk entry baru (tiap poll_sec) ---
            if not self._stop and time.time() - last_eval >= poll_sec:
                last_eval = time.time()
                _tr, reject = self._maybe_open(symbol, qty)
                # risk menolak & tidak ada posisi: berhenti ( perilaku lama)
                if reject and not self.open_trades:
                    print(f"[{_now_wib()}] risk guard menolak: {reject} — "
                          f"berhenti.", flush=True)
                    self._emit(type="stopped", reason=reject)
                    break

            time.sleep(monitor_sec)

        # --- stop diminta: tutup semua posisi terbuka satu per satu ---
        for tr in list(self.open_trades):
            px = self._live_price(symbol) or tr["entry"]
            self._close_trade(tr, px, "manual-stop")

        print(f"[{_now_wib()}] selesai: {self.trades_done} trade, "
              f"total PnL {_fmt_pnl(self.total_pnl)}", flush=True)
        self._emit(type="finished", trades=self.trades_done,
                   total_pnl=self.total_pnl)
        return self.trades_done
