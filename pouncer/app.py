"""Tkinter GUI for POUNCer."""

from __future__ import annotations

import logging
import queue
import time
import tkinter as tk
from tkinter import ttk

from . import __version__
from .bands import CONTEST_BANDS, DEFAULT_BAND_KHZ, band_from_khz
from .config import Config
from .flow import flow_positions
from .cty import Entity, load_cty, wpx_prefix
from .n1mm import (
    N1MMController, RadioListener, RadioState, cat_macro, next_tune_khz,
)
from .rbn import RBNClient, Spot, is_beacon, ports_for_modes
from .runhist import RARE_RUN_PCT, RunHistory
from .web import WebServer, pick_web_address
from .win32 import (
    force_foreground, is_n1mm_running, send_cw_speed, send_enter, send_filter_toggle,
    send_fkey,
    type_text,
    wipe_callsign,
)
from .worked import WorkedLog, n1mm_station_call

log = logging.getLogger(__name__)

REFRESH_MS = 500
TOUCH_MAX_COLUMNS = 2
WEB_MAX_SPOTS = 60
RADIO_ALIVE_SECONDS = 30
ON_FREQ_KHZ = 0.5  # a spot this close to the radio frequency = "tuned to it"
ON_FREQ_BG = "#0277bd"  # touch board: tile of the station we are tuned to  # RadioInfo newer than this => N1MM is running
FKEY_BG = "#2e7d32"          # key buttons: green at rest
FKEY_PRESSED_BG = "#c62828"  # ... red while / just after being pressed


class FlowFrame(ttk.Frame):
    """Frame that lays its children out left to right, wrapping onto new
    rows when the window gets narrow (see ``flow.flow_positions``).

    Children must be created with this frame as master and added with
    ``add()``; the frame sets its own height to fit the rows.
    """

    def __init__(self, master, padx: int = 6, pady: int = 4, **kw) -> None:
        super().__init__(master, **kw)
        self._items: list[tuple[tk.Widget, int]] = []
        self._padx, self._pady = padx, pady
        self._pending = False
        self._last: tuple | None = None
        self.bind("<Configure>", lambda e: self._schedule())

    def add(self, widget, gap: int = 0):
        """Add a child; ``gap`` = extra space before it inside a row."""
        self._items.append((widget, gap))
        # A child that changes size (e.g. a sort arrow) re-flows the rows.
        widget.bind("<Configure>", lambda e: self._schedule(), add="+")
        self._schedule()
        return widget

    def _schedule(self) -> None:
        if not self._pending:
            self._pending = True
            self.after_idle(self._layout)

    def _layout(self) -> None:
        self._pending = False
        width = max(self.winfo_width(), 1)
        sizes = [(w.winfo_reqwidth(), w.winfo_reqheight()) for w, _ in self._items]
        pos, height = flow_positions(
            sizes, width, self._padx, self._pady, [g for _, g in self._items]
        )
        key = (width, tuple(sizes), tuple(pos), height)
        if key == self._last:
            return
        self._last = key
        for (widget, _), (x, y) in zip(self._items, pos):
            widget.place(x=x, y=y)
        self.configure(height=max(height, 1))


class PouncerApp:
    def __init__(self, config: Config) -> None:
        self.cfg = config
        self.spot_queue: "queue.Queue[Spot]" = queue.Queue(maxsize=20000)
        self.status_queue: "queue.Queue[str]" = queue.Queue()
        self.spots: dict[tuple, Spot] = {}
        self.displayed: list[Spot] = []
        self.state = RadioState()
        self.worked = WorkedLog(
            config.worked_db, config.n1mm_ini, config.worked_refresh_seconds,
            n1mm_running=self._n1mm_running,
        )
        self._worked_hidden = 0
        # No [rbn] callsign in config.ini: use the one N1MM has.
        if not config.rbn_callsign:
            config.rbn_callsign = n1mm_station_call(config.n1mm_ini or None)
            if config.rbn_callsign:
                log.info("callsign %s taken from N1MM", config.rbn_callsign)
            else:
                log.warning("no callsign: set [rbn] callsign in config.ini")
        # How often each spotted call runs, from past contest logs.
        self.runhist = (
            RunHistory(config.run_history_dir or "cache", server=config.run_server,
                       run_list=config.run_list)
            if config.run_history else None
        )
        # Callsign -> continent (N1MM's wl_cty.dat) for EU / DX marking.
        self.cty = load_cty(config.cty_file, config.n1mm_ini)
        self.home_cont = (
            self.cty.continent(config.rbn_callsign) if self.cty else None
        ) or "EU"
        self._cty_cache: dict[str, Entity | None] = {}
        self.controller = N1MMController(
            config.n1mm_control_ip,
            config.n1mm_control_port,
            config.n1mm_radionr,
        )
        self.columns = (
            "time", "freq", "snr", "wpm", "call", "cont", "mult", "mode", "type",
            "spotter",
        )
        self.headings = {
            "time": "Time", "freq": "Freq kHz", "snr": "SNR dB", "wpm": "WPM",
            "call": "Callsign", "cont": "Cont", "mult": "Mult", "mode": "Mode",
            "type": "Type",
            "spotter": "Spotter",
        }
        self._sort_labels = {"time": "Time", "freq": "Freq", "snr": "SNR"}
        self._sort_buttons: dict[str, ttk.Button] = {}
        self._touch_sort_buttons: dict[str, tk.Button] = {}
        self.sort_col = config.sort if config.sort in self.columns else "snr"
        self.sort_reverse = self.sort_col == "snr"
        self.rbn_status = "starting"
        self._rbn_clients: dict[int, RBNClient] = {}
        self._listener: RadioListener | None = None
        self._dirty = True
        self._last_band: str | None = None
        self.touch_window: tk.Toplevel | None = None
        self.web: WebServer | None = None
        self.web_url = ""
        self._web_addr_checked = 0.0
        self._band_memory: dict[str, float] = {}
        self._last_radio_khz: float | None = None
        # Calls removed from the list by hand (long-press on the phone page),
        # per band; they stay hidden until POUNCer restarts.
        self._removed: set[tuple[str, str]] = set()
        self._tune_target: float | None = None  # last -/+ step we sent
        self._tune_time = 0.0  # last radio kHz per band
        # RX filter width POUNCer last set via Alt+' (None = unknown)
        self._filter_width: str | None = config.filter_start or None
        self._web_queue: "queue.Queue[tuple]" = queue.Queue()
        self._web_cache: dict = {}
        self._build_ui()

    def _build_ui(self) -> None:
        self.root = tk.Tk()
        self.root.title(f"POUNCer {__version__} - RBN band follower for N1MM")
        self.root.geometry("940x520")
        # Narrow windows are fine: text lines wrap and the buttons flow onto
        # more rows instead of being cut off at the right edge.
        self.root.minsize(420, 300)

        header = ttk.Frame(self.root, padding=(8, 6, 8, 2))
        header.pack(fill="x")
        self.n1mm_label = self._wrap_label(header, "N1MM: waiting for radio info...")
        self.rbn_label = self._wrap_label(header, "RBN: starting...")
        self.log_label = self._wrap_label(header, "Log: -")

        # Sort, options, keys and actions: one flowing block of controls.
        outer = ttk.Frame(self.root, padding=(8, 4))
        outer.pack(fill="x")
        flow = FlowFrame(outer)
        flow.pack(fill="x")
        flow.add(ttk.Label(flow, text="Sort:"))
        for col in ("time", "freq", "snr"):
            btn = flow.add(ttk.Button(
                flow, text=self._sort_labels[col],
                command=lambda c=col: self._set_sort(c),
            ))
            self._sort_buttons[col] = btn
        self._update_sort_buttons()

        self.set_call_var = tk.BooleanVar(value=self.cfg.set_callsign)
        flow.add(ttk.Checkbutton(
            flow, text="Also enter callsign in N1MM", variable=self.set_call_var
        ), gap=12)

        self.hide_worked_var = tk.BooleanVar(value=self.cfg.worked_hide)
        flow.add(ttk.Checkbutton(
            flow, text="Hide worked (same band+mode)",
            variable=self.hide_worked_var, command=self._on_toggle_worked,
        ), gap=12)

        if self.cfg.fkeys:
            flow.add(ttk.Label(flow, text="Keys:"), gap=12)
            for key, label in self.cfg.fkeys:
                flow.add(ttk.Button(
                    flow, text=self._key_text(key, label, " "),
                    command=lambda k=key: self._press_key(k),
                ))

        flow.add(ttk.Button(flow, text="Clear spots", command=self._clear_spots), gap=12)
        flow.add(ttk.Button(flow, text="Reconnect log", command=self._reconnect_log))
        flow.add(ttk.Button(flow, text="Touch view", command=self._toggle_touch_view))

        widths = {
            "time": 70, "freq": 90, "snr": 70, "wpm": 60,
            "call": 130, "cont": 50, "mult": 45, "mode": 70, "type": 80,
            "spotter": 100,
        }
        tree_frame = ttk.Frame(self.root, padding=(8, 0, 8, 4))
        tree_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            tree_frame, columns=self.columns, show="headings", selectmode="browse"
        )
        for col in self.columns:
            anchor = "e" if col in ("freq", "snr", "wpm") else "w"
            self.tree.heading(col, text=self.headings[col], command=lambda c=col: self._on_sort(c))
            self.tree.column(col, width=widths[col], anchor=anchor, stretch=(col == "call"))
        self._update_headings()
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.tag_configure("strong", foreground="#005f00")
        self.tree.tag_configure("dx", background="#fff3e0")
        self.tree.tag_configure("onfreq", background="#b3e5fc")  # tuned to it
        if self.cfg.trigger == "double":
            self.tree.bind("<Double-1>", self._on_qsy)
        else:
            self.tree.bind("<ButtonRelease-1>", self._on_qsy)

        bottom = ttk.Frame(self.root, padding=(8, 2))
        bottom.pack(fill="x")
        self.web_label = self._wrap_label(bottom, "Phone view: starting...")
        self.status_label = self._wrap_label(bottom, "")

    @staticmethod
    def _wrap_label(parent, text: str) -> ttk.Label:
        """A full-width label that wraps onto more lines when the window is narrow."""
        label = ttk.Label(parent, text=text, anchor="w", justify="left")
        label.pack(fill="x")
        label.bind(
            "<Configure>",
            lambda e, lbl=label: lbl.configure(wraplength=max(e.width - 4, 60)),
        )
        return label

    def _on_sort(self, col: str) -> None:
        self._set_sort(col)

    def _set_sort(self, col: str) -> None:
        if col == self.sort_col:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_col = col
            self.sort_reverse = col in ("snr", "time", "mult")
        self._update_headings()
        self._update_sort_buttons()
        self._dirty = True

    def _update_headings(self) -> None:
        for col in self.columns:
            text = self.headings[col]
            if col == self.sort_col:
                text += " \u25bc" if self.sort_reverse else " \u25b2"
            self.tree.heading(col, text=text)

    def _update_sort_buttons(self) -> None:
        for col, btn in self._sort_buttons.items():
            arrow = ""
            if col == self.sort_col:
                arrow = " \u25bc" if self.sort_reverse else " \u25b2"
            btn.configure(text=self._sort_labels[col] + arrow)
        for col, btn in self._touch_sort_buttons.items():
            active = col == self.sort_col
            btn.configure(bg="#2e7d32" if active else "#263238")

    def _sort_key(self, col: str):
        if col == "freq":
            return lambda s: s.freq_khz
        if col == "snr":
            return lambda s: s.snr
        if col == "wpm":
            return lambda s: s.wpm
        if col == "time":
            return lambda s: s.time_utc
        if col == "call":
            return lambda s: s.dxcall.upper()
        if col == "mode":
            return lambda s: s.mode.upper()
        if col == "type":
            return lambda s: s.spot_type.upper()
        if col == "spotter":
            return lambda s: s.spotter.upper()
        if col == "mult":
            return lambda s: self._mults(s)
        if col == "cont":
            return lambda s: self._continent(s.dxcall) or "~"
        return lambda s: s.freq_khz

    def _clear_spots(self) -> None:
        self.spots.clear()
        self._dirty = True

    def _on_toggle_worked(self) -> None:
        self._dirty = True

    def _n1mm_running(self) -> bool | None:
        """Is N1MM Logger+ running? A recent RadioInfo packet proves it;
        otherwise fall back to the Windows process scan."""
        snap = self.state.snapshot()
        if snap and snap.get("age") is not None and snap["age"] < RADIO_ALIVE_SECONDS:
            return True
        return is_n1mm_running()

    def _reconnect_log(self) -> None:
        """Re-read which log N1MM has open and reload it now."""
        self.worked.request_recheck()
        self.worked.maybe_reload()
        self._dirty = True
        if self.worked.log_open:
            msg = f"Log reconnected: {self.worked.db_name} ({self.worked.status})"
        else:
            msg = self.worked.log_message
        self.status_label.configure(text=msg)

    def _desired_rbn_ports(self) -> set[int]:
        return ports_for_modes(
            self.cfg.follow_modes, self.cfg.rbn_port, self.cfg.rbn_ft8_port
        )

    def _ensure_rbn_clients(self) -> None:
        desired = self._desired_rbn_ports()
        for port in [p for p in self._rbn_clients if p not in desired]:
            self._rbn_clients.pop(port).stop()
        for port in sorted(desired):
            if port in self._rbn_clients:
                continue
            client = RBNClient(
                self.cfg.rbn_host,
                port,
                self.cfg.rbn_callsign,
                self.cfg.rbn_spotter,
                self.spot_queue,
                on_status=self.status_queue.put,
            )
            client.start()
            self._rbn_clients[port] = client
            log.info("RBN client started on %s:%s", self.cfg.rbn_host, port)

    def _on_qsy(self, event) -> None:
        item = self.tree.identify_row(event.y)
        if not item:
            return
        try:
            spot = self.displayed[int(item)]
        except (ValueError, IndexError):
            return
        self._act_on_spot(spot)

    def _act_on_spot(self, spot: Spot) -> None:
        hwnd = self.state.entry_window_hwnd()
        focused = False
        if self.cfg.focus_n1mm and hwnd:
            focused = force_foreground(hwnd)
            if focused:
                time.sleep(0.15)

        try:
            self.controller.set_frequency(spot.freq_khz)
        except OSError as exc:
            self.status_label.configure(text=f"N1MM QSY failed: {exc}")
            return

        msg = f"QSY {spot.freq_khz:.1f} kHz -> {spot.dxcall}"
        if self.set_call_var.get():
            can_type = focused or not self.cfg.focus_n1mm
            if can_type:
                if self.cfg.wipe_before_call:
                    wipe_callsign()
                    time.sleep(0.05)
                type_text(spot.dxcall)
                msg += " (call typed, no TX)"
            else:
                msg += " (call skipped: N1MM not focused)"
        if self.cfg.focus_n1mm:
            msg += " [N1MM focused]" if focused else " [N1MM focus failed]"
        self.status_label.configure(text=msg)

    def _tick(self) -> None:
        self._drain_status()
        self._drain_spots()
        self._ensure_rbn_clients()
        if self.worked.maybe_reload():
            self._dirty = True
        self._prune()
        self._remember_band_freq()
        # Retuning moves the "tuned to" highlight, so redraw when it changes.
        khz = self._radio_khz()
        if khz != self._last_radio_khz:
            self._last_radio_khz = khz
            self._dirty = True
        self._update_labels()
        if self._dirty:
            self._rebuild()
            self._update_touch()
            self._dirty = False
        self._drain_web()
        if self.web is not None:
            self._web_cache = self._build_web_payload()
            # ZeroTier can come up after POUNCer starts: re-check every minute.
            if time.time() - self._web_addr_checked >= 60:
                self._refresh_web_address()
        self.root.after(REFRESH_MS, self._tick)

    def _build_web_payload(self) -> dict:
        spots = sorted(
            self._visible_spots(),
            key=lambda s: s.snr if s.snr is not None else -999,
            reverse=True,
        )[:WEB_MAX_SPOTS]
        return {
            "band": self._current_band(),
            "freq": self._radio_khz(),
            "home_cont": self.home_cont,
            "contest_bands": list(CONTEST_BANDS),
            "tune_step": self.cfg.tune_step_hz,
            "rare_run": RARE_RUN_PCT,
            "filter": self._filter_width,
            # "cat": NAR/WIDE set the filter (always send); "toggle": Alt+'
            "filter_mode": (
                "cat" if self.cfg.filter_narrow_cat and self.cfg.filter_wide_cat
                else "toggle"
            ),
            "modes": list(self.cfg.follow_modes),
            "worked": self.worked.status,
            "qsos": {
                "total": self.worked.qso_count,
                "bands": [
                    {"band": b, "count": n} for b, n in self.worked.band_qsos()
                ],
            },
            "log": {
                "open": self.worked.log_open,
                "state": self.worked.log_state,
                "message": self.worked.log_message,
                "name": self.worked.db_name,
            },
            "fkeys": [{"key": k, "label": v} for k, v in self.cfg.fkeys],
            "spots": [
                {
                    "call": s.dxcall, "freq": s.freq_khz, "mode": s.mode,
                    "snr": s.snr, "wpm": s.wpm, "time": s.time_utc,
                    "cont": self._continent(s.dxcall),
                    "country": self._country(s.dxcall),
                    "dx": self._is_dx(s.dxcall),
                    "mult": self._mults(s),
                    "run": self._run_pct(s),
                }
                for s in spots
            ],
        }

    def _web_spots(self) -> dict:
        return self._web_cache

    def _web_qsy(self, call: str, freq: float, mode: str) -> None:
        self._web_queue.put(("qsy", call, float(freq), mode))

    def _web_key(self, key: str) -> None:
        self._web_queue.put(("key", key))

    def _web_reconnect(self) -> None:
        self._web_queue.put(("reconnect",))

    def _web_band(self, band: str) -> None:
        self._web_queue.put(("band", band))

    def _web_tune(self, hz: int) -> None:
        self._web_queue.put(("tune", int(hz)))

    def _web_filter(self, width: str, fix: bool) -> None:
        self._web_queue.put(("filter", width, bool(fix)))

    def _web_hide(self, call: str, freq: float, hide: bool) -> None:
        self._web_queue.put(("hide", call, float(freq), hide))

    def _drain_web(self) -> None:
        while True:
            try:
                item = self._web_queue.get_nowait()
            except queue.Empty:
                return
            if item[0] == "key":
                self._press_key(item[1])
                continue
            if item[0] == "reconnect":
                self._reconnect_log()
                continue
            if item[0] == "band":
                self._qsy_band(item[1])
                continue
            if item[0] == "tune":
                self._tune(item[1])
                continue
            if item[0] == "filter":
                self._set_filter(item[1], item[2])
                continue
            if item[0] == "hide":
                self._set_removed(item[1], item[2], item[3])
                continue
            _, call, freq, mode = item
            spot = Spot(dxcall=call, freq_khz=freq, mode=mode or "CW",
                        snr=None, wpm=None, spot_type="", spotter="",
                        time_utc="", received=time.time())
            self._act_on_spot(spot)

    @staticmethod
    def _key_text(key: str, label: str, sep: str) -> str:
        """Button text: key name (F2 / ENTER) plus its label, e.g. "F2 RPRT"."""
        big = key.upper()
        if label and label.upper() != big:
            return f"{big}{sep}{label}"
        return big

    def _press_touch_key(self, key: str, btn: tk.Button) -> None:
        """Touch-board key: flash the button red, then press the key."""
        btn.configure(bg=FKEY_PRESSED_BG)
        btn.after(800, lambda: btn.winfo_exists() and btn.configure(bg=FKEY_BG))
        self._press_key(key)

    def _press_key(self, key: str) -> None:
        """A TX / log button was pressed: focus N1MM, then send the key.

        ``key`` is ``F1``-``F12`` (function-key message; transmits) or
        ``Enter`` (logs the QSO in N1MM; does not transmit).
        """
        hwnd = self.state.entry_window_hwnd()
        focused = False
        if self.cfg.focus_n1mm and hwnd:
            focused = force_foreground(hwnd)
            if focused:
                time.sleep(0.15)
        if not self._send_key(key):
            self.status_label.configure(text=f"Unknown key: {key}")
            return
        msg = f"Sent {key} to N1MM"
        if key == "PgUp":
            msg = "CW speed up (PgUp)"
        elif key == "PgDn":
            msg = "CW speed down (PgDn)"
        elif key != "Enter":
            msg += " (transmitting)"
        if self.cfg.focus_n1mm and hwnd and not focused:
            msg += " [N1MM focus failed]"
        self.status_label.configure(text=msg)

    @staticmethod
    def _send_key(key: str) -> bool:
        if key.upper().startswith("F") and key[1:].isdigit():
            return send_fkey(int(key[1:]))
        if key.lower() in ("enter", "return", "cr"):
            return send_enter()
        if key == "PgUp":
            return send_cw_speed(True)
        if key == "PgDn":
            return send_cw_speed(False)
        return False

    def _drain_status(self) -> None:
        while True:
            try:
                self.rbn_status = self.status_queue.get_nowait()
            except queue.Empty:
                break

    def _drain_spots(self) -> None:
        got = 0
        while got < 500:
            try:
                spot = self.spot_queue.get_nowait()
            except queue.Empty:
                break
            self.spots[spot.key] = spot
            got += 1
        if got:
            self._dirty = True

    def _prune(self) -> None:
        cutoff = time.time() - self.cfg.max_age_minutes * 60
        stale = [k for k, s in self.spots.items() if s.received < cutoff]
        for key in stale:
            del self.spots[key]
        if stale:
            self._dirty = True

    def _entity(self, call: str) -> Entity | None:
        """Country-file entity for a callsign (cached); None if unknown."""
        key = call.upper()
        if key not in self._cty_cache:
            if len(self._cty_cache) > 20000:
                self._cty_cache.clear()
            self._cty_cache[key] = self.cty.lookup(key) if self.cty else None
        return self._cty_cache[key]

    def _continent(self, call: str) -> str | None:
        ent = self._entity(call)
        return ent.continent if ent else None

    def _country(self, call: str) -> str:
        ent = self._entity(call)
        return ent.name if ent else ""

    def _mults(self, s: Spot) -> int:
        """New multipliers this spot would give on its band: 0, 1 or 2
        (double = e.g. new zone + new country in CQWW)."""
        ent = self._entity(s.dxcall)
        zone = None
        if ent:
            zone = ent.itu if self.worked.zone_is_itu else ent.cq
        return self.worked.new_mults(
            s.freq_khz, ent.prefix if ent else None, zone, wpx_prefix(s.dxcall),
        )

    def _run_pct(self, s: Spot) -> int | None:
        """% of past QSOs (same contest + mode) this call made running."""
        if self.runhist is None:
            return None
        return self.runhist.get(s.dxcall, self.worked.contest_name, s.mode)

    def _is_dx(self, call: str) -> bool:
        """DX = a known continent other than ours (EU for S5)."""
        cont = self._continent(call)
        return cont is not None and cont != self.home_cont

    def _tune(self, hz: int) -> None:
        """Footer -/+ buttons: move the VFO by hz via N1MM (no TX)."""
        now = time.time()
        khz = next_tune_khz(
            self._radio_khz(), self._tune_target, now - self._tune_time, hz,
        )
        if khz is None:
            self.status_label.configure(text="Tune: no radio frequency from N1MM yet")
            return
        try:
            self.controller.set_frequency(khz)
        except OSError as exc:
            self.status_label.configure(text=f"N1MM QSY failed: {exc}")
            return
        self._tune_target, self._tune_time = khz, now
        self.status_label.configure(text=f"Tune {hz:+d} Hz -> {khz:.3f} kHz")

    def _filter_cat(self, width: str) -> str:
        return self.cfg.filter_narrow_cat if width == "narrow" else self.cfg.filter_wide_cat

    def _set_filter(self, width: str, fix: bool = False) -> None:
        """Footer NAR/WIDE: set the radio's RX filter.

        With ``[ui] filter_narrow_cat`` / ``filter_wide_cat`` set, the CAT
        string is sent through N1MM (``{CATnASC ...}`` in a RadioCmd packet):
        an absolute setting, sent on every tap. Otherwise N1MM's Alt+'
        wide/narrow toggle is pressed: we remember the width we set and send
        the toggle only when it changes; ``fix`` toggles the radio without
        changing the remembered width (resync).
        """
        cat = self._filter_cat(width)
        if cat:
            nr = self.state.active_radio_nr()
            try:
                self.controller.send_radio_command(
                    cat_macro(cat, nr), nr, self.state.entry_window_hwnd()
                )
            except OSError as exc:
                self.status_label.configure(text=f"Filter: N1MM send failed: {exc}")
                return
            self._filter_width = width
            self.status_label.configure(text=f"Filter {width} (CAT {cat} via N1MM)")
            return
        if not fix and width == self._filter_width:
            self.status_label.configure(text=f"Filter already {width}")
            return
        hwnd = self.state.entry_window_hwnd()
        if self.cfg.focus_n1mm and hwnd and force_foreground(hwnd):
            time.sleep(0.15)
        if not send_filter_toggle():
            self.status_label.configure(text="Filter toggle needs Windows")
            return
        if fix:
            self.status_label.configure(text="Filter toggled (Alt+'), label kept")
            return
        self._filter_width = width
        self.status_label.configure(text=f"Filter {width} (Alt+')")

    def _set_removed(self, call: str, freq_khz: float, removed: bool) -> None:
        """Remove a call from the list on that band (or bring it back)."""
        band = band_from_khz(freq_khz)
        if not band:
            return
        key = (call.strip().upper(), band)
        if removed:
            self._removed.add(key)
            msg = f"Removed {key[0]} on {band} from the list"
        else:
            self._removed.discard(key)
            msg = f"{key[0]} on {band} is back in the list"
        self._dirty = True
        self.status_label.configure(text=msg)

    def _remember_band_freq(self) -> None:
        """Remember the radio's last frequency on each band (for band QSY)."""
        snap = self.state.snapshot()
        if snap and snap.get("freq_khz") and snap.get("band"):
            self._band_memory[snap["band"]] = snap["freq_khz"]

    def _qsy_band(self, band: str) -> None:
        """Band picker: QSY to the last frequency used on ``band`` (or a CW
        default). Only a frequency change - never transmits."""
        if band not in CONTEST_BANDS:
            self.status_label.configure(text=f"Unknown band: {band}")
            return
        khz = self._band_memory.get(band, DEFAULT_BAND_KHZ[band])
        try:
            self.controller.set_frequency(khz)
        except OSError as exc:
            self.status_label.configure(text=f"N1MM QSY failed: {exc}")
            return
        self.status_label.configure(text=f"Band {band}: QSY {khz:.1f} kHz")

    def _radio_khz(self) -> float | None:
        """The radio's current frequency in kHz (from N1MM RadioInfo)."""
        snap = self.state.snapshot()
        return (snap or {}).get("freq_khz") or None

    def _current_band(self) -> str | None:
        snap = self.state.snapshot()
        return (snap or {}).get("band")

    def _update_labels(self) -> None:
        snap = self.state.snapshot()
        if snap and snap.get("freq_khz"):
            self.n1mm_label.configure(
                text=(
                    f"N1MM: {snap['freq_khz']:.2f} kHz  "
                    f"{snap.get('band') or '?'}  {snap.get('mode') or ''}"
                    f"   (updated {snap['age']:.0f}s ago)"
                )
            )
        else:
            self.n1mm_label.configure(
                text="N1MM: no radio info (enable Config > Broadcast Data > Radio to 127.0.0.1:12060)"
            )
        band = self._current_band()
        if self.worked.log_open or self.worked.log_state == "unknown":
            worked = f"Log: {self.worked.db_name or '-'}   worked: {self.worked.status}"
            if self.worked.log_open:
                per_band = " ".join(f"{b} {n}" for b, n in self.worked.band_qsos())
                worked += f"   QSOs: {per_band or '-'} (total {self.worked.qso_count})"
        else:
            worked = f"LOG: {self.worked.log_message}"
        self.rbn_label.configure(
            text=(
                f"RBN: {self.rbn_status}   spotter={self.cfg.rbn_spotter or 'ALL'}"
                f"   mode={','.join(self.cfg.follow_modes)}"
                f"   band={band or 'ALL'}   spots={len(self.displayed)}/{len(self.spots)}"
                f"   hidden={self._worked_hidden}"
            )
        )
        self.log_label.configure(text=worked)

    def _visible_spots(self) -> list[Spot]:
        """Spots matching the current band, modes and worked filter."""
        band = self._current_band()
        if band != self._last_band:
            self._last_band = band
        modes = {m.upper() for m in self.cfg.follow_modes}
        items = [
            s for s in self.spots.values()
            if (band is None or band_from_khz(s.freq_khz) == band)
            and s.mode.upper() in modes
            and not (self.cfg.hide_beacons and is_beacon(s))
            and (s.dxcall.upper(), band_from_khz(s.freq_khz)) not in self._removed
        ]
        if self.hide_worked_var.get():
            kept = []
            hidden = 0
            for s in items:
                if self.worked.is_worked(s.dxcall, s.freq_khz, s.mode):
                    hidden += 1
                else:
                    kept.append(s)
            items = kept
            self._worked_hidden = hidden
        else:
            self._worked_hidden = 0
        return items

    def _sorted_visible(self) -> list[Spot]:
        items = self._visible_spots()
        key = self._sort_key(self.sort_col)
        present = [s for s in items if key(s) is not None]
        missing = [s for s in items if key(s) is None]
        present.sort(key=key, reverse=self.sort_reverse)
        return present + missing

    def _on_freq_spot(self, spots: list[Spot]) -> Spot | None:
        """The spot we are tuned to: nearest to the radio, within ON_FREQ_KHZ."""
        khz = self._radio_khz()
        if not khz:
            return None
        best = min(spots, key=lambda s: abs(s.freq_khz - khz), default=None)
        if best is None or abs(best.freq_khz - khz) > ON_FREQ_KHZ:
            return None
        return best

    def _rebuild(self) -> None:
        items = self._sorted_visible()
        tuned = self._on_freq_spot(items)

        self.tree.delete(*self.tree.get_children())
        self.displayed = items
        for idx, s in enumerate(items):
            tags = ("strong",) if s.snr is not None and s.snr >= 20 else ()
            if self._is_dx(s.dxcall):
                tags += ("dx",)
            if s is tuned:
                tags += ("onfreq",)
            self.tree.insert(
                "", "end", iid=str(idx), tags=tags,
                values=(
                    s.time_utc,
                    f"{s.freq_khz:.1f}",
                    "" if s.snr is None else s.snr,
                    "" if s.wpm is None else s.wpm,
                    s.dxcall,
                    self._continent(s.dxcall) or "",
                    {0: "", 1: "M", 2: "MM"}.get(self._mults(s), ""),
                    s.mode,
                    s.spot_type,
                    s.spotter,
                ),
            )

    def _toggle_touch_view(self) -> None:
        if self.touch_window is not None and self.touch_window.winfo_exists():
            self._close_touch_view()
            return
        self._build_touch_view()

    def _close_touch_view(self) -> None:
        if self.touch_window is not None:
            self.touch_window.destroy()
        self.touch_window = None
        self._touch_sort_buttons = {}

    def _build_touch_view(self) -> None:
        win = tk.Toplevel(self.root)
        self.touch_window = win
        win.title("POUNCer - touch view")
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        win.geometry(f"{sw}x{sh}+0+0")
        win.configure(bg="#101418")
        sorts = tk.Frame(win, bg="#101418")
        sorts.pack(fill="x", padx=6, pady=(8, 2))
        tk.Label(
            sorts, text="Sort", bg="#101418", fg="#888888", font=("Segoe UI", 12)
        ).pack(side="left", padx=(0, 8))
        for col, label in (("time", "Time"), ("freq", "Freq"), ("snr", "SNR")):
            btn = tk.Button(
                sorts, text=label, font=("Segoe UI", 15, "bold"),
                bg="#263238", fg="#ffffff", activebackground="#2e7d32",
                activeforeground="#ffffff", relief="flat", bd=0, padx=20, pady=6,
                command=lambda c=col: self._set_sort(c),
            )
            btn.pack(side="left", padx=4)
            self._touch_sort_buttons[col] = btn
        if self.cfg.fkeys:
            bar = tk.Frame(win, bg="#101418")
            bar.pack(fill="x", padx=6, pady=(8, 2))
            tk.Label(
                bar, text="Keys", bg="#101418", fg="#888888", font=("Segoe UI", 12)
            ).pack(side="left", padx=(0, 8))
            for key, label in self.cfg.fkeys:
                btn = tk.Button(
                    bar, text=self._key_text(key, label, "\n"), font=("Segoe UI", 18, "bold"),
                    bg=FKEY_BG, fg="#ffffff", activebackground=FKEY_PRESSED_BG,
                    activeforeground="#ffffff", relief="flat", bd=0,
                    padx=28, pady=8,
                )
                btn.configure(command=lambda k=key, b=btn: self._press_touch_key(k, b))
                btn.pack(side="left", padx=4)
        cols = max(1, min(self.cfg.touch_columns, TOUCH_MAX_COLUMNS))
        rows = max(1, self.cfg.touch_rows)
        self._touch_cols = cols
        self._touch_rows = rows
        self._touch_capacity = cols * rows
        self._touch_wrap = max(120, int(sw / cols) - 60)
        board = tk.Frame(win, bg="#101418")
        board.pack(fill="both", expand=True, padx=6, pady=6)
        self._touch_board = board
        for r in range(rows):
            board.rowconfigure(r, weight=1, uniform="trow")
        for c in range(cols):
            board.columnconfigure(c, weight=1, uniform="tcol")
        win.bind("<Escape>", lambda _e: self._close_touch_view())
        win.protocol("WM_DELETE_WINDOW", self._close_touch_view)
        self._update_sort_buttons()
        self._update_touch()

    def _update_touch(self) -> None:
        if self.touch_window is None or not self.touch_window.winfo_exists():
            return
        for child in self._touch_board.winfo_children():
            child.destroy()
        spots = self._sorted_visible()[: self._touch_capacity]
        tuned = self._on_freq_spot(spots)
        cols = self._touch_cols
        rows = self._touch_rows
        font = ("Segoe UI", self.cfg.touch_font, "bold")
        for idx, s in enumerate(spots):
            row, col = idx % rows, idx // rows
            snr = "--" if s.snr is None else f"{s.snr} dB"
            strong = s.snr is not None and s.snr >= 20
            bg = "#1b5e20" if strong else "#263238"
            if s is tuned:
                bg = ON_FREQ_BG
            text = f"{s.dxcall}\n{s.freq_khz:.1f} {s.mode} {snr}"
            fg = "#ffeb3b" if self._is_dx(s.dxcall) else "#ffffff"  # DX yellow
            btn = tk.Button(
                self._touch_board, text=text, font=font,
                bg=bg, fg=fg, activebackground="#2e7d32",
                activeforeground=fg, relief="flat", bd=0,
                wraplength=self._touch_wrap, justify="center",
                command=lambda sp=s: self._act_on_spot(sp),
            )
            btn.grid(row=row, column=col, sticky="nsew", padx=4, pady=4)

    def run(self) -> None:
        if self.cfg.touch_view:
            self._build_touch_view()
        self._listener = RadioListener(
            self.cfg.n1mm_listen_ip, self.cfg.n1mm_listen_port, self.state
        )
        self._listener.start()
        self._ensure_rbn_clients()
        if self.cfg.web_enabled:
            self._start_web()
        else:
            self.web_label.configure(text="Phone view: disabled ([web] enabled = false)")
        self.root.after(REFRESH_MS, self._tick)
        try:
            self.root.mainloop()
        finally:
            self._shutdown()

    def _start_web(self) -> None:
        try:
            self.web = WebServer(
                self.cfg.web_host, self.cfg.web_port,
                self._web_spots, self._web_qsy, self._web_key,
                self._web_reconnect, self._web_band, self._web_hide,
                self._web_tune, self._web_filter,
            )
            self.web.start()
        except OSError as exc:
            log.warning(
                "web server failed to start on %s:%s: %s",
                self.cfg.web_host, self.cfg.web_port, exc,
            )
            self.web = None
            self.web_url = ""
            self.web_label.configure(
                text=f"Phone view: FAILED to listen on port {self.cfg.web_port} "
                     f"({exc}) - port in use? Change [web] port in config.ini"
            )
            return
        self._refresh_web_address()
        log.info("phone view: %s", self.web_url)

    def _refresh_web_address(self) -> None:
        """Work out the URL to show for the phone view (ZeroTier, else LAN)."""
        if self.web is None:
            return
        port = self.web.port
        ip, network = pick_web_address(self.cfg.web_host)
        self.web_url = f"http://{ip}:{port}"
        self.web_label.configure(
            text=(
                f"Phone view: {self.web_url}   ({network})"
                f"   listening on {self.cfg.web_host or '0.0.0.0'}:{port}"
            )
        )
        self._web_addr_checked = time.time()

    def _shutdown(self) -> None:
        if self.web is not None:
            self.web.stop()
            self.web = None
        for client in self._rbn_clients.values():
            client.stop()
        self._rbn_clients.clear()
        if self._listener is not None:
            self._listener.stop()
        self.controller.close()
