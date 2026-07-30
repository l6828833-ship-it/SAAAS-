"""Date correctness is the product. These tests are the safety net."""

from __future__ import annotations

import calendar
import datetime as dt

import pytest

from artisan_forge.brief import parse_brief
from artisan_forge.models import CalendarSpec
from artisan_forge.packaging import etsy_tags, etsy_title
from artisan_forge.pdf import generate_calendar_pdf, verify_calendar_pdf
from artisan_forge.pdf.dates import (
    easter,
    holiday_map,
    month_grid,
    moon_phases,
    us_holidays,
    weekday_labels,
)
from artisan_forge.pdf.verify import verify_grid_math, verify_years

YEARS = [1900, 1999, 2000, 2023, 2024, 2025, 2026, 2027, 2028, 2100, 2400]


@pytest.mark.parametrize("year", YEARS)
@pytest.mark.parametrize("first_weekday", [6, 0])
def test_grid_math_is_clean(year: int, first_weekday: int) -> None:
    report = verify_grid_math(year, first_weekday)
    assert report["ok"], report["errors"]


def test_a_century_of_grids() -> None:
    for first_weekday in (6, 0):
        report = verify_years(range(2000, 2101), first_weekday)
        assert report["ok"], report["failures"]


@pytest.mark.parametrize(
    "year,expected",
    [(1900, 28), (2000, 29), (2024, 29), (2025, 28), (2026, 28), (2028, 29), (2100, 28)],
)
def test_february_length(year: int, expected: int) -> None:
    days = [d for week in month_grid(year, 2) for d in week if d.month == 2]
    assert len(days) == expected
    assert days[-1].day == expected


def test_every_date_matches_datetime() -> None:
    for month in range(1, 13):
        for week in month_grid(2026, month, 6):
            for day in week:
                assert day.weekday() == dt.date(day.year, day.month, day.day).weekday()


def test_week_alignment() -> None:
    for week in month_grid(2026, 3, 6):
        assert week[0].weekday() == 6  # Sunday
    for week in month_grid(2026, 3, 0):
        assert week[0].weekday() == 0  # Monday


def test_weekday_labels_order() -> None:
    assert weekday_labels(6) == ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    assert weekday_labels(0) == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    assert weekday_labels(6, "narrow") == ["S", "M", "T", "W", "T", "F", "S"]


def test_known_anchors() -> None:
    assert dt.date(2026, 1, 1).strftime("%A") == "Thursday"
    assert easter(2026) == dt.date(2026, 4, 5)
    assert easter(2024) == dt.date(2024, 3, 31)
    holidays = us_holidays(2026)
    assert holidays[dt.date(2026, 11, 26)] == "Thanksgiving"
    assert holidays[dt.date(2026, 1, 19)] == "MLK Day"
    assert holidays[dt.date(2026, 5, 25)] == "Memorial Day"
    assert holidays[dt.date(2026, 9, 7)] == "Labor Day"


def test_holidays_land_inside_their_year() -> None:
    for year in (2025, 2026, 2027, 2032):
        for region in ("US", "UK"):
            for day in holiday_map(year, region):
                assert day.year == year


def test_moon_phases_are_plausible() -> None:
    phases = moon_phases(2026, 6)
    assert 3 <= len(phases) <= 5
    for day, phase in phases.items():
        assert day.month == 6
        assert phase in {"new", "first", "full", "last"}


def test_leap_day_appears_only_in_leap_years() -> None:
    for year in (2024, 2028, 2000):
        assert calendar.isleap(year)
        days = {d for week in month_grid(year, 2) for d in week if d.month == 2}
        assert dt.date(year, 2, 29) in days
    for year in (2025, 2026, 1900, 2100):
        assert not calendar.isleap(year)


@pytest.mark.parametrize(
    "spec",
    [
        CalendarSpec(year=2026, theme="minimalist", orientation="portrait", paper="letter"),
        CalendarSpec(year=2028, theme="boho", orientation="landscape", paper="a4",
                     start_day="Monday", include_notes_column=True, include_moon_phases=True),
        CalendarSpec(year=2027, theme="dark_luxe", paper="12x12", include_cover=False,
                     include_year_overview=False, holidays=None),
    ],
)
def test_rendered_pdf_passes_verification(spec: CalendarSpec, tmp_path) -> None:
    pdf = generate_calendar_pdf(spec, tmp_path / "calendar.pdf")
    assert pdf.exists() and pdf.stat().st_size > 5_000
    report = verify_calendar_pdf(pdf, spec)
    assert report["ok"], report["errors"]
    expected_pages = 12 + int(spec.include_cover) + int(spec.include_year_overview)
    assert report["rendered_pages"] == expected_pages


def test_brief_parsing() -> None:
    spec = parse_brief("2026 minimalist calendar with watercolor floral theme, 8.5x11, Sunday start")
    assert spec.year == 2026
    assert spec.theme == "watercolor_floral"
    assert spec.paper == "letter"
    assert spec.start_day == "Sunday"
    assert spec.orientation == "portrait"

    spec = parse_brief("2027 boho landscape A4 calendar, monday start, no holidays, with notes")
    assert (spec.year, spec.theme, spec.paper) == (2027, "boho", "a4")
    assert spec.orientation == "landscape"
    assert spec.start_day == "Monday"
    assert spec.holidays is None
    assert spec.include_notes_column is True

    spec = parse_brief("dark luxe 12x12 square calendar for 2030 with moon phases")
    assert spec.trim_size_in == (12.0, 12.0)
    assert spec.include_moon_phases is True


def test_brief_rejects_impossible_specs() -> None:
    with pytest.raises(ValueError):
        parse_brief("calendar", year=1200)
    with pytest.raises(ValueError):
        parse_brief("calendar", bleed_in=2.0)


def test_etsy_copy_within_platform_limits() -> None:
    spec = parse_brief("2026 watercolor floral calendar 8.5x11")
    tags = etsy_tags(spec)
    assert len(tags) <= 13
    assert all(len(tag) <= 20 for tag in tags)
    assert len(set(tags)) == len(tags)
    assert len(etsy_title(spec)) <= 140
