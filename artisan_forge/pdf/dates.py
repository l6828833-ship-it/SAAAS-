"""Date maths for the calendar engine.

Everything here is derived from the standard library `calendar` / `datetime`
modules, so leap years, century rules and week alignment are correct by
construction for any year - no hand-maintained tables.
"""

from __future__ import annotations

import calendar
import datetime as dt
import math

MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

# Monday=0 ... Sunday=6 (the `calendar`/`datetime` convention)
_WEEKDAY_FULL = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# --------------------------------------------------------------------- grids
def month_grid(year: int, month: int, first_weekday: int = 6) -> list[list[dt.date]]:
    """Weeks of 7 real `date` objects, including adjacent-month spill days.

    `first_weekday` uses the `calendar` convention (Monday=0, Sunday=6).
    """
    cal = calendar.Calendar(firstweekday=first_weekday)
    return cal.monthdatescalendar(year, month)


def weekday_labels(first_weekday: int = 6, style: str = "short") -> list[str]:
    """Weekday headers ordered to match the grid.

    style: "short" (Sun) | "narrow" (S) | "full" (Sunday) | "two" (Su)
    """
    order = [(first_weekday + i) % 7 for i in range(7)]
    out = []
    for idx in order:
        name = _WEEKDAY_FULL[idx]
        if style == "full":
            out.append(name)
        elif style == "narrow":
            out.append(name[0])
        elif style == "two":
            out.append(name[:2])
        else:
            out.append(name[:3])
    return out


def is_weekend(day: dt.date) -> bool:
    return day.weekday() >= 5  # Saturday, Sunday


def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


# ------------------------------------------------------------------ holidays
def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    """n-th `weekday` of a month (n>=1). weekday: Monday=0 ... Sunday=6."""
    first = dt.date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + dt.timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> dt.date:
    last = dt.date(year, month, days_in_month(year, month))
    offset = (last.weekday() - weekday) % 7
    return last - dt.timedelta(days=offset)


def easter(year: int) -> dt.date:
    """Gregorian Easter Sunday (Anonymous / Meeus algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    lam = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * lam) // 451
    month = (h + lam - 7 * m + 114) // 31
    day = ((h + lam - 7 * m + 114) % 31) + 1
    return dt.date(year, month, day)


def us_holidays(year: int) -> dict[dt.date, str]:
    e = easter(year)
    items: list[tuple[dt.date, str]] = [
        (dt.date(year, 1, 1), "New Year's Day"),
        (_nth_weekday(year, 1, 0, 3), "MLK Day"),
        (dt.date(year, 2, 2), "Groundhog Day"),
        (dt.date(year, 2, 14), "Valentine's Day"),
        (_nth_weekday(year, 2, 0, 3), "Presidents' Day"),
        (dt.date(year, 3, 17), "St. Patrick's Day"),
        (e, "Easter"),
        (dt.date(year, 4, 22), "Earth Day"),
        (_nth_weekday(year, 5, 6, 2), "Mother's Day"),
        (_last_weekday(year, 5, 0), "Memorial Day"),
        (dt.date(year, 6, 14), "Flag Day"),
        (_nth_weekday(year, 6, 6, 3), "Father's Day"),
        (dt.date(year, 6, 19), "Juneteenth"),
        (dt.date(year, 7, 4), "Independence Day"),
        (_nth_weekday(year, 9, 0, 1), "Labor Day"),
        (_nth_weekday(year, 10, 0, 2), "Columbus Day"),
        (dt.date(year, 10, 31), "Halloween"),
        (dt.date(year, 11, 11), "Veterans Day"),
        (_nth_weekday(year, 11, 3, 4), "Thanksgiving"),
        (dt.date(year, 12, 24), "Christmas Eve"),
        (dt.date(year, 12, 25), "Christmas Day"),
        (dt.date(year, 12, 31), "New Year's Eve"),
    ]
    return dict(items)


def uk_holidays(year: int) -> dict[dt.date, str]:
    e = easter(year)
    items: list[tuple[dt.date, str]] = [
        (dt.date(year, 1, 1), "New Year's Day"),
        (dt.date(year, 2, 14), "Valentine's Day"),
        (e - dt.timedelta(days=21), "Mothering Sunday"),
        (dt.date(year, 3, 17), "St. Patrick's Day"),
        (e - dt.timedelta(days=2), "Good Friday"),
        (e, "Easter Sunday"),
        (e + dt.timedelta(days=1), "Easter Monday"),
        (_nth_weekday(year, 5, 0, 1), "Early May Bank Hol."),
        (_last_weekday(year, 5, 0), "Spring Bank Hol."),
        (_nth_weekday(year, 6, 6, 3), "Father's Day"),
        (_last_weekday(year, 8, 0), "Summer Bank Hol."),
        (dt.date(year, 10, 31), "Halloween"),
        (dt.date(year, 11, 5), "Bonfire Night"),
        (dt.date(year, 12, 25), "Christmas Day"),
        (dt.date(year, 12, 26), "Boxing Day"),
        (dt.date(year, 12, 31), "New Year's Eve"),
    ]
    return dict(items)


def holiday_map(year: int, region: str | None) -> dict[dt.date, str]:
    if not region:
        return {}
    region = region.strip().upper()
    if region in {"US", "USA", "UNITED STATES"}:
        return us_holidays(year)
    if region in {"UK", "GB", "BRITAIN", "UNITED KINGDOM"}:
        return uk_holidays(year)
    return {}


# --------------------------------------------------------------- moon phases
_SYNODIC = 29.530588853
_REF_NEW_MOON = dt.datetime(2000, 1, 6, 18, 14, tzinfo=dt.timezone.utc)

PHASE_GLYPHS = {"new": "\u25cf", "first": "\u25d0", "full": "\u25cb", "last": "\u25d1"}


def _moon_age(day: dt.date) -> float:
    """Days since the last new moon (0 .. 29.53)."""
    moment = dt.datetime(day.year, day.month, day.day, 12, tzinfo=dt.timezone.utc)
    elapsed = (moment - _REF_NEW_MOON).total_seconds() / 86400.0
    return math.fmod(math.fmod(elapsed, _SYNODIC) + _SYNODIC, _SYNODIC)


def moon_phases(year: int, month: int) -> dict[dt.date, str]:
    """Principal moon phases falling in a month: new / first / full / last.

    Approximate (mean synodic cycle) - decorative accuracy, +/- ~1 day.
    """
    targets = {"new": 0.0, "first": _SYNODIC * 0.25, "full": _SYNODIC * 0.5, "last": _SYNODIC * 0.75}
    total = days_in_month(year, month)
    best: dict[str, tuple[float, dt.date]] = {}
    for dom in range(1, total + 1):
        day = dt.date(year, month, dom)
        age = _moon_age(day)
        for name, target in targets.items():
            diff = min(abs(age - target), _SYNODIC - abs(age - target))
            if diff <= 0.5 and (name not in best or diff < best[name][0]):
                best[name] = (diff, day)
    return {day: name for name, (_diff, day) in best.items()}
