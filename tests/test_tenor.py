from __future__ import annotations

from datetime import date

import pytest

from cdslib import Tenor, TenorUnit


def test_parse_variants() -> None:
    assert Tenor.parse("1w") == Tenor(1, TenorUnit.WEEK)
    assert Tenor.parse("3M") == Tenor(3, TenorUnit.MONTH)
    assert Tenor.parse("5y") == Tenor(5, TenorUnit.YEAR)
    assert str(Tenor.parse("3m")) == "3M"


def test_parse_invalid() -> None:
    with pytest.raises(ValueError):
        Tenor.parse("banana")


def test_add_and_subtract() -> None:
    start = date(2024, 1, 31)
    assert Tenor.parse("1M").add_to(start) == date(2024, 2, 29)
    assert Tenor.parse("1Y").add_to(date(2024, 6, 4)) == date(2025, 6, 4)
    assert Tenor.parse("1Y").subtract_from(date(2025, 6, 4)) == date(2024, 6, 4)
    assert Tenor.parse("2w").add_to(date(2024, 1, 1)) == date(2024, 1, 15)
