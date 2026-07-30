"""Date-correctness verification.

Two independent layers, because "no date errors" is the one thing a calendar
product cannot get wrong:

1. `verify_grid_math`     - checks the grid the engine builds against
                            `datetime` / `calendar` ground truth.
2. `verify_calendar_pdf`  - re-reads the *rendered* PDF, extracts the text of
                            every month page and confirms the month name and
                            all day numbers actually landed on the page.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
from pathlib import Path

from ..models import CalendarSpec
from . import dates as D
from .calendar_pdf import first_month_page_index, month_page_count

_INT_RE = re.compile(r"\d{1,2}")


# ------------------------------------------------------------------- level 1
def verify_grid_math(year: int, first_weekday: int = 6) -> dict:
    """Validate the month grids for one year. Returns a report dict."""
    errors: list[str] = []
    checks = 0

    leap = calendar.isleap(year)
    expected_feb = 29 if leap else 28
    if D.days_in_month(year, 2) != expected_feb:
        errors.append(f"February {year} should have {expected_feb} days")
    checks += 1

    total_days = 0
    for month in range(1, 13):
        weeks = D.month_grid(year, month, first_weekday)
        expected = D.days_in_month(year, month)

        # every week is a full aligned 7-day run
        for index, week in enumerate(weeks):
            if len(week) != 7:
                errors.append(f"{year}-{month:02d} week {index} has {len(week)} days")
            if week and week[0].weekday() != first_weekday:
                errors.append(
                    f"{year}-{month:02d} week {index} starts on "
                    f"{week[0].strftime('%A')}, expected first weekday index {first_weekday}"
                )
            for offset in range(1, len(week)):
                if week[offset] - week[offset - 1] != dt.timedelta(days=1):
                    errors.append(f"{year}-{month:02d} week {index} is not consecutive")
            checks += 3

        # the in-month days must be exactly 1..n, each once, in order
        in_month = [day for week in weeks for day in week if day.month == month]
        numbers = [day.day for day in in_month]
        if numbers != list(range(1, expected + 1)):
            errors.append(
                f"{year}-{month:02d} day numbers wrong: got {len(numbers)} days, "
                f"expected {expected} sequential"
            )
        for day in in_month:
            if day.year != year:
                errors.append(f"{day.isoformat()} leaked into {year}-{month:02d}")
            if day != dt.date(year, month, day.day):
                errors.append(f"{day.isoformat()} does not round-trip")
        checks += 2
        total_days += expected

        # flow: grid spans a contiguous run of real dates
        flat = [day for week in weeks for day in week]
        for offset in range(1, len(flat)):
            if flat[offset] - flat[offset - 1] != dt.timedelta(days=1):
                errors.append(f"{year}-{month:02d} grid has a gap at position {offset}")
        checks += 1

    expected_year_days = 366 if leap else 365
    if total_days != expected_year_days:
        errors.append(f"{year} totals {total_days} days, expected {expected_year_days}")
    checks += 1

    # month boundaries chain correctly (Dec 31 -> Jan 1)
    for month in range(1, 13):
        last = dt.date(year, month, D.days_in_month(year, month))
        nxt = last + dt.timedelta(days=1)
        expected_next = (year + 1, 1) if month == 12 else (year, month + 1)
        if (nxt.year, nxt.month, nxt.day) != (*expected_next, 1):
            errors.append(f"Boundary after {last.isoformat()} is wrong")
        checks += 1

    return {"ok": not errors, "year": year, "checks": checks, "errors": errors}


# ------------------------------------------------------------------- level 2
def extract_page_texts(pdf_path: str | Path) -> list[str]:
    """Text content of each PDF page. Empty list if pypdfium2 is unavailable."""
    try:
        import pypdfium2 as pdfium
    except Exception:
        return []

    texts: list[str] = []
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        for page in document:
            textpage = page.get_textpage()
            texts.append(textpage.get_text_range())
            textpage.close()
            page.close()
    finally:
        document.close()
    return texts


def verify_calendar_pdf(pdf_path: str | Path, spec: CalendarSpec) -> dict:
    """Full verification of a rendered document against its spec."""
    report = verify_grid_math(spec.year, spec.first_weekday)
    errors: list[str] = list(report["errors"])
    report["pdf"] = str(pdf_path)

    texts = extract_page_texts(pdf_path)
    if not texts:
        report["rendered_pages"] = 0
        report["text_check"] = "skipped (pypdfium2 unavailable)"
        report["ok"] = not errors
        report["errors"] = errors
        return report

    expected_pages = month_page_count(spec)
    report["rendered_pages"] = len(texts)
    if len(texts) != expected_pages:
        errors.append(f"PDF has {len(texts)} pages, expected {expected_pages}")

    start = first_month_page_index(spec)
    for month in range(1, 13):
        index = start + month - 1
        if index >= len(texts):
            errors.append(f"Missing page for {D.MONTH_NAMES[month - 1]}")
            continue
        page_text = texts[index]
        name = D.MONTH_NAMES[month - 1]
        if name.upper() not in page_text.upper():
            errors.append(f"Page {index + 1} does not name {name}")
        found = {int(token) for token in _INT_RE.findall(page_text)}
        missing = [
            day for day in range(1, D.days_in_month(spec.year, month) + 1) if day not in found
        ]
        if missing:
            errors.append(f"{name}: day numbers missing from page: {missing}")

    report["text_check"] = "passed" if not errors else "failed"
    report["ok"] = not errors
    report["errors"] = errors
    return report


def verify_years(years: range | list[int], first_weekday: int = 6) -> dict:
    """Batch math check - handy for smoke testing a decade of output."""
    failures = {}
    for year in years:
        result = verify_grid_math(year, first_weekday)
        if not result["ok"]:
            failures[year] = result["errors"]
    return {"ok": not failures, "years_checked": len(list(years)), "failures": failures}
