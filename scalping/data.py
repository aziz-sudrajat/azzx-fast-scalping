# Copyright (c) 2026 aziz-sudrajat <azizsudrajat.stb4@gmail.com>
# https://github.com/aziz-sudrajat
#
# All rights reserved. This file is part of the azzx-fast-scalping project.
# Unauthorized copying, modification, distribution, or false claim of
# authorship is prohibited.

"""Akses data market Binance Futures (endpoint publik, tanpa API key).

Fail-closed: setiap kegagalan jaringan / respons tak dikenal menaikkan
``RuntimeError`` — fungsi ini TIDAK PERNAH mengembalikan data palsu/sintetis.
Hanya candle yang sudah CLOSED yang dikembalikan (candle yang masih berjalan
selalu dibuang).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .config import BINANCE_FAPI, BINANCE_TESTNET, HTTP_TIMEOUT, KLINES_LIMIT

_VALID_INTERVALS = ("1m", "5m", "15m")
_MAX_LIMIT = 1500  # batas atas Binance untuk satu request klines

# Urutan kolom respons Binance /fapi/v1/klines yang kita pakai:
# [open_time, open, high, low, close, volume, close_time, ...]
_CANDLE_KEYS = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
)


def normalize_symbol(symbol: str) -> str:
    """Normalisasi ke format simbol Binance Futures USDT-M.

    Contoh: ``normalize_symbol("btc")`` -> ``"BTCUSDT"``,
    ``normalize_symbol(" eth/usdt ")`` -> ``"ETHUSDT"``.

    Raise ValueError bila input kosong/bukan string.
    """
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError(f"symbol tidak valid: {symbol!r}")
    cleaned = "".join(ch for ch in symbol.strip().upper() if ch.isalnum())
    if not cleaned:
        raise ValueError(f"symbol tidak valid: {symbol!r}")
    return cleaned if cleaned.endswith("USDT") else cleaned + "USDT"


def _parse_kline_row(symbol: str, row: Any) -> dict[str, float]:
    """Ubah satu baris kline Binance menjadi dict candle.

    Semua nilai dikembalikan sebagai float (waktu dalam milidetik epoch).
    Raise RuntimeError bila format baris tak dikenal.
    """
    try:
        values = [float(row[i]) for i in range(len(_CANDLE_KEYS))]
    except (IndexError, TypeError, ValueError) as exc:
        raise RuntimeError(
            f"format kline tak dikenal untuk {symbol}: {row!r}"
        ) from exc
    return dict(zip(_CANDLE_KEYS, values))


class BinanceData:
    """Client tipis untuk klines Binance Futures (public REST, stdlib saja)."""

    def __init__(self, testnet: bool = False, timeout: float = HTTP_TIMEOUT) -> None:
        """Args:
            testnet: True -> pakai BINANCE_TESTNET, False -> BINANCE_FAPI.
            timeout: batas detik per request HTTP (harus > 0).
        """
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError(f"timeout harus angka, dapat {timeout!r}")
        if timeout <= 0:
            raise ValueError(f"timeout harus > 0, dapat {timeout!r}")
        self.testnet = bool(testnet)
        self.base_url = BINANCE_TESTNET if testnet else BINANCE_FAPI
        self.timeout = float(timeout)

    def _http_get_json(self, url: str) -> Any:
        """GET url dan parse JSON. Selalu raise RuntimeError saat gagal."""
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "azzx-fast-scalping/0.1",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                body = resp.read()
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"Binance HTTP {exc.code} untuk {url}: {exc.reason}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"gagal koneksi ke Binance ({url}): {exc.reason}"
            ) from exc
        except Exception as exc:  # timeout socket, dll.
            raise RuntimeError(
                f"request ke Binance gagal ({url}): {exc}"
            ) from exc
        try:
            return json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise RuntimeError(
                f"respons Binance bukan JSON valid ({url})"
            ) from exc

    def get_klines(
        self, symbol: str, interval: str, limit: int = KLINES_LIMIT
    ) -> list[dict[str, float]]:
        """Ambil candle CLOSED untuk ``symbol``/``interval``.

        Args:
            symbol: mis. "BTCUSDT" (input "btc" otomatis dinormalisasi).
            interval: "1m" | "5m" | "15m".
            limit: jumlah baris yang diminta ke Binance (1..1500).

        Returns:
            List dict candle ``{"open_time","open","high","low","close",
            "volume","close_time"}`` urut oldest-first, semua float.

        Catatan: baris TERAKHIR respons Binance selalu candle yang masih
        berjalan, sehingga selalu dibuang — jumlah candle yang dikembalikan
        bisa satu lebih sedikit dari ``limit``.

        Raise:
            ValueError: interval/limit/symbol tidak valid.
            RuntimeError: kegagalan jaringan atau format respons tak dikenal.
        """
        norm_symbol = normalize_symbol(symbol)
        if interval not in _VALID_INTERVALS:
            raise ValueError(
                f"interval tidak valid: {interval!r} "
                f"(harus salah satu dari {', '.join(_VALID_INTERVALS)})"
            )
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError(f"limit harus int, dapat {limit!r}")
        if not 1 <= limit <= _MAX_LIMIT:
            raise ValueError(f"limit harus 1..{_MAX_LIMIT}, dapat {limit}")

        query = urllib.parse.urlencode(
            {"symbol": norm_symbol, "interval": interval, "limit": limit}
        )
        url = f"{self.base_url}/fapi/v1/klines?{query}"

        raw = self._http_get_json(url)
        if not isinstance(raw, list) or not raw:
            raise RuntimeError(
                f"respons klines tak dikenal untuk {norm_symbol} {interval}: "
                f"{str(raw)[:120]!r}"
            )

        candles = [_parse_kline_row(norm_symbol, row) for row in raw]
        candles = candles[:-1]  # buang candle yang masih berjalan
        return candles
