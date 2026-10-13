"""N1MM Logger+ network interface: RadioInfo listener and control sender."""

from __future__ import annotations

import logging
import socket
import threading
import time
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape as xml_escape

from .bands import band_from_khz, tens_of_hz_to_khz

log = logging.getLogger(__name__)


def _to_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _to_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_radio_info(data: bytes) -> dict | None:
    """Parse an N1MM <RadioInfo> datagram. Returns None for other packets."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None
    if root.tag != "RadioInfo":
        return None

    def text(tag: str) -> str | None:
        el = root.find(tag)
        return el.text if el is not None else None

    freq = _to_float(text("Freq"))
    tx_freq = _to_float(text("TXFreq"))
    return {
        "radio_nr": _to_int(text("RadioNr")),
        "freq_khz": tens_of_hz_to_khz(freq) if freq else None,
        "tx_freq_khz": tens_of_hz_to_khz(tx_freq) if tx_freq else None,
        "mode": (text("Mode") or "").strip().upper(),
        "active_radio": _to_int(text("ActiveRadioNr")),
        "station": (text("StationName") or "").strip(),
        "entry_window_hwnd": _to_int(text("EntryWindowHwnd")),
    }


def next_tune_khz(
    reported_khz: float | None,
    last_target_khz: float | None,
    seconds_since_last: float,
    step_hz: float,
) -> float | None:
    """Frequency for one +/-step of the VFO (step_hz may be negative).

    N1MM reports the radio in 10 Hz units, so after setting 14011.625 it may
    report 14011.62 - stepping from that would drift. Step from our own last
    target when it was set moments ago (the RadioInfo may not be back yet)
    or still matches the radio; otherwise (tuned by hand since) from the
    reported frequency.
    """
    base = reported_khz
    if last_target_khz is not None and (
        seconds_since_last < 3.0
        or reported_khz is None
        or abs(last_target_khz - reported_khz) < 0.015
    ):
        base = last_target_khz
    if not base:
        return None
    return round(base + step_hz / 1000.0, 3)


class RadioState:
    """Thread-safe holder for the most recent N1MM radio information."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._radios: dict[int, dict] = {}
        self._active: int | None = None
        self._last = 0.0

    def update(self, info: dict) -> None:
        with self._lock:
            nr = info.get("radio_nr")
            if nr is not None:
                self._radios[nr] = info
            if info.get("active_radio") is not None:
                self._active = info["active_radio"]
            self._last = time.time()

    # SO2R / SO2V: N1MM sends one RadioInfo per radio (RadioNr = the radio
    # the packet is about), each with that radio's own EntryWindowHwnd (the
    # A or B Entry Window). ``nr`` picks a radio; if N1MM hasn't reported
    # that radio (e.g. SO1R), the active one is used, as before.
    def _pick(self, nr: int | None) -> int:
        if nr is not None and nr in self._radios:
            return nr
        return self._active if self._active in self._radios else max(self._radios)

    def radio_numbers(self) -> list[int]:
        """Radios N1MM has reported (e.g. [1] or [1, 2])."""
        with self._lock:
            return sorted(self._radios)

    def active_radio_nr(self, nr: int | None = None) -> int:
        with self._lock:
            if not self._radios:
                return nr or 1
            return self._pick(nr)

    def entry_window_hwnd(self, nr: int | None = None) -> int | None:
        with self._lock:
            if not self._radios:
                return None
            return self._radios[self._pick(nr)].get("entry_window_hwnd")

    def snapshot(self, nr: int | None = None) -> dict | None:
        with self._lock:
            if not self._radios:
                return None
            nr = self._pick(nr)
            info = dict(self._radios[nr])
            info["age"] = time.time() - self._last
            info["band"] = band_from_khz(info.get("freq_khz"))
            return info


class RadioListener(threading.Thread):
    """Listens for N1MM RadioInfo UDP broadcasts."""

    def __init__(self, ip: str, port: int, state: RadioState, on_update=None) -> None:
        super().__init__(daemon=True, name="n1mm-rx")
        self.ip = ip
        self.port = port
        self.state = state
        self.on_update = on_update
        self._stop = threading.Event()
        self._sock: socket.socket | None = None

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass

    def run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except OSError:
            pass
        try:
            sock.bind((self.ip, self.port))
        except OSError as exc:
            log.error("cannot bind UDP %s:%s for N1MM info: %s", self.ip, self.port, exc)
            return
        sock.settimeout(1.0)
        self._sock = sock
        log.info("listening for N1MM RadioInfo on %s:%s", self.ip, self.port)
        while not self._stop.is_set():
            try:
                data, _addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            info = parse_radio_info(data)
            if info is None:
                continue
            self.state.update(info)
            if self.on_update is not None:
                try:
                    self.on_update(info)
                except Exception:
                    log.exception("radio update callback failed")
        try:
            sock.close()
        except OSError:
            pass


def radio_command_xml(command: str, radio_nr: int = 1, focus_hwnd: int | None = None) -> str:
    """N1MM ``<RadioCmd>`` packet: run ``command`` (macros like
    ``{CAT1ASC ZZFI05;}``) as if typed in that radio's Entry Window.

    Not in N1MM's UDP docs, but accepted on the control port (13064) and
    used by other N1MM control apps (e.g. the TouchPortal N1MM plugin).
    ``focus_hwnd`` is the Entry Window handle from RadioInfo.
    """
    return (
        f"<RadioCmd><RadioNr>{int(radio_nr or 1)}</RadioNr>"
        f"<FocusEntry>{int(focus_hwnd or 0)}</FocusEntry>"
        f"<RadioCommand>{xml_escape(command)}</RadioCommand></RadioCmd>"
    )


def cat_macro(cat: str, radio_nr: int = 1) -> str:
    """N1MM macro sending a raw ASCII CAT string to the radio: {CAT1ASC ...}."""
    return f"{{CAT{int(radio_nr or 1)}ASC {cat.strip()}}}"


class N1MMController:
    """Sends control packets (QSY) to N1MM on UDP 13064. Never transmits."""

    def __init__(self, ip: str, port: int, radionr: int = 1) -> None:
        self.addr = (ip, port)
        self.radionr = radionr
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass

    def _send(self, payload: str) -> None:
        self._sock.sendto(payload.encode("utf-8"), self.addr)

    def send_radio_command(
        self, command: str, radio_nr: int | None = None, focus_hwnd: int | None = None
    ) -> None:
        """Run an N1MM macro / command for a radio (see ``radio_command_xml``)."""
        self._send(radio_command_xml(command, radio_nr or self.radionr, focus_hwnd))

    def set_frequency(self, freq_khz: float, mousebutton: str | None = None,
                      radio_nr: int | None = None) -> None:
        """QSY a radio. N1MM docs: mouse button Left = radio 1, and when
        running SO2V/SO2R Right = radio 2 - so radio 2 gets "Right"."""
        nr = int(radio_nr or self.radionr or 1)
        if mousebutton is None:
            mousebutton = "Right" if nr == 2 else "Left"
        payload = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            "<radio_setfrequency>\n"
            "  <app>POUNCer</app>\n"
            f"  <radionr>{nr}</radionr>\n"
            f"  <frequency>{freq_khz:.3f}</frequency>\n"
            f"  <mousebutton>{mousebutton}</mousebutton>\n"
            "</radio_setfrequency>\n"
        )
        self._send(payload)

