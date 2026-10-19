# Copyright (c) 2026 aziz-sudrajat <azizsudrajat.stb4@gmail.com>
# https://github.com/aziz-sudrajat
#
# All rights reserved. This file is part of the azzx-fast-scalping project.
# Unauthorized copying, modification, distribution, or false claim of
# authorship is prohibited.

"""🐵 MONKEY DESK — dashboard web untuk azzx-fast-scalping.

    python -m scalping.web_ui [--port 8080]

atau via env:  SCALP_WEBUI_PORT=8080 python -m scalping.web_ui

Buka http://127.0.0.1:8080 di browser. Fitur:
- Hero row: balance, P&L sesi, win rate, posisi terbuka
- Equity curve + grafik candle 5m (canvas, tanpa CDN)
- Open positions + trade log (auto-refresh 3 detik)
- Empat "desk tile" ala robot: Scanner / Signal Engine / Executor / Risk Guard
  dengan lampu status hijau/kuning/merah + timestamp aktivitas terakhir
- Kontrol: Start / Stop / Kill-switch, symbol, margin, lev, mode, exchange,
  strategy — mode live dilindungi banner merah + konfirmasi ketik SAYA PAHAM
  (ditegakkan di server, bukan cuma di browser)

Stdlib only (http.server + threading). Bind 127.0.0.1 saja.
Jalan offline — semua CSS/JS inline, tanpa CDN eksternal.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from .live_loop import (AlternatingTrader, _build_broker, _build_data,
                        _now_wib, fmt_price)
from . import config as cfg_mod
from .risk import RiskGuard

_lock = threading.Lock()

_state = {
    "trader": None,
    "thread": None,
    "running": False,
    "mode": "paper",
    "exchange": "binance",
    "strategy": "signal",
    "symbol": "BTCUSDT",
    "qty": 0.001,
    "margin": 100.0,
    "lev": 10,
    "data": None,          # klien data (untuk klines candle chart)
    "start_balance": None,
    "start_ts": None,
    "events": queue.Queue(maxsize=1000),
    "last": {},            # tick terakhir
    "log": [],             # baris trade log (maks 200)
    "closed": [],          # trade tertutup (maks 500): dict
    "equity": [],          # [{"t": ts_wib, "eq": pnl_kumulatif}]
    "engine_tile": {"status": "idle", "last_signal": None, "ts": None, "epoch": 0.0},
    "exec_tile": {"status": "idle", "last_order": None, "mode": "paper",
                  "ts": None, "epoch": 0.0},
    "scan_tile": {"status": "idle", "coins": [], "ts": None, "epoch": 0.0},
    "klines": [],          # [[o,h,l,c], ...] cache
    "klines_ts": 0.0,
    "klines_symbol": "",
}


def _tile(status, **kw):
    d = {"status": status, "ts": _now_wib(), "epoch": time.time()}
    d.update(kw)
    return d


def _emit(ev: dict):
    """Terima event dari trader, update queue SSE + state dashboard."""
    ev = dict(ev)
    ev.setdefault("ts", _now_wib())
    q = _state["events"]
    try:
        q.put_nowait(ev)
    except queue.Full:
        try:
            q.get_nowait()
        except Exception:
            pass
        try:
            q.put_nowait(ev)
        except Exception:
            pass
    with _lock:
        et = ev.get("type")
        if et == "trade_close":
            line = (f"{ev['time']} | {ev['side']} | Entry: {fmt_price(ev['entry'])} | "
                    f"TP: {fmt_price(ev['tp'])} | PnL: {ev['pnl']:+.2f} [{ev['why']}]")
            _state["log"].append(line)
            _state["log"] = _state["log"][-200:]
            _state["closed"].append({
                "id": ev.get("trade_id"),
                "time": ev.get("time"), "side": ev.get("side"),
                "symbol": ev.get("symbol", _state["symbol"]),
                "entry": ev.get("entry"), "tp": ev.get("tp"),
                "pnl": ev.get("pnl"), "why": ev.get("why"),
            })
            _state["closed"] = _state["closed"][-500:]
            _state["equity"].append(
                {"t": ev.get("time"), "eq": float(ev.get("total_pnl", 0.0))})
            _state["exec_tile"] = _tile(
                "running" if _state["running"] else "stopped",
                last_order={"kind": "close", "side": ev.get("side"),
                            "pnl": ev.get("pnl"), "why": ev.get("why"),
                            "time": ev.get("time")},
                mode=_state["mode"])
        elif et == "trade_open":
            _state["engine_tile"] = _tile(
                "open", last_signal={"side": ev.get("side"),
                                     "strength": ev.get("strength"),
                                     "reason": ev.get("reason"),
                                     "entry": ev.get("entry"),
                                     "time": ev.get("time")})
            _state["exec_tile"] = _tile(
                "running",
                last_order={"kind": "open", "side": ev.get("side"),
                            "qty": _state["qty"], "entry": ev.get("entry"),
                            "time": ev.get("time")},
                mode=_state["mode"])
        elif et == "tick":
            _state["last"] = ev
        elif et == "waiting":
            if _state["engine_tile"]["status"] not in ("open", "error"):
                _state["engine_tile"] = _tile("waiting")
        elif et == "started":
            _state["exec_tile"] = _tile("running", mode=_state["mode"])
            _state["engine_tile"] = _tile("waiting")
        elif et in ("stopped", "finished"):
            _state["exec_tile"] = _tile(
                "stopped", last_order=_state["exec_tile"].get("last_order"),
                mode=_state["mode"],
                note=ev.get("reason") or f"{ev.get('trades', 0)} trade")
            if _state["engine_tile"]["status"] == "open":
                _state["engine_tile"] = _tile("idle")
        elif et == "error":
            _state["exec_tile"] = _tile("error", mode=_state["mode"],
                                        note=str(ev.get("error", ""))[:120])
            _state["engine_tile"] = _tile("error",
                                          note=str(ev.get("error", ""))[:120])


# ------------------------------------------------------------------ scanner
def _scanner_loop():
    """Background: scan ringan top-coin tiap 60 detik untuk desk tile."""
    try:
        from . import scanner as sm
    except Exception:
        return
    while True:
        try:
            tickers = sm.fetch_tickers()
            cands = sm.pick_candidates(tickers, 2_000_000, 5)
            top = []
            for c in (cands or []):
                top.append({
                    "symbol": c.get("symbol"),
                    "quote_vol": c.get("quote_vol"),
                    "change_pct": round(float(c.get("change_pct", 0) or 0), 2),
                })
            with _lock:
                _state["scan_tile"] = _tile("ok", coins=top)
        except Exception as e:
            with _lock:
                _state["scan_tile"] = _tile("error", coins=[],
                                            note=str(e)[:120])
        time.sleep(60)


# ------------------------------------------------------------------ state
def _safe_float(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _get_balance():
    t = _state["trader"]
    if t is None:
        return None
    b = t.broker
    try:
        if hasattr(b, "balance") and callable(getattr(b, "balance")):
            return _safe_float(b.balance())
        if hasattr(b, "get_balance") and callable(getattr(b, "get_balance")):
            return _safe_float(b.get_balance())
    except Exception:
        return None
    return None


def _get_positions():
    t = _state["trader"]
    if t is None:
        return []
    mark = _safe_float((_state["last"] or {}).get("price"))
    trades = getattr(t, "open_trades", None)
    if trades is not None:
        # multi-position: tiap entry = baris terpisah (TP/SL sendiri)
        out = []
        for tr in trades:
            entry = _safe_float(tr.get("entry")) or 0.0
            qty = _safe_float(tr.get("qty")) or 0.0
            side = str(tr.get("side", "")).upper()
            upnl = None
            if mark and entry and qty:
                upnl = (mark - entry) * qty if side == "LONG" \
                    else (entry - mark) * qty
            out.append({"id": tr.get("id"),
                        "symbol": tr.get("symbol", _state["symbol"]),
                        "side": side, "qty": qty, "entry": entry,
                        "tp": tr.get("tp"), "sl": tr.get("sl"),
                        "mark": mark, "upnl": upnl})
        return out
    # fallback: trader tanpa registry (1 posisi via broker)
    sym = _state["symbol"]
    try:
        pos = t.broker.get_position(sym)
    except Exception:
        return []
    if not pos:
        return []
    side = str(pos.get("side", "")).upper()
    qty = _safe_float(pos.get("qty", pos.get("contracts", 0))) or 0.0
    entry = _safe_float(pos.get("avg_price",
                                pos.get("entry_price",
                                        pos.get("entryPrice", 0)))) or 0.0
    upnl = None
    if mark and entry and qty:
        upnl = (mark - entry) * qty if side in ("BUY", "LONG") \
            else (entry - mark) * qty
    return [{"symbol": sym, "side": side, "qty": qty, "entry": entry,
             "mark": mark, "upnl": upnl}]


def _get_klines():
    """Klines 5m untuk candle chart; cache 30 detik. Fail-soft -> []."""
    data = _state["data"]
    sym = _state["symbol"]
    if data is None:
        return []
    now = time.time()
    if sym == _state["klines_symbol"] and now - _state["klines_ts"] < 30 \
            and _state["klines"]:
        return _state["klines"]
    try:
        raw = data.get_klines(sym, "5m", limit=60) or []
        out = []
        for k in raw:
            o = _safe_float(k.get("open")); h = _safe_float(k.get("high"))
            l = _safe_float(k.get("low")); c = _safe_float(k.get("close"))
            if None not in (o, h, l, c):
                out.append([o, h, l, c])
        _state["klines"] = out
        _state["klines_ts"] = now
        _state["klines_symbol"] = sym
        return out
    except Exception:
        return _state["klines"]


def _risk_tile():
    t = _state["trader"]
    risk = t.risk if t is not None and hasattr(t, "risk") else None
    if risk is None:
        return _tile("idle", daily_pnl=0.0, killed=False, kill_reason="")
    killed = bool(risk.is_killed())
    dpnl = float(risk.daily_pnl())
    limit = float(getattr(risk, "max_daily_loss_usd", 100.0) or 100.0)
    reason = getattr(risk, "_kill_reason", "")
    if killed:
        status = "killed"
    elif dpnl <= -0.5 * limit:
        status = "warning"
    else:
        status = "ok"
    return _tile(status, daily_pnl=round(dpnl, 2), killed=killed,
                 kill_reason=reason, max_daily_loss=limit)


def _summary():
    with _lock:
        closed = list(_state["closed"])
        equity = list(_state["equity"])
        engine_tile = dict(_state["engine_tile"])
        exec_tile = dict(_state["exec_tile"])
        scan_tile = dict(_state["scan_tile"])
        running = _state["running"]
        mode = _state["mode"]
    t = _state["trader"]
    trades_done = getattr(t, "trades_done", 0) if t else 0
    wins = getattr(t, "wins", 0) if t else 0
    total_pnl = getattr(t, "total_pnl", 0.0) if t else 0.0
    balance = _get_balance()
    start_bal = _state["start_balance"]
    pnl_pct = (total_pnl / start_bal * 100.0) if start_bal else None
    positions = _get_positions()
    klines = _get_klines()
    price = _safe_float((_state["last"] or {}).get("price"))
    risk_mp = 0
    if t is not None and hasattr(t, "risk"):
        try:
            risk_mp = int(getattr(t.risk, "max_positions", 0) or 0)
        except (TypeError, ValueError):
            risk_mp = 0
    return {
        "mode": mode,
        "exchange": _state["exchange"],
        "strategy": _state["strategy"],
        "symbol": _state["symbol"],
        "running": running,
        "balance": balance,
        "start_balance": start_bal,
        "session_pnl": round(float(total_pnl), 2),
        "session_pnl_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
        "trades": trades_done,
        "wins": wins,
        "win_rate": round(wins / trades_done * 100.0, 1) if trades_done else None,
        "open_positions": len(positions),
        "positions": positions,
        "max_positions": risk_mp,
        "price": price,
        "klines": klines,
        "equity": equity,
        "closed_count": len(closed),
        "desks": {
            "scanner": scan_tile,
            "engine": engine_tile,
            "executor": exec_tile,
            "risk": _risk_tile(),
        },
        "updated": _now_wib(),
    }


# ------------------------------------------------------------------ http
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _start_trader(self, symbol, margin, lev, margin_mode, mode,
                      exchange="binance", strategy="signal", max_trades=0,
                      confirm="", max_positions=None):
        if _state["running"]:
            return {"ok": False, "msg": "sudah berjalan",
                    "mode": _state["mode"]}
        # ---- GUARD LIVE: wajib ketik SAYA PAHAM (server-side, tidak bisa
        # ---- dilewati dari browser) ----
        if mode == "live" and confirm != "SAYA PAHAM":
            return {"ok": False,
                    "msg": "mode live butuh konfirmasi ketik SAYA PAHAM",
                    "mode": mode}
        cfg = cfg_mod.load()
        cfg["MODE"] = mode
        broker = _build_broker(mode, exchange)  # SystemExit jika key kurang
        data = _build_data(mode, exchange)
        if mode in ("testnet", "live"):
            mt = "ISOLATED" if margin_mode == "isolated" else "CROSSED"
            try:
                broker.set_margin_type(symbol, mt)
            except Exception as e:
                return {"ok": False, "msg": f"set margin gagal: {e}"}
            try:
                broker.set_leverage(symbol, lev)
            except Exception as e:
                return {"ok": False, "msg": f"set leverage gagal: {e}"}
        price = None
        getter = getattr(data, "get_ticker_price", None)
        if callable(getter):
            try:
                price = float(getter(symbol))
            except Exception:
                pass
        if not price:
            kl = data.get_klines(symbol, "1m", limit=2)
            price = float(kl[-1]["close"]) if kl else 0
        if price <= 0:
            return {"ok": False, "msg": "harga tidak didapat"}
        qty = margin * lev / price
        if hasattr(broker, "get_qty_step"):
            from .exchanges.base import quantize_down
            qty = quantize_down(qty, broker.get_qty_step(symbol))
        if qty <= 0:
            return {"ok": False, "msg": "qty 0 setelah quantize"}
        if max_positions in (None, ""):
            mp = int(cfg.get("MAX_POSITIONS", 0))
        else:
            mp = int(max_positions)
        if mp < 0:
            return {"ok": False,
                    "msg": "maxpositions tidak boleh negatif (0 = tanpa batas)"}
        risk = RiskGuard(
            max_daily_loss_usd=float(cfg.get("MAX_DAILY_LOSS_USD", 100.0)),
            max_positions=mp,
            cooldown_sec=float(cfg.get("COOLDOWN_SEC", 300.0)))
        if strategy == "signal":
            from .signal_loop import SignalTrader
            trader = SignalTrader(broker, data, cfg_mod, risk,
                                  on_event=_emit, mode=mode, leverage=lev)
        else:
            trader = AlternatingTrader(broker, data, cfg, risk, on_event=_emit)
        start_bal = None
        try:
            if hasattr(broker, "balance"):
                start_bal = float(broker.balance())
        except Exception:
            start_bal = None
        with _lock:
            _state.update(trader=trader, running=True, mode=mode,
                          symbol=symbol, qty=qty, exchange=exchange,
                          strategy=strategy, margin=margin, lev=lev,
                          data=data, start_balance=start_bal,
                          start_ts=_now_wib())
            _state["closed"] = []
            _state["equity"] = []
            _state["engine_tile"] = _tile("idle")
            _state["exec_tile"] = _tile("idle", mode=mode)
            _state["klines"] = []
            _state["klines_symbol"] = ""
        t = threading.Thread(target=_run_trader,
                             args=(trader, symbol, qty, max_trades),
                             daemon=True)
        _state["thread"] = t
        t.start()
        return {"ok": True,
                "msg": f"mulai {symbol} {strategy} margin={margin} lev={lev}x qty={qty:.6f}",
                "mode": mode}

    def do_GET(self):
        p = urlparse(self.path)
        if p.path == "/":
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif p.path == "/api/state":
            self._json(_summary())
        elif p.path == "/api/trades":
            with _lock:
                self._json({"trades": list(_state["closed"])})
        elif p.path == "/api/positions":
            self._json({"positions": _get_positions()})
        elif p.path == "/api/equity":
            with _lock:
                self._json({"equity": list(_state["equity"])})
        elif p.path == "/api/log":
            with _lock:
                self._json(list(_state["log"]))
        elif p.path == "/api/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                while True:
                    try:
                        ev = _state["events"].get(timeout=20)
                        msg = f"data: {json.dumps(ev)}\n\n".encode()
                    except queue.Empty:
                        msg = b": ping\n\n"
                    self.wfile.write(msg)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        p = urlparse(self.path)
        q = parse_qs(p.query)
        g = lambda k, d="": q.get(k, [d])[0]
        if p.path == "/api/start":
            try:
                r = self._start_trader(
                    g("symbol", "BTCUSDT").upper(),
                    float(g("margin", "100") or "100"),
                    int(g("lev", "10") or "10"),
                    g("marginmode", "isolated"),
                    g("mode", "paper"),
                    g("exchange", "binance"),
                    g("strategy", "signal"),
                    int(g("maxtrades", "0") or "0"),
                    g("confirm", ""),
                    max_positions=g("maxpositions", ""))
                self._json(r)
            except SystemExit as e:
                self._json({"ok": False, "msg": str(e)}, 400)
            except Exception as e:
                self._json({"ok": False, "msg": f"error: {e}"}, 500)
        elif p.path == "/api/stop":
            t = _state.get("trader")
            if t:
                t.stop()
            _state["running"] = False
            self._json({"ok": True, "msg": "stop diminta — posisi terbuka "
                                           "diselesaikan dulu",
                        "mode": _state["mode"]})
        elif p.path == "/api/kill":
            t = _state.get("trader")
            if t and hasattr(t, "risk"):
                t.risk.kill("kill-switch dari dashboard")
                t.stop()
            _state["running"] = False
            self._json({"ok": True, "msg": "KILL-SWITCH aktif",
                        "mode": _state["mode"]})
        else:
            self._json({"error": "not found"}, 404)


def _run_trader(trader, symbol, qty, max_trades=0):
    try:
        from .signal_loop import SignalTrader
        if isinstance(trader, SignalTrader):
            trader.run(symbol, qty, max_trades=max_trades)
        else:
            trader.run(symbol, qty, max_trades=max_trades, poll_sec=3.0)
    except Exception as e:
        _emit(type="error", error=str(e))
    finally:
        _state["running"] = False


PAGE = """<!DOCTYPE html>
<html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>&#x1F435; MONKEY DESK &mdash; azzx-fast-scalping</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#07080f;color:#e8ecf5;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;min-height:100vh;
background-image:radial-gradient(1200px 500px at 20% -10%,rgba(0,229,255,.07),transparent),radial-gradient(1000px 500px at 90% 0%,rgba(255,45,149,.06),transparent)}
header{display:flex;align-items:center;gap:12px;padding:14px 22px;border-bottom:1px solid #1b2138;background:rgba(10,12,22,.9);position:sticky;top:0;z-index:10;backdrop-filter:blur(6px)}
.dot{width:13px;height:13px;border-radius:50%;background:#00e676;box-shadow:0 0 10px #00e676;animation:pulse 1.2s infinite}
.dot.off{background:#5a6378;box-shadow:none;animation:none}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
h1{font-size:19px;letter-spacing:2px;background:linear-gradient(90deg,#00e5ff,#ff2d95,#ffd60a);-webkit-background-clip:text;background-clip:text;color:transparent;font-weight:800}
.badge{font-size:11px;padding:4px 12px;border-radius:20px;background:#131829;border:1px solid #2a3352;text-transform:uppercase;letter-spacing:1px}
.badge.paper{color:#00e676;border-color:#00e67655;box-shadow:0 0 10px #00e67633}
.badge.testnet{color:#ffd60a;border-color:#ffd60a55;box-shadow:0 0 10px #ffd60a33}
.badge.live{color:#ff5252;border-color:#ff525255;box-shadow:0 0 10px #ff525233;animation:blink 1s infinite}
@keyframes blink{50%{opacity:.6}}
main{padding:16px;max-width:1280px;margin:0 auto}
.hero{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:12px}
.herocard{background:linear-gradient(160deg,#101527,#0a0d1a);border:1px solid #232c4d;border-radius:14px;padding:16px;box-shadow:0 4px 24px rgba(0,0,0,.45)}
.hlabel{font-size:11px;color:#7b86a8;letter-spacing:2px;margin-bottom:6px}
.hbig{font-size:34px;font-weight:800;text-shadow:0 0 18px currentColor}
.hsub{font-size:12px;color:#7b86a8;margin-top:6px}
.hbig.pos{color:#00e676}.hbig.neg{color:#ff5252}.hbig.neu{color:#00e5ff}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px}
.card{background:#0b0e1a;border:1px solid #1c2440;border-radius:14px;padding:14px;margin-bottom:12px;box-shadow:0 4px 24px rgba(0,0,0,.4)}
.card h2{font-size:12px;color:#8b96bd;margin-bottom:10px;text-transform:uppercase;letter-spacing:2px}
canvas{width:100%;height:220px;background:#05060d;border:1px solid #1c2440;border-radius:10px}
.row{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px dashed #1c2440;font-size:13px}
.row:last-child{border:none}
.log{background:#04050b;border:1px solid #1c2440;border-radius:10px;padding:10px;height:300px;overflow-y:auto;font-size:12.5px;line-height:1.8}
.log .win{color:#00e676;font-weight:700;text-shadow:0 0 12px rgba(0,230,118,.4)}
.log .loss{color:#ff5252;font-weight:700;text-shadow:0 0 12px rgba(255,82,82,.4)}
.log .trow{padding:5px 10px;border-radius:8px;margin:3px 0}
.log .trow-win{background:rgba(0,230,118,.07);border-left:3px solid #00e676}
.log .trow-loss{background:rgba(255,82,82,.07);border-left:3px solid #ff5252}
.log .info{color:#7b86a8}
.desks{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:12px}
.desk{position:relative;overflow:hidden}
.desk::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,#00e5ff,#ff2d95)}
.deskhead{display:flex;align-items:center;gap:8px;margin-bottom:8px}
.lamp{width:11px;height:11px;border-radius:50%;flex-shrink:0}
.lamp.ok{background:#00e676;box-shadow:0 0 10px #00e676;animation:pulse 1.4s infinite}
.lamp.warn{background:#ffd60a;box-shadow:0 0 10px #ffd60a}
.lamp.err{background:#ff5252;box-shadow:0 0 10px #ff5252;animation:blink .8s infinite}
.lamp.idle{background:#5a6378}
.deskb{font-size:12.5px;line-height:1.7;color:#c3cbE4}
.deskb .ts{color:#5f6a8c;font-size:11px}
.botwrap{position:relative;margin-left:auto;width:46px;height:46px;flex-shrink:0}
.botwrap .radar{display:none;position:absolute;inset:-7px;border-radius:50%;background:conic-gradient(from 0deg,rgba(0,229,255,.4),transparent 28%);animation:radar-spin 2.2s linear infinite;pointer-events:none}
.botwrap.scanning .radar{display:block}
.botwrap.sleeping::after{content:'\1F4A4';position:absolute;top:-8px;right:-4px;font-size:14px;animation:zzz 2.6s ease-in infinite;pointer-events:none}
.bot{display:block;width:46px;height:46px}
.bot .p{transform-box:fill-box;transform-origin:center}
.bot-eye{animation:bot-blink 4.2s infinite}
.bot-ant{animation:ant-pulse 2s ease-in-out infinite}
.bot-idle{animation:bot-breathe 3.2s ease-in-out infinite}
.bot-walk{animation:bot-walk 1.7s ease-in-out infinite}
.bot-work{animation:bot-nod 1.1s ease-in-out infinite}
.bot-jump{animation:bot-jump .7s ease-in-out 4}
.bot-alert{--ac:#ff5252!important;animation:bot-shake .32s linear infinite}
.bot-sleep{animation:bot-sleep 3s ease-in-out infinite}
@keyframes bot-blink{0%,92%,100%{transform:scaleY(1)}95%{transform:scaleY(.12)}}
@keyframes ant-pulse{0%,100%{opacity:1}50%{opacity:.3}}
@keyframes bot-breathe{0%,100%{transform:scaleY(1)}50%{transform:scaleY(1.06)}}
@keyframes bot-walk{0%,100%{transform:translateX(-7px) rotate(-2deg)}50%{transform:translateX(7px) rotate(2deg)}}
@keyframes bot-nod{0%,100%{transform:rotate(-8deg)}50%{transform:rotate(8deg)}}
@keyframes bot-jump{0%,100%{transform:translateY(0)}40%{transform:translateY(-9px)}}
@keyframes bot-shake{0%,100%{transform:translateX(0)}25%{transform:translateX(-2.5px) rotate(-3deg)}75%{transform:translateX(2.5px) rotate(3deg)}}
@keyframes bot-sleep{0%,100%{transform:rotate(-5deg) translateY(0)}50%{transform:rotate(-5deg) translateY(2.5px)}}
@keyframes radar-spin{to{transform:rotate(360deg)}}
@keyframes zzz{0%{opacity:0;transform:translateY(5px) scale(.7)}30%{opacity:1}100%{opacity:0;transform:translateY(-11px) scale(1.15)}}
.controls{display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end}
button{font-family:inherit;font-size:13px;font-weight:700;padding:10px 20px;border-radius:10px;border:1px solid #2a3352;background:#161c33;color:#e8ecf5;cursor:pointer;transition:.15s}
button:hover{transform:translateY(-1px);background:#1d2440}
button.go{background:linear-gradient(135deg,#00b35c,#00e676);border:none;color:#04120a;box-shadow:0 0 18px rgba(0,230,118,.35)}
button.stopb{background:linear-gradient(135deg,#a02020,#ff5252);border:none;color:#fff;box-shadow:0 0 18px rgba(255,82,82,.3)}
button:disabled{opacity:.35;cursor:default;transform:none}
input,select{font-family:inherit;font-size:13px;padding:9px;border-radius:10px;border:1px solid #2a3352;background:#04050b;color:#e8ecf5}
label{font-size:12px;color:#8b96bd;display:flex;flex-direction:column;gap:4px}
#livewarn{display:none;background:linear-gradient(135deg,#7f1d1d,#b91c1c);border:2px solid #ff5252;border-radius:10px;padding:12px;margin:10px 0;color:#fff;font-weight:800;animation:blink 1s infinite;font-size:14px}
#liveconfirm{display:none;margin:10px 0}
#status{font-size:13px;color:#8b96bd;margin-top:10px}
table.pos{width:100%;border-collapse:collapse;font-size:13px}
table.pos th{color:#7b86a8;text-align:left;padding:6px;font-size:11px;letter-spacing:1px;text-transform:uppercase}
table.pos td{padding:8px 6px;border-top:1px solid #1c2440}
.up{color:#00e676}.dn{color:#ff5252}
@keyframes trow-in{0%{opacity:0;transform:translateX(-16px);background:rgba(0,229,255,.20);box-shadow:0 0 16px rgba(0,229,255,.45)}55%{opacity:1;background:rgba(0,229,255,.06)}100%{opacity:1;transform:none;background:transparent;box-shadow:none}}
.log .trow-new{animation:trow-in 1.8s ease-out}
@keyframes shimmer{0%,100%{opacity:.4}50%{opacity:1}}
.log .info{animation:shimmer 2.4s ease-in-out infinite}
@keyframes upnl-up{0%{background:rgba(0,230,118,.4)}100%{background:transparent}}
@keyframes upnl-dn{0%{background:rgba(255,82,82,.4)}100%{background:transparent}}
td.flash-up{animation:upnl-up 1.1s ease-out}
td.flash-dn{animation:upnl-dn 1.1s ease-out}
@media(max-width:900px){.hero{grid-template-columns:1fr 1fr}.grid2{grid-template-columns:1fr}.desks{grid-template-columns:1fr 1fr}}
@media(max-width:560px){.hero{grid-template-columns:1fr}.desks{grid-template-columns:1fr}}
</style></head><body>
<header>
<div class="dot off" id="livedot"></div>
<h1>&#x1F435; MONKEY DESK</h1>
<span class="badge paper" id="modebadge">paper</span>
<span class="badge" id="excbadge">binance</span>
<span style="margin-left:auto;font-size:12px;color:#8b96bd" id="clock"></span>
</header>
<main>
<section class="hero">
<div class="herocard"><div class="hlabel">BALANCE</div><div class="hbig neu" id="balance">&mdash;</div><div class="hsub" id="balmode">paper</div></div>
<div class="herocard"><div class="hlabel">P&amp;L SESI</div><div class="hbig" id="pnl">+0.00</div><div class="hsub" id="pnlpct">0.00%</div></div>
<div class="herocard"><div class="hlabel">WIN RATE</div><div class="hbig neu" id="winrate">&mdash;</div><div class="hsub" id="tradecount">0 trade</div></div>
<div class="herocard"><div class="hlabel">POSISI TERBUKA</div><div class="hbig neu" id="npos">0</div><div class="hsub" id="posupnl">uPnL &mdash;</div></div>
</section>
<div class="grid2">
<div class="card"><h2>&#x1F4C8; Equity Curve (sesi)</h2><canvas id="eqchart" width="560" height="220"></canvas></div>
<div class="card"><h2>&#x1F56F;&#xFE0F; <span id="klsym">BTCUSDT</span> &mdash; 5m</h2><canvas id="klchart" width="560" height="220"></canvas></div>
</div>
<div class="card"><h2>&#x1F4C2; Open Positions</h2><div id="positions"><span class="info" style="color:#5f6a8c">tidak ada posisi</span></div></div>
<div class="card"><h2>&#x1F9FE; Trade Log</h2><div class="log" id="tradelog"><div class="info">menunggu trade&hellip;</div></div></div>
<section class="desks">
<div class="card desk"><div class="deskhead"><div class="lamp idle" id="lamp-scanner"></div><h2>&#x1F4E1; Scanner</h2><span class="botwrap" id="wrap-scanner"><span class="radar"></span><svg class="bot bot-idle" id="bot-scanner" viewBox="0 0 48 48" style="--ac:#00e5ff"><line x1="24" y1="5" x2="24" y2="11" stroke="var(--ac)" stroke-width="2"/><circle class="p bot-ant" cx="24" cy="4.5" r="2.6" fill="var(--ac)"/><rect x="13" y="11" width="22" height="14" rx="6" fill="#1a2140" stroke="var(--ac)" stroke-width="1.8"/><circle class="p bot-eye" cx="20" cy="18" r="2.4" fill="var(--ac)"/><circle class="p bot-eye" cx="28" cy="18" r="2.4" fill="var(--ac)"/><rect x="17" y="27" width="14" height="11" rx="4" fill="#1a2140" stroke="var(--ac)" stroke-width="1.8"/><line x1="16" y1="29" x2="10.5" y2="33" stroke="var(--ac)" stroke-width="2.6" stroke-linecap="round"/><line x1="32" y1="29" x2="37.5" y2="33" stroke="var(--ac)" stroke-width="2.6" stroke-linecap="round"/><rect x="18" y="40" width="5" height="5" rx="2" fill="var(--ac)"/><rect x="25" y="40" width="5" height="5" rx="2" fill="var(--ac)"/></svg></span></div><div class="deskb" id="desk-scanner">memuat&hellip;</div></div>
<div class="card desk"><div class="deskhead"><div class="lamp idle" id="lamp-engine"></div><h2>&#x2699;&#xFE0F; Signal Engine</h2><span class="botwrap" id="wrap-engine"><span class="radar"></span><svg class="bot bot-idle" id="bot-engine" viewBox="0 0 48 48" style="--ac:#b388ff"><line x1="24" y1="5" x2="24" y2="11" stroke="var(--ac)" stroke-width="2"/><circle class="p bot-ant" cx="24" cy="4.5" r="2.6" fill="var(--ac)"/><rect x="13" y="11" width="22" height="14" rx="6" fill="#1a2140" stroke="var(--ac)" stroke-width="1.8"/><circle class="p bot-eye" cx="20" cy="18" r="2.4" fill="var(--ac)"/><circle class="p bot-eye" cx="28" cy="18" r="2.4" fill="var(--ac)"/><rect x="17" y="27" width="14" height="11" rx="4" fill="#1a2140" stroke="var(--ac)" stroke-width="1.8"/><line x1="16" y1="29" x2="10.5" y2="33" stroke="var(--ac)" stroke-width="2.6" stroke-linecap="round"/><line x1="32" y1="29" x2="37.5" y2="33" stroke="var(--ac)" stroke-width="2.6" stroke-linecap="round"/><rect x="18" y="40" width="5" height="5" rx="2" fill="var(--ac)"/><rect x="25" y="40" width="5" height="5" rx="2" fill="var(--ac)"/></svg></span></div><div class="deskb" id="desk-engine">idle</div></div>
<div class="card desk"><div class="deskhead"><div class="lamp idle" id="lamp-exec"></div><h2>&#x1F680; Executor</h2><span class="botwrap" id="wrap-exec"><span class="radar"></span><svg class="bot bot-idle" id="bot-exec" viewBox="0 0 48 48" style="--ac:#00e676"><line x1="24" y1="5" x2="24" y2="11" stroke="var(--ac)" stroke-width="2"/><circle class="p bot-ant" cx="24" cy="4.5" r="2.6" fill="var(--ac)"/><rect x="13" y="11" width="22" height="14" rx="6" fill="#1a2140" stroke="var(--ac)" stroke-width="1.8"/><circle class="p bot-eye" cx="20" cy="18" r="2.4" fill="var(--ac)"/><circle class="p bot-eye" cx="28" cy="18" r="2.4" fill="var(--ac)"/><rect x="17" y="27" width="14" height="11" rx="4" fill="#1a2140" stroke="var(--ac)" stroke-width="1.8"/><line x1="16" y1="29" x2="10.5" y2="33" stroke="var(--ac)" stroke-width="2.6" stroke-linecap="round"/><line x1="32" y1="29" x2="37.5" y2="33" stroke="var(--ac)" stroke-width="2.6" stroke-linecap="round"/><rect x="18" y="40" width="5" height="5" rx="2" fill="var(--ac)"/><rect x="25" y="40" width="5" height="5" rx="2" fill="var(--ac)"/></svg></span></div><div class="deskb" id="desk-exec">idle</div></div>
<div class="card desk"><div class="deskhead"><div class="lamp idle" id="lamp-risk"></div><h2>&#x1F6E1;&#xFE0F; Risk Guard</h2><span class="botwrap" id="wrap-risk"><span class="radar"></span><svg class="bot bot-sleep" id="bot-risk" viewBox="0 0 48 48" style="--ac:#ffd60a"><line x1="24" y1="5" x2="24" y2="11" stroke="var(--ac)" stroke-width="2"/><circle class="p bot-ant" cx="24" cy="4.5" r="2.6" fill="var(--ac)"/><rect x="13" y="11" width="22" height="14" rx="6" fill="#1a2140" stroke="var(--ac)" stroke-width="1.8"/><circle class="p bot-eye" cx="20" cy="18" r="2.4" fill="var(--ac)"/><circle class="p bot-eye" cx="28" cy="18" r="2.4" fill="var(--ac)"/><rect x="17" y="27" width="14" height="11" rx="4" fill="#1a2140" stroke="var(--ac)" stroke-width="1.8"/><line x1="16" y1="29" x2="10.5" y2="33" stroke="var(--ac)" stroke-width="2.6" stroke-linecap="round"/><line x1="32" y1="29" x2="37.5" y2="33" stroke="var(--ac)" stroke-width="2.6" stroke-linecap="round"/><rect x="18" y="40" width="5" height="5" rx="2" fill="var(--ac)"/><rect x="25" y="40" width="5" height="5" rx="2" fill="var(--ac)"/></svg></span></div><div class="deskb" id="desk-risk">idle</div></div>
</section>
<div class="card"><h2>&#x1F39B;&#xFE0F; Kontrol</h2>
<div class="controls">
<label>Symbol<input id="symbol" value="BTCUSDT" size="10"></label>
<label>Margin<input id="margin" value="100" size="6"></label>
<label>Lev<input id="lev" value="10" size="4"></label>
<label>Margin mode<select id="marginmode"><option value="isolated">isolated</option><option value="cross">cross</option></select></label>
<label>Mode<select id="mode" onchange="modeChanged()"><option value="paper">paper</option><option value="testnet">testnet</option><option value="live">live</option></select></label>
<label>Exch<select id="exchange"><option value="binance">binance</option><option value="okx">okx</option><option value="bybit">bybit</option><option value="bitget">bitget</option></select></label>
<label>Strategy<select id="strategy"><option value="signal">signal</option><option value="alternating">alternating</option></select></label>
<label>MaxTr<input id="maxtrades" value="0" size="4"></label>
<label>MaxPos<input id="maxpos" value="0" size="4" title="0 = tanpa batas"></label>
</div>
<div id="livewarn">&#x26A0;&#xFE0F; MODE LIVE &mdash; UANG ASLI! Ketik <code>SAYA PAHAM</code> di bawah untuk mengaktifkan Start.</div>
<div id="liveconfirm"><label>Ketik SAYA PAHAM: <input id="confirmtxt" size="14" oninput="modeChanged()"></label></div>
<div class="controls">
<button class="go" id="bstart" onclick="ctl('start')">&#x25B6; Start</button>
<button class="stopb" id="bstop" onclick="ctl('stop')">&#x23F8; Stop</button>
<button class="stopb" onclick="if(confirm('KILL-SWITCH? Hentikan semua &amp; cegah trade baru.'))ctl('kill')">&#x2620;&#xFE0F; Kill-switch</button>
</div>
<div id="status">idle &mdash; tekan Start untuk mulai.</div>
<div style="font-size:11px;color:#5f6a8c;margin-top:8px">Testnet/live butuh API key di environment server. Mode live butuh konfirmasi ketik <b>SAYA PAHAM</b> (dicek server).</div>
</div>
</main>
<script>
function fp(x){x=Number(x);const a=Math.abs(x);
 if(a>=100)return x.toFixed(2); if(a>=10)return x.toFixed(3);
 if(a>=1)return x.toFixed(4); if(a>=0.1)return x.toFixed(5);
 if(a>=0.01)return x.toFixed(6); return x.toFixed(8);}
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;');}
function setLamp(id,st){const el=document.getElementById(id);
 el.className='lamp '+(st==='ok'?'ok':st==='warn'?'warn':st==='err'?'err':'idle');}
function lampFor(status){return status==='ok'||status==='running'||status==='open'?'ok':
 status==='waiting'||status==='warning'||status==='stopped'?'warn':
 status==='error'||status==='killed'?'err':'idle';}
const eqc=document.getElementById('eqchart'),klc=document.getElementById('klchart');
function drawEquity(eq){
 const c=eqc.getContext('2d'),W=eqc.width,H=eqc.height;
 c.clearRect(0,0,W,H);
 c.strokeStyle='#1c2440';c.beginPath();c.moveTo(0,H/2);c.lineTo(W,H/2);c.stroke();
 const pts=[{eq:0}].concat(eq||[]);
 if(pts.length<2){c.fillStyle='#5f6a8c';c.font='12px monospace';c.fillText('belum ada trade tertutup',14,H/2-8);return;}
 let mn=0,mx=0;pts.forEach(p=>{mn=Math.min(mn,p.eq);mx=Math.max(mx,p.eq);});
 const rg=(mx-mn)||1,pad=14;
 const X=i=>pad+i*(W-2*pad)/(pts.length-1),Y=v=>H-pad-((v-mn)/rg)*(H-2*pad);
 const last=pts[pts.length-1].eq,col=last>=0?'#00e676':'#ff5252';
 const gr=c.createLinearGradient(0,0,0,H);gr.addColorStop(0,last>=0?'rgba(0,230,118,.25)':'rgba(255,82,82,.25)');gr.addColorStop(1,'rgba(0,0,0,0)');
 c.beginPath();pts.forEach((p,i)=>{i?c.lineTo(X(i),Y(p.eq)):c.moveTo(X(i),Y(p.eq));});
 c.strokeStyle=col;c.lineWidth=2;c.stroke();
 c.lineTo(X(pts.length-1),H);c.lineTo(X(0),H);c.closePath();c.fillStyle=gr;c.fill();
 pts.forEach((p,i)=>{c.fillStyle=col;c.beginPath();c.arc(X(i),Y(p.eq),3,0,7);c.fill();});
 c.fillStyle=col;c.font='bold 13px monospace';c.fillText((last>=0?'+':'')+last.toFixed(2)+' USDT',W-110,20);
}
function drawKlines(kl){
 const c=klc.getContext('2d'),W=klc.width,H=klc.height;
 c.clearRect(0,0,W,H);
 if(!kl||!kl.length){c.fillStyle='#5f6a8c';c.font='12px monospace';c.fillText('memuat klines…',14,H/2);return;}
 let mn=1e18,mx=-1e18;kl.forEach(k=>{mn=Math.min(mn,k[2]);mx=Math.max(mx,k[1]);});
 const rg=(mx-mn)||1,pad=10,cw=(W-2*pad)/kl.length;
 const Y=v=>H-pad-((v-mn)/rg)*(H-2*pad);
 kl.forEach((k,i)=>{
  const o=k[0],h=k[1],l=k[2],cl=k[3],up=cl>=o,col=up?'#00e676':'#ff5252';
  const x=pad+i*cw+cw/2;
  c.strokeStyle=col;c.fillStyle=col;c.lineWidth=1;
  c.beginPath();c.moveTo(x,Y(h));c.lineTo(x,Y(l));c.stroke();
  const y1=Y(Math.max(o,cl)),y2=Y(Math.min(o,cl));
  c.fillRect(x-Math.max(2,cw*0.32)/2,y1,Math.max(2,cw*0.32),Math.max(1.5,y2-y1));
 });
 c.fillStyle='#8b96bd';c.font='11px monospace';
 c.fillText(mx.toFixed(2),6,12);c.fillText(mn.toFixed(2),6,H-6);
}
const seenTrades=new Set();const prevUpnl={};
function render(){
 fetch('/api/state').then(r=>r.json()).then(d=>{
  const b=document.getElementById('modebadge');b.textContent=d.mode;b.className='badge '+d.mode;
  document.getElementById('excbadge').textContent=d.exchange;
  document.getElementById('livedot').className='dot'+(d.running?'':' off');
  document.getElementById('klsym').textContent=d.symbol;
  const bal=document.getElementById('balance');
  bal.textContent=d.balance==null?'\u2014':fp(d.balance)+' USDT';
  const pnl=document.getElementById('pnl'),pv=d.session_pnl||0;
  pnl.textContent=(pv>=0?'+':'')+pv.toFixed(2);
  pnl.className='hbig '+(pv>0?'pos':pv<0?'neg':'neu');
  document.getElementById('pnlpct').textContent=d.session_pnl_pct==null?'\u2014':(pv>=0?'+':'')+d.session_pnl_pct.toFixed(2)+'%';
  document.getElementById('winrate').textContent=d.win_rate==null?'\u2014':d.win_rate.toFixed(1)+'%';
  document.getElementById('tradecount').textContent=d.trades+' trade ('+d.wins+' win)';
  document.getElementById('npos').textContent=d.open_positions;
  let upnlTot=0,hasU=false;
  (d.positions||[]).forEach(p=>{if(p.upnl!=null){upnlTot+=p.upnl;hasU=true;}});
  document.getElementById('posupnl').textContent=hasU?('uPnL '+(upnlTot>=0?'+':'')+upnlTot.toFixed(2)):'uPnL \u2014';
  drawEquity(d.equity);drawKlines(d.klines);
  const pos=document.getElementById('positions');
  if(d.positions&&d.positions.length){
   let h='<table class="pos"><tr><th>#</th><th>Symbol</th><th>Side</th><th>Qty</th><th>Entry</th><th>TP</th><th>SL</th><th>Mark</th><th>uPnL</th></tr>';
   d.positions.forEach(p=>{
    const up=p.upnl==null?'\u2014':(p.upnl>=0?'+':'')+p.upnl.toFixed(2);
    const pid='p'+(p.id==null?(p.symbol+':'+p.entry):p.id);
    const prev=prevUpnl[pid];let fcls='';
    if(prev!=null&&p.upnl!=null&&p.upnl!==prev)fcls=p.upnl>prev?'flash-up':'flash-dn';
    if(p.upnl!=null)prevUpnl[pid]=p.upnl;
    h+='<tr><td style="color:#5f6a8c">'+(p.id==null?'':p.id)+'</td><td>'+esc(p.symbol)+'</td><td class="'+(p.side==='BUY'||p.side==='LONG'?'up':'dn')+'">'+esc(p.side)+'</td><td>'+p.qty+'</td><td>'+fp(p.entry)+'</td><td class="up">'+(p.tp==null?'\u2014':fp(p.tp))+'</td><td class="dn">'+(p.sl==null?'\u2014':fp(p.sl))+'</td><td>'+(p.mark==null?'\u2014':fp(p.mark))+'</td><td class="'+(p.upnl!=null&&p.upnl<0?'dn':'up')+' '+fcls+'">'+up+'</td></tr>';});
   pos.innerHTML=h+'</table>';
  }else{pos.innerHTML='<span style="color:#5f6a8c">tidak ada posisi</span>';for(const k in prevUpnl)delete prevUpnl[k];}
  // trade log: pakai /api/trades agar lengkap
  fetch('/api/trades').then(r=>r.json()).then(t=>{
   const lg=document.getElementById('tradelog');
   const arr=(t.trades||[]).slice(-40).reverse();
   if(!arr.length){lg.innerHTML='<div class="info">menunggu trade&hellip;</div>';return;}
   const prevN=Number(lg.dataset.n||0);
   lg.innerHTML='';
   arr.forEach(tr=>{
    const win=tr.pnl>=0,dv=document.createElement('div');
    const tid=tr.id==null?('k'+tr.time+'|'+tr.entry):('id'+tr.id);
    dv.className='trow '+(win?'trow-win':'trow-loss')+(seenTrades.has(tid)?'':' trow-new');
    seenTrades.add(tid);
    dv.innerHTML='<span class="'+(win?'win':'loss')+'">'+(win?'\u25B2':'\u25BC')+' '+esc(tr.time)+' | '+esc(tr.side)+' '+esc(tr.symbol||'')+' | Entry '+fp(tr.entry)+' | PnL '+(tr.pnl>=0?'+':'')+Number(tr.pnl).toFixed(2)+' ['+esc(tr.why||'')+']</span>';
    lg.appendChild(dv);});
   lg.dataset.n=arr.length;
   if(arr.length>prevN)lg.scrollTop=0;
  });
function tileAge(t){return (t&&t.epoch)?(Date.now()/1000-t.epoch):1e9;}
function setBot(svgId,wrapId,cls,extra){
 var s=document.getElementById(svgId);if(!s)return;
 s.setAttribute('class','bot '+cls);
 var w=document.getElementById(wrapId);if(!w)return;
 w.classList.toggle('scanning',!!(extra&&extra.scanning));
 w.classList.toggle('sleeping',cls==='bot-sleep');
}
  // desk tiles
  const dk=d.desks||{};
  const sc=dk.scanner||{},en=dk.engine||{},ex=dk.executor||{},rk=dk.risk||{};
  setLamp('lamp-scanner',lampFor(sc.status));
  setBot('bot-scanner','wrap-scanner',sc.status==='ok'?'bot-walk':(sc.status==='error'?'bot-alert':'bot-idle'),{scanning:sc.status==='ok'});
  document.getElementById('desk-scanner').innerHTML=
   (sc.coins||[]).map(c=>esc(c.symbol)+' <b>'+(Number(c.change_pct)>=0?'+':'')+c.change_pct+'%</b>').join('<br>')||'<span class="ts">belum ada hasil scan</span>'+
   '<br><span class="ts">'+esc(sc.ts||'')+'</span>';
  setLamp('lamp-engine',lampFor(en.status));
  setBot('bot-engine','wrap-engine',en.status==='error'?'bot-alert':((en.status==='open'||en.status==='waiting')?'bot-work':'bot-idle'));
  const ls=en.last_signal;
  document.getElementById('desk-engine').innerHTML=
   (ls?('<b class="'+(ls.side==='LONG'?'up':'dn')+'">'+esc(ls.side)+'</b> @ '+fp(ls.entry)+(ls.strength==null?'':' (str '+ls.strength+'/3)')+'<br><span class="ts">'+esc(ls.reason||'')+'</span>'):'<span class="ts">menunggu sinyal</span>')+
   '<br><span class="ts">status: '+esc(en.status)+' &middot; '+esc(en.ts||'')+'</span>';
  setLamp('lamp-exec',lampFor(ex.status));
  setBot('bot-exec','wrap-exec',ex.status==='error'?'bot-alert':((ex.status==='running'&&tileAge(ex)<60)?'bot-jump':'bot-idle'));
  const lo=ex.last_order;
  document.getElementById('desk-exec').innerHTML=
   'mode: <b>'+esc(ex.mode||d.mode)+'</b><br>'+
   (lo?('last: '+esc(lo.kind)+' <b>'+esc(lo.side)+'</b>'+(lo.pnl!=null?' PnL '+(lo.pnl>=0?'+':'')+Number(lo.pnl).toFixed(2):'')+'<br><span class="ts">'+esc(lo.time||'')+'</span>'):'<span class="ts">belum ada order</span>')+
   '<br><span class="ts">status: '+esc(ex.status)+' &middot; '+esc(ex.ts||'')+'</span>';
  setLamp('lamp-risk',lampFor(rk.status));
  setBot('bot-risk','wrap-risk',(rk.killed||rk.status==='warning'||rk.status==='killed')?'bot-alert':'bot-sleep');
  document.getElementById('desk-risk').innerHTML=
   'max pos: <b>'+(d.max_positions>0?d.max_positions:'\u221E')+'</b><br>'+
   'daily PnL: <b class="'+((rk.daily_pnl||0)<0?'dn':'up')+'">'+((rk.daily_pnl||0)>=0?'+':'')+Number(rk.daily_pnl||0).toFixed(2)+'</b> / -'+Number(rk.max_daily_loss||100).toFixed(0)+'<br>'+
   'kill-switch: <b class="'+(rk.killed?'dn':'up')+'">'+(rk.killed?'AKTIF':'off')+'</b>'+(rk.kill_reason?'<br><span class="ts">'+esc(rk.kill_reason)+'</span>':'')+
   '<br><span class="ts">'+esc(rk.ts||'')+'</span>';
 }).catch(e=>{});
}
function modeChanged(){
 const m=document.getElementById('mode').value,isLive=m==='live';
 document.getElementById('livewarn').style.display=isLive?'block':'none';
 document.getElementById('liveconfirm').style.display=isLive?'block':'none';
 const ok=!isLive||document.getElementById('confirmtxt').value==='SAYA PAHAM';
 document.getElementById('bstart').disabled=!ok;
}
function ctl(a){
 const gv=id=>document.getElementById(id).value;
 const q='/api/'+a+'?symbol='+encodeURIComponent(gv('symbol').toUpperCase())+
  '&margin='+encodeURIComponent(gv('margin'))+'&lev='+encodeURIComponent(gv('lev'))+
  '&marginmode='+encodeURIComponent(gv('marginmode'))+'&mode='+encodeURIComponent(gv('mode'))+
  '&exchange='+encodeURIComponent(gv('exchange'))+'&strategy='+encodeURIComponent(gv('strategy'))+
  '&maxtrades='+encodeURIComponent(gv('maxtrades'))+'&maxpositions='+encodeURIComponent(gv('maxpos'))+'&confirm='+encodeURIComponent(gv('confirmtxt')||'');
 fetch(q,{method:'POST'}).then(r=>r.json()).then(d=>{
  document.getElementById('status').textContent=d.msg||a;
  const b=document.getElementById('modebadge');b.textContent=d.mode||'paper';b.className='badge '+(d.mode||'paper');
  render();
 });
}
setInterval(()=>{document.getElementById('clock').textContent=new Date().toLocaleString('id-ID');},1000);
render();setInterval(render,3000);
</script></body></html>
"""


# ------------------------------------------------------------------ server
def make_server(host="127.0.0.1", port=8080, start_scanner=True):
    """Buat HTTP server (tanpa jalan). start_scanner=False untuk testing."""
    srv = ThreadingHTTPServer((host, port), Handler)
    if start_scanner:
        t = threading.Thread(target=_scanner_loop, daemon=True)
        t.start()
    return srv


def main(argv=None):
    import sys
    args = list(argv or sys.argv[1:])
    port = int(os.environ.get("SCALP_WEBUI_PORT", "8080"))
    if "--port" in args:
        try:
            port = int(args[args.index("--port") + 1])
        except (IndexError, ValueError):
            raise SystemExit("--port butuh angka")
    srv = make_server("127.0.0.1", port, start_scanner=True)
    print(f"\U0001F435 MONKEY DESK: http://127.0.0.1:{port}  (Ctrl+C berhenti)",
          flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        t = _state.get("trader")
        if t:
            t.stop()
        print("\nMonkey Desk dihentikan.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
