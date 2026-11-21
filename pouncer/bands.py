"""Amateur band plan helpers and N1MM frequency conversions."""

from __future__ import annotations

import functools

BANDS = (
    ("160m", 1800.0, 2000.0),
    ("80m", 3500.0, 4000.0),
    ("60m", 5330.0, 5410.0),
    ("40m", 7000.0, 7300.0),
    ("30m", 10100.0, 10150.0),
    ("20m", 14000.0, 14350.0),
    ("17m", 18068.0, 18168.0),
    ("15m", 21000.0, 21450.0),
    ("12m", 24890.0, 24990.0),
    ("10m", 28000.0, 29700.0),
    ("6m", 50000.0, 54000.0),
)

# WARC bands (and 60 m): no contests there - "Remove WARC spots" drops them
WARC_BANDS = frozenset(("60m", "30m", "17m", "12m"))


def is_warc(khz: float | None) -> bool:
    """A frequency on 60, 30, 17 or 12 m."""
    return band_from_khz(khz) in WARC_BANDS


def band_hidden(cfg, khz: float | None) -> str:
    """Spots > Exclude (v0.7.245): 'WARC' for 30 / 17 / 12 m with [rbn]
    hide_warc, '60M' for 60 m with [rbn] hide_60m - else ''."""
    band = band_from_khz(khz)
    if band == "60m":
        return "60M" if getattr(cfg, "hide_60m", getattr(cfg, "hide_warc", False)) else ""
    if band in WARC_BANDS:
        return "WARC" if getattr(cfg, "hide_warc", False) else ""
    return ""


# The contest bands offered by the band picker (no WARC / 60 m / 6 m), with
# the frequency used when we have not been on that band yet (CW segment).
CONTEST_BANDS = ("160m", "80m", "40m", "20m", "15m", "10m")
DEFAULT_BAND_KHZ = {
    "160m": 1820.0, "80m": 3520.0, "40m": 7020.0,
    "20m": 14020.0, "15m": 21020.0, "10m": 28020.0,
}


@functools.lru_cache(maxsize=16384)   # pure; asked millions of times a minute (v0.7.182)
def band_from_khz(khz: float | None) -> str | None:
    """Return the band name for a frequency in kHz, or None if unknown."""
    if khz is None:
        return None
    for name, low, high in BANDS:
        if low <= khz <= high:
            return name
    return None


def tens_of_hz_to_khz(tens: float) -> float:
    """Convert N1MM's tens-of-Hz frequency value to kHz."""
    return tens / 100.0


def khz_to_tens_of_hz(khz: float) -> int:
    """Convert kHz to N1MM's tens-of-Hz frequency value."""
    return int(round(khz * 100.0))


def normalize_band_view(value: str | None) -> str | None:
    """Canonical browser band selection, or None for an invalid selection."""
    if not value:
        return None
    if value == "ALL":
        return value
    selected = {b.strip() for b in value.split(",")}
    if not selected or not selected.issubset(CONTEST_BANDS):
        return None
    return ",".join(b for b in CONTEST_BANDS if b in selected)
