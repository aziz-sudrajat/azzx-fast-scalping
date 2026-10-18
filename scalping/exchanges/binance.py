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
        res.update(self._track_close(symbol, price))
        return res

    def get_balance(self) -> float:
        """Saldo wallet USDT (futures). Juga validasi API key."""
        data = self._request("GET", "/fapi/v2/account", {}, signed=True)
        return float(data.get("totalWalletBalance", "0"))

    def set_margin_type(self, symbol: str, margin_type: str) -> dict:
        # BinanceBroker.set_margin_type sudah ada; normalisasi nama
        return super().set_margin_type(symbol,
                                       _norm_margin_type(margin_type))
