# Copyright (c) 2026 aziz-sudrajat <azizsudrajat.stb4@gmail.com>
# https://github.com/aziz-sudrajat
#
# All rights reserved. This file is part of the azzx-fast-scalping project.
# Unauthorized copying, modification, distribution, or false claim of
# authorship is prohibited.

"""Indikator teknikal murni stdlib (tanpa numpy).

Semua fungsi adalah fungsi murni: tidak ada I/O, tidak ada state global.
Konvensi umum:
- Fungsi yang butuh "cukup data" mengembalikan ``None`` untuk indeks yang
  belum terdefinisi (bukan 0.0), agar komponen hilir bisa membedakan
  "belum ada nilai" dari "nilai nol".
- ``candles`` adalah urutan dict seperti yang dihasilkan
  ``scalping.data.BinanceData.get_klines`` (kunci: open/high/low/close/volume,
  urut oldest-first).
"""

from __future__ import annotations

from typing import Mapping, Sequence


def _check_period(period: int, name: str = "period") -> int:
    """Validasi parameter period. Raise ValueError bila bukan int >= 1."""
    if isinstance(period, bool) or not isinstance(period, int) or period < 1:
        raise ValueError(f"{name} harus int >= 1, dapat {period!r}")
    return period


def ema(values: Sequence[float], period: int) -> list[float | None]:
    """Exponential Moving Average.

    Seed memakai SMA dari ``period`` nilai pertama; entri sebelum seed
    adalah ``None``. ``k = 2 / (period + 1)``.

    Contoh (bisa dicek tangan): ``ema([10, 12, 14, 16], 2)``
    -> ``[None, 11.0, 13.0, 15.0]``.
    """
    _check_period(period)
    n = len(values)
    out: list[float | None] = [None] * n
    if n < period:
        return out
    k = 2.0 / (period + 1)
    prev = sum(values[:period]) / period  # seed SMA
    out[period - 1] = prev
    for i in range(period, n):
        prev = values[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def macd(
    values: Sequence[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """MACD: (macd_line, signal_line, histogram).

    - ``macd_line[i] = EMA_fast[i] - EMA_slow[i]`` (``None`` sampai kedua
      EMA terdefinisi, yaitu indeks < ``slow - 1``).
    - ``signal_line`` = EMA(``signal``) dari bagian non-None macd_line,
      posisinya disejajarkan kembali ke indeks asli.
    - ``histogram[i] = macd_line[i] - signal_line[i]``.

    Raise ValueError bila ``fast >= slow`` atau period tidak valid.
    """
    _check_period(fast, "fast")
    _check_period(slow, "slow")
    _check_period(signal, "signal")
    if fast >= slow:
        raise ValueError(f"fast ({fast}) harus < slow ({slow})")

    n = len(values)
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)
    macd_line: list[float | None] = [
        None if a is None or b is None else a - b
        for a, b in zip(ema_fast, ema_slow)
    ]

    signal_line: list[float | None] = [None] * n
    histogram: list[float | None] = [None] * n
    first = next(
        (i for i, v in enumerate(macd_line) if v is not None), None
    )
    if first is not None:
        sig_vals = ema([v for v in macd_line[first:] if v is not None], signal)
        for offset, s in enumerate(sig_vals):
            i = first + offset
            signal_line[i] = s
            m = macd_line[i]
            if s is not None and m is not None:
                histogram[i] = m - s
    return macd_line, signal_line, histogram


def _rsi_from_averages(avg_gain: float, avg_loss: float) -> float:
    """Rumus RSI dari rata-rata gain/loss (Wilder)."""
    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0.0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def rsi(values: Sequence[float], period: int = 14) -> list[float | None]:
    """Relative Strength Index (Wilder).

    Rata-rata gain/loss awal memakai SMA dari ``period`` perubahan pertama,
    lalu di-smoothing ala Wilder: ``avg = (avg_prev * (period-1) + now) / period``.
    RSI pertama terdefinisi di indeks ``period``; sebelumnya ``None``.

    Contoh (bisa dicek tangan): ``rsi([10, 11, 12, 11, 10], 2)``
    -> ``[None, None, 100.0, 50.0, 25.0]``.
    """
    _check_period(period)
    n = len(values)
    out: list[float | None] = [None] * n
    if n <= period:
        return out

    avg_gain = (
        sum(max(values[i] - values[i - 1], 0.0) for i in range(1, period + 1))
        / period
    )
    avg_loss = (
        sum(max(values[i - 1] - values[i], 0.0) for i in range(1, period + 1))
        / period
    )
    out[period] = _rsi_from_averages(avg_gain, avg_loss)

    for i in range(period + 1, n):
        change = values[i] - values[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period
        out[i] = _rsi_from_averages(avg_gain, avg_loss)
    return out


def _true_range(high: float, low: float, prev_close: float | None) -> float:
    if prev_close is None:
        return high - low
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def atr(
    candles: Sequence[Mapping[str, float]], period: int = 14
) -> list[float | None]:
    """Average True Range (Wilder).

    True range candle pertama = high - low (tanpa prev close). Seed ATR
    memakai SMA dari ``period`` true range pertama -> ATR pertama di indeks
    ``period - 1``; selanjutnya smoothing Wilder.

    Contoh (bisa dicek tangan), period=2, candle (high, low, close):
    (12, 9, 11), (13, 10, 12), (11, 8, 9)
    -> ``[None, 3.0, 3.5]``.
    """
    _check_period(period)
    n = len(candles)
    out: list[float | None] = [None] * n
    if n < period:
        return out

    true_ranges: list[float] = []
    for i, c in enumerate(candles):
        high = float(c["high"])
        low = float(c["low"])
        prev_close = float(candles[i - 1]["close"]) if i > 0 else None
        true_ranges.append(_true_range(high, low, prev_close))

    prev = sum(true_ranges[:period]) / period  # seed SMA
    out[period - 1] = prev
    for i in range(period, n):
        prev = (prev * (period - 1) + true_ranges[i]) / period
        out[i] = prev
    return out


def vwap(candles: Sequence[Mapping[str, float]]) -> list[float | None]:
    """Volume-Weighted Average Price berjalan (kumulatif dari awal data).

    ``typical = (high + low + close) / 3``;
    ``vwap[i] = sum(typical * volume) / sum(volume)`` sampai indeks ``i``.
    ``None`` selama volume kumulatif masih nol.
    """
    out: list[float | None] = []
    cum_pv = 0.0
    cum_vol = 0.0
    for c in candles:
        typical = (float(c["high"]) + float(c["low"]) + float(c["close"])) / 3.0
        vol = float(c["volume"])
        cum_pv += typical * vol
        cum_vol += vol
        out.append(cum_pv / cum_vol if cum_vol > 0.0 else None)
    return out


def cvd(candles: Sequence[Mapping[str, float]]) -> list[float]:
    """Cumulative Volume Delta (aturan tick sederhana).

    Tiap candle: ``+volume`` bila ``close >= open``, selain itu ``-volume``;
    diakumulasi dari awal. Tidak pernah ``None`` (candle pertama langsung
    punya nilai).
    """
    out: list[float] = []
    cumulative = 0.0
    for c in candles:
        vol = float(c["volume"])
        cumulative += vol if float(c["close"]) >= float(c["open"]) else -vol
        out.append(cumulative)
    return out


def atr_percentile(
    atr_values: Sequence[float | None], lookback: int = 100
) -> float | None:
    """Persentil ATR terakhir terhadap ``lookback`` nilai historisnya.

    Definisi: ``100 * (#nilai <= nilai_terakhir) / #nilai`` dalam jendela
    (nilai ``None`` diabaikan). Mengembalikan ``None`` bila tidak ada nilai
    valid. Berguna untuk filter ``ATR_MIN_PERCENTILE``: ambil sinyal hanya
    bila volatilitas relatif cukup tinggi.
    """
    _check_period(lookback, "lookback")
    window = [v for v in atr_values[-lookback:] if v is not None]
    if not window:
        return None
    latest = window[-1]
    count_le = sum(1 for v in window if v <= latest)
    return count_le / len(window) * 100.0
