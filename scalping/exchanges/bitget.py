# Copyright (c) 2026 aziz-sudrajat <azizsudrajat.stb4@gmail.com>
# https://github.com/aziz-sudrajat
#
# All rights reserved. This file is part of the azzx-fast-scalping project.
# Unauthorized copying, modification, distribution, or false claim of
# authorship is prohibited.

"""Exchange Bitget (v2, USDT-FUTURES perpetual).

Auth: HMAC-SHA256 base64 dari timestamp+method+path(+query/body).
Env: BITGET_KEY / BITGET_SECRET / BITGET_PASSPHRASE.
"""
from __future__ import annotations

import time
import urllib.parse

from .base import (LocalPositionTracker, HttpError, check_keys,
                   hmac_sha256_b64, http_get_json, http_post_json,
                   norm_symbol_usdt, _norm_margin_type, _norm_side)

BASE_URL = "https://api.bitget.com"
_PRODUCT = "USDT-FUTURES"
_VALID_INTERVALS = {"1m": "1m", "5m": "5m", "15m": "15m"}


class BitgetExchangeData:
    EXCHANGE = "bitget"

    def __init__(self) -> None:
        self.base_url = BASE_URL

    def get_klines(self, symbol: str, interval: str = "1m",
                   limit: int = 200) -> list[dict]:
        if interval not in _VALID_INTERVALS:
            raise ValueError(f"interval tidak valid: {interval!r}")
        if not 1 <= limit <= 1000:
            raise ValueError("limit harus 1..1000")
        sym = norm_symbol_usdt(symbol)
        q = urllib.parse.urlencode({"symbol": sym, "productType": _PRODUCT,
                                    "granularity": _VALID_INTERVALS[interval],
                                    "limit": limit})
        data = http_get_json(f"{self.base_url}/api/v2/mix/market/candles?{q}")
        rows = data.get("data") if isinstance(data, dict) else None
        if data.get("code") != "00000" or not rows:
            raise HttpError(f"Bitget klines gagal: {str(data)[:150]}")
        rows = list(reversed(rows))  # newest-first -> oldest-first
        out = []
        for r in rows:
            out.append({"open_time": int(r[0]), "open": float(r[1]),
                        "high": float(r[2]), "low": float(r[3]),
                        "close": float(r[4]), "volume": float(r[5]),
                        "close_time": int(r[0])})
        return out[:-1]  # buang candle berjalan


class BitgetExchangeBroker(LocalPositionTracker):
    EXCHANGE = "bitget"

    def __init__(self, api_key: str, api_secret: str,
                 passphrase: str) -> None:
        check_keys({"key": api_key, "secret": api_secret,
                    "passphrase": passphrase},
                   ["key", "secret", "passphrase"], "Bitget")
        self._key = api_key
        self._secret = api_secret
        self._pass = passphrase
        self.base_url = BASE_URL
        LocalPositionTracker.__init__(self)

    def _headers(self, method: str, path: str, body: str = "") -> dict:
        ts = str(int(time.time() * 1000))
        sign = hmac_sha256_b64(self._secret, ts + method.upper() + path + body)
        return {"ACCESS-KEY": self._key, "ACCESS-SIGN": sign,
                "ACCESS-TIMESTAMP": ts, "ACCESS-PASSPHRASE": self._pass,
                "Content-Type": "application/json"}

    def _get(self, path: str, params: dict | None = None) -> dict:
        q = ("?" + urllib.parse.urlencode(params)) if params else ""
        full = path + q
        data = http_get_json(self.base_url + full,
                             self._headers("GET", full))
        if not isinstance(data, dict) or data.get("code") != "00000":
            raise HttpError(f"Bitget API gagal: {str(data)[:200]}")
        return data

    def _post(self, path: str, payload: dict) -> dict:
        import json as _json
        body = _json.dumps(payload, separators=(",", ":"))
        data = http_post_json(self.base_url + path, payload,
                              self._headers("POST", path, body))
        if not isinstance(data, dict) or data.get("code") != "00000":
            raise HttpError(f"Bitget API gagal: {str(data)[:200]}")
        return data

    # --- kontrak broker ---
    def market_open(self, symbol: str, side: str, qty: float,
                    price: float) -> dict:
        side = _norm_side(side)
        sym = norm_symbol_usdt(symbol)
        mt = getattr(self, "_margin_mode", "isolated")
        res = self._post("/api/v2/mix/order/place-order", {
            "symbol": sym, "productType": _PRODUCT,
            "marginMode": mt, "marginCoin": "USDT",
            "size": f"{qty:.8f}",
            "side": "buy" if side == "BUY" else "sell",
            "orderType": "market"})
        self._track_open(symbol, side, qty, price)
        return {"orderId": (res.get("data") or {}).get("orderId"),
                "simulated": False}

    def market_close(self, symbol: str, price: float) -> dict:
        pos = self.get_position(symbol)
        if pos is None:
            return {"pnl_usd": 0.0, "flat": True}
        close_side = "SELL" if pos["side"] == "BUY" else "BUY"
        self.market_open(symbol, close_side, abs(pos["amt"]), price)
        return self._track_close(symbol, price)

    def get_position(self, symbol: str) -> dict | None:
        sym = norm_symbol_usdt(symbol)
        data = self._get("/api/v2/mix/position/all-position",
                         {"productType": _PRODUCT, "marginCoin": "USDT"})
        for row in data.get("data") or []:
            if row.get("symbol") != sym:
                continue
            amt = float(row.get("total", "0"))
            if amt == 0:
                continue
            return {"symbol": sym, "amt": amt,
                    "entry_price": float(row.get("openPriceAvg", "0")),
                    "side": "BUY" if row.get("holdSide") == "long" else "SELL"}
        return None

    def get_balance(self) -> float:
        """Available USDT futures. Juga validasi API key."""
        data = self._get("/api/v2/mix/account/accounts",
                         {"productType": "USDT-FUTURES",
                          "marginCoin": "USDT"})
        rows = data.get("data") or []
        return float(rows[0].get("available", "0")) if rows else 0.0

    def set_leverage(self, symbol: str, leverage: int) -> dict:
        leverage = int(leverage)
        if not 1 <= leverage <= 125:
            raise ValueError("leverage harus 1-125")
        sym = norm_symbol_usdt(symbol)
        out = []
        for hold in ("long", "short"):
            out.append(self._post("/api/v2/mix/account/set-leverage", {
                "symbol": sym, "productType": _PRODUCT,
                "marginCoin": "USDT", "leverage": str(leverage),
                "holdSide": hold}))
        return {"symbol": sym, "leverage": leverage}

    def set_margin_type(self, symbol: str, margin_type: str) -> dict:
        mt = _norm_margin_type(margin_type)
        # Bitget: crossed | isolated
        self._margin_mode = "isolated" if mt == "ISOLATED" else "crossed"
        return {"symbol": norm_symbol_usdt(symbol), "marginType": mt}
