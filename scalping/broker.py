"""Broker: eksekusi order.

- PaperBroker   : simulasi murni, cocokkan dengan PositionTracker untuk PnL.
- BinanceBroker : REST API Binance Futures (urllib stdlib saja).
                  Fail-closed: semua error network/API -> RuntimeError.
                  TIDAK ADA kode yang menempatkan order saat import/test.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError, URLError


def _norm_side(side: str) -> str:
    s = side.strip().upper()
    if s in ("BUY", "LONG"):
        return "BUY"
    if s in ("SELL", "SHORT"):
        return "SELL"
    raise ValueError(f"side tidak valid: {side!r} (pakai BUY/SELL atau LONG/SHORT)")


class PaperBroker:
    """Simulasi paper trading. Qty dalam base asset (mis. BTC untuk BTCUSDT)."""

    def __init__(self, starting_balance_usd: float = 1000.0) -> None:
        if starting_balance_usd <= 0:
            raise ValueError("starting_balance_usd harus > 0")
        self._balance: float = float(starting_balance_usd)
        self._positions: dict[str, dict] = {}

    def market_open(self, symbol: str, side: str, qty: float, price: float) -> dict:
        side = _norm_side(side)
        qty = float(qty)
        price = float(price)
        if qty <= 0:
            raise ValueError("qty harus > 0")
        if price <= 0:
            raise ValueError("price harus > 0")
        symbol = symbol.strip().upper()

        pos = self._positions.get(symbol)
        if pos is not None:
            if pos["side"] != side:
                raise RuntimeError(
                    f"posisi {symbol} sudah ada ({pos['side']}); close dulu sebelum buka berlawanan arah"
                )
            # Averaging searah: samakan dengan tracker (weighted avg).
            total_qty = pos["qty"] + qty
            pos["avg_price"] = (pos["avg_price"] * pos["qty"] + price * qty) / total_qty
            pos["qty"] = total_qty
        else:
            self._positions[symbol] = {"symbol": symbol, "side": side, "qty": qty, "avg_price": price}

        return {
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "price": price,
            "filled": True,
            "status": "FILLED",
        }

    def market_close(self, symbol: str, price: float) -> dict:
        symbol = symbol.strip().upper()
        price = float(price)
        if price <= 0:
            raise ValueError("price harus > 0")
        pos = self._positions.get(symbol)
        if pos is None:
            raise RuntimeError(f"tidak ada posisi terbuka untuk {symbol}")

        entry = pos["avg_price"]
        qty = pos["qty"]
        if pos["side"] == "BUY":
            pnl = (price - entry) * qty
        else:
            pnl = (entry - price) * qty

        self._balance += pnl
        del self._positions[symbol]
        return {
            "symbol": symbol,
            "side": pos["side"],
            "qty": qty,
            "entry_price": entry,
            "exit_price": price,
            "pnl_usd": pnl,
            "balance": self._balance,
            "status": "FILLED",
        }

    def get_position(self, symbol: str) -> dict | None:
        symbol = symbol.strip().upper()
        pos = self._positions.get(symbol)
        return dict(pos) if pos is not None else None

    def balance(self) -> float:
        return self._balance

    def set_leverage(self, symbol: str, leverage: int) -> dict:
        """Paper: catat saja (tidak ada efek margin sungguhan)."""
        if not hasattr(self, "_leverage"):
            self._leverage = {}
        self._leverage[symbol.strip().upper()] = int(leverage)
        return {"symbol": symbol.strip().upper(), "leverage": int(leverage),
                "simulated": True}

    def set_margin_type(self, symbol: str, margin_type: str) -> dict:
        """Paper: catat saja."""
        mt = margin_type.strip().upper()
        if mt not in ("ISOLATED", "CROSSED"):
            raise ValueError("margin_type harus ISOLATED atau CROSSED")
        if not hasattr(self, "_margin_type"):
            self._margin_type = {}
        self._margin_type[symbol.strip().upper()] = mt
        return {"symbol": symbol.strip().upper(), "marginType": mt,
                "simulated": True}


class BinanceBroker:
    """Broker Binance Futures via REST (urllib stdlib).

    testnet=True  -> https://testnet.binancefuture.com
    testnet=False -> https://fapi.binance.com  (LIVE - uang sungguhan)
    """

    RECV_WINDOW = 5000

    def __init__(self, api_key: str, api_secret: str, testnet: bool = True) -> None:
        if not api_key or not api_secret:
            raise ValueError("api_key dan api_secret wajib diisi")
        self._api_key = api_key
        self._api_secret = api_secret.encode("utf-8")
        self.testnet = bool(testnet)
        self.base_url = (
            "https://testnet.binancefuture.com"
            if self.testnet
            else "https://fapi.binance.com"
        )

    def _sign(self, params: dict) -> dict:
        """Tambah timestamp + recvWindow lalu tanda tangani HMAC-SHA256."""
        signed = dict(params)
        signed["timestamp"] = int(time.time() * 1000)
        signed["recvWindow"] = self.RECV_WINDOW
        query = urllib.parse.urlencode(signed, doseq=True)
        signature = hmac.new(self._api_secret, query.encode("utf-8"), hashlib.sha256).hexdigest()
        signed["signature"] = signature
        return signed

    def _request(self, method: str, path: str, params: dict, signed: bool = True) -> dict:
        if signed:
            params = self._sign(params)
        query = urllib.parse.urlencode(params, doseq=True)
        url = self.base_url + path
        headers = {"X-MBX-APIKEY": self._api_key}
        if method.upper() == "GET":
            req = urllib.request.Request(url + "?" + query, headers=headers, method="GET")
        else:
            req = urllib.request.Request(
                url, data=query.encode("utf-8"), headers=headers, method=method.upper()
            )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode("utf-8")
        except HTTPError as e:
            detail = e.read().decode("utf-8", "replace") if hasattr(e, "read") else ""
            raise RuntimeError(f"Binance API error {e.code}: {detail}") from e
        except URLError as e:
            raise RuntimeError(f"Binance network error: {e.reason}") from e
        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Binance respons bukan JSON: {body[:200]}") from e

    def place_market(self, symbol: str, side: str, quantity: float) -> dict:
        """Tempatkan market order. side: BUY/SELL. quantity: base asset."""
        side = _norm_side(side)
        symbol = symbol.strip().upper()
        quantity = float(quantity)
        if quantity <= 0:
            raise ValueError("quantity harus > 0")
        params = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": repr(quantity),
        }
        data = self._request("POST", "/fapi/v1/order", params, signed=True)
        if not isinstance(data, dict) or "orderId" not in data:
            raise RuntimeError(f"order gagal / respons tak terduga: {data!r}")
        return data

    def set_leverage(self, symbol: str, leverage: int) -> dict:
        """Set leverage: POST /fapi/v1/leverage."""
        symbol = symbol.strip().upper()
        leverage = int(leverage)
        if not 1 <= leverage <= 125:
            raise ValueError("leverage harus 1-125")
        data = self._request("POST", "/fapi/v1/leverage",
                             {"symbol": symbol, "leverage": leverage},
                             signed=True)
        return data

    def set_margin_type(self, symbol: str, margin_type: str) -> dict:
        """Set margin type: POST /fapi/v1/marginType.

        Gagal (-4046) bila ada posisi/order terbuka — tangkap di pemanggil.
        """
        symbol = symbol.strip().upper()
        mt = margin_type.strip().upper()
        if mt not in ("ISOLATED", "CROSSED"):
            raise ValueError("margin_type harus ISOLATED atau CROSSED")
        data = self._request("POST", "/fapi/v1/marginType",
                             {"symbol": symbol, "marginType": mt},
                             signed=True)
        return data

    def get_position(self, symbol: str) -> dict | None:
        """Ambil posisi terbuka dari /fapi/v2/positionRisk. None bila flat."""
        symbol = symbol.strip().upper()
        data = self._request("GET", "/fapi/v2/positionRisk", {}, signed=True)
        if not isinstance(data, list):
            raise RuntimeError(f"positionRisk respons tak terduga: {data!r}")
        for row in data:
            if row.get("symbol") == symbol:
                amt = float(row.get("positionAmt", "0"))
                if amt == 0:
                    return None
                return {
                    "symbol": symbol,
                    "amt": amt,
                    "entry_price": float(row.get("entryPrice", "0")),
                    "side": "BUY" if amt > 0 else "SELL",
                }
        return None
