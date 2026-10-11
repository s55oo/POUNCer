"""Amateur band plan helpers and N1MM frequency conversions."""

from __future__ import annotations

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

# The contest bands offered by the band picker (no WARC / 60 m / 6 m), with
# the frequency used when we have not been on that band yet (CW segment).
CONTEST_BANDS = ("160m", "80m", "40m", "20m", "15m", "10m")
DEFAULT_BAND_KHZ = {
    "160m": 1820.0, "80m": 3520.0, "40m": 7020.0,
    "20m": 14020.0, "15m": 21020.0, "10m": 28020.0,
}


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
