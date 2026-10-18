# Copyright (c) 2026 aziz-sudrajat <azizsudrajat.stb4@gmail.com>
# https://github.com/aziz-sudrajat
#
# All rights reserved. This file is part of the azzx-fast-scalping project.
# Unauthorized copying, modification, distribution, or false claim of
# authorship is prohibited.

"""Adapter Binance: bungkus BinanceData/BinanceBroker yang sudah ada
dengan interface uniform market_open/market_close."""
from __future__ import annotations

from ..broker import BinanceBroker as _BinanceBroker
from ..data import BinanceData as _BinanceData
from .base import LocalPositionTracker, _norm_margin_type


class BinanceExchangeData(_BinanceData):
    """Sama dengan BinanceData (sudah sesuai kontrak)."""
    EXCHANGE = "binance"


class BinanceExchangeBroker(_BinanceBroker, LocalPositionTracker):
    """BinanceBroker + market_open/market_close uniform."""
    EXCHANGE = "binance"

    def __init__(self, *a, **kw):
        _BinanceBroker.__init__(self, *a, **kw)
        LocalPositionTracker.__init__(self)
        self._stop_ids: dict[str, int] = {}
        self._step_cache: dict[str, float] = {}

    def market_open(self, symbol: str, side: str, qty: float,
                    price: float) -> dict:
        res = self.place_market(symbol, side, qty)
        self._track_open(symbol, side, qty, price)
        res["simulated"] = False
        return res

    def market_close(self, symbol: str, price: float) -> dict:
        pos = self.get_position(symbol)
        if pos is None:
            return {"pnl_usd": 0.0, "flat": True}
        close_side = "SELL" if pos["side"] == "BUY" else "BUY"
        res = self.place_market(symbol, close_side, abs(pos["amt"]))
        res.update(self._track_close(
            symbol, price,
            fallback_entry=pos.get("entry_price"),
            fallback_side=pos.get("side"),
            fallback_qty=abs(pos.get("amt", 0))))
        return res

    def get_qty_step(self, symbol: str) -> float:
        """Step LOT_SIZE dari exchangeInfo (cache per simbol)."""
        from .base import norm_symbol_usdt, http_get_json
        sym = norm_symbol_usdt(symbol)
        if sym in self._step_cache:
            return self._step_cache[sym]
        base = self._base_url()
        data = http_get_json(f"{base}/fapi/v1/exchangeInfo?symbol={sym}")
        step = 0.001
        for sinfo in data.get("symbols", []):
            for f in sinfo.get("filters", []):
                if f.get("filterType") == "LOT_SIZE":
                    step = float(f["stepSize"])
                    break
        self._step_cache[sym] = step
        return step

    def _base_url(self) -> str:
        return self.base_url

    def place_stop_loss(self, symbol: str, side: str, qty: float,
                        stop_price: float) -> dict:
        """STOP_MARKET darurat di exchange (K1). side = arah posisi."""
        from .base import norm_symbol_usdt, _norm_side
        sym = norm_symbol_usdt(symbol)
        close_side = "SELL" if _norm_side(side) == "BUY" else "BUY"
        res = self._request("POST", "/fapi/v1/order", {
            "symbol": sym, "side": close_side, "type": "STOP_MARKET",
            "stopPrice": f"{stop_price:.8f}".rstrip("0").rstrip(".") or "0",
            "closePosition": "true", "workingType": "MARK_PRICE",
        }, signed=True)
        oid = res.get("orderId")
        self._stop_ids[sym] = oid
        return {"orderId": oid}

    def cancel_stop_loss(self, symbol: str) -> dict:
        from .base import norm_symbol_usdt
        sym = norm_symbol_usdt(symbol)
        oid = self._stop_ids.pop(sym, None)
        if oid is None:
            return {"cancelled": False}
        try:
            self._request("DELETE", "/fapi/v1/order",
                          {"symbol": sym, "orderId": oid}, signed=True)
        except Exception:
            pass
        return {"cancelled": True, "orderId": oid}

    def get_balance(self) -> float:
        """Saldo wallet USDT (futures). Juga validasi API key."""
        data = self._request("GET", "/fapi/v2/account", {}, signed=True)
        return float(data.get("totalWalletBalance", "0"))

    def set_margin_type(self, symbol: str, margin_type: str) -> dict:
        # BinanceBroker.set_margin_type sudah ada; normalisasi nama
        return super().set_margin_type(symbol,
                                       _norm_margin_type(margin_type))
