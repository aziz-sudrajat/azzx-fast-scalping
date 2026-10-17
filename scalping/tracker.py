"""PositionTracker: lacak posisi, avg price, level DCA, unrealized PnL.

Aturan DCA anti-martingale:
- DCA hanya searah posisi awal (entry berlawanan arah ditolak).
- Qty tiap fill DCA <= qty entry awal.
- Level DCA dibatasi max_dca_levels (level 1 = entry pertama).
"""

from __future__ import annotations


def _norm_side(side: str) -> str:
    s = side.strip().upper()
    if s in ("BUY", "LONG"):
        return "LONG"
    if s in ("SELL", "SHORT"):
        return "SHORT"
    raise ValueError(f"side tidak valid: {side!r} (pakai BUY/SELL atau LONG/SHORT)")


class PositionTracker:
    def __init__(self, max_dca_levels: int = 3) -> None:
        if max_dca_levels < 1:
            raise ValueError("max_dca_levels minimal 1")
        self.max_dca_levels = max_dca_levels
        self._positions: dict[str, dict] = {}

    def add_fill(self, symbol: str, side: str, qty: float, price: float, ts_ms: int) -> None:
        """Catat fill. Fill searah dirata-rata (DCA); fill berlawanan arah ditolak."""
        side = _norm_side(side)
        qty = float(qty)
        price = float(price)
        if qty <= 0:
            raise ValueError("qty harus > 0")
        if price <= 0:
            raise ValueError("price harus > 0")
        symbol = symbol.strip().upper()

        pos = self._positions.get(symbol)
        if pos is None:
            self._positions[symbol] = {
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "initial_qty": qty,
                "cost": qty * price,
                "level": 1,
                "fills": [{"side": side, "qty": qty, "price": price, "ts_ms": ts_ms}],
            }
            return

        if pos["side"] != side:
            raise ValueError(
                f"fill berlawanan arah ditolak untuk {symbol}: posisi {pos['side']}, fill {side}"
            )
        if pos["level"] >= self.max_dca_levels:
            raise ValueError(
                f"DCA {symbol} mentok di level {self.max_dca_levels} (max_dca_levels)"
            )
        if qty > pos["initial_qty"]:
            raise ValueError(
                f"qty DCA {qty} > qty awal {pos['initial_qty']} (anti-martingale)"
            )
        pos["qty"] += qty
        pos["cost"] += qty * price
        pos["level"] += 1
        pos["fills"].append({"side": side, "qty": qty, "price": price, "ts_ms": ts_ms})

    def avg_price(self, symbol: str) -> float | None:
        pos = self._positions.get(symbol.strip().upper())
        if pos is None or pos["qty"] <= 0:
            return None
        return pos["cost"] / pos["qty"]

    def dca_level(self, symbol: str) -> int:
        """1 = entry pertama; 0 = tidak ada posisi."""
        pos = self._positions.get(symbol.strip().upper())
        return pos["level"] if pos is not None else 0

    def can_dca(self, symbol: str) -> bool:
        """True bila ada posisi dan level < max_dca_levels."""
        pos = self._positions.get(symbol.strip().upper())
        return pos is not None and pos["level"] < self.max_dca_levels

    def unrealized_pnl(self, symbol: str, mark_price: float) -> float | None:
        pos = self._positions.get(symbol.strip().upper())
        if pos is None:
            return None
        avg = pos["cost"] / pos["qty"]
        if pos["side"] == "LONG":
            return (mark_price - avg) * pos["qty"]
        return (avg - mark_price) * pos["qty"]

    def close(self, symbol: str) -> dict | None:
        """Hapus posisi, kembalikan ringkasan. None bila tidak ada posisi."""
        symbol = symbol.strip().upper()
        pos = self._positions.pop(symbol, None)
        if pos is None:
            return None
        return {
            "symbol": symbol,
            "side": pos["side"],
            "qty": pos["qty"],
            "avg_price": pos["cost"] / pos["qty"],
            "level": pos["level"],
            "fills": pos["fills"],
        }

    def open_positions(self) -> list[str]:
        return sorted(self._positions.keys())
