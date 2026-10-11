"""Callsign -> country / continent, from a CTY country file.

N1MM Logger+ keeps an up-to-date one at
``Documents\\N1MM Logger+\\SupportFiles\\wl_cty.dat`` (the standard AD1C
``cty.dat`` format), so POUNCer uses that by default. Used to tell European
stations from DX (everything outside the operator's own continent).

Format: one record per entity, terminated by ``;``::

    Slovenia:  15:  28:  EU:  46.00:  -14.00:  -1.0:  S5:
        S5;

Header fields are name, CQ zone, ITU zone, continent, lat, lon, UTC offset,
primary prefix; then a comma list of prefixes. ``=CALL`` is an exact
callsign; ``(cq)``, ``[itu]``, ``<lat/lon>``, ``{CONT}`` and ``~tz~`` are
per-alias overrides (only ``{CONT}`` matters here).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

CONTINENTS = ("EU", "NA", "SA", "AS", "AF", "OC", "AN")

# Callsign suffixes that are not a location prefix ("/P", "/QRP", ...).
_PLAIN_SUFFIXES = {"P", "M", "QRP", "QRPP", "A", "B", "LH", "J", "R", "T"}
_OVERRIDE = re.compile(r"[\(\[<\{~]")
_CONT = re.compile(r"\{([A-Z]{2})\}")
_CQ = re.compile(r"\((\d+)\)")
_ITU = re.compile(r"\[(\d+)\]")


def _int(text: str) -> int | None:
    try:
        return int(text.strip())
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class Entity:
    name: str
    continent: str
    prefix: str  # primary prefix, as N1MM stores it in DXLOG.CountryPrefix
    cq: int | None = None   # CQ zone (CQWW zone multiplier)
    itu: int | None = None  # ITU zone (IARU zone multiplier)


class CtyDB:
    """Prefix / exact-call lookup table built from a CTY file."""

    def __init__(self) -> None:
        self._prefixes: dict[str, Entity] = {}
        self._exact: dict[str, Entity] = {}
        self._max_len = 0
        # Call-area zone hints from N1MM's "#" blocks, per primary prefix:
        # [(pattern, cq, itu)], e.g. K -> ("K6", 3, 6), UA9 -> ("UA9H", 18, 31).
        self._hints: dict[str, list[tuple[str, int | None, int | None]]] = {}
        self.path = ""

    def __len__(self) -> int:
        return len(self._prefixes) + len(self._exact)

    @classmethod
    def from_text(cls, text: str) -> "CtyDB":
        db = cls()
        # N1MM's wl_cty.dat has "#" blocks that run over several lines up to
        # their own ";". They hold call-area zone hints for big countries
        # ("#   K: K0(4)[7],K6(3)[6],..."), which we keep per entity, then the
        # blocks are dropped so they don't split the records.
        db._collect_hints(text)
        text = re.sub(r"#[^;]*;", "", text)
        for record in text.split(";"):
            parts = record.split(":")
            if len(parts) < 9:
                continue
            name = parts[0].strip()
            cont = parts[3].strip().upper()
            primary = parts[7].strip().lstrip("*").upper()
            base = Entity(name, cont, primary, _int(parts[1]), _int(parts[2]))
            for alias in ":".join(parts[8:]).replace("\n", " ").split(","):
                alias = alias.strip().upper()
                if not alias:
                    continue
                exact = alias.startswith("=")
                alias = alias.lstrip("=")
                m = _CONT.search(alias)
                key = _OVERRIDE.split(alias, 1)[0].strip()
                if not key:
                    continue
                cq = _CQ.search(alias)
                itu = _ITU.search(alias)
                ent = base
                if m or cq or itu:
                    ent = Entity(
                        name,
                        m.group(1) if m else base.continent,
                        primary,
                        int(cq.group(1)) if cq else base.cq,
                        int(itu.group(1)) if itu else base.itu,
                    )
                if exact:
                    db._exact[key] = ent
                else:
                    db._prefixes[key] = ent
                    db._max_len = max(db._max_len, len(key))
        return db

    _HEADER = re.compile(
        r"^[^#\s][^:\n]*:\s*\d+:\s*\d+:\s*[A-Z]{2}:[^:\n]*:[^:\n]*:[^:\n]*:\s*\*?([A-Z0-9/]+):",
        re.M,
    )
    _HINT_BLOCK = re.compile(r"#\s*[A-Z0-9]+:([^;]*);")

    def _collect_hints(self, text: str) -> None:
        headers = [(m.start(), m.group(1).upper()) for m in self._HEADER.finditer(text)]
        for m in self._HINT_BLOCK.finditer(text):
            owner = None
            for pos, primary in headers:
                if pos > m.start():
                    break
                owner = primary
            if owner is None:
                continue
            for item in m.group(1).replace("\n", " ").split(","):
                item = item.strip().upper()
                key = _OVERRIDE.split(item, 1)[0].strip()
                if not key:
                    continue
                cq = _CQ.search(item)
                itu = _ITU.search(item)
                self._hints.setdefault(owner, []).append((
                    key,
                    int(cq.group(1)) if cq else None,
                    int(itu.group(1)) if itu else None,
                ))
        for lst in self._hints.values():
            lst.sort(key=lambda h: len(h[0]), reverse=True)

    def _apply_hints(self, ent: Entity, call: str) -> Entity:
        """Refine the zones from the call area (W6 -> zone 3, RA9H.. -> 18)."""
        hints = self._hints.get(ent.prefix)
        if not hints:
            return ent
        m = re.search(r"\d", call)
        if not m:
            return ent
        after = call[m.end():m.end() + 2]
        for pattern, cq, itu in hints:
            root = re.match(r"[A-Z]+", pattern)
            if not root:
                continue
            canon = root.group(0) + m.group(0) + after
            if canon.startswith(pattern):
                return Entity(
                    ent.name, ent.continent, ent.prefix,
                    cq if cq is not None else ent.cq,
                    itu if itu is not None else ent.itu,
                )
        return ent

    @classmethod
    def load(cls, path: str) -> "CtyDB":
        with open(path, encoding="latin-1") as fh:
            db = cls.from_text(fh.read())
        db.path = path
        return db

    def _by_prefix(self, text: str) -> Entity | None:
        for n in range(min(len(text), self._max_len), 0, -1):
            ent = self._prefixes.get(text[:n])
            if ent is not None:
                return ent
        return None

    def lookup(self, call: str) -> Entity | None:
        """Entity for a callsign (handles ``/P``, ``EA8/DL1ABC``, ``DL1ABC/EA8``)."""
        call = (call or "").strip().upper()
        if not call:
            return None
        ent = self._exact.get(call)
        if ent is not None:
            return ent
        parts = [p for p in call.split("/") if p]
        if len(parts) <= 1:
            ent = self._by_prefix(call)
            return self._apply_hints(ent, call) if ent else None
        # Drop "/P", "/QRP", "/1" (call-area digit) style suffixes.
        core = [p for p in parts if p not in _PLAIN_SUFFIXES and not p.isdigit()]
        if any(p in ("MM", "AM") for p in parts):
            return None  # maritime / aeronautical mobile: no entity
        if not core:
            return None
        if len(core) == 1:
            exact = self._exact.get(core[0])
            if exact:
                return exact
            ent = self._by_prefix(core[0])
            return self._apply_hints(ent, core[0]) if ent else None
        # Two parts: the shorter one is the location prefix (EA8/DL1ABC,
        # DL1ABC/EA8); on a tie, the first one.
        loc = min(core[:2], key=len)
        return self._by_prefix(loc)

    def continent(self, call: str) -> str | None:
        ent = self.lookup(call)
        return ent.continent if ent else None


def default_cty_paths() -> list[str]:
    """Where N1MM keeps its country file, in preference order."""
    from .worked import default_n1mm_dirs  # local: avoid an import cycle

    out = []
    for base in default_n1mm_dirs():
        out.append(os.path.join(base, "SupportFiles", "wl_cty.dat"))
        out.append(os.path.join(base, "SupportFiles", "cty.dat"))
    return out


def load_cty(path: str = "", n1mm_ini: str = "") -> CtyDB | None:
    """Load the configured CTY file, or N1MM's own one. None if not found."""
    candidates = [path] if path else []
    if n1mm_ini:
        base = os.path.dirname(n1mm_ini)
        candidates.append(os.path.join(base, "SupportFiles", "wl_cty.dat"))
        candidates.append(os.path.join(base, "SupportFiles", "cty.dat"))
    candidates += default_cty_paths()
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            try:
                db = CtyDB.load(candidate)
            except OSError as exc:
                log.warning("cty file %s unreadable: %s", candidate, exc)
                continue
            log.info("loaded %d cty entries from %s", len(db), candidate)
            return db
    log.warning("no cty file found - EU/DX marking disabled")
    return None


def wpx_prefix(call: str) -> str:
    """CQ WPX prefix of a callsign (S55OO -> S55, 3Z75HSC -> 3Z75, K1ABC -> K1).

    Rules: drop /P /M /QRP style suffixes; a portable location (EA8/DL1ABC or
    DL1ABC/EA8) is the prefix, with a 0 added if it has no digit; a call-area
    digit suffix replaces the digit (K1ABC/4 -> K4); otherwise the prefix is
    everything up to and including the last digit before the final letters.
    """
    call = (call or "").strip().upper()
    parts = [p for p in call.split("/") if p and p not in _PLAIN_SUFFIXES]
    if not parts:
        return ""
    area = next((p for p in parts[1:] if p.isdigit()), None)
    parts = [p for p in parts if not p.isdigit()]
    if not parts:
        return ""
    if len(parts) >= 2:
        loc = min(parts[:2], key=len)
        return loc if re.search(r"\d", loc) else loc + "0"
    base = parts[0]
    m = re.match(r"^(.*\d)[A-Z]*$", base)
    prefix = m.group(1) if m else base[:2] + "0"
    if area is not None:
        prefix = re.sub(r"\d+$", area, prefix)
    return prefix
