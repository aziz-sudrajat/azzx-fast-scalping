"""Test run.py paper dry-run (mock data, no network)."""
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from scalping import config, run


def _candles(n, start=100.0, step=0.5):
    out = []
    for i in range(n):
        c = start + i * step
        out.append({"open_time": float(i * 60000), "open": c - 0.1,
                    "high": c + 0.2, "low": c - 0.2, "close": c,
                    "volume": 10.0 + i * 0.1, "close_time": float(i * 60000 + 59999)})
    return out


class TestRun(unittest.TestCase):
    def test_paper_dry_run_no_signal(self):
        flat = _candles(200, step=0.0)
        with patch.object(run, "BinanceData") as mock_cls:
            inst = mock_cls.return_value
            inst.get_klines.side_effect = [flat, flat, flat]
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = run.main(["BTCUSDT"])
            self.assertEqual(rc, 0)
            self.assertIn("tidak ada sinyal", buf.getvalue())

    def test_non_paper_mode_refuses(self):
        with patch.object(config, "load", return_value={"MODE": "live"}):
            rc = run.main(["BTCUSDT"])
            self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
