"""Signal engine scalping MTF (1m / 5m / 15m).

Fail-closed: data kurang / kondisi tak terpenuhi -> evaluate() mengembalikan None.

Dipakai dari ``scalping.indicators`` (API modul aslinya):
    - ``ema(values, period) -> list[float | None]`` (None = warmup)
    - ``macd(values, fast, slow, signal) -> (line, signal_line, hist)``
    - ``atr(candles, period) -> list[float | None]``
    - ``atr_percentile(atr_values, lookback=100) -> float | None``
      (ranking persentil ATR terakhir vs 100 nilai historis)
    - ``rsi(values, period) -> list[float | None]``
Nilai ``None`` (warmup) dilewati via helper ``_last`` / ``_last_two``.

Dipakai dari ``scalping.config`` (atribut modul, dengan default fallback):
    EMA_FAST/EMA_SLOW (9/21), MACD_FAST/MACD_SLOW/MACD_SIGNAL (12/26/9),
    ATR_PERIOD (14), ATR_MIN_PERCENTILE (20), VOLUME_SPIKE_MULT (1.5),
    MAX_SPREAD_BPS (5.0), TP_ATR_MULT (1.5), SL_ATR_MULT (1.0),
    TIME_STOP_MIN (15), MIN_BARS_1M/MIN_BARS_5M/MIN_BARS_15M (100/22/35).
"""

from dataclasses import dataclass
from typing import Optional

from scalping import indicators as ind


@dataclass
class Signal:
    side: str            # "long" | "short"
    price: float         # harga entry (close terakhir 1m)
    atr: float           # ATR(14) 1m terakhir
    tp: float            # price +/- TP_ATR_MULT * ATR
    sl: float            # price -/+ SL_ATR_MULT * ATR
    reason: str          # kenapa sinyal muncul
    strength: int        # 1..3 (dari kualitas konfirmasi)
    time_stop_min: int   # batas waktu posisi (menit)


def _last(series):
    """Nilai valid terakhir (lewati None warmup); None bila tak ada."""
    for v in reversed(series):
        if v is not None:
            return v
    return None


def _last_two(series):
    """Dua nilai valid terakhir; (None, None) bila kurang dari 2."""
    vals = [v for v in series if v is not None]
    if len(vals) < 2:
        return None, None
    return vals[-2], vals[-1]


class ScalpEngine:
    """Engine evaluasi sinyal scalping multi-timeframe."""

    def __init__(self, cfg=None):
        if cfg is None:
            from scalping import config as cfg_mod  # import di dalam, sesuai kontrak
            cfg = cfg_mod
        self.cfg = cfg

    # ------------------------------------------------------------------ util
    def _g(self, name, default):
        """Ambil nilai config dengan fallback default (tahan terhadap config stub)."""
        return getattr(self.cfg, name, default)

    @staticmethod
    def _closes(candles):
        return [float(c["close"]) for c in candles]

    # ------------------------------------------------------------------ main
    def evaluate(self, k1m, k5m, k15m, spread_bps: Optional[float] = None) -> Optional[Signal]:
        """Evaluasi satu bar berjalan. Kembalikan Signal atau None (fail-closed)."""
        try:
            if not all(isinstance(k, list) for k in (k1m, k5m, k15m)):
                return None
            if len(k1m) < int(self._g("MIN_BARS_1M", 100)):
                return None
            if len(k5m) < int(self._g("MIN_BARS_5M", 22)):
                return None
            if len(k15m) < int(self._g("MIN_BARS_15M", 35)):
                return None

            c1 = self._closes(k1m)
            c5 = self._closes(k5m)
            c15 = self._closes(k15m)

            ema_fast = int(self._g("EMA_FAST", 9))
            ema_slow = int(self._g("EMA_SLOW", 21))
            mf = int(self._g("MACD_FAST", 12))
            ms = int(self._g("MACD_SLOW", 26))
            mg = int(self._g("MACD_SIGNAL", 9))
            atr_p = int(self._g("ATR_PERIOD", 14))

            # 1. Bias dari 15m: EMA(21) naik & MACD hist > 0 -> long;
            #    EMA(21) turun & MACD hist < 0 -> short; selain itu None.
            prev21, last21 = _last_two(ind.ema(c15, ema_slow))
            h15 = _last(ind.macd(c15, mf, ms, mg)[2])
            if prev21 is None or h15 is None:
                return None
            if last21 > prev21 and h15 > 0:
                bias = "long"
            elif last21 < prev21 and h15 < 0:
                bias = "short"
            else:
                return None

            # 2. Trigger 1m: cross EMA(9) vs EMA(21) pada 2 candle terakhir,
            #    searah bias. Konfirmasi 5m: posisi EMA(9) vs EMA(21) searah.
            f2, f1 = _last_two(ind.ema(c1, ema_fast))
            s2, s1 = _last_two(ind.ema(c1, ema_slow))
            if None in (f2, f1, s2, s1):
                return None
            if bias == "long":
                crossed = f2 <= s2 and f1 > s1
            else:
                crossed = f2 >= s2 and f1 < s1
            if not crossed:
                return None

            f5 = _last(ind.ema(c5, ema_fast))
            s5 = _last(ind.ema(c5, ema_slow))
            if f5 is None or s5 is None:
                return None
            if bias == "long" and not f5 > s5:
                return None
            if bias == "short" and not f5 < s5:
                return None

            # 3. Filter: ATR persenil, spike volume, spread.
            a1 = ind.atr(k1m, atr_p)
            atr_now = _last(a1)
            if atr_now is None or not atr_now > 0:
                return None  # ATR nol/degenerate -> tolak
            pct_rank = ind.atr_percentile(a1, 100)
            if pct_rank is None or pct_rank < float(self._g("ATR_MIN_PERCENTILE", 20)):
                return None  # volatilitas relatif terlalu rendah -> chop

            vols = [float(c.get("volume", 0.0) or 0.0) for c in k1m]
            base = vols[-21:-1]  # 20 candle sebelum candle terakhir
            if len(base) < 20 or sum(base) <= 0:
                return None
            avg_vol = sum(base) / len(base)
            vol_ratio = vols[-1] / avg_vol
            if vols[-1] < float(self._g("VOLUME_SPIKE_MULT", 1.5)) * avg_vol:
                return None

            if spread_bps is not None and float(spread_bps) > float(self._g("MAX_SPREAD_BPS", 5.0)):
                return None

            # 4. Strength: 1 + bonus MACD 5m searah + bonus RSI(7) 1m ekstrem
            #    searah (RSI<30 utk long, RSI>70 utk short). Maks 3.
            strength = 1
            h5 = _last(ind.macd(c5, mf, ms, mg)[2])
            if h5 is not None and ((bias == "long" and h5 > 0) or (bias == "short" and h5 < 0)):
                strength += 1
            r7 = _last(ind.rsi(c1, 7))
            if r7 is not None and ((bias == "long" and r7 < 30) or (bias == "short" and r7 > 70)):
                strength += 1
            strength = min(strength, 3)

            # 5. Entry & exit dari ATR 1m.
            price = c1[-1]
            tp = price + float(self._g("TP_ATR_MULT", 1.5)) * atr_now
            sl = price - float(self._g("SL_ATR_MULT", 1.0)) * atr_now
            if bias == "short":
                tp, sl = price - float(self._g("TP_ATR_MULT", 1.5)) * atr_now, \
                         price + float(self._g("SL_ATR_MULT", 1.0)) * atr_now
            ts = int(self._g("TIME_STOP_MIN", 15))

            reason = (
                "bias %s 15m (ema%d %s, macd hist %.4f); "
                "1m %s cross ema%d/ema%d; 5m ema%d %s ema%d%s; "
                "atr %.4f (pct-rank %.1f); vol %.2fx avg20; rsi7 %.1f"
                % (
                    bias, ema_slow, "naik" if bias == "long" else "turun", h15,
                    "bullish" if bias == "long" else "bearish", ema_fast, ema_slow,
                    ema_fast, ">" if bias == "long" else "<", ema_slow,
                    " (macd hist %.4f)" % h5 if h5 is not None else "",
                    atr_now, pct_rank, vol_ratio,
                    r7 if r7 is not None else float("nan"),
                )
            )
            return Signal(
                side=bias,
                price=price,
                atr=atr_now,
                tp=tp,
                sl=sl,
                reason=reason,
                strength=strength,
                time_stop_min=ts,
            )
        except (KeyError, TypeError, ValueError, IndexError, ZeroDivisionError):
            return None
