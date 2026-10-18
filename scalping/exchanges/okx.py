# Copyright (c) 2026 aziz-sudrajat <azizsudrajat.stb4@gmail.com>
# https://github.com/aziz-sudrajat
#
# All rights reserved. This file is part of the azzx-fast-scalping project.
# Unauthorized copying, modification, distribution, or false claim of
# authorship is prohibited.

"""Exchange OKX (perpetual SWAP).

Auth: HMAC-SHA256 base64 dari timestamp+method+path+body.
Env: OKX_KEY / OKX_SECRET / OKX_PASSPHRASE (+ OKX_DEMO=1 untuk demo).
"""
from __future__ import annotations

import time
import urllib.parse

from .base import (LocalPositionTracker, HttpError, check_keys,
                   hmac_sha256_b64, http_get_json, http_post_json,
                   norm_symbol_usdt, _norm_margin_type, _norm_side)

BASE_URL = "https://www.okx.com"
_VALID_INTERVALS = {"1m": "1m", "5m": "5m", "15m": "15m"}


def to_inst_id(symbol: str) -> str:
    """BTCUSDT -> BTC-USDT-SWAP."""
    s = norm_symbol_usdt(symbol)
    base = s[:-4]
    return f"{base}-USDT-SWAP"


class OKXExchangeData:
    EXCHANGE = "okx"

    def __init__(self, demo: bool = False) -> None:
        self.demo = demo
        self.base_url = BASE_URL

    def get_klines(self, symbol: str, interval: str = "1m",
                   limit: int = 200) -> list[dict]:
        if interval not in _VALID_INTERVALS:
            raise ValueError(f"interval tidak valid: {interval!r}")
        if not 1 <= limit <= 300:
            raise ValueError("limit harus 1..300")
        inst = to_inst_id(symbol)
        q = urllib.parse.urlencode({"instId": inst,
                                    "bar": _VALID_INTERVALS[interval],
                                    "limit": limit})
        data = http_get_json(f"{self.base_url}/api/v5/market/candles?{q}")
        rows = data.get("data") if isinstance(data, dict) else None
        if data.get("code") != "0" or not rows:
            raise HttpError(f"OKX klines gagal: {str(data)[:150]}")
        # OKX: newest-first -> balik jadi oldest-first, buang candle berjalan
        rows = list(reversed(rows))
        out = []
        for r in rows:
            out.append({"open_time": int(r[0]), "open": float(r[1]),
                        "high": float(r[2]), "low": float(r[3]),
                        "close": float(r[4]), "volume": float(r[5]),
                        "close_time": int(r[0])})
        return out[:-1]


class OKXExchangeBroker(LocalPositionTracker):
    EXCHANGE = "okx"

    def __init__(self, api_key: str, api_secret: str, passphrase: str,
                 demo: bool = False) -> None:
        check_keys({"key": api_key, "secret": api_secret,
                    "passphrase": passphrase},
                   ["key", "secret", "passphrase"], "OKX")
        self._key = api_key
        self._secret = api_secret
        self._pass = passphrase
        self.demo = demo
        self.base_url = BASE_URL
        LocalPositionTracker.__init__(self)

    def _headers(self, method: str, path: str, body: str = "") -> dict:
        ts = str(int(time.time() * 1000))
        sign = hmac_sha256_b64(self._secret, ts + method.upper() + path + body)
        h = {"OK-ACCESS-KEY": self._key, "OK-ACCESS-SIGN": sign,
             "OK-ACCESS-TIMESTAMP": ts, "OK-ACCESS-PASSPHRASE": self._pass}
        if self.demo:
            h["x-simulated-trading"] = "1"
        return h

    def _get(self, path: str, params: dict | None = None) -> dict:
        q = ("?" + urllib.parse.urlencode(params)) if params else ""
        full = path + q
        data = http_get_json(self.base_url + full,
                             self._headers("GET", full))
        if not isinstance(data, dict) or data.get("code") != "0":
            raise HttpError(f"OKX API gagal: {str(data)[:200]}")
        return data

    def _post(self, path: str, payload: dict) -> dict:
        import json as _json
        body = _json.dumps(payload)
        data = http_post_json(self.base_url + path, payload,
                              self._headers("POST", path, body))
        if not isinstance(data, dict) or data.get("code") != "0":
            raise HttpError(f"OKX API gagal: {str(data)[:200]}")
        return data

    # --- kontrak broker ---
    def market_open(self, symbol: str, side: str, qty: float,
                    price: float) -> dict:
        side = _norm_side(side)
        inst = to_inst_id(symbol)
        td = getattr(self, "_td_mode", "isolated")
        res = self._post("/api/v5/trade/order", {
            "instId": inst, "tdMode": td,
            "side": "buy" if side == "BUY" else "sell",
            "ordType": "market", "sz": f"{qty:.8f}"})
        self._track_open(symbol, side, qty, price)
        return {"orderId": res["data"][0].get("ordId"), "simulated": False}

    def market_close(self, symbol: str, price: float) -> dict:
        pos = self.get_position(symbol)
        if pos is None:
            return {"pnl_usd": 0.0, "flat": True}
        close_side = "SELL" if pos["side"] == "BUY" else "BUY"
        self.market_open(symbol, close_side, abs(pos["amt"]), price)
        return self._track_close(symbol, price)

    def get_position(self, symbol: str) -> dict | None:
        inst = to_inst_id(symbol)
        data = self._get("/api/v5/account/positions",
                         {"instType": "SWAP", "instId": inst})
        for row in data.get("data", []):
            amt = float(row.get("pos", "0"))
            if amt == 0:
                continue
            return {"symbol": symbol.upper(), "amt": amt,
                    "entry_price": float(row.get("avgPx", "0")),
                    "side": "BUY" if row.get("posSide") == "long" else "SELL"}
        return None

    def get_balance(self) -> float:
        """Total equity USDT. Juga validasi API key."""
        data = self._get("/api/v5/account/balance", {})
        rows = data.get("data") or []
        return float(rows[0].get("totalEq", "0")) if rows else 0.0

    def set_leverage(self, symbol: str, leverage: int) -> dict:
        leverage = int(leverage)
        if not 1 <= leverage <= 125:
            raise ValueError("leverage harus 1-125")
        inst = to_inst_id(symbol)
        td = getattr(self, "_td_mode", "isolated")
        return self._post("/api/v5/account/set-leverage",
                          {"instId": inst, "lever": str(leverage),
                           "mgnMode": td})

    def set_margin_type(self, symbol: str, margin_type: str) -> dict:
        mt = _norm_margin_type(margin_type)
        self._td_mode = "isolated" if mt == "ISOLATED" else "cross"
        # OKX terapkan saat set leverage / order; sinkronkan leverage aktif
        try:
            cur = self.get_position(symbol)
            lev = 10
            self.set_leverage(symbol, lev)
        except Exception:
            pass
        return {"symbol": symbol.upper(), "marginType": mt}
