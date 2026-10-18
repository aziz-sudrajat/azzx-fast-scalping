# Copyright (c) 2026 aziz-sudrajat <azizsudrajat.stb4@gmail.com>
# https://github.com/aziz-sudrajat
#
# All rights reserved. This file is part of the azzx-fast-scalping project.
# Unauthorized copying, modification, distribution, or false claim of
# authorship is prohibited.

"""RiskGuard: gerbang risiko sebelum trade baru dibuka.

Tolak trade baru jika:
- kill-switch aktif (manual),
- kerugian harian sudah mencapai max_daily_loss_usd,
- jumlah posisi terbuka >= max_positions,
- masih dalam cooldown setelah trade yang loss.

Fail-closed: bila ragu, tolak (return (False, alasan)).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta


class RiskGuard:
    def __init__(
        self,
        max_daily_loss_usd: float = 100.0,
        max_positions: int = 3,
        cooldown_sec: int = 300,
    ) -> None:
        if max_daily_loss_usd <= 0:
            raise ValueError("max_daily_loss_usd harus > 0")
        if max_positions < 1:
            raise ValueError("max_positions minimal 1")
        if cooldown_sec < 0:
            raise ValueError("cooldown_sec tidak boleh negatif")
        self.max_daily_loss_usd = float(max_daily_loss_usd)
        self.max_positions = int(max_positions)
        self.cooldown_sec = float(cooldown_sec)

        self._killed = False
        self._kill_reason = ""
        self._daily_pnl = 0.0
        self._last_loss_ts: float | None = None
        self._day = self._today_wib()

    @staticmethod
    def _today_wib() -> str:
        wib = timezone(timedelta(hours=7))
        return datetime.now(wib).strftime("%Y-%m-%d")

    def _maybe_roll_day(self) -> None:
        """Reset otomatis bila sudah ganti hari (WIB)."""
        today = self._today_wib()
        if today != self._day:
            self._day = today
            self._daily_pnl = 0.0
            self._last_loss_ts = None

    # ---- gerbang ----
    def check(self, symbol: str, open_count: int) -> tuple[bool, str]:
        """(boleh_buka?, alasan). symbol dicatat untuk konteks; aturan global."""
        self._maybe_roll_day()
        if self._killed:
            return False, f"kill-switch aktif: {self._kill_reason or 'manual'}"
        if self._daily_pnl <= -self.max_daily_loss_usd:
            return (
                False,
                f"daily loss tercapai ({self._daily_pnl:.2f} <= -{self.max_daily_loss_usd:.2f} USD)",
            )
        if open_count >= self.max_positions:
            return False, f"max posisi tercapai ({open_count}/{self.max_positions})"
        if self._last_loss_ts is not None and self.cooldown_sec > 0:
            elapsed = time.time() - self._last_loss_ts
            if elapsed < self.cooldown_sec:
                sisa = int(self.cooldown_sec - elapsed)
                return False, f"cooldown setelah loss: tunggu {sisa}s lagi"
        return True, "ok"

    # ---- pencatatan ----
    def record_trade(self, pnl_usd: float, ts: float | None = None) -> None:
        """Catat hasil trade tertutup. Trade loss memicu cooldown."""
        self._maybe_roll_day()
        pnl_usd = float(pnl_usd)
        self._daily_pnl += pnl_usd
        if pnl_usd < 0:
            self._last_loss_ts = float(ts) if ts is not None else time.time()

    # ---- kill-switch ----
    def unkill(self) -> None:
        """Cabut kill-switch manual secara eksplisit (S3-Azz)."""
        self._killed = False
        self._kill_reason = ""

    def kill(self, reason: str = "") -> None:
        self._killed = True
        self._kill_reason = reason

    def is_killed(self) -> bool:
        return self._killed

    # ---- reset harian ----
    def reset_day(self) -> None:
        """Nolkan PnL harian & cooldown. Kill-switch manual TIDAK ikut direset
        (fail-safe: pembunuhan manual hanya bisa dicabut eksplisit)."""
        self._daily_pnl = 0.0
        self._last_loss_ts = None

    # ---- introspeksi (untuk monitoring) ----
    def daily_pnl(self) -> float:
        return self._daily_pnl
