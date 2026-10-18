# Copyright (c) 2026 aziz-sudrajat <azizsudrajat.stb4@gmail.com>
# https://github.com/aziz-sudrajat
#
# All rights reserved. This file is part of the azzx-fast-scalping project.
# Unauthorized copying, modification, distribution, or false claim of
# authorship is prohibited.

"""Exchange Bybit (v5, perpetual linear).

Auth: HMAC-SHA256 hex dari timestamp+apiKey+recvWindow+query/body.
Env: BYBIT_KEY / BYBIT_SECRET. Testnet: https://api-testnet.bybit.com
"""
from __future__ import annotations

import time
import urllib.parse

from .base import (LocalPositionTracker, HttpError, check_keys,
                   hmac_sha256_hex, http_get_json, http_post_json,
                   norm_symbol_usdt, _norm_margin_type, _norm_side,
                   quantize_down)

BASE_URL = "https://api.bybit.com"
TESTNET_URL = "https://api-testnet.bybit.com"


def _fmt(x: float) -> str:
    """Format desimal polos tanpa notasi ilmiah."""
    s = f"{float(x):.10f}".rstrip("0").rstrip(".")
    return s if s else "0"
_VALID_INTERVALS = {"1m": "1", "5m": "5", "15m": "15"}
_RECV_WINDOW = "5000"


class BybitExchangeData:
    EXCHANGE = "bybit"

    def __init__(self, testnet: bool = False) -> None:
        self.base_url = TESTNET_URL if testnet else BASE_URL

    def get_ticker_price(self, symbol: str) -> float:
        import urllib.parse as _up
        sym = norm_symbol_usdt(symbol)
        q = _up.urlencode({"category": "linear", "symbol": sym})
        data = http_get_json(f"{self.base_url}/v5/market/tickers?{q}")
        rows = (data.get("result") or {}).get("list") or []
        return float(rows[0]["lastPrice"])

    def get_klines(self, symbol: str, interval: str = "1m",
                   limit: int = 200) -> list[dict]:
        if interval not in _VALID_INTERVALS:
            raise ValueError(f"interval tidak valid: {interval!r}")
        if not 1 <= limit <= 1000:
            raise ValueError("limit harus 1..1000")
        sym = norm_symbol_usdt(symbol)
        q = urllib.parse.urlencode({"category": "linear", "symbol": sym,
                                    "interval": _VALID_INTERVALS[interval],
                                    "limit": limit})
        data = http_get_json(f"{self.base_url}/v5/market/kline?{q}")
        rows = (data.get("result") or {}).get("list") \
            if isinstance(data, dict) else None
        if data.get("retCode") != 0 or not rows:
            raise HttpError(f"Bybit klines gagal: {str(data)[:150]}")
        rows = list(reversed(rows))  # newest-first -> oldest-first
        out = []
        for r in rows:
            out.append({"open_time": int(r[0]), "open": float(r[1]),
                        "high": float(r[2]), "low": float(r[3]),
                        "close": float(r[4]), "volume": float(r[5]),
                        "close_time": int(r[0]) + {"1m": 60000, "5m": 300000,
                                            "15m": 900000}[interval]})
        return out[:-1]  # buang candle berjalan


class BybitExchangeBroker(LocalPositionTracker):
    EXCHANGE = "bybit"

    def __init__(self, api_key: str, api_secret: str,
                 testnet: bool = False) -> None:
        check_keys({"key": api_key, "secret": api_secret},
                   ["key", "secret"], "Bybit")
        self._key = api_key
        self._secret = api_secret
        self.base_url = TESTNET_URL if testnet else BASE_URL
        LocalPositionTracker.__init__(self)
        self._step_cache: dict[str, float] = {}
        self._stop_ids: dict[str, str] = {}

    def _sign(self, payload: str) -> tuple[str, str]:
        ts = str(int(time.time() * 1000))
        sign = hmac_sha256_hex(self._secret,
                               ts + self._key + _RECV_WINDOW + payload)
        return ts, sign

    def _headers(self, ts: str, sign: str) -> dict:
        return {"X-BAPI-API-KEY": self._key, "X-BAPI-TIMESTAMP": ts,
                "X-BAPI-RECV-WINDOW": _RECV_WINDOW, "X-BAPI-SIGN": sign}

    def _get(self, path: str, params: dict) -> dict:
        q = urllib.parse.urlencode(params)
        ts, sign = self._sign(q)
        data = http_get_json(f"{self.base_url}{path}?{q}",
                             self._headers(ts, sign))
        if not isinstance(data, dict) or data.get("retCode") != 0:
            raise HttpError(f"Bybit API gagal: {str(data)[:200]}")
        return data

    def _post(self, path: str, payload: dict) -> dict:
        import json as _json
        body = _json.dumps(payload, separators=(",", ":"))
        ts, sign = self._sign(body)
        data = http_post_json(self.base_url + path, payload,
                              self._headers(ts, sign), raw_body=body)
        if not isinstance(data, dict) or data.get("retCode") != 0:
            raise HttpError(f"Bybit API gagal: {str(data)[:200]}")
        return data

    # --- kontrak broker ---
    def market_open(self, symbol: str, side: str, qty: float,
                    price: float, track: bool = True) -> dict:
        side = _norm_side(side)
        sym = norm_symbol_usdt(symbol)
        qty = quantize_down(qty, self.get_qty_step(symbol))
        if qty <= 0:
            raise ValueError(f"qty di bawah step LOT_SIZE untuk {sym}")
        res = self._post("/v5/order/create", {
            "category": "linear", "symbol": sym,
            "side": "Buy" if side == "BUY" else "Sell",
            "orderType": "Market", "qty": f"{qty:.8f}"})
        if track:
            self._track_open(symbol, side, qty, price)
        return {"orderId": (res.get("result") or {}).get("orderId"),
                "simulated": False}

    def market_close(self, symbol: str, price: float) -> dict:
        pos = self.get_position(symbol)
        if pos is None:
            return {"pnl_usd": 0.0, "flat": True}
        close_side = "SELL" if pos["side"] == "BUY" else "BUY"
        self.market_open(symbol, close_side, abs(pos["amt"]), price,
                         track=False)
        return self._track_close(
            symbol, price,
            fallback_entry=pos.get("entry_price"),
            fallback_side=pos.get("side"),
            fallback_qty=abs(pos.get("amt", 0)))

    def get_qty_step(self, symbol: str) -> float:
        """Step qty dari instruments-info (cache per simbol)."""
        sym = norm_symbol_usdt(symbol)
        if sym in self._step_cache:
            return self._step_cache[sym]
        data = self._get("/v5/market/instruments-info",
                         {"category": "linear", "symbol": sym})
        step = 0.001
        lst = (data.get("result") or {}).get("list") or []
        if lst:
            filt = lst[0].get("lotSizeFilter") or {}
            step = float(filt.get("qtyStep", step))
        self._step_cache[sym] = step
        return step

    def place_stop_loss(self, symbol: str, side: str, qty: float,
                        stop_price: float) -> dict:
        """Stop-market darurat di Bybit (K1). side = arah posisi."""
        side = _norm_side(side)
        sym = norm_symbol_usdt(symbol)
        qty = quantize_down(qty, self.get_qty_step(symbol))
        if qty <= 0:
            raise ValueError(f"qty SL di bawah step untuk {sym}")
        close_side = "Sell" if side == "BUY" else "Buy"
        # triggerDirection: 1 = harga turun menyentuh (close Sell),
        # 2 = harga naik menyentuh (close Buy)
        trig_dir = 1 if close_side == "Sell" else 2
        res = self._post("/v5/order/create", {
            "category": "linear", "symbol": sym,
            "side": close_side, "orderType": "Market",
            "qty": f"{qty:.8f}",
            "triggerPrice": _fmt(stop_price),
            "triggerDirection": trig_dir,
            "triggerBy": "MarkPrice",
            "reduceOnly": True,
            "timeInForce": "IOC"})
        oid = (res.get("result") or {}).get("orderId")
        self._stop_ids[sym] = oid
        return {"orderId": oid}

    def cancel_stop_loss(self, symbol: str) -> dict:
        sym = norm_symbol_usdt(symbol)
        oid = self._stop_ids.pop(sym, None)
        if oid is None:
            return {"cancelled": False}
        try:
            self._post("/v5/order/cancel",
                       {"category": "linear", "symbol": sym,
                        "orderId": oid})
        except Exception:
            pass
        return {"cancelled": True}

    def get_position(self, symbol: str) -> dict | None:
        sym = norm_symbol_usdt(symbol)
        data = self._get("/v5/position/list",
                         {"category": "linear", "symbol": sym})
        for row in (data.get("result") or {}).get("list", []):
            amt = float(row.get("size", "0"))
            if amt == 0:
                continue
            return {"symbol": sym, "amt": amt,
                    "entry_price": float(row.get("avgPrice", "0")),
                    "side": "BUY" if row.get("side") == "Buy" else "SELL"}
        return None

    def get_balance(self) -> float:
        """Total wallet balance USDT. Juga validasi API key."""
        data = self._get("/v5/account/wallet-balance",
                         {"accountType": "UNIFIED"})
        lst = (data.get("result") or {}).get("list") or []
        return float(lst[0].get("totalWalletBalance", "0")) if lst else 0.0

    def set_leverage(self, symbol: str, leverage: int) -> dict:
        leverage = int(leverage)
        if not 1 <= leverage <= 125:
            raise ValueError("leverage harus 1-125")
        sym = norm_symbol_usdt(symbol)
        return self._post("/v5/position/set-leverage", {
            "category": "linear", "symbol": sym,
            "buyLeverage": str(leverage), "sellLeverage": str(leverage)})

    def set_margin_type(self, symbol: str, margin_type: str) -> dict:
        mt = _norm_margin_type(margin_type)
        sym = norm_symbol_usdt(symbol)
        # tradeMode: 0 = cross, 1 = isolated
        try:
            self._post("/v5/position/switch-isolated", {
                "category": "linear", "symbol": sym,
                "tradeMode": 1 if mt == "ISOLATED" else 0,
                "buyLeverage": "10", "sellLeverage": "10"})
        except HttpError as e:
            # sudah dalam mode yang diminta -> Bybit error, anggap OK
            if "same" not in str(e).lower() and "110025" not in str(e):
                raise
        return {"symbol": sym, "marginType": mt}
