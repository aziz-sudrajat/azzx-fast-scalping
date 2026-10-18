# Copyright (c) 2026 aziz-sudrajat <azizsudrajat.stb4@gmail.com>
# https://github.com/aziz-sudrajat
#
# All rights reserved. This file is part of the azzx-fast-scalping project.
# Unauthorized copying, modification, distribution, or false claim of
# authorship is prohibited.

"""Factory exchange: satu pintu untuk semua exchange yang didukung.

    from scalping.exchanges import get_exchange, SUPPORTED

    ex = get_exchange("okx")          # default: "binance"
    data = ex["data_cls"](**ex["data_kwargs"])
    broker = ex["broker_cls"](**ex["broker_kwargs"](mode))

Env per exchange (testnet & live):
    binance: BINANCE_TESTNET_KEY/SECRET, BINANCE_LIVE_KEY/SECRET
    okx:     OKX_KEY/SECRET/PASSPHRASE (+ OKX_DEMO=1 untuk demo trading)
    bybit:   BYBIT_KEY/SECRET
    bitget:  BITGET_KEY/SECRET/PASSPHRASE
"""
from __future__ import annotations

import os

from .binance import BinanceExchangeData, BinanceExchangeBroker
from .okx import OKXExchangeData, OKXExchangeBroker
from .bybit import BybitExchangeData, BybitExchangeBroker
from .bitget import BitgetExchangeData, BitgetExchangeBroker

SUPPORTED = ("binance", "okx", "bybit", "bitget")


def _env(name: str) -> str:
    return os.environ.get(name, "")


def _broker_kwargs(exchange: str, mode: str) -> dict:
    """Kembalikan kwargs broker sesuai exchange & mode. Paper -> {}."""
    if mode == "paper":
        return {}
    testnet = (mode == "testnet")
    if exchange == "binance":
        if mode == "live":
            return {"api_key": _env("BINANCE_LIVE_KEY"),
                    "api_secret": _env("BINANCE_LIVE_SECRET"),
                    "testnet": False}
        return {"api_key": _env("BINANCE_TESTNET_KEY"),
                "api_secret": _env("BINANCE_TESTNET_SECRET"),
                "testnet": True}
    if exchange == "okx":
        demo = _env("OKX_DEMO") == "1" or testnet
        return {"api_key": _env("OKX_KEY"), "api_secret": _env("OKX_SECRET"),
                "passphrase": _env("OKX_PASSPHRASE"), "demo": demo}
    if exchange == "bybit":
        return {"api_key": _env("BYBIT_KEY"),
                "api_secret": _env("BYBIT_SECRET"), "testnet": testnet}
    if exchange == "bitget":
        if testnet:
            raise ValueError("bitget tidak punya testnet publik — pakai paper/live")
        return {"api_key": _env("BITGET_KEY"),
                "api_secret": _env("BITGET_SECRET"),
                "passphrase": _env("BITGET_PASSPHRASE")}
    raise ValueError(f"exchange tak dikenal: {exchange!r}")


def _data_kwargs(exchange: str, mode: str) -> dict:
    testnet = (mode == "testnet")
    if exchange == "binance":
        return {"testnet": testnet}
    if exchange == "okx":
        return {"demo": _env("OKX_DEMO") == "1" or testnet}
    if exchange == "bybit":
        return {"testnet": testnet}
    if exchange == "bitget":
        return {}
    raise ValueError(f"exchange tak dikenal: {exchange!r}")


def get_exchange(name: str | None) -> dict:
    """Normalisasi nama -> info exchange. None/kosong -> binance."""
    ex = (name or "binance").strip().lower()
    if ex not in SUPPORTED:
        raise ValueError(
            f"exchange tidak didukung: {name!r} (pilih: {', '.join(SUPPORTED)})")
    table = {
        "binance": {"data_cls": BinanceExchangeData,
                    "broker_cls": BinanceExchangeBroker},
        "okx": {"data_cls": OKXExchangeData,
                "broker_cls": OKXExchangeBroker},
        "bybit": {"data_cls": BybitExchangeData,
                  "broker_cls": BybitExchangeBroker},
        "bitget": {"data_cls": BitgetExchangeData,
                   "broker_cls": BitgetExchangeBroker},
    }
    info = dict(table[ex])
    info["name"] = ex
    info["broker_kwargs"] = lambda mode: _broker_kwargs(ex, mode)
    info["data_kwargs"] = lambda mode: _data_kwargs(ex, mode)
    return info
