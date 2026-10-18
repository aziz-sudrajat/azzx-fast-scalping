# Copyright (c) 2026 aziz-sudrajat <azizsudrajat.stb4@gmail.com>
# https://github.com/aziz-sudrajat
#
# All rights reserved. This file is part of the azzx-fast-scalping project.
# Unauthorized copying, modification, distribution, or false claim of
# authorship is prohibited.

"""Live web dashboard untuk azzx-fast-scalping — interaktif & hidup.

    python -m scalping.webui [--port 8080]

Buka http://127.0.0.1:8080 di browser. Fitur:
- Trade log streaming real-time (format: time | side | Entry | TP | PnL)
- Harga live + P&L berjalan (ticking)
- Indikator LIVE berdenyut, badge mode (paper/testnet/live)
- Kontrol: Start / Stop / Kill-switch, ganti symbol & qty & mode
- Event dikirim via SSE (Server-Sent Events), tanpa refresh

Stdlib only (http.server + threading). Bind 127.0.0.1 saja.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from .live_loop import (AlternatingTrader, _build_broker, _build_data,
                        _now_wib, fmt_price)
from . import config as cfg_mod
from .risk import RiskGuard

_state = {
    "trader": None,
    "thread": None,
    "running": False,
    "mode": "paper",
    "exchange": "binance",
    "symbol": "BTCUSDT",
    "qty": 0.001,
    "events": queue.Queue(maxsize=1000),
    "last": {},   # status terakhir: price, position, pnl
    "log": [],    # baris trade log (maks 200)
}


def _emit(ev: dict):
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
    if ev.get("type") == "trade_close":
        line = (f"{ev['time']} | {ev['side']} | Entry: {fmt_price(ev['entry'])} | "
                f"TP: {fmt_price(ev['tp'])} | PnL: {ev['pnl']:+.2f} [{ev['why']}]")
        _state["log"].append(line)
        _state["log"] = _state["log"][-200:]
    if ev.get("type") == "tick":
        _state["last"] = ev


PAGE = """<!DOCTYPE html>
<html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>azzx-fast-scalping — live</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0b0e14;color:#e6edf3;font-family:ui-monospace,Menlo,Consolas,monospace;min-height:100vh}
header{display:flex;align-items:center;gap:12px;padding:14px 20px;border-bottom:1px solid #1c2333;background:#0d1117;position:sticky;top:0}
.dot{width:12px;height:12px;border-radius:50%;background:#3fb950;box-shadow:0 0 8px #3fb950;animation:pulse 1.2s infinite}
.dot.off{background:#6e7681;box-shadow:none;animation:none}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
h1{font-size:16px}
.badge{font-size:11px;padding:3px 10px;border-radius:20px;background:#1c2333;border:1px solid #30363d}
.badge.paper{color:#3fb950}.badge.testnet{color:#d29922}.badge.live{color:#f85149}
main{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding:14px;max-width:1200px;margin:0 auto}
.card{background:#0d1117;border:1px solid #1c2333;border-radius:10px;padding:14px}
.card h2{font-size:12px;color:#8b949e;margin-bottom:10px;text-transform:uppercase;letter-spacing:1px}


.row{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px dashed #1c2333;font-size:13px}
.row:last-child{border:none}
#log{background:#010409;border:1px solid #1c2333;border-radius:10px;padding:12px;height:380px;overflow-y:auto;font-size:12.5px;line-height:1.7;grid-column:1/-1}
#log .win{color:#00e676;font-weight:700;text-shadow:0 0 14px rgba(0,230,118,.45)}
#log .loss{color:#ff5252;font-weight:700;text-shadow:0 0 14px rgba(255,82,82,.45)}
#log .trow{padding:4px 8px;border-radius:6px;margin:2px 0}
#log .trow-win{background:rgba(0,230,118,.08);border-left:3px solid #00e676}
#log .trow-loss{background:rgba(255,82,82,.08);border-left:3px solid #ff5252}
#pnl{font-size:34px;font-weight:800;text-shadow:0 0 18px currentColor}
#pnl.pos{color:#00e676}#pnl.neg{color:#ff5252}
#price{font-size:38px;font-weight:800;text-shadow:0 0 16px currentColor}
.controls{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
button{font-family:inherit;font-size:13px;padding:9px 18px;border-radius:8px;border:1px solid #30363d;background:#1c2333;color:#e6edf3;cursor:pointer}
button:hover{background:#262f42}
button.go{background:#1a7f37;border-color:#2ea043}
button.stopb{background:#7f1d1d;border-color:#a02e2e}
button:disabled{opacity:.4;cursor:default}
input,select{font-family:inherit;font-size:13px;padding:8px;border-radius:8px;border:1px solid #30363d;background:#010409;color:#e6edf3}
label{font-size:12px;color:#8b949e}
@media(max-width:700px){main{grid-template-columns:1fr}}
</style></head><body>
<header>
<div class="dot off" id="livedot"></div>
<h1>azzx-fast-scalping</h1>
<span class="badge paper" id="modebadge">paper</span>
<span style="margin-left:auto;font-size:12px;color:#8b949e" id="clock"></span>
</header>
<main>
<div class="card"><h2>Harga live</h2><div id="price">—</div><div class="row"><span>Posisi</span><span id="pos">—</span></div><div class="row"><span>Entry</span><span id="entry">—</span></div><div class="row"><span>TP / SL</span><span id="tpsl">—</span></div></div>
<div class="card"><h2>P&amp;L sesi</h2><div id="pnl">+0.00</div><div class="row"><span>Trade</span><span id="ntrades">0</span></div><div class="row"><span>Win</span><span id="nwins">0</span></div><div class="row"><span>Mode</span><span id="modetxt">paper</span></div></div>
<div class="card"><h2>Kontrol</h2>
<div class="controls">
<label>Symbol <input id="symbol" value="BTCUSDT" size="10"></label>
<label>Margin <input id="margin" value="100" size="6"></label>
<label>Lev <input id="lev" value="10" size="4"></label>
<label><select id="marginmode"><option value="isolated">isolated</option><option value="cross">cross</option></select></label>
<label>Mode <select id="mode"><option value="paper">paper</option><option value="testnet">testnet</option></select></label>
<label>Exch <select id="exchange"><option value="binance">binance</option><option value="okx">okx</option><option value="bybit">bybit</option><option value="bitget">bitget</option></select></label>
<label>Strategy <select id="strategy"><option value="signal">signal</option><option value="alternating">alternating</option></select></label>
<label>MaxTr <input id="maxtrades" value="0" size="4"></label>
</div>
<div class="controls">
<button class="go" id="bstart" onclick="ctl('start')">▶ Start</button>
<button class="stopb" id="bstop" onclick="ctl('stop')">⏸ Stop</button>
<button class="stopb" onclick="if(confirm('KILL-SWITCH? Hentikan semua & cegah trade baru.'))ctl('kill')">☠ Kill-switch</button>
</div>
<div style="font-size:11px;color:#8b949e;margin-top:8px">Testnet butuh BINANCE_TESTNET_KEY/SECRET di environment server.</div>
</div>
<div class="card"><h2>Status</h2><div id="status" style="font-size:13px;color:#8b949e">idle — tekan Start untuk mulai alternating long/short.</div></div>
<div id="log"></div>
</main>
<script>
const log=document.getElementById('log'),price=document.getElementById('price');
function fp(x){x=Number(x);const a=Math.abs(x);
 if(a>=100)return x.toFixed(2); if(a>=10)return x.toFixed(3);
 if(a>=1)return x.toFixed(4); if(a>=0.1)return x.toFixed(5);
 if(a>=0.01)return x.toFixed(6); return x.toFixed(8);}
let lastPx=0;
function addLog(html){const d=document.createElement('div');d.innerHTML=html;log.prepend(d);while(log.children.length>200)log.lastChild.remove();}
const es=new EventSource('/api/events');
es.onmessage=e=>{const d=JSON.parse(e.data);
 if(d.type==='tick'){const p=d.price;price.textContent=fp(p);
  price.className=p>lastPx?'up':'dn';lastPx=p;
  document.getElementById('pos').textContent=d.side;
  document.getElementById('entry').textContent=fp(d.entry);
  document.getElementById('tpsl').textContent=fp(d.tp)+' / '+fp(d.sl);}
 if(d.type==='trade_open'){addLog(`<span style="color:#8b949e">[${d.time}] OPEN ${d.side} @ ${fp(d.entry)}</span>`);setLive(true);}
 if(d.type==='trade_close'){const win=d.pnl>=0;const c=win?'win':'loss';const sym=win?'▲':'▼';
  addLog(`<div class="trow trow-${c}"><span class="${c}">${sym} ${d.time} | ${d.side} | Entry: ${fp(d.entry)} | TP: ${fp(d.tp)} | PnL: ${d.pnl>=0?'+':''}${d.pnl.toFixed(2)} [${d.why}]</span></div>`);
  const pnl=document.getElementById('pnl');pnl.textContent=(d.total_pnl>=0?'+':'')+d.total_pnl.toFixed(2);
  pnl.className=d.total_pnl>=0?'pos':'neg';
  document.getElementById('ntrades').textContent=d.trades;
  document.getElementById('nwins').textContent=d.wins;}
 if(d.type==='started'){setLive(true);setStatus('berjalan — alternating long/short...');}
 if(d.type==='stopped'||d.type==='finished'){setLive(false);setStatus(d.reason||('selesai — '+d.trades+' trade'));}
 if(d.type==='error'){addLog(`<span style="color:#f85149">ERROR: ${d.error}</span>`);}
};
function setLive(on){document.getElementById('livedot').className='dot'+(on?'':' off');}
function setStatus(t){document.getElementById('status').textContent=t;}
function ctl(a){const gv=id=>document.getElementById(id).value;
const q=`/api/${a}?symbol=${gv('symbol')}&margin=${gv('margin')}&lev=${gv('lev')}&marginmode=${gv('marginmode')}&mode=${gv('mode')}&exchange=${gv('exchange')}&strategy=${gv('strategy')}&maxtrades=${gv('maxtrades')}`;
 fetch(q,{method:'POST'}).then(r=>r.json()).then(d=>{setStatus(d.msg||a);
  const b=document.getElementById('modebadge');b.textContent=d.mode||'paper';b.className='badge '+(d.mode||'paper');
  document.getElementById('modetxt').textContent=d.mode||'paper';});}
setInterval(()=>{document.getElementById('clock').textContent=new Date().toLocaleString('id-ID');},1000);
// riwayat log saat load
fetch('/api/log').then(r=>r.json()).then(a=>a.reverse().forEach(l=>{const win=l.includes('PnL: +');const c=win?'win':'loss';const sym=win?'▲':'▼';addLog(`<div class="trow trow-${c}"><span class="${c}">${sym} ${l}</span></div>`);}));
</script></body></html>
"""


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
                      exchange="binance", strategy="signal", max_trades=0):
        if _state["running"]:
            return {"ok": False, "msg": "sudah berjalan", "mode": _state["mode"]}
        if mode == "live":
            return {"ok": False, "msg": "live hanya via CLI --i-understand",
                    "mode": mode}
        cfg = cfg_mod.load()
        cfg["MODE"] = mode
        broker = _build_broker(mode, exchange)  # SystemExit jika key kurang
        data = _build_data(mode, exchange)
        # set leverage & margin-type (testnet; paper di-skip aman)
        if mode == "testnet":
            mt = "ISOLATED" if margin_mode == "isolated" else "CROSSED"
            try:
                broker.set_margin_type(symbol, mt)
            except Exception as e:
                return {"ok": False, "msg": f"set margin gagal: {e}"}
            try:
                broker.set_leverage(symbol, lev)
            except Exception as e:
                return {"ok": False, "msg": f"set leverage gagal: {e}"}
        # hitung qty dari margin x lev
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
        risk = RiskGuard(
            max_daily_loss_usd=float(cfg.get("MAX_DAILY_LOSS_USD", 100.0)),
            max_positions=int(cfg.get("MAX_POSITIONS", 1)),
            cooldown_sec=float(cfg.get("COOLDOWN_SEC", 300.0)))
        if strategy == "signal":
            from .signal_loop import SignalTrader
            trader = SignalTrader(broker, data, cfg_mod, risk,
                                  on_event=_emit, mode=mode)
        else:
            trader = AlternatingTrader(broker, data, cfg, risk, on_event=_emit)
        _state.update(trader=trader, running=True, mode=mode,
                      symbol=symbol, qty=qty, exchange=exchange,
                      strategy=strategy)
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
        elif p.path == "/api/log":
            self._json(_state["log"])
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
                    int(g("maxtrades", "0") or "0"))
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
            if t:
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


def main(argv=None):
    import sys
    args = list(argv or sys.argv[1:])
    port = 8080
    if "--port" in args:
        port = int(args[args.index("--port") + 1])
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"dashboard live: http://127.0.0.1:{port}  (Ctrl+C berhenti)",
          flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        t = _state.get("trader")
        if t:
            t.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
