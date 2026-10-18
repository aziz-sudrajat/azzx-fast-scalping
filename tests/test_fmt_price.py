"""Test fmt_price: presisi adaptif, jangan bulatkan 0.085xxx jadi 0.09."""
from scalping.live_loop import fmt_price


def test_btc():
    assert fmt_price(81225.9) == "81225.90"
    assert fmt_price(1244.6) == "1244.60"


def test_hbar_tidak_dibulatkan():
    # kasus user: 0.085xxx jangan jadi 0.09
    assert fmt_price(0.085234) == "0.085234"
    assert fmt_price(0.0852) == "0.085200"
    assert fmt_price(0.085234) != "0.09"


def test_mid_range():
    assert fmt_price(81.026) == "81.026"
    assert fmt_price(5.4321) == "5.4321"


def test_tiny():
    assert fmt_price(0.001234) == "0.00123400"


def test_paint_no_tty():
    from scalping.live_loop import paint, _USE_COLOR
    # di pytest stdout bukan tty -> tanpa kode ANSI
    assert paint("halo", "green", "bold") == "halo"
