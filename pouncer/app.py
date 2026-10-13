"""Tkinter GUI for POUNCer."""

from __future__ import annotations

import logging
import dataclasses
import queue
import time
import tkinter as tk
from tkinter import ttk

from . import __version__
from .bands import CONTEST_BANDS, DEFAULT_BAND_KHZ, band_from_khz
from .config import Config
from .flow import flow_positions
from . import lights
from .cty import Entity, load_cty, wpx_prefix
from .n1mm import (
    N1MMController, RadioListener, RadioState, cat_macro, next_tune_khz,
)
from .rbn import OnFreqTracker, RBNClient, Spot, SpotterFilter, is_beacon, ports_for_modes
from .runhist import RARE_HOURS, RARE_RUN_PCT, RunHistory
from . import summary as summarymod
from .team import Team
from .update import Updater
from .web import WebServer, pick_web_address
from .win32 import (
    focus_snapshot, force_foreground, is_n1mm_running, restore_focus, send_cw_speed,
    send_enter, send_filter_toggle, window_pid,
    send_fkey,
    type_text,
    clear_focused_edit,
    wipe_callsign,
)
from .worked import WorkedLog, n1mm_station_call, same_station

log = logging.getLogger(__name__)

REFRESH_MS = 500
WEB_MAX_SPOTS = 60
WEB_VIEW_S = 10  # keep building a band a phone looks at this long after its last ask
RADIO_ALIVE_SECONDS = 30
ON_FREQ_KHZ = 0.5  # a spot this close to the radio frequency = "tuned to it"
ON_FREQ_STAY_KHZ = 0.5  # RBN re-spot within this = station still on that frequency
ON_FREQ_GAP_S = 20 * 60  # ... and within 20 min of the last spot (skimmers re-spot sparsely)


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
        # How long each station has been calling on one frequency, from RBN
        self._on_freq = OnFreqTracker(ON_FREQ_STAY_KHZ, ON_FREQ_GAP_S)
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
        self.updater = Updater(__version__, enabled=config.auto_update)
        # Every tapped station (for the contest summary): cache\taps.jsonl
        self.taps = summarymod.TapLog(config.run_history_dir or "cache")
        if self.runhist is not None and config.warmup:
            self.runhist.warm_up()  # every station of CQWW / ARRL DX / WPX CW
        # Callsign -> continent (N1MM's wl_cty.dat) for EU / DX marking.
        self.cty = load_cty(config.cty_file, config.n1mm_ini)
        self._home_call: str | None = None
        self._home_cont = "EU"
        # S53WW, or skimmers on our continent while S53WW is quiet (backup)
        self.spotter_filter = SpotterFilter(
            config.rbn_spotter, config.backup_spotters,
            continent_of=lambda call: self._continent(call),
            home_cont=lambda: self.home_cont,
            quiet_s=config.backup_after_minutes * 60,
        )
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
        self.sort_col = config.sort if config.sort in self.columns else "snr"
        self.sort_reverse = self.sort_col == "snr"
        self.rbn_status = "starting"
        self.team: Team | None = None  # other POUNCers on the network
        self._team_queue: "queue.Queue[Spot]" = queue.Queue(maxsize=5000)
        self._last_own_spot = 0.0
        self._rbn_clients: dict[int, RBNClient] = {}
        self._listener: RadioListener | None = None
        self._dirty = True
        self._last_band: str | None = None
        self.web: WebServer | None = None
        self.web_url = ""
        self._web_addr_checked = 0.0
        self._band_memory: dict[tuple[int, str], float] = {}  # (radio, band) -> kHz
        # SO2R: radio the main window acts on / follows (1 = A, 2 = B); the
        # phone page sends its own choice with every request.
        self.radio = config.default_radio
        self._last_radio_khz: float | None = None
        # Calls removed from the list by hand (long-press on the phone page),
        # per band; they stay hidden until POUNCer restarts.
        self._removed: set[tuple[str, str]] = set()
        # last -/+ step sent, per radio: radio -> (target kHz, time)
        self._tune_last: dict[int, tuple[float, float]] = {}
        # RX filter width POUNCer last set via Alt+' (None = unknown)
        self._filter_width: str | None = config.filter_start or None
        self._web_queue: "queue.Queue[tuple]" = queue.Queue()
        self._web_cache: dict[int, dict] = {}  # radio -> phone payload
        # phone band menu: (radio, band) a phone looks at -> last asked / payload
        self._web_views: dict[tuple[int, str], float] = {}
        self._web_view_cache: dict[tuple[int, str], dict] = {}
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
        # Team role, big and coloured: MASTER (talks to RBN) / SLAVE / alone
        self.team_label = tk.Label(header, text="", anchor="w", font=("Segoe UI", 12, "bold"),
                                   padx=6, pady=2)
        self.team_label.pack(fill="x", pady=(0, 4))
        # Connection lights: green = working, amber = waiting, red = down, grey = off
        self.lights_frame = FlowFrame(header, padx=14, pady=2)
        self.lights_frame.pack(fill="x", pady=(0, 4))
        self._lights: list[tuple[tk.Label, tk.Label]] = []
        self._lights_last: list | None = None
        self._restart_btn: tk.Button | None = None
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

        flow.add(ttk.Button(flow, text="Clear spots", command=self._clear_spots), gap=12)
        flow.add(ttk.Button(flow, text="Reconnect log", command=self._reconnect_log))
        flow.add(ttk.Button(flow, text="Open in browser", command=self._open_browser))
        flow.add(ttk.Button(flow, text="Summary",
                            command=lambda: self._open_browser("summary")))
        # SO2R: which radio (N1MM Entry Window A / B) clicks, F-keys and the
        # band follow go to; with one radio only A is used.
        self.radio_btn = flow.add(ttk.Button(flow, text=self._radio_btn_text(),
                                             command=self._toggle_radio), gap=12)

        # Function keys: always ONE row of equal buttons that share the width
        # (they get narrower with the window instead of wrapping).
        self.fkey_row = None
        if self.cfg.fkeys:
            row = ttk.Frame(self.root, padding=(8, 0, 8, 4))
            row.pack(fill="x")
            self.fkey_row = row
            for i, (key, label) in enumerate(self.cfg.fkeys):
                row.columnconfigure(i, weight=1, uniform="fkey")
                ttk.Button(
                    row, text=self._key_text(key, label, " "), width=1,
                    command=lambda k=key: self._press_key(k),
                ).grid(row=0, column=i, sticky="ew", padx=2)

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

        # F1-F12 typed while POUNCer has the focus go to N1MM's Entry Window
        # (focus N1MM, then the key = that N1MM message is SENT, like there).
        if self.cfg.forward_fkeys:
            for n in range(1, 13):
                self.root.bind_all(f"<F{n}>", lambda _e, k=f"F{n}": self._forward_key(k))
            # Enter too, when it is one of the set keys (logs the QSO in N1MM)
            if any(k.lower() == "enter" for k, _ in self.cfg.fkeys):
                for seq in ("<Return>", "<KP_Enter>"):
                    self.root.bind_all(seq, lambda _e: self._forward_key("Enter"))

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
        """Forget every spot (kept until the next clear, for Undo)."""
        self._cleared = dict(self.spots)
        self.spots.clear()
        self._dirty = True

    def _unclear_spots(self) -> None:
        """Undo a clear: bring the cleared spots back (newer ones win)."""
        for key, spot in (getattr(self, "_cleared", None) or {}).items():
            self.spots.setdefault(key, spot)
        self._cleared = {}
        self._dirty = True

    def _web_clear(self, undo: bool) -> None:
        self._web_queue.put(("clear", bool(undo)))

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
        if self.team is not None:
            # Team: only the master talks to RBN; give discovery 3 s at start
            # so a later POUNCer doesn't log in just to hand over at once.
            if time.time() - self.team.started < 3 or self.team.role() == "slave":
                desired = set()
        for port in [p for p in self._rbn_clients if p not in desired]:
            self._rbn_clients.pop(port).stop()
        for port in sorted(desired):
            if port in self._rbn_clients:
                continue
            client = RBNClient(
                self.cfg.rbn_host,
                port,
                self.cfg.rbn_callsign,
                self.spotter_filter,
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

    def _radio_nr(self, radio: int | None = None) -> int:
        """N1MM radio number for a request: the chosen radio (A = 1, B = 2)
        if N1MM reports it, else the active one (SO1R: always the one radio)."""
        return self.state.active_radio_nr(radio or self.radio)

    def _snapshot_focus(self):
        """SO2R: where the cursor is before we go to the other radio's window."""
        if not getattr(self.cfg, "return_focus", False) or not self._so2r():
            return None
        try:
            return focus_snapshot()
        except Exception:   # never let this stop a QSY
            return None

    def _return_focus(self, snap, target_hwnd) -> str:
        """Put the cursor back (window, box, position) if it was in ANOTHER
        N1MM Entry Window than the one we used. Returns a status note."""
        if not snap or not target_hwnd or snap.get("fg") == target_hwnd:
            return ""
        if not snap.get("pid") or snap["pid"] != window_pid(target_hwnd):
            return ""          # it wasn't in N1MM (e.g. POUNCer's own window)
        time.sleep(0.1)
        try:
            ok = restore_focus(snap)
        except Exception:
            ok = False
        if not ok:
            return " [cursor not returned]"
        back = next((self._radio_tag(n).strip(": ") for n in self.state.radio_numbers()
                     if self.state.entry_window_hwnd(n) == snap.get("fg")), "")
        return f" [cursor back in Entry Window {back}]" if back else " [cursor back]"

    def _act_on_spot(self, spot: Spot, radio: int | None = None) -> None:
        nr = self._radio_nr(radio)
        hwnd = self.state.entry_window_hwnd(nr)  # that radio's Entry Window (A / B)
        snap = self._snapshot_focus()
        focused = False
        if self.cfg.focus_n1mm and hwnd:
            focused = force_foreground(hwnd)
            if focused:
                time.sleep(0.15)

        if not self._qsy(nr, spot.freq_khz, hwnd, focused):
            return
        self.taps.record(spot.dxcall, spot.freq_khz)   # for the contest summary

        msg = f"QSY {self._radio_tag(nr)}{spot.freq_khz:.1f} kHz -> {spot.dxcall}"
        if self.set_call_var.get():
            can_type = focused or not self.cfg.focus_n1mm
            if can_type:
                # Always start from an empty Entry Window: N1MM's wipe, then
                # empty the focused callsign box directly in case the wipe
                # didn't land yet, then type the clicked call.
                if self.cfg.wipe_before_call:
                    # N1MM's own wipe ({WIPE} macro via its UDP command port,
                    # works whatever has the keyboard), plus Alt+W as backup
                    try:
                        self.controller.send_radio_command("{WIPE}", nr, hwnd)
                    except OSError:
                        pass
                    wipe_callsign()
                    time.sleep(0.2)  # let N1MM finish the wipe before we type
                cleared = clear_focused_edit(hwnd) if hwnd else False
                type_text(spot.dxcall)
                msg += " (call typed, no TX)" + ("" if cleared else " [box not cleared]")
            else:
                msg += " (call skipped: N1MM not focused)"
        if self.cfg.focus_n1mm:
            msg += " [N1MM focused]" if focused else " [N1MM focus failed]"
        msg += self._return_focus(snap, hwnd)
        self.status_label.configure(text=msg)

    def _tick(self) -> None:
        self.updater.maybe_check()
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
            self._dirty = False
        self._drain_web()
        if self.web is not None:
            # one payload per radio: each phone shows its own radio's band
            radios = set(self.state.radio_numbers()) | {1, self.radio}
            self._web_cache = {r: self._build_web_payload(r) for r in sorted(radios)}
            # bands phones look at without the radio being there (asked < 10 s ago)
            now = time.time()
            views = {k: t for k, t in dict(self._web_views).items() if now - t < WEB_VIEW_S}
            self._web_views = views
            self._web_view_cache = {k: self._build_web_payload(*k) for k in views}
            # ZeroTier can come up after POUNCer starts: re-check every minute.
            if time.time() - self._web_addr_checked >= 60:
                self._refresh_web_address()
        self.root.after(REFRESH_MS, self._tick)

    def _build_web_payload(self, radio: int | None = None, view_band: str | None = None) -> dict:
        radio = radio or self.radio
        real_band = self._current_band(radio)
        spots = sorted(
            self._visible_spots(radio, view_band),
            key=lambda s: s.snr if s.snr is not None else -999,
            reverse=True,
        )[:WEB_MAX_SPOTS]
        return {
            # band of the list: the radio's, or the one picked in the band menu
            "band": view_band or real_band,
            "radio_band": real_band,
            "view": bool(view_band and view_band != real_band),
            "freq": self._radio_khz(radio),
            # SO2R: radio this payload is for (1 = A, 2 = B) and the radios
            # N1MM reports - the page shows its A / B switch with two
            "radio": radio,
            "radios": self.state.radio_numbers(),
            # SO2R: every radio's frequency, so the page shows A and B
            "freqs": {str(nr): self._radio_khz(nr) for nr in self.state.radio_numbers()},
            "rbands": {str(nr): self._current_band(nr) for nr in self.state.radio_numbers()},
            "home_cont": self.home_cont,
            "station": self.own_call(),
            "team": self._team_text(),
            "fade_s": self.cfg.fade_minutes * 60,
            "backup": self._backup_text(),
            "contest_bands": list(CONTEST_BANDS),
            "tune_step": self.cfg.tune_step_hz,
            "rare_run": RARE_RUN_PCT,
            "rare_hours": RARE_HOURS,
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
                    **self._run_fields(s),
                    "cq_min": self._cq_minutes(s),
                    "first": self._first_heard_utc(s),
                    "age": int(time.time() - s.received),
                }
                for s in spots
            ],
        }

    def _web_spots(self, radio: int | None = None, band: str | None = None) -> dict:
        """Phone list: the radio's band, or ``band`` from the band menu (the
        radio isn't moved - tapping a station there QSYs it)."""
        cache = self._web_cache
        base = cache.get(radio or self.radio) or cache.get(self.radio) or cache.get(1) or {}
        if not band or band not in CONTEST_BANDS or band == base.get("band"):
            return base
        key = (base.get("radio") or radio or self.radio, band)
        self._web_views[key] = time.time()        # the next tick builds it
        got = self._web_view_cache.get(key)
        return got if got is not None else {**base, "pending": band}

    def _web_qsy(self, call: str, freq: float, mode: str, radio: int | None = None) -> None:
        self._web_queue.put(("qsy", call, float(freq), mode, radio))

    def _web_key(self, key: str, radio: int | None = None) -> None:
        self._web_queue.put(("key", key, radio))

    def _web_reconnect(self) -> None:
        self._web_queue.put(("reconnect",))

    def _web_band(self, band: str, radio: int | None = None) -> None:
        self._web_queue.put(("band", band, radio))

    def _web_tune(self, hz: int, radio: int | None = None) -> None:
        self._web_queue.put(("tune", int(hz), radio))

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
                self._press_key(item[1], item[2])
                continue
            if item[0] == "clear":
                if item[1]:
                    self._unclear_spots()
                else:
                    self._clear_spots()
                continue
            if item[0] == "reconnect":
                self._reconnect_log()
                continue
            if item[0] == "band":
                self._qsy_band(item[1], item[2])
                continue
            if item[0] == "tune":
                self._tune(item[1], item[2])
                continue
            if item[0] == "filter":
                self._set_filter(item[1], item[2])
                continue
            if item[0] == "hide":
                self._set_removed(item[1], item[2], item[3])
                continue
            _, call, freq, mode, radio = item
            spot = Spot(dxcall=call, freq_khz=freq, mode=mode or "CW",
                        snr=None, wpm=None, spot_type="", spotter="",
                        time_utc="", received=time.time())
            self._act_on_spot(spot, radio)

    @staticmethod
    def _key_text(key: str, label: str, sep: str) -> str:
        """Button text: key name (F2 / ENTER) plus its label, e.g. "F2 RPRT"."""
        big = key.upper()
        if label and label.upper() != big:
            return f"{big}{sep}{label}"
        return big

    def _radio_btn_text(self) -> str:
        return f"Radio {'AB'[self.radio - 1]} (A/B)"

    def _toggle_radio(self) -> None:
        """Main window A / B button: act on / follow the other radio."""
        self.radio = 2 if self.radio == 1 else 1
        self.radio_btn.configure(text=self._radio_btn_text())
        self._dirty = True
        note = "" if self.radio in self.state.radio_numbers() or not self.state.radio_numbers() \
            else " - N1MM reports no radio B (SO1R): radio A is used"
        self.status_label.configure(
            text=f"Radio {'AB'[self.radio - 1]}: clicks, F-keys and band follow go to "
                 f"N1MM Entry Window {'AB'[self.radio - 1]}{note}")

    def _radio_tag(self, nr: int) -> str:
        """'B: ' when two radios are in use, '' with one (SO1R)."""
        return f"{'AB'[nr - 1] if nr in (1, 2) else nr}: " if self._so2r() else ""

    def _so2r(self) -> bool:
        return len(self.state.radio_numbers()) > 1

    def _forward_key(self, key: str) -> str:
        """A function key pressed on the keyboard in POUNCer: pass it to N1MM."""
        self._press_key(key)
        return "break"  # Tk does nothing else with it (F10 = no menu)

    def _press_key(self, key: str, radio: int | None = None) -> None:
        """A TX / log button was pressed: focus N1MM, then send the key.

        ``key`` is ``F1``-``F12`` (function-key message; transmits) or
        ``Enter`` (logs the QSO in N1MM; does not transmit). The key goes
        to the chosen radio's Entry Window (A / B).
        """
        hwnd = self.state.entry_window_hwnd(self._radio_nr(radio))
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
            self._track_on_freq(spot)
            got += 1
            self._last_own_spot = time.time()
            if self.team is not None and self.team.is_master():
                self.team.send_spot(self._spot_dict(spot))
        # spots passed on by the team master (slave): use, don't pass on
        while got < 1000:
            try:
                spot = self._team_queue.get_nowait()
            except queue.Empty:
                break
            self.spots[spot.key] = spot
            self._track_on_freq(spot)
            got += 1
        if got:
            self._dirty = True

    def _track_on_freq(self, spot: Spot) -> None:
        self._on_freq.add(spot, band_from_khz(spot.freq_khz))

    def _cq_minutes(self, s: Spot) -> int | None:
        """Minutes this station has been on this spot's frequency (RBN)."""
        return self._on_freq.minutes(s.dxcall, band_from_khz(s.freq_khz), s.freq_khz,
                                     time.time())

    def _first_heard_utc(self, s: Spot) -> str:
        """UTC time it was first spotted on this frequency (e.g. 1132Z)."""
        t = self._on_freq.first_heard(s.dxcall, band_from_khz(s.freq_khz), s.freq_khz)
        return time.strftime("%H%MZ", time.gmtime(t)) if t else (s.time_utc or "")

    @staticmethod
    def _spot_dict(spot: Spot) -> dict:
        d = dataclasses.asdict(spot)
        d.pop("received", None)
        return d

    def _team_spot(self, d: dict) -> None:
        """A spot passed on by the team master (slave side; any thread)."""
        names = {f.name for f in dataclasses.fields(Spot)} - {"received"}
        try:
            spot = Spot(**{k: d.get(k) for k in names}, received=time.time())
            spot.freq_khz = float(spot.freq_khz)
            spot.dxcall = str(spot.dxcall)
        except (TypeError, ValueError):
            return
        self._team_queue.put(spot)

    def _rbn_ok(self) -> bool:
        """Our own RBN connection works (logged in or spots lately)."""
        return (self.rbn_status.startswith("logged in")
                or time.time() - self._last_own_spot < 120)

    def _start_team(self) -> None:
        try:
            self.team = Team(
                web_port=self.cfg.web_port if self.cfg.web_enabled else 0,
                on_spot=self._team_spot,
                on_runhist=(self.runhist.merge if self.runhist else None),
                rbn_ok=self._rbn_ok,
                port=self.cfg.team_port,
                version=__version__,
                peers=list(self.cfg.team_peers),
            )
            self.team.start()
        except OSError as exc:
            log.warning("team disabled: %s", exc)
            self.team = None
            return
        if self.runhist is not None:
            self.runhist.on_learn = self.team.send_runhist
            if self.web is not None:
                self.web.set_runhist_provider(self.runhist.snapshot)

    def _update_team_label(self) -> None:
        """Top line + window title: MASTER / SLAVE / stand-alone."""
        role = self.team.role() if self.team is not None else "off"
        n = len(self.team.alive_peers()) + 1 if self.team is not None else 1
        text, bg = {
            "master": (f"MASTER - this POUNCer talks to RBN for the team ({n} POUNCers)", "#2e7d32"),
            "slave": (f"SLAVE - spots come from the master at {self.team.master_ip() if self.team else ''}"
                      f" ({n} POUNCers)", "#1565c0"),
            "solo": ("ALONE - no other POUNCer on the network (team on)", "#546e7a"),
        }.get(role, ("Team off - stand-alone", "#546e7a"))
        note = self._version_note()
        if note and role in ("master", "slave"):
            text, bg = f"{text}   |   {note}", "#ef6c00"
        if self.team_label.cget("text") != text:
            self.team_label.configure(text=text, bg=bg, fg="#ffffff")
            tag = {"master": " - MASTER", "slave": " - SLAVE"}.get(role, "")
            self.root.title(f"POUNCer {__version__}{tag} - RBN band follower for N1MM")

    def _backup_text(self) -> str:
        """'' normally; a note while backup skimmers are used."""
        f = self.spotter_filter
        if not f.backup_active():
            return ""
        which = ", ".join(f.backups) if f.backups else f"skimmers in {self.home_cont}"
        return f"{f.primary} quiet - using {which}"

    def _version_note(self) -> str:
        """'' when the whole team runs this version; else who differs."""
        if self.team is None:
            return ""
        diff = self.team.version_mismatch()
        if not diff:
            return ""
        who = ", ".join(f"{ip} runs {ver}" for ip, ver in diff)
        return f"VERSIONS DIFFER: this one is {__version__}, {who} - update all to the same"

    def _team_text(self) -> str:
        if self.team is None:
            return ""
        role = self.team.role()
        n = len(self.team.alive_peers())
        note = self._version_note()
        note = f" - {note}" if note and role in ("master", "slave") else ""
        if role == "slave":
            return f"team: slave of {self.team.master_ip()} ({n + 1} POUNCers){note}"
        if role == "master":
            return f"team: master ({n + 1} POUNCers){note}"
        return "team: alone"

    def _prune(self) -> None:
        cutoff = time.time() - self.cfg.max_age_minutes * 60
        stale = [k for k, s in self.spots.items() if s.received < cutoff]
        for key in stale:
            del self.spots[key]
        self._on_freq.prune(time.time() - ON_FREQ_GAP_S)
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

    def _run_fields(self, s: Spot) -> dict:
        """From the call's past log (same contest + mode): % of QSOs made
        running, hours on the air, and whether the log was reconstructed."""
        info = None
        if self.runhist is not None:
            info = self.runhist.get(s.dxcall, self.worked.contest_name, s.mode)
        info = info or {}
        return {"run": info.get("run"), "hours": info.get("hours"),
                "rec": bool(info.get("rec"))}

    def own_call(self) -> str:
        """Our call: the open log's station call (N1MM Config > Change Your
        Station Data), else [rbn] callsign / N1MM's ini."""
        return self.worked.station_call or self.cfg.rbn_callsign

    @property
    def home_cont(self) -> str:
        """Our continent, from our call (follows the log: CR3A -> AF)."""
        call = self.own_call()
        if call != self._home_call:
            self._home_call = call
            self._home_cont = (self.cty.continent(call) if self.cty and call else None) or "EU"
        return self._home_cont

    def _is_dx(self, call: str) -> bool:
        """DX = a known continent other than ours (EU for S5)."""
        cont = self._continent(call)
        return cont is not None and cont != self.home_cont

    def _types_qsy(self, nr: int) -> bool:
        """SO2R radio B: QSY by typing the frequency into Entry Window B
        (N1MM's UDP QSY command moved radio A there)."""
        return nr == 2 and self._so2r() and getattr(self.cfg, "radio_b_qsy", "type") == "type"

    def _qsy(self, nr: int, khz: float, hwnd, focused: bool) -> bool:
        """QSY radio ``nr``. Radio A: N1MM's UDP command. SO2R radio B: type
        the frequency in kHz into its Entry Window and press Enter - N1MM
        then tunes the radio of that window (no transmit). False = failed
        (status line says why)."""
        if self._types_qsy(nr):
            if not (hwnd and focused):
                self.status_label.configure(
                    text="QSY B failed: Entry Window B not in front (N1MM focus failed)")
                return False
            clear_focused_edit(hwnd)
            type_text(f"{khz:.3f}".rstrip("0").rstrip("."))   # e.g. 14025.3
            send_enter()
            time.sleep(0.3)   # let N1MM tune and clear the box before the call
            return True
        try:
            self.controller.set_frequency(khz, radio_nr=nr)
        except OSError as exc:
            self.status_label.configure(text=f"N1MM QSY failed: {exc}")
            return False
        return True

    def _focus_radio(self, nr: int) -> bool:
        """SO2R: bring that radio's Entry Window (A / B) to the front before a
        QSY - like a spot tap does. With one radio nothing is needed (keeps
        the band menu / tune from stealing focus as before)."""
        if not self._so2r() or not self.cfg.focus_n1mm:
            return False
        hwnd = self.state.entry_window_hwnd(nr)
        if not hwnd or not force_foreground(hwnd):
            return False
        time.sleep(0.15)
        return True

    def _tune(self, hz: int, radio: int | None = None) -> None:
        """Footer -/+ buttons: move the VFO by hz via N1MM (no TX)."""
        now = time.time()
        nr = self._radio_nr(radio)
        target, when = self._tune_last.get(nr, (None, 0.0))
        khz = next_tune_khz(self._radio_khz(nr), target, now - when, hz)
        if khz is None:
            self.status_label.configure(text="Tune: no radio frequency from N1MM yet")
            return
        snap = self._snapshot_focus()
        hwnd = self.state.entry_window_hwnd(nr)
        focused = self._focus_radio(nr)
        if not self._qsy(nr, khz, hwnd, focused):
            return
        self._tune_last[nr] = (khz, now)
        note = self._return_focus(snap, hwnd) if focused else ""
        self.status_label.configure(text=f"Tune {hz:+d} Hz -> {khz:.3f} kHz{note}")

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
            nr = self._radio_nr()
            try:
                self.controller.send_radio_command(
                    cat_macro(cat, nr), nr, self.state.entry_window_hwnd(nr)
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
        hwnd = self.state.entry_window_hwnd(self._radio_nr())
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
        """Remember each radio's last frequency on each band (for band QSY)."""
        for nr in self.state.radio_numbers():
            snap = self.state.snapshot(nr)
            if snap and snap.get("freq_khz") and snap.get("band"):
                self._band_memory[(nr, snap["band"])] = snap["freq_khz"]

    def _qsy_band(self, band: str, radio: int | None = None) -> None:
        """Band picker: QSY to the last frequency used on ``band`` (or a CW
        default). Only a frequency change - never transmits."""
        if band not in CONTEST_BANDS:
            self.status_label.configure(text=f"Unknown band: {band}")
            return
        nr = self._radio_nr(radio)
        khz = self._band_memory.get((nr, band), DEFAULT_BAND_KHZ[band])
        focused = self._focus_radio(nr)
        if not self._qsy(nr, khz, self.state.entry_window_hwnd(nr), focused):
            return
        self.status_label.configure(text=f"Band {band}: QSY {self._radio_tag(nr)}{khz:.1f} kHz")

    def _radio_khz(self, radio: int | None = None) -> float | None:
        """The (chosen) radio's frequency in kHz (from N1MM RadioInfo)."""
        snap = self.state.snapshot(radio or self.radio)
        return (snap or {}).get("freq_khz") or None

    def _current_band(self, radio: int | None = None) -> str | None:
        """Band of the chosen radio (A / B): the list follows that radio."""
        snap = self.state.snapshot(radio or self.radio)
        return (snap or {}).get("band")

    def _update_labels(self) -> None:
        snap = self.state.snapshot(self.radio)
        if snap and snap.get("freq_khz"):
            self.n1mm_label.configure(
                text=(
                    f"N1MM{' radio ' + 'AB'[self._radio_nr() - 1] if self._so2r() else ''}:"
                    f" {snap['freq_khz']:.2f} kHz  "
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
            worked = f"Log: {self.worked.db_name or '-'}   station: {self.own_call() or '?'}" \
                f" ({self.home_cont})   worked: {self.worked.status}"
            if self.worked.log_open:
                per_band = " ".join(f"{b} {n}" for b, n in self.worked.band_qsos())
                worked += f"   QSOs: {per_band or '-'} (total {self.worked.qso_count})"
        else:
            worked = f"LOG: {self.worked.log_message}"
        self.rbn_label.configure(
            text=(
                f"RBN: {self.rbn_status if not (self.team and self.team.role() == 'slave') else 'via team master'}"
                f"   {self._team_text()}   spotter={self.cfg.rbn_spotter or 'ALL'}"
                f"{'   BACKUP: ' + self._backup_text() if self._backup_text() else ''}"
                f"   mode={','.join(self.cfg.follow_modes)}"
                f"   band={band or 'ALL'}   spots={len(self.displayed)}/{len(self.spots)}"
                f"   hidden={self._worked_hidden}"
                f"{'   warm-up: ' + self.runhist.warm_status if self.runhist and self.runhist.warm_status else ''}"
            )
        )
        self.log_label.configure(text=worked)
        self._update_team_label()
        self._update_lights(snap)

    def _light_states(self, snap) -> list[tuple[str, str]]:
        role = self.team.role() if self.team is not None else "off"
        n = len(self.team.alive_peers()) + 1 if self.team is not None else 1
        spot_age = time.time() - self._last_own_spot if self._last_own_spot else None
        return [
            lights.rbn_light(self.rbn_status, spot_age, role),
            lights.n1mm_light(snap["age"] if snap and snap.get("freq_khz") else None),
            lights.log_light(self.worked.log_open, self.worked.log_state),
            lights.team_light(role, n, self.team.version_mismatch() if self.team else None),
            lights.web_light(self.cfg.web_enabled, self.web is not None),
            lights.warm_light(self.runhist is not None and self.cfg.warmup,
                              self.runhist.warm_status if self.runhist else ""),
            lights.update_light(self.updater.state, self.updater.text()),
        ]

    def _update_lights(self, snap) -> None:
        """Coloured dot per connection (RBN, N1MM, log, team, phone, warm-up)."""
        states = self._light_states(snap)
        if states == self._lights_last:
            return
        self._lights_last = states
        bg = self.lights_frame.winfo_toplevel().cget("bg")
        while len(self._lights) < len(states):
            box = tk.Frame(self.lights_frame, bg=bg)
            dot = tk.Label(box, text="\u25cf", font=("Segoe UI", 14), bg=bg)
            dot.pack(side="left")
            name = tk.Label(box, text="", font=("Segoe UI", 10, "bold"), bg=bg)
            name.pack(side="left")
            self.lights_frame.add(box)
            self._lights.append((dot, name))
        for (dot, name), (state, text) in zip(self._lights, states):
            dot.configure(fg=lights.COLORS[state])
            name.configure(text=text)
        if self.updater.state == "ready" and self._restart_btn is None:
            self._restart_btn = tk.Button(
                self.lights_frame, text=f"Restart -> {self.updater.new_version}",
                bg=lights.COLORS[lights.NEW], fg="#ffffff", relief="flat",
                padx=8, command=self._restart_for_update)
            self.lights_frame.add(self._restart_btn)

    def _restart_for_update(self) -> None:
        """Restart button: quit and start the new version right away."""
        self.status_label.configure(text=f"Restarting with {self.updater.new_version}...")
        self.root.update_idletasks()
        self.updater.restart(before_exit=self._shutdown)

    def _visible_spots(self, radio: int | None = None, band: str | None = None) -> list[Spot]:
        """Spots matching the band of the radio (default: the main window's
        A / B choice) - or ``band`` (phone band menu: look at another band) -
        modes and worked filter."""
        band = band or self._current_band(radio)
        if radio is None and band != self._last_band:
            self._last_band = band
        modes = {m.upper() for m in self.cfg.follow_modes}
        own = self.own_call()
        items = [
            s for s in self.spots.values()
            if (band is None or band_from_khz(s.freq_khz) == band)
            and s.mode.upper() in modes
            and not (self.cfg.hide_beacons and is_beacon(s))
            and (s.dxcall.upper(), band_from_khz(s.freq_khz)) not in self._removed
            and not same_station(s.dxcall, own)  # never list ourselves
        ]
        # one tile per station and band: the newest spot (several skimmers,
        # or a small drift, give the same station more than one spot)
        newest: dict[tuple[str, str | None], Spot] = {}
        for s in items:
            k = (s.dxcall.upper(), band_from_khz(s.freq_khz))
            if k not in newest or s.received > newest[k].received:
                newest[k] = s
        items = list(newest.values())
        if self.hide_worked_var.get():
            kept = []
            hidden = 0
            for s in items:
                if self.worked.is_worked(s.dxcall, s.freq_khz, s.mode):
                    hidden += 1
                else:
                    kept.append(s)
            items = kept
            if radio is None:
                self._worked_hidden = hidden
        elif radio is None:
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

    def _local_web_url(self) -> str:
        """The phone page as seen from this PC."""
        host = self.cfg.web_host
        if not host or host in ("0.0.0.0", "::"):
            host = "127.0.0.1"
        return f"http://{host}:{self.web.port}/" if self.web is not None else ""

    def _summary(self) -> dict:
        """Contest summary of the open N1MM log (web thread, read-only)."""
        w = self.worked
        path = getattr(w, "_path", None)
        if not w.log_open or not path:
            return {"ok": False, "error": w.log_message or "No log open in N1MM"}
        try:
            qsos = summarymod.read_qsos(path, w.contest_nr, w.mult_types)
        except Exception as exc:   # locked / gone
            return {"ok": False, "error": f"Could not read the log: {exc}"}
        out = summarymod.build(qsos, self.taps.snapshot(), w.mult_types)
        out.update(log=w.db_name, contest=w.contest_name,
                   station=w.station_call or self.own_call())
        return out

    def _open_browser(self, page: str = "") -> None:
        """Touch / big-button view = the phone page in the default browser."""
        url = self._local_web_url()
        if url and page:
            url += page
        if not url:
            self.status_label.configure(
                text="Browser view needs the phone view: [web] enabled = true")
            return
        import webbrowser
        try:
            webbrowser.open(url, new=2)
            self.status_label.configure(text=f"Opened {url} in the browser")
        except Exception as exc:  # no browser registered
            self.status_label.configure(text=f"Could not open the browser: {exc}")

    def run(self) -> None:
        self._listener = RadioListener(
            self.cfg.n1mm_listen_ip, self.cfg.n1mm_listen_port, self.state
        )
        self._listener.start()
        if self.cfg.web_enabled:
            self._start_web()
            if self.cfg.touch_view:
                self.root.after(1500, self._open_browser)
        else:
            self.web_label.configure(text="Phone view: disabled ([web] enabled = false)")
        if self.cfg.team:
            self._start_team()
        self._ensure_rbn_clients()
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
                self._web_tune, self._web_filter, self._web_clear,
            )
            self.web.set_summary_provider(self._summary)
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
        if self.team is not None:
            self.team.stop()
        if self.web is not None:
            self.web.stop()
            self.web = None
        for client in self._rbn_clients.values():
            client.stop()
        self._rbn_clients.clear()
        if self._listener is not None:
            self._listener.stop()
        self.controller.close()
