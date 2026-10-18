# Copyright (c) 2026 aziz-sudrajat <azizsudrajat.stb4@gmail.com>
# https://github.com/aziz-sudrajat
#
# All rights reserved. This file is part of the azzx-fast-scalping project.
# Unauthorized copying, modification, distribution, or false claim of
# authorship is prohibited.

"""Dashboard web untuk azzx-fast-scalping (paper/testnet mode).

Dibuka:  python -m scalping.webui
Buka:     http://127.0.0.1:8080

Hanya untuk mode paper/testnet. Tidak untuk live trading.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config as cfg_mod
from .broker import PaperBroker
from .data import BinanceData
from .risk import RiskGuard
from .signal import ScalpEngine
from .signal_loop import SignalTrader


class _AppState:
    """State bersama untuk dashboard."""

    def __init__(self):
        self.symbol = "BTCUSDT"
        self.qty = 0.001
        self.mode = "paper"
        self.exchange = "binance"
        self.trades = []
        self.current_signal = None
        self.live_prices = {}
        self.lock = threading.Lock()
        self.running = False

        # Inisialisasi kelas
        self.broker = PaperBroker()
        self.data = BinanceData(testnet=(self.mode == "testnet"))
        self.risk = RiskGuard()
        self.engine = ScalpEngine(cfg_mod)

    def snapshot(self):
        with self.lock:
            return {
                "symbol": self.symbol,
                "qty": self.qty,
                "mode": self.mode,
                "trades": list(self.trades),
                "current_signal": self.current_signal,
                "live_prices": dict(self.live_prices),
                "running": self.running,
            }


class _Handler(BaseHTTPRequestHandler):
    app = _AppState()  # class attribute shared

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            self.serve_index()
        elif path == "/api/state":
            self.serve_state()
        elif path == "/api/trades":
            self.serve_trades()
        elif path == "/api/signal":
            self.serve_signal()
        elif path == "/api/price":
            self.serve_price()
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/api/start":
            self.serve_start()
        elif path == "/api/stop":
            self.serve_stop()
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

    def serve_index(self):
        html = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>azzx-fast-scalping • Dashboard</title>
  <style>
    body { font-family: monospace; background: #14141c; color: #c9c9d4; margin: 0; padding: 20px; }
    h1 { color: #ffd60a; margin-bottom: 10px; }
    .card { background: #1e1e2e; border: 1px solid #3a3a4a; border-radius: 8px; padding: 16px; margin: 12px 0; }
    .row { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid #2a2a3a; }
    .row:last-child { border-bottom: none; }
    .label { color: #888899; }
    .val { color: #c9c9d4; font-weight: bold; }
    .trade { padding: 8px; margin: 4px 0; background: #1e1e2e; border-left: 3px solid #3a3a4a; }
    .trade.win { border-left-color: #26a69a; }
    .trade.loss { border-left-color: #ef5350; }
    .signal { padding: 10px; margin: 10px 0; background: #1e1e2e; border-radius: 6px; }
    .signal.long { border-left: 4px solid #3a86ff; }
    .signal.short { border-left: 4px solid #ff5d8f; }
    button { background: #ffd60a; color: #14141c; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; font-family: monospace; }
    button:hover { background: #ffe066; }
    button.danger { background: #ef5350; }
    button.danger:hover { background: #ff6b6b; }
    #trades { max-height: 300px; overflow-y: auto; }
  </style>
</head>
<body>
  <h1>⚡ azzx-fast-scalping Dashboard</h1>
  <div class="card">
    <div class="row"><span class="label">Mode</span><span class="val">{mode}</span></div>
    <div class="row"><span class="label">Symbol</span><span class="val" id="symbol">{symbol}</span></div>
    <div class="row"><span class="label">Qty</span><span class="val" id="qty">{qty}</span></div>
    <div class="row"><span class="label">Live</span><span class="val" id="live-price">-</span></div>
  </div>
  <div class="card">
    <h2>Sinyal Terbaru</h2>
    <div id="signal" class="signal">Menunggu sinyal...</div>
  </div>
  <div class="card">
    <h2>History Trades ({count})</h2>
    <div id="trades"></div>
  </div>
  <button onclick="start()">Mulai</button>
  <button class="danger" onclick="stop()">Hentikan</button>
  <script>
    const state = {symbol:"{symbol}", qty:{qty}, mode:"{mode}"};
    function render() {
      fetch('/api/state').then(r => r.json()).then(d => {
        document.getElementById('symbol').textContent = d.symbol;
        document.getElementById('qty').textContent = d.qty;
        document.getElementById('live-price').textContent = d.live_prices[d.symbol] || '-';
        const sig = d.current_signal;
        const s = document.getElementById('signal');
        if (sig) {
          s.className = 'signal ' + (sig.side === 'long' ? 'long' : 'short');
          s.innerHTML = `<strong>${sig.side.toUpperCase()}</strong> @ ${sig.price.toFixed(4)} (strength ${sig.strength})<br>${sig.reason}`;
        } else {
          s.className = 'signal';
          s.textContent = 'Menunggu sinyal...';
        }
        const t = document.getElementById('trades');
        t.innerHTML = '';
        d.trades.slice(-20).reverse().forEach(tr => {
          const div = document.createElement('div');
          div.className = 'trade ' + (tr.pnl >= 0 ? 'win' : 'loss');
          div.textContent = `${tr.side} ${tr.symbol} Entry ${tr.entry_price.toFixed(2)} → Exit ${tr.exit_price.toFixed(2)} | PnL ${tr.pnl_net.toFixed(2)} (${tr.reason})`;
          t.appendChild(div);
        });
        setTimeout(render, 2000);
      });
    }
    function start() {
      fetch('/api/start', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(state)})
        .then(r => r.json()).then(d => alert('Mulai: ' + (d.status || 'ok')));
    }
    function stop() { fetch('/api/stop', {method: 'POST'}).then(r => r.json()).then(d => alert('Hentikan: ' + (d.status || 'ok'))); }
    render();
  </script>
</body>
</html>"""
        self.send_html(html.format(mode=self.app.mode, symbol=self.app.symbol, qty=self.app.qty))

    def serve_state(self):
        self.send_json(self.app.snapshot())

    def serve_trades(self):
        with self.app.lock:
            trades = list(self.app.trades)
        self.send_json({"trades": trades})

    def serve_signal(self):
        with self.app.lock:
            sig = self.app.current_signal
        self.send_json({"signal": sig})

    def serve_price(self):
        symbol = self.app.symbol
        price = self.app.live_prices.get(symbol)
        self.send_json({"symbol": symbol, "price": price})

    def serve_start(self):
        body = json.loads(self.read_body())
        with self.app.lock:
            self.app.symbol = body.get("symbol", self.app.symbol)
            self.app.qty = float(body.get("qty", self.app.qty))
            self.app.mode = body.get("mode", self.app.mode)
            self.app.exchange = body.get("exchange", self.app.exchange)
            self.app.running = True
        # Set config mode
        cfg_mod.MODE = self.app.mode
        # Start trader loop in background
        thread = threading.Thread(target=self._run_trader, daemon=True)
        thread.start()
        self.send_json({"status": "started", "symbol": self.app.symbol,
                        "mode": self.app.mode, "exchange": self.app.exchange})

    def serve_stop(self):
        self.app.running = False
        self.send_json({"status": "stopped"})

    def _run_trader(self):
        """Jalankan SignalTrader di background thread."""
        trader = SignalTrader(
            self.app.broker, self.app.data, cfg_mod, self.app.risk,
            on_event=self._on_event, mode=self.app.mode,
        )
        trader.run(self.app.symbol, self.app.qty)
        with self.app.lock:
            self.app.running = False

    def _on_event(self, event):
        with self.app.lock:
            if event.get("type") == "trade_close":
                self.app.trades.append(event)
            elif event.get("type") == "trade_open":
                self.app.current_signal = {
                    "symbol": event.get("symbol", self.app.symbol),
                    "side": event.get("side", ""),
                    "entry": event.get("entry", 0.0),
                    "tp": event.get("tp", 0.0),
                    "sl": event.get("sl", 0.0),
                    "strength": event.get("strength", 0),
                    "reason": event.get("reason", ""),
                }
            elif event.get("type") == "tick":
                self.app.live_prices[event.get("symbol", self.app.symbol)] = event.get("price", 0.0)

    def send_html(self, html):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html.encode("utf-8"))))
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def send_json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(json.dumps(data).encode("utf-8"))))
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length).decode("utf-8") if length else "{}"

    def log_message(self, format, *args):
        pass  # hapus log server


def main(argv=None):
    host = "127.0.0.1"
    port = 8080
    print(f"Dashboard azzx-fast-scalping → http://{host}:{port}")
    server = ThreadingHTTPServer((host, port), _Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard dihentikan.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
