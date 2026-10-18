# Copyright (c) 2026 aziz-sudrajat <azizsudrajat.stb4@gmail.com>
# https://github.com/aziz-sudrajat
#
# All rights reserved. This file is part of the azzx-fast-scalping project.
# Unauthorized copying, modification, distribution, or false claim of
# authorship is prohibited.

"""Base untuk semua exchange: HTTP helper + kontrak interface.

Kontrak Data (klines):
    get_klines(symbol, interval, limit) -> list[dict]
    dict: {"open_time","open","high","low","close","volume","close_time"}
    interval: "1m" | "5m" | "15m". Candle berjalan TERAKHIR dibuang.

Kontrak Broker (uniform untuk live_loop):
    market_open(symbol, side, qty, price) -> dict   # side: BUY/SELL
    market_close(symbol, price) -> dict             # {"pnl_usd": float, ...}
    get_position(symbol) -> dict | None
    set_leverage(symbol, leverage) -> dict
    set_margin_type(symbol, margin_type) -> dict    # ISOLATED / CROSSED
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError, URLError


class HttpError(RuntimeError):
    pass


_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/120.0 Safari/537.36"}


def http_get_json(url: str, headers: dict | None = None,
                  timeout: int = 15) -> object:
    h = dict(_UA)
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise HttpError(f"HTTP {e.code}: {detail}") from e
    except URLError as e:
        raise HttpError(f"network error: {e.reason}") from e


def http_post_json(url: str, payload: dict,
                   headers: dict | None = None,
                   timeout: int = 15,
                   raw_body: str | None = None) -> object:
    """POST JSON. raw_body: pakai string persis ini sebagai body
    (penting untuk exchange yang sign body — harus identik)."""
    body_str = raw_body if raw_body is not None else json.dumps(payload)
    body = body_str.encode("utf-8")
    h = dict(_UA)
    h["Content-Type"] = "application/json"
    h.update(headers or {})
    req = urllib.request.Request(url, data=body, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise HttpError(f"HTTP {e.code}: {detail}") from e
    except URLError as e:
        raise HttpError(f"network error: {e.reason}") from e


def hmac_sha256_hex(secret: str, msg: str) -> str:
    return hmac.new(secret.encode("utf-8"), msg.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def hmac_sha256_b64(secret: str, msg: str) -> str:
    import base64
    sig = hmac.new(secret.encode("utf-8"), msg.encode("utf-8"),
                   hashlib.sha256).digest()
    return base64.b64encode(sig).decode("utf-8")


def check_keys(mapping: dict, names: list[str], exchange: str) -> None:
    missing = [n for n in names if not mapping.get(n)]
    if missing:
        raise ValueError(
            f"{exchange}: API credential hilang: {', '.join(missing)} "
            f"(set via environment)")


def norm_symbol_usdt(symbol: str) -> str:
    """BTCUSDT / btcusdt / BTC-USDT -> BTCUSDT."""
    s = symbol.strip().upper().replace("-", "").replace("_", "").replace("/", "")
    if s.endswith("USDT"):
        return s
    return s + "USDT"


def _norm_side(side: str) -> str:
    s = side.strip().upper()
    if s in ("BUY", "LONG"):
        return "BUY"
    if s in ("SELL", "SHORT"):
        return "SELL"
    raise ValueError(f"side tidak valid: {side!r}")


def _norm_margin_type(mt: str) -> str:
    m = mt.strip().upper()
    if m in ("ISOLATED", "CROSSED", "CROSS"):
        return "ISOLATED" if m == "ISOLATED" else "CROSSED"
    raise ValueError("margin_type harus ISOLATED atau CROSSED")


class LocalPositionTracker:
    """Tracker entry lokal untuk broker real (pnl display).

    Order sungguhan tetap lewat API exchange; tracker ini hanya untuk
    hitung PnL tampilan di live_loop (exchange adalah source of truth).
    """

    def __init__(self) -> None:
        self._pos: dict[str, dict] = {}

    def _track_open(self, symbol: str, side: str, qty: float,
                    price: float) -> None:
        self._pos[symbol.upper()] = {"side": _norm_side(side),
                                     "qty": float(qty),
                                     "entry": float(price)}

    def _track_close(self, symbol: str, price: float,
                     fee_rate: float = 0.0005) -> dict:
        symbol = symbol.upper()
        pos = self._pos.pop(symbol, None)
        if pos is None:
            return {"pnl_usd": 0.0, "untracked": True}
        entry, qty = pos["entry"], pos["qty"]
        gross = (price - entry) * qty if pos["side"] == "BUY" \
            else (entry - price) * qty
        fees = (entry + price) * qty * fee_rate
        return {"pnl_usd": gross - fees, "entry": entry,
                "exit": price, "qty": qty}
