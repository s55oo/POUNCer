"""Tkinter GUI for POUNCer."""

from __future__ import annotations

import logging
import os
import dataclasses
import queue
import sys
import threading
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
from .rbn import (
    ContinentFilter, OnFreqTracker, RBNClient, Spot, SpotterFilter, is_beacon, is_phone_contest,
    ports_for_modes, spot_from_n1mm, digital_sure, digital_overlap,
)
from .runhist import RARE_HOURS, RARE_RUN_PCT, RunHistory
from .rbnhttp import RBNHttpFeed
from .scp import ScpList
from .cwspeed import SpeedReader
from . import checks as checksmod
from .bandopen import BandActivity, dx_pct, share_color, state_of
from .zones import ZoneMap
from .timeline import Timeline
from .perf import PerfMonitor
from .alerts import AlertWatch, SelfSpotWatch, beep as alert_beep
from .panels import TimelineWindow, ZonesWindow
from .console import TrafficConsole
from .skimwatch import SkimmerWatch
from . import stationtest
from .pskr import PskChecker
from .tiles import TileList
from . import theme
from . import autostart as autostartmod
from . import diag as diagmod
from . import win32 as win32mod
from . import summary as summarymod
from . import settings as settingsmod
from . import winstate
from .settings_window import SettingsWindow
from .team import Team
from .update import Updater
from .web import WebServer, pick_web_address
from .win32 import (
    decimal_separator, focus_snapshot, focused_edit_text, force_foreground, is_n1mm_running, restore_focus,
    send_cw_speed, set_focused_edit_text,
    send_enter, send_filter_toggle, window_pid, running_process_names,
    send_alt_p,
    send_fkey,
    type_text,
    clear_focused_edit,
    wipe_callsign,
)
from .worked import WorkedLog, default_n1mm_dirs, n1mm_station_call, same_station

log = logging.getLogger(__name__)

GITHUB_URL = "https://github.com/s55oo/POUNCer"
REFRESH_MS = 500
CW_PUMP_MS = 80   # phone CW keyboard: typed text handled this often
WEB_MAX_SPOTS = 60
WEB_MAX_SPOTS_ALL = 120  # phone band menu > ALL: every band, new mults first
ALL_BANDS = "ALL"
WEB_VIEW_S = 10  # keep building a band a phone looks at this long after its last ask
RADIO_ALIVE_SECONDS = 30
GUARD_CALL_S = 1.0  # radio B: after the call, keep it in the box this long if N1MM's QSY came late
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
        self._hidden: set = set()           # widgets left out of the rows
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

    def set_visible(self, widget, visible: bool) -> None:
        """Show / hide one child; the others close up (no gap)."""
        if visible == (widget not in self._hidden):
            return
        if visible:
            self._hidden.discard(widget)
        else:
            self._hidden.add(widget)
            widget.place_forget()
        self._last = None
        self._schedule()

    def _schedule(self) -> None:
        if not self._pending:
            self._pending = True
            self.after_idle(self._layout)

    def _layout(self) -> None:
        self._pending = False
        width = max(self.winfo_width(), 1)
        items = [(w, g) for w, g in self._items if w not in self._hidden]
        sizes = [(w.winfo_reqwidth(), w.winfo_reqheight()) for w, _ in items]
        pos, height = flow_positions(
            sizes, width, self._padx, self._pady, [g for _, g in items]
        )
        key = (width, tuple(sizes), tuple(pos), height)
        if key == self._last:
            return
        self._last = key
        for (widget, _), (x, y) in zip(items, pos):
            widget.place(x=x, y=y)
        self.configure(height=max(height, 1))


def _note_tap(app, spot: Spot, nr: int, ok: bool, short: str, full: str = "") -> None:
    """What a tap really did - the phone shows it (``tap`` in /api/spots)."""
    n = (getattr(app, "_last_tap", None) or {}).get("n", 0) + 1
    app._last_tap = {"n": n, "call": spot.dxcall, "freq": spot.freq_khz, "radio": nr,
                     "ok": ok, "text": short, "full": full or short, "t": time.time()}


PIN_S = 60     # a clicked / tapped station keeps its row this long (or until logged)

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
        self._snr_hidden = 0
        # the open log / contest as N1MM broadcasts it (AppInfo) wins over the ini
        self.worked.app_info = self.state.app
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
        # Super Check Partial: "?" on calls not in MASTER.SCP (kept fresh from the web)
        self.scp = (ScpList(config.run_history_dir or "cache", config.n1mm_ini,
                            url=config.scp_url, refresh_days=config.scp_refresh_days,
                            update_n1mm=config.scp_update_n1mm,
                            n1mm_dirs=default_n1mm_dirs())
                    if config.scp_enabled else None)
        # N1MM's CW speed, read from the Entry Windows' speed box (Windows only)
        self.cwspeed = (SpeedReader(
            self._entry_windows, win32mod.number_boxes, win32mod.window_text,
            win32mod.is_window,
            report_path=os.path.join(config.run_history_dir or "cache", "cwspeed.txt"),
            describe=win32mod.child_summary)
            if config.cw_speed and win32mod._WIN else None)
        # RBN over HTTP while the telnet feed is down / silent
        self._http_feed: RBNHttpFeed | None = None
        self._rbn_bad_since: float | None = None
        self._rbn_good_since: float | None = None
        # Every tapped station (for the contest summary): cache\taps.jsonl
        self.taps = summarymod.TapLog(config.run_history_dir or "cache")
        self.clock = checksmod.ClockCheck()   # PC clock vs NTP (check line)
        # band open? spots of every skimmer on our continent, per band, 10 min
        self.bandact = BandActivity(lambda c: self._continent(c), lambda: self.home_cont)
        # band opening timeline: spots per band in 10-minute boxes (the /timeline page)
        self.timeline = Timeline(os.path.join(config.run_history_dir or "cache", "timeline.json"))
        self.bandact.on_count = self.timeline.add
        # load counters for cache/perf.csv (one row a minute, see perf.py)
        self._perf = {"spots_in": 0, "raw": 0, "ticks": 0, "tick_ms": 0.0, "tick_max": 0.0,
                      "rebuilds": 0, "t": time.monotonic()}
        self.perf = PerfMonitor(config.run_history_dir or "cache", stats=self._perf_stats)
        # who hears me / what I hear, per band and CQ zone (the /zones page)
        self.zonemap = ZoneMap(self._entity, lambda: self.home_cont, self.own_call)
        self._checks: list[dict] = []
        if self.runhist is not None and config.warmup:
            self.runhist.warm_up()  # CQWW / ARRL DX / WPX: CW list + separate SSB list
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
            zone_of=lambda call: getattr(self._entity(call), "cq", None),
        )
        # our own skimmer(s) still spotting? (S53WW, TK0C-1 / -2 ...)
        self.skimwatch = SkimmerWatch(self.spotter_filter.calls,
                                      quiet_s=config.skimmer_quiet_minutes * 60)
        self._skim_quiet_text = ""
        self._last_team_spot = 0.0
        # (call, band) -> (time, receiver): heard by our own skimmer (the first
        # listed, TK0C / S53WW) - marked ★ when others are listed too
        self._own_heard: dict[tuple[str, str], tuple[float, str]] = {}
        # SSB spots in an FT8 / FT4 window inside the SSB segment (7074 - 7077):
        # PSK Reporter is asked about that one call; FT8 reports there = FT8
        self.pskr = PskChecker() if getattr(config, "ssb_pskreporter", True) else None
        self._overlap_keys: set = set()
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
        self.ssb_status = "off"
        self._ssb_status_q: "queue.Queue[str]" = queue.Queue()
        self._self_spot_q: queue.Queue = queue.Queue()   # my call spotted by a person
        self._ssb_client = None
        self._ssb_last_needed = 0.0
        # N1MM's own spots (<spot> packets on the RadioInfo port): while they
        # come they replace our SSB cluster connection (same spots, one telnet)
        self._n1mm_spot_first: float | None = None   # start of the current stream
        self._n1mm_spot_last = 0.0
        self._ssb_back_at = 0.0                      # N1MM went quiet: own cluster since
        self._n1mm_gap: float | None = None          # usual seconds between N1MM spots
        self._n1mm_streak = 0                        # spots in the current stream
        self._n1mm_spots = 0
        self._ssb_via_n1mm = False
        self._n1mm_filter = None
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
        # phone CW keyboard: N1MM's Ctrl+K window while open, its radio, last error
        self._cw_win: int | None = None
        self._cw_radio: int | None = None
        self._cw_error = ""
        self._cw_error_n = 0
        self._cw_pumping = False
        # phone band menu: (radio, band) a phone looks at -> last asked / payload
        self._web_views: dict[tuple[int, str], float] = {}
        self._web_view_cache: dict[tuple[int, str], dict] = {}
        self._build_ui()
        self._restore_window()

    def _build_ui(self) -> None:
        self.root = tk.Tk()
        self.root.title(f"POUNCer {__version__} - RBN band follower for N1MM")
        theme.apply(self.root, getattr(self.cfg, "look", "classic"))   # first: SCALE, colours
        self.root.geometry(f"{theme.px(940)}x{theme.px(520)}")
        # Narrow windows are fine: text lines wrap and the buttons flow onto
        # more rows instead of being cut off at the right edge.
        self.root.minsize(theme.px(420), theme.px(300))
        self.compact_var = tk.BooleanVar(value=False)
        self.tiles_var = tk.BooleanVar(value=bool(getattr(self.cfg, "tiles", False)))
        self.topmost_var = tk.BooleanVar(value=False)
        self._normal_geom = ""
        self._compact_geom = ""
        self.set_call_var = tk.BooleanVar(value=self.cfg.set_callsign)
        self.hide_worked_var = tk.BooleanVar(value=self.cfg.worked_hide)
        self._settings_win = SettingsWindow(
            self.root, lambda: settingsmod.current(self.cfg), self._save_setting,
            restart=(self._restart_now if self.updater.exe else None),
            config_path=self.cfg.config_path)
        self._build_menu()
        self._build_wpm_popup()
        self._alerts = AlertWatch()
        self._self_spots = SelfSpotWatch()
        self._build_alert_popup()
        # View > CQ zones / Band opening timeline: windows of their own
        self._zones_win = ZonesWindow(self.root, self._zones,
                                      browser=lambda: self._open_browser("zones"))
        self._timeline_win = TimelineWindow(self.root, self._timeline,
                                            browser=lambda: self._open_browser("timeline"))
        # View > Traffic console: the taps only work while it is open
        self._console = TrafficConsole(self.root, notes=self._console_notes)
        self.root.bind_all("<Control-Shift-D>", lambda _e: self._console.open())
        self.root.bind_all("<Control-Shift-d>", lambda _e: self._console.open())

        header = ttk.Frame(self.root, padding=0)
        header.pack(fill="x")
        self._header = header
        # compact mode (View > Compact window): one line with A / B instead
        # of the header, sort row, F-keys and status lines
        self._compact_bar = ttk.Frame(self.root, padding=(6, 4, 6, 2))
        self._compact_btn = ttk.Button(self._compact_bar, text="", command=self._toggle_radio)
        self._compact_btn.pack(side="left")
        # one click back to the full window (or Ctrl+M, or untick it in View)
        self._compact_full = ttk.Button(self._compact_bar, text="\u2922", width=3,
                                        command=self._toggle_compact)
        self._compact_full.pack(side="right")
        self._compact_info = ttk.Label(self._compact_bar, text="", anchor="w")
        self._compact_info.pack(side="left", padx=(8, 0), fill="x", expand=True)
        # ... and under it the band chips, small: colour = band open, number =
        # new mults spotted there; click = list that band (like the full window)
        self._compact_bands = tk.Frame(self.root, bg=theme.BG, padx=6, pady=1)
        self._compact_chips: dict[str, tk.Label] = {}
        for band in CONTEST_BANDS:
            lbl = tk.Label(self._compact_bands, text=band, padx=3, pady=0, cursor="hand2",
                           font=(theme.FONT, 8, "bold"))
            lbl.pack(side="left", padx=1, fill="x", expand=True)
            lbl.bind("<Button-1>", lambda _e, b=band: self._on_band_chip(b))
            self._compact_chips[band] = lbl
        self.root.bind_all("<Control-m>", lambda _e: self._toggle_compact())
        self.root.bind_all("<Control-M>", lambda _e: self._toggle_compact())
        # Top bar (dark): the radio frequency big, band / mode / WPM next to
        # it; on the right the pre-contest READY badge and the team role.
        top = tk.Frame(header, bg=theme.TOP_BG, padx=12, pady=6)
        top.pack(fill="x")
        self._top = top
        self.freq_big = tk.Label(top, text="----.-", bg=theme.TOP_BG, fg=theme.TOP_FG,
                                 font=(theme.FONT, 20, "bold"))
        self.freq_big.pack(side="left")
        self.freq_sub = tk.Label(top, text="waiting for N1MM", bg=theme.TOP_BG,
                                 fg=theme.TOP_MUTED, font=(theme.FONT, 11), padx=10)
        self.freq_sub.pack(side="left", pady=(6, 0))
        # Team role: MASTER (talks to RBN) / SLAVE / alone - a coloured badge
        self.team_label = tk.Label(top, text="", font=(theme.FONT, 9, "bold"), padx=8, pady=2)
        self.team_label.pack(side="right", padx=(8, 0))
        # Pre-contest check: READY / CHECK / NOT READY (click: details on / off)
        self.ready_badge = tk.Label(top, text="", font=(theme.FONT, 10, "bold"), padx=10,
                                    pady=3, cursor="hand2")
        self.ready_badge.pack(side="right")
        self.ready_badge.bind("<Button-1>", lambda e: self._toggle_check_details())
        self._check_details = False
        # ... and its items, shown when something is wrong (or clicked)
        # one colour per item: red = not ok, amber = check, green = ok
        self.check_label = tk.Text(header, height=1, wrap="word", bd=0, highlightthickness=0,
                                   font=(theme.FONT, 9, "bold"), padx=12, pady=3,
                                   bg=theme.PANEL, cursor="arrow", takefocus=0)
        for st, (_bg, fg) in theme.CHECK.items():
            self.check_label.tag_configure(st, foreground=fg)
        self.check_label.configure(state="disabled")
        self.check_label.bind("<Configure>", lambda e: self._fit_check_line())
        self._check_segs = None
        # Status chips: connection lights, then band open (one flowing row)
        self.lights_frame = FlowFrame(header, padx=12, pady=2)
        self.lights_frame.pack(fill="x", padx=12, pady=(6, 2))
        # rate / QSOs / mults per band from the open N1MM log (every 30 s)
        self.rate_label = tk.Label(header, text="", anchor="w", justify="left",
                                   font=(theme.FONT, 9), bg=theme.BG, fg=theme.MUTED, padx=12)
        self.rate_label.bind("<Configure>", lambda e: self.rate_label.configure(
            wraplength=max(200, e.width - 24)))
        if getattr(self.cfg, "rate_line", True):
            self.rate_label.pack(fill="x", padx=12, pady=(0, 2))
        self._rate_text = None
        self._rate_at = 0.0
        self._rate_busy = False
        self.bands_frame = None          # made after the lights (same row)
        self._band_lights: dict[str, tk.Label] = {}
        # band chip clicked: the list shows that band (radio stays); None = follow the radio
        self._view_band: str | None = None
        self._view_from: str | None = None
        self._bands_last = None
        self._lights: list[tuple[tk.Label, tk.Label]] = []
        self._lights_last: list | None = None
        self._restart_btn: tk.Button | None = None
        info = ttk.Frame(header, padding=(12, 2, 12, 2))
        info.pack(fill="x")
        self.n1mm_label = self._wrap_label(info, "N1MM: waiting for radio info...", muted=True)
        self.rbn_label = self._wrap_label(info, "RBN: starting...", muted=True)
        self.log_label = self._wrap_label(info, "Log: -", muted=True)

        # Sort, options, keys and actions: one flowing block of controls.
        outer = ttk.Frame(self.root, padding=(12, 6, 12, 4))
        outer.pack(fill="x")
        self._outer = outer
        flow = FlowFrame(outer)
        flow.pack(fill="x")
        self._flow = flow
        flow.add(ttk.Label(flow, text="Sort", style="Muted.TLabel"))
        for col in ("time", "freq", "snr"):
            btn = flow.add(ttk.Button(
                flow, text=self._sort_labels[col], style="Sort.TButton",
                command=lambda c=col: self._set_sort(c),
            ))
            self._sort_buttons[col] = btn
        self._update_sort_buttons()
        # Min SNR next to the sort buttons: weaker spots are not listed
        # (window and phone); 0 = all. Arrows / wheel / typed + Enter.
        box = ttk.Frame(flow)
        ttk.Label(box, text="Min SNR", style="Muted.TLabel").pack(side="left")
        self.min_snr_var = tk.StringVar(value=str(self.cfg.min_snr))
        self._min_snr_box = ttk.Spinbox(
            box, from_=0, to=60, increment=1, width=3, justify="center",
            textvariable=self.min_snr_var, command=self._on_min_snr)
        self._min_snr_box.pack(side="left", padx=(4, 2))
        self._min_snr_box.bind("<Return>", lambda e: self._on_min_snr())
        self._min_snr_box.bind("<FocusOut>", lambda e: self._on_min_snr())
        ttk.Label(box, text="dB").pack(side="left")
        flow.add(box, gap=12)
        self._min_snr_save = None

        # (the options and actions are in the menu bar: File / View /
        # Settings / Help)
        # SO2R: which radio (N1MM Entry Window A / B) clicks, F-keys and the
        # band follow go to; with one radio only A is used.
        self.radio_btn = flow.add(ttk.Button(flow, text=self._radio_btn_text(),
                                             style="Accent.TButton",
                                             command=self._toggle_radio), gap=16)

        # Function keys: always ONE row of equal buttons that share the width
        # (they get narrower with the window instead of wrapping).
        self.fkey_row = None
        if self.cfg.fkeys:
            row = ttk.Frame(self.root, padding=(10, 0, 10, 6))
            row.pack(fill="x")
            self.fkey_row = row
            for i, (key, label) in enumerate(self.cfg.fkeys):
                row.columnconfigure(i, weight=1, uniform="fkey")
                if theme.MODERN:      # flat coloured keys, like the phone
                    kind = ("log" if key.lower() == "enter" else
                            "spot" if key.lower() == "spotit" else "fkey")
                    theme.key_button(row, self._key_text(key, label, " "), kind,
                                     lambda k=key: self._press_key(k)).grid(
                        row=0, column=i, sticky="ew", padx=2)
                else:
                    ttk.Button(
                        row, text=self._key_text(key, label, " "), width=1,
                        style="Log.TButton" if key.lower() == "enter" else "FKey.TButton",
                        command=lambda k=key: self._press_key(k),
                    ).grid(row=0, column=i, sticky="ew", padx=2)

        widths = {
            "time": 70, "freq": 90, "snr": 70, "wpm": 60,
            "call": 130, "cont": 50, "mult": 45, "mode": 70, "type": 80,
            "spotter": 100,
        }
        tree_frame = ttk.Frame(self.root, padding=(12, 0, 12, 6))
        tree_frame.pack(fill="both", expand=True)
        self._tree_frame = tree_frame
        self.tree = ttk.Treeview(
            tree_frame, columns=self.columns, show="headings", selectmode="browse"
        )
        for col in self.columns:
            anchor = ("e" if col in ("freq", "snr", "wpm")
                      else "center" if col in ("cont", "mult", "mode", "type") else "w")
            self.tree.heading(col, text=self.headings[col], command=lambda c=col: self._on_sort(c))
            self.tree.column(col, width=theme.px(widths[col]), anchor=anchor,
                             stretch=(col == "call"))
        self._update_headings()
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self._tree_scroll = scroll
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        # the same list as tiles, like the phone (View > Spot list as tiles)
        self.tiles = TileList(tree_frame, on_tap=self._tile_tap)
        self.root.bind_all("<Control-t>", lambda _e: self._toggle_tiles(flip=True))
        self.root.bind_all("<Control-T>", lambda _e: self._toggle_tiles(flip=True))
        self._show_tiles()
        self.tree.tag_configure("stripe", background=theme.STRIPE)   # every other row
        self.tree.tag_configure("strong", foreground=theme.T_STRONG)
        self.tree.tag_configure("dx", background=theme.T_DX)
        self.tree.tag_configure("onfreq", background=theme.T_ONFREQ)  # tuned to it
        self.tree.tag_configure("mult", foreground=theme.T_MULT,
                                font=(theme.FONT, 9 if theme.MODERN else 10, "bold"))
        self.tree.tag_configure("worked", foreground=theme.T_WORKED)
        self._row_spots: list = []
        # keyboard: arrows move (Treeview), Space taps the selected spot
        self.tree.bind("<space>", self._on_tree_key)
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

        # status bar: what the last action did, and the phone page address
        bottom = tk.Frame(self.root, bg=theme.BAR_BG, padx=12, pady=4)
        bottom.pack(fill="x")
        self._bottom = bottom
        self.status_label = self._wrap_label(bottom, "", bg=theme.BAR_BG)
        self.web_label = self._wrap_label(bottom, "Phone view: starting...", bg=theme.BAR_BG,
                                          muted=True)

    def _build_menu(self) -> None:
        """Menu bar: File / View / Settings... / Help - the actions and
        options that used to be a row of buttons (sort, A / B and the F-keys
        stay as buttons: they're used all contest long)."""
        bar = tk.Menu(self.root)
        file = tk.Menu(bar, tearoff=False)
        file.add_command(label="Open the config folder", command=self._open_config_folder)
        file.add_command(label="Reconnect log", command=self._reconnect_log)
        file.add_command(label="Start autostart programs now", command=self._start_autostart)
        file.add_command(label="Don't wait for N1MM (start the rest now)",
                         command=self._stop_waiting_n1mm)
        file.add_command(label="Open the autostart folder",
                         command=lambda: self._open_path(self._autostart_folder()))
        file.add_separator()
        file.add_command(label="Restart POUNCer", command=self._restart_now,
                         state="normal" if self.updater.exe else "disabled")
        file.add_command(label="Exit", command=self._shutdown_and_quit)
        bar.add_cascade(label="File", menu=file)
        view = tk.Menu(bar, tearoff=False)
        view.add_checkbutton(label="Also enter callsign in N1MM", variable=self.set_call_var)
        view.add_checkbutton(label="Hide worked (same band + mode)",
                             variable=self.hide_worked_var, command=self._on_toggle_worked)
        view.add_separator()
        view.add_checkbutton(label="Compact window (beside N1MM)", variable=self.compact_var,
                             command=self._apply_compact, accelerator="Ctrl+M")
        view.add_checkbutton(label="Always on top", variable=self.topmost_var,
                             command=self._apply_topmost)
        self.rate_var = tk.BooleanVar(value=bool(getattr(self.cfg, "rate_line", True)))
        view.add_checkbutton(label="Rate / QSOs / mults per band line", variable=self.rate_var,
                             command=self._toggle_rate_line)
        view.add_checkbutton(label="Spot list as tiles (like the phone)",
                             variable=self.tiles_var, accelerator="Ctrl+T",
                             command=lambda: self._toggle_tiles(flip=False))
        view.add_separator()
        # rare CQ + new mult alert: pop-up and sound, each on its own
        self.alert_popup_var = tk.BooleanVar(value=bool(self.cfg.alert_popup))
        self.alert_sound_var = tk.BooleanVar(value=bool(self.cfg.alert_sound))
        view.add_checkbutton(label="Alert pop-up (rare CQ + new mult)",
                             variable=self.alert_popup_var,
                             command=lambda: self._set_alert("popup"))
        view.add_checkbutton(label="Alert sound", variable=self.alert_sound_var,
                             command=lambda: self._set_alert("sound"))
        view.add_separator()
        view.add_command(label="Clear spots", command=self._clear_spots)
        view.add_command(label="Contest summary", command=lambda: self._open_browser("summary"))
        view.add_command(label="CQ zones: who hears me", command=lambda: self._zones_win.open())
        view.add_command(label="Band opening timeline", command=lambda: self._timeline_win.open())
        view.add_command(label="Phone page in the browser", command=self._open_browser)
        view.add_command(label="Phone page as an app window", command=self._open_app_window)
        view.add_command(label="Traffic console (raw data in / out)", accelerator="Ctrl+Shift+D",
                         command=lambda: self._console.open())
        view.add_command(label="Station test (QSY + callsign, each radio)",
                         command=self._station_test)
        view.add_separator()
        looks = tk.Menu(view, tearoff=False)
        self.look_var = tk.StringVar(value=getattr(self.cfg, "look", "classic"))
        for value, text in (("dark", "Dark (Windows 11 style)"), ("light", "Light (Windows 11 style)"),
                            ("classic", "Classic")):
            looks.add_radiobutton(label=text, value=value, variable=self.look_var,
                                  command=self._set_look)
        view.add_cascade(label="Look (from the next start)", menu=looks)
        bar.add_cascade(label="View", menu=view)
        bar.add_command(label="Settings...", command=self._settings_win.open)
        helpm = tk.Menu(bar, tearoff=False)
        helpm.add_command(label="What every button does", command=lambda: self._open_browser("help"))
        helpm.add_command(label="Check for a new version", command=self._on_update_click)
        helpm.add_command(label="POUNCer on GitHub", command=lambda: self._open_url(GITHUB_URL))
        helpm.add_command(label="Save diagnostics (zip)", command=self._save_diagnostics)
        helpm.add_command(label="Open the log file", command=lambda: self._open_path(
            diagmod.log_path(self.cfg.run_history_dir or "cache")))
        helpm.add_command(label="Open the performance log (CPU / memory / disk)",
                          command=lambda: self._open_path(self.perf.path))
        helpm.add_separator()
        helpm.add_command(label=f"About POUNCer {__version__}", command=self._about)
        bar.add_cascade(label="Help", menu=helpm)
        self.root.configure(menu=bar)
        self.menubar = bar

    COMPACT_COLUMNS = ("freq", "call", "mult")
    COMPACT_GEOMETRY = "330x430"

    def _apply_compact(self) -> None:
        """View > Compact window: a narrow window beside N1MM - one line with
        the A / B button and both frequencies, then the spot list with just
        frequency, call (⚡ / ?) and mult. Same clicks and keys as always.
        Turned off: everything back, at the size it had before."""
        compact = bool(self.compact_var.get())
        frames = [self._header, self._outer, self.fkey_row, self._bottom]
        if compact:
            if not self._normal_geom:
                self._normal_geom = self.root.geometry()
            for f in frames:
                if f is not None:
                    f.pack_forget()
            self._compact_bar.pack(fill="x", before=self._tree_frame)
            bands = getattr(self, "_compact_bands", None)
            if bands is not None and self.cfg.band_lights:
                bands.pack(fill="x", before=self._tree_frame)
                self._bands_last = None
            self.tree.configure(displaycolumns=self.COMPACT_COLUMNS)
            self.root.minsize(theme.px(240), theme.px(200))
            cw, ch = (int(v) for v in self.COMPACT_GEOMETRY.split("x"))
            geom = self._compact_geom or f"{theme.px(cw)}x{theme.px(ch)}"
            x, y = self.root.winfo_x(), self.root.winfo_y()
            self.root.geometry(geom if "+" in geom else f"{geom}+{x}+{y}")
            if not self.topmost_var.get():          # beside N1MM: stay visible
                self.topmost_var.set(True)
            self._update_compact_bar()
        else:
            self._compact_geom = self.root.geometry()
            self._compact_bar.pack_forget()
            if getattr(self, "_compact_bands", None) is not None:
                self._compact_bands.pack_forget()
            self._tree_frame.pack_forget()
            for f in frames[:3]:
                if f is not None:
                    f.pack(fill="x")
            self._tree_frame.pack(fill="both", expand=True)
            self._bottom.pack(fill="x")
            self.tree.configure(displaycolumns="#all")
            self.root.minsize(theme.px(420), theme.px(300))
            if self._normal_geom:
                self.root.geometry(self._normal_geom)
                self._normal_geom = ""
            self.topmost_var.set(False)
        self._apply_topmost()

    def _toggle_compact(self) -> str:
        """Ctrl+M / the ⤢ button: compact window on / off."""
        self.compact_var.set(not self.compact_var.get())
        self._apply_compact()
        return "break"

    def _update_compact_bands(self, data: dict, here: str | None) -> None:
        """The small band chips of the compact window: colour = band open,
        '40·3' = 3 new mults spotted on 40 m; frame = radio's band, ▶ = shown."""
        chips = getattr(self, "_compact_chips", None)
        if not chips or not self.compact_var.get():
            return
        for band, lbl in chips.items():
            c = data.get(band) or {"state": "closed", "dx": 0, "spots": 0}
            bg, fg = self._band_chip_color(c)
            m = c.get("mults") or 0
            viewed = band == self._view_band
            lbl.configure(text=("\u25b6" if viewed else "") + band.rstrip("m")
                          + (f"\u00b7{m}" if m else ""), bg=bg, fg=fg,
                          relief="solid" if band in (here, self._view_band) else "flat",
                          bd=2 if viewed else (1 if band == here else 0))

    # ---- rare CQ + new mult alert (window) --------------------------------
    ALERT_MS = 8000

    def _set_alert(self, which: str) -> None:
        """View > Alert pop-up / Alert sound: used at once, saved in config.ini."""
        var = self.alert_popup_var if which == "popup" else self.alert_sound_var
        on = bool(var.get())
        try:
            self._save_setting(f"ui.alert_{which}", "true" if on else "false")
        except Exception as exc:  # noqa: BLE001 - still works until the restart
            log.info("alert setting not saved: %s", exc)
            setattr(self.cfg, f"alert_{which}", on)
        if which == "sound" and on:
            alert_beep(self.root)                 # so you hear what it sounds like

    def _build_alert_popup(self) -> None:
        """A yellow pop-up over the window, made once (like the CW speed one):
        it never takes the keyboard from N1MM, but a click on it = QSY and
        call that station."""
        self._alert_pop = None
        self._alert_after = None
        self._alert_spot = None
        self._alert_shown = False
        try:
            # never withdrawn / re-shown (showing a window can take the
            # keyboard): it stays mapped, moved off screen and transparent
            pop = tk.Toplevel(self.root)
            pop.overrideredirect(True)
            pop.attributes("-topmost", True)
            pop.attributes("-alpha", 0.0)
            pop.geometry("+-3000+-3000")
            self._alert_label = tk.Label(pop, text="", font=(theme.FONT, 13, "bold"),
                                         bg="#ffd600", fg="#212121", padx=16, pady=8,
                                         cursor="hand2", justify="left")
            self._alert_label.pack()
            self._alert_label.bind("<Button-1>", lambda _e: self._on_alert_click())
            pop.update_idletasks()
            if win32mod._WIN:
                try:
                    win32mod.make_no_activate(int(pop.wm_frame(), 16), click_through=False)
                except (ValueError, tk.TclError):
                    pass
            self._alert_pop = pop
        except tk.TclError:
            self._alert_pop = None

    def _check_alerts(self, candidates: list) -> None:
        """Rows that are a rare CQ and a new mult now: alert the new ones."""
        fresh = self._alerts.fresh([(self._row_key(s), s) for s in candidates])
        if not fresh:
            return
        s = fresh[0]
        more = f"  (+{len(fresh) - 1} more)" if len(fresh) > 1 else ""
        log.info("alert: rare CQ + new mult %s %.1f%s", s.dxcall, s.freq_khz, more)
        if self.cfg.alert_popup:
            self._show_alert(s, f"\u26a1 rare CQ + new mult: {s.dxcall}  {s.freq_khz:.1f}{more}"
                                "\nclick = QSY and call")
        if self.cfg.alert_sound:
            alert_beep(self.root)

    def _note_self_spot(self, dxcall: str, spotter: str, freq_khz: float,
                        comment: str = "") -> None:
        """Any thread: a spot of MY call by a person (not a skimmer)."""
        if not getattr(self.cfg, "self_spot_alert", True):
            return
        if self._self_spots.check(dxcall, spotter, freq_khz, self.own_call()):
            self._self_spot_q.put((dxcall, spotter, float(freq_khz), comment or "",
                                   time.strftime("%H%MZ", time.gmtime())))

    def _drain_self_spots(self) -> None:
        while True:
            try:
                call, spotter, khz, comment, t = self._self_spot_q.get_nowait()
            except queue.Empty:
                return
            own = " (self-spot)" if SelfSpotWatch.is_self(spotter, self.own_call()) else ""
            text = (f"SPOTTED: {call} by {spotter}{own} on {khz:.1f}  {t}"
                    + (f"\n\u201c{comment}\u201d" if comment else ""))
            log.info("self spot: %s spotted by %s on %.1f %s", call, spotter, khz, comment)
            self.status_label.configure(text=text.replace("\n", "  "))
            self._show_alert(None, text)
            if self.cfg.alert_sound:
                alert_beep(self.root)

    def _show_alert(self, spot, text: str) -> None:
        pop = getattr(self, "_alert_pop", None)
        if pop is None:
            return
        try:
            self._alert_spot = spot
            self._alert_label.configure(text=text)
            pop.update_idletasks()
            w, h = pop.winfo_reqwidth(), pop.winfo_reqheight()
            if self.root.state() in ("iconic", "withdrawn"):
                x, y = (self.root.winfo_screenwidth() - w) // 2, 40
            else:
                x = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
                y = self.root.winfo_rooty() + 8
            x = max(0, min(x, self.root.winfo_screenwidth() - w))
            pop.geometry(f"+{x}+{y}")
            pop.attributes("-alpha", 0.96)
            pop.lift()
            self._alert_shown = True
            if self._alert_after is not None:
                self.root.after_cancel(self._alert_after)
            self._alert_after = self.root.after(self.ALERT_MS, self._hide_alert)
        except tk.TclError:
            pass

    def _hide_alert(self) -> None:
        self._alert_after = None
        self._alert_shown = False
        if getattr(self, "_alert_pop", None) is not None:
            try:
                self._alert_pop.attributes("-alpha", 0.0)
                self._alert_pop.geometry("+-3000+-3000")
            except tk.TclError:
                pass

    def _on_alert_click(self) -> None:
        s = self._alert_spot
        self._hide_alert()
        if s is not None:
            self._act_on_spot(s)

    # ---- CW speed popup ---------------------------------------------------

    def _build_wpm_popup(self) -> None:
        """A small 'CW 30 WPM' popup, shown for a moment when N1MM's speed
        changes. Made once and only moved / faded in and out, never shown
        anew - so it can't take the keyboard from N1MM (no activation,
        clicks go through it)."""
        self._wpm_pop = None
        self._wpm_pop_after = None
        if not self.cfg.cw_speed or not win32mod._WIN:
            return
        try:
            pop = tk.Toplevel(self.root)
            pop.overrideredirect(True)
            pop.attributes("-topmost", True)
            pop.attributes("-alpha", 0.0)
            pop.geometry("+-3000+-3000")
            self._wpm_pop_label = tk.Label(pop, text="", font=("Segoe UI", 22, "bold"),
                                           bg="#6a1b9a", fg="white", padx=22, pady=10)
            self._wpm_pop_label.pack()
            pop.update_idletasks()
            try:
                win32mod.make_no_activate(int(pop.wm_frame(), 16))
            except (ValueError, tk.TclError):
                pass
            self._wpm_pop = pop
        except tk.TclError:
            self._wpm_pop = None

    def _show_wpm_popup(self, text: str) -> None:
        pop = self._wpm_pop
        if pop is None:
            return
        try:
            self._wpm_pop_label.configure(text=text)
            pop.update_idletasks()
            w, h = pop.winfo_reqwidth(), pop.winfo_reqheight()
            if self.root.state() in ("iconic", "withdrawn"):
                x = (self.root.winfo_screenwidth() - w) // 2
                y = self.root.winfo_screenheight() // 3
            else:
                x = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
                y = self.root.winfo_rooty() + max(10, self.root.winfo_height() // 3 - h // 2)
            pop.geometry(f"+{x}+{y}")
            pop.attributes("-alpha", 0.94)
            if self._wpm_pop_after is not None:
                self.root.after_cancel(self._wpm_pop_after)
            self._wpm_pop_after = self.root.after(1500, self._hide_wpm_popup)
        except tk.TclError:
            pass

    def _hide_wpm_popup(self) -> None:
        self._wpm_pop_after = None
        if self._wpm_pop is None:
            return
        try:
            self._wpm_pop.attributes("-alpha", 0.0)
            self._wpm_pop.geometry("+-3000+-3000")
        except tk.TclError:
            pass

    def _entry_windows(self) -> dict[int, int]:
        """Radio -> its Entry Window (for the CW speed reader)."""
        out = {}
        for nr in self.state.radio_numbers():
            hwnd = self.state.entry_window_hwnd(nr)
            if hwnd:
                out[nr] = hwnd
        return out

    def _tried(self, s) -> tuple[int, int] | None:
        """Tapped this station on its band lately: (times, minutes since the
        last tap); None when not (or [ui] mark_tried is off)."""
        if not self.cfg.mark_tried:
            return None
        got = self.taps.tried(s.dxcall, band_from_khz(s.freq_khz), self.cfg.tried_minutes * 60)
        if not got:
            return None
        return got[0], int((time.time() - got[1]) // 60)

    def _tried_fields(self, s) -> dict:
        t = self._tried(s)
        return {"tried": t[1], "tries": t[0]} if t else {}

    def _wpm(self, nr: int) -> int | None:
        return self.cwspeed.wpm.get(nr) if self.cwspeed is not None else None

    def _wpm_text(self) -> str:
        """'CW 28 WPM' (SO2R: 'CW A 28 / B 30 WPM'); '' if unknown."""
        if self.cwspeed is None:
            return ""
        nrs = self.state.radio_numbers() or [1]
        known = [(nr, self._wpm(nr)) for nr in nrs if self._wpm(nr) is not None]
        if not known:
            note = self.cwspeed.status(self._radio_nr())
            return f"CW speed: {note}" if note else ""
        if len(nrs) > 1:
            return "CW " + " / ".join(f"{'AB'[n - 1] if n in (1, 2) else n} {w}" for n, w in known) + " WPM"
        return f"CW {known[0][1]} WPM"

    def _drain_wpm(self) -> None:
        if self.cwspeed is None:
            return
        while True:
            try:
                nr, old, new = self.cwspeed.changes.get_nowait()
            except queue.Empty:
                break
            if old is None or not self.cfg.cw_speed_popup:
                continue          # first reading: no popup
            arrow = "\u25b2" if new > old else "\u25bc"
            self._show_wpm_popup(f"{self._radio_tag(nr)}CW {new} WPM {arrow}")

    # ---- minimum SNR -----------------------------------------------------

    def _on_min_snr(self) -> None:
        """Main window Min SNR box changed (arrow, typed + Enter)."""
        try:
            v = int(float(self.min_snr_var.get().strip() or 0))
        except ValueError:
            self.min_snr_var.set(str(self.cfg.min_snr))
            return
        self._set_min_snr(v)

    def _set_min_snr(self, v: int) -> None:
        """New minimum SNR (window or phone): list now, config.ini a moment
        later (clicking the arrows doesn't write the file every time)."""
        v = max(0, min(60, int(v)))
        if hasattr(self, "min_snr_var") and self.min_snr_var.get() != str(v):
            self.min_snr_var.set(str(v))
        if v == self.cfg.min_snr:
            return
        self.cfg.min_snr = v
        self._dirty = True
        self.status_label.configure(
            text=f"Min SNR {v} dB: weaker spots are not listed (window and phone)"
                 + ("" if v else " - 0 = all spots"))
        if self._min_snr_save is not None:
            self.root.after_cancel(self._min_snr_save)
        self._min_snr_save = self.root.after(1000, self._save_min_snr)

    def _save_min_snr(self) -> None:
        self._min_snr_save = None
        try:
            self._save_setting("ui.min_snr", str(self.cfg.min_snr))
        except Exception as exc:  # noqa: BLE001 - the list works anyway
            log.info("min SNR not saved: %s", exc)

    def _web_min_snr(self, db: int) -> None:
        self._web_queue.put(("minsnr", int(db)))

    # ---- autostart -----------------------------------------------------------

    def _autostart_folder(self) -> str:
        f = self.cfg.autostart_folder
        if not f:
            return ""
        if not os.path.isabs(f):
            f = os.path.join(os.path.dirname(self.cfg.config_path or ".") or ".", f)
        if not os.path.isdir(f):          # make it, so it is easy to find
            try:
                os.makedirs(f, exist_ok=True)
                with open(os.path.join(f, "README.txt"), "w", encoding="utf-8") as fh:
                    fh.write("Put programs (.exe .bat .cmd) or shortcuts (.lnk) here: POUNCer\n"
                             "starts them when it starts, in name order (1 rotor.lnk, 2 cw.bat),\n"
                             "[autostart] folder_delay seconds apart, after app1-app8.\n")
            except OSError:
                return ""
        return f

    def _start_autostart(self, defer_pouncer: bool = False) -> bool:
        """Start programs 1-8 and the autostart folder, each after its delay.

        Program 1 = N1MM Logger+ that isn't running yet (and [autostart]
        wait_n1mm): start it, wait until it is up, then the other programs -
        and with ``defer_pouncer`` the rest of POUNCer too. True = waiting."""
        apps = [(getattr(self.cfg, f"autostart_app{n}", ""), getattr(self.cfg, f"autostart_delay{n}", 5),
                 getattr(self.cfg, f"autostart_wait{n}", False))
                for n in range(1, autostartmod.APPS + 1)]
        items = autostartmod.plan(apps, self._autostart_folder(), self.cfg.autostart_folder_delay)
        if not items:
            return False
        if (getattr(self.cfg, "autostart_wait_n1mm", True) and autostartmod.n1mm_first(items)
                and self._n1mm_running() is not True and os.path.exists(items[0]["path"])):
            first, rest = items[0], items[1:]
            self._n1mm_wait = None
            self._n1mm_wait_defer = defer_pouncer
            self._n1mm_wait_rest = rest
            self.status_label.configure(
                text=f"Autostart: N1MM first, in {first['delay']} s - the rest when N1MM is up")
            self.root.after(first["delay"] * 1000, lambda: self._launch_n1mm(first))
            return True
        self._run_autostart(items)
        return False

    def _run_autostart(self, items: list[dict]) -> None:
        if not items:
            return
        self._autostart = autostartmod.Autostart(
            items, running=running_process_names,
            on_status=lambda t: self._web_queue.put(("status", "Autostart: " + t)),
            n1mm_up=self._n1mm_up, wait_s=getattr(self.cfg, "autostart_n1mm_timeout", 180))
        self._autostart.start()
        self.status_label.configure(text=f"Autostart: {len(items)} program(s) - "
                                         f"first in {items[0]['delay']} s")

    def _n1mm_up(self) -> bool:
        """N1MM broadcasts (RadioInfo in the last 30 s, or AppInfo) - thread-safe."""
        last = self.state.last_update()
        if last and time.time() - last < 30:
            return True
        app = self.state.app() or {}
        return app.get("kind") == "AppInfo" and time.time() - app.get("t", 0) < 120

    def _launch_n1mm(self, item: dict) -> None:
        try:
            autostartmod._launch(item["path"], item["args"])
        except Exception as exc:  # noqa: BLE001 - go on without it
            log.warning("autostart: N1MM failed to start: %s", exc)
            self.status_label.configure(text=f"Autostart: N1MM failed to start: {exc}")
            self._n1mm_wait_done()
            return
        self._n1mm_wait = autostartmod.N1mmWait(
            time.time(), timeout_s=getattr(self.cfg, "autostart_n1mm_timeout", 180))
        self._n1mm_wait_tick()

    def _n1mm_wait_tick(self) -> None:
        w = getattr(self, "_n1mm_wait", None)
        if w is None:
            return
        app = self.state.app() or {}
        done, text = w.check(time.time(), self.state.last_update(), app.get("t", 0.0),
                             self._n1mm_running())
        self.status_label.configure(text="Autostart: " + text + (
            "" if done else "   (File > Don't wait for N1MM)"))
        if done:
            self._n1mm_wait_done()
        else:
            self.root.after(1000, self._n1mm_wait_tick)

    def _n1mm_wait_done(self) -> None:
        """N1MM is up (or we stop waiting): the rest of POUNCer, then the
        other programs (program 2's delay counts from now)."""
        if getattr(self, "_n1mm_wait_rest", None) is None:
            return
        self._n1mm_wait = None
        rest, self._n1mm_wait_rest = self._n1mm_wait_rest, None
        if self._n1mm_wait_defer:
            self._start_services()
        self._run_autostart(rest)

    def _stop_waiting_n1mm(self) -> None:
        """File > Don't wait for N1MM: go on now."""
        if getattr(self, "_n1mm_wait_rest", None) is not None:
            self._n1mm_wait_done()

    def _apply_topmost(self) -> None:
        try:
            self.root.attributes("-topmost", bool(self.topmost_var.get()))
        except tk.TclError:
            pass

    def _update_compact_bar(self) -> None:
        """A / B button and both radios' frequencies (compact mode)."""
        if not self.compact_var.get():
            return
        self._compact_btn.configure(text=f"Radio {'AB'[self.radio - 1]}")
        parts = []
        for nr in self.state.radio_numbers() or [1]:
            khz = self._radio_khz(nr)
            parts.append(f"{'AB'[nr - 1] if nr in (1, 2) else nr} "
                         + (f"{khz:.1f}" if khz else "-"))
        wpm = self._wpm(self._radio_nr())
        self._compact_info.configure(text="   ".join(parts) + (f"   {wpm} WPM" if wpm else "")
                                     + f"   ({len(self.displayed)} spots)")

    def _open_url(self, url: str) -> None:
        import webbrowser
        try:
            webbrowser.open(url, new=2)
        except Exception as exc:
            self.status_label.configure(text=f"Could not open the browser: {exc}")

    def _open_config_folder(self) -> None:
        import os
        folder = os.path.dirname(self.cfg.config_path) if self.cfg.config_path else os.getcwd()
        try:
            if sys.platform == "win32":
                os.startfile(folder)            # Explorer
            else:
                import subprocess
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", folder])
            self.status_label.configure(text=f"Opened {folder}")
        except Exception as exc:
            self.status_label.configure(text=f"Could not open {folder}: {exc}")

    def _window_file(self) -> str:
        import os
        return os.path.join(self.cfg.run_history_dir or "cache", "window.json")

    def _restore_window(self) -> None:
        """Size / position (if it still fits this screen), maximised, column
        widths and sort from the last time (cache/window.json)."""
        data = winstate.load(self._window_file())
        if not data:
            return
        try:
            # saved at another display scaling (or before POUNCer was DPI aware)
            factor = theme.SCALE / float(data.get("scale") or 1.0)
            for k in ("geometry", "compact_geometry"):
                if data.get(k):
                    data[k] = winstate.scale_geometry(data[k], factor)
            if abs(factor - 1.0) >= 0.05 and isinstance(data.get("columns"), dict):
                data["columns"] = {c: round(w * factor) for c, w in data["columns"].items()
                                   if isinstance(w, (int, float))}
            geom = winstate.fit_geometry(data.get("geometry", ""),
                                         self.root.winfo_screenwidth(),
                                         self.root.winfo_screenheight())
            if geom:
                self.root.geometry(geom)
            for col, width in winstate.column_widths(data.get("columns"), self.columns).items():
                self.tree.column(col, width=width)
            col, rev = winstate.sort_choice(data, self.columns, (self.sort_col, self.sort_reverse))
            if (col, rev) != (self.sort_col, self.sort_reverse):
                self.sort_col, self.sort_reverse = col, rev
                self._update_headings()
                self._update_sort_buttons()
            cg = winstate.fit_geometry(data.get("compact_geometry", ""),
                                       self.root.winfo_screenwidth(),
                                       self.root.winfo_screenheight())
            self._compact_geom = cg or ""
            if data.get("compact"):
                self.compact_var.set(True)
                self._apply_compact()
            elif data.get("topmost"):
                self.topmost_var.set(True)
                self._apply_topmost()
            if data.get("zoomed") and not data.get("compact"):
                self.root.state("zoomed")
        except (tk.TclError, ValueError, TypeError) as exc:
            # a bad saved window must never stop POUNCer from starting
            log.info("window state not restored: %s", exc)

    def _save_window(self) -> None:
        """Remember the window for the next start (only while it exists)."""
        tl = getattr(self, "timeline", None)
        if tl is not None:           # the band timeline too (close / restart / update)
            tl.save()
        try:
            zoomed = self.root.state() == "zoomed"
            compact = bool(self.compact_var.get())
            data = {
                "geometry": self._normal_geom if compact and self._normal_geom
                else self.root.geometry(),
                "compact": compact,
                "compact_geometry": self.root.geometry() if compact else self._compact_geom,
                "topmost": bool(self.topmost_var.get()),
                "zoomed": zoomed,
                "columns": {c: int(self.tree.column(c, "width")) for c in self.columns},
                "sort": self.sort_col, "reverse": bool(self.sort_reverse),
                "scale": round(theme.SCALE, 3),
            }
        except (tk.TclError, AttributeError):
            return                  # window already gone: keep the last save
        if zoomed:                  # keep the normal size from before
            old = winstate.load(self._window_file()).get("geometry")
            if old:
                data["geometry"] = old
        winstate.save(self._window_file(), data)

    RATE_EVERY_S = 30

    def _maybe_rate_line(self) -> None:
        """Rate line: read the log in a thread every RATE_EVERY_S, show the
        result here (main thread)."""
        lbl = getattr(self, "rate_label", None)
        if lbl is None or not getattr(self.cfg, "rate_line", True):
            return
        text = getattr(self, "_rate_text", None)
        if text is not None and lbl.cget("text") != text:
            lbl.configure(text=text)
        if self._rate_busy or time.time() - self._rate_at < self.RATE_EVERY_S:
            return
        self._rate_at = time.time()
        self._rate_busy = True

        def work():
            try:
                self._rate_text = summarymod.rate_line(self._summary())
            except Exception as exc:  # noqa: BLE001
                log.debug("rate line: %s", exc)
            finally:
                self._rate_busy = False
        threading.Thread(target=work, name="rate-line", daemon=True).start()

    def _toggle_rate_line(self) -> None:
        on = bool(self.rate_var.get())
        self.cfg.rate_line = on
        if on:
            self.rate_label.pack(fill="x", padx=12, pady=(0, 2), after=self.lights_frame)
            self._rate_at = 0.0
        else:
            self.rate_label.pack_forget()
        self._save_setting("ui.rate_line", "true" if on else "false")

    def _station_test(self) -> None:
        """View > Station test: per radio QSY 0.1 kHz up / back and a test call
        into the empty callsign box (in a thread - the window stays live)."""
        if getattr(self, "_station_testing", False):
            return
        self._station_testing = True
        self.status_label.configure(text="Station test running ...")
        out: "queue.Queue[str]" = queue.Queue()

        def qsy(nr, khz):
            button = ("Left" if nr == 2 and self._so2r()
                      and getattr(self.cfg, "radio_b_qsy", "udp-left") == "udp-left" else None)
            try:
                self.controller.set_frequency(khz, mousebutton=button, radio_nr=nr)
                return True
            except OSError:
                return False

        win = sys.platform == "win32"

        def work():
            try:
                res = stationtest.run(
                    self.state.radio_numbers(), self.state.snapshot, qsy, self._wait_for_qsy,
                    entry_hwnd=self.state.entry_window_hwnd,
                    read_box=focused_edit_text if win else None,
                    set_box=set_focused_edit_text if win else None,
                    radio_name=lambda nr: (f"Radio {'AB'[nr - 1]}" if self._so2r() and nr in (1, 2)
                                           else "Radio"),
                    skip_qsy=lambda nr: ("radio B is tuned by typing into its Entry Window "
                                         "([n1mm] radio_b_qsy = type)" if self._types_qsy(nr) else ""))
                text = stationtest.report(res)
            except Exception as exc:  # noqa: BLE001
                log.exception("station test")
                text = f"Station test failed: {exc}"
            log.info("station test:\n%s", text)
            out.put(text)

        def poll():
            try:
                text = out.get_nowait()
            except queue.Empty:
                self.root.after(200, poll)
                return
            self._station_testing = False
            ok = "PROBLEM" not in text and "failed" not in text
            self.status_label.configure(text="Station test: " + ("all OK" if ok else "see the result"))
            self._show_text_window("POUNCer - station test", text)

        threading.Thread(target=work, name="station-test", daemon=True).start()
        self.root.after(200, poll)

    def _show_text_window(self, title: str, text: str) -> None:
        w = tk.Toplevel(self.root)
        w.title(title)
        w.configure(bg=theme.BG)
        w.after(10, lambda: theme.title_bar(w))
        t = tk.Text(w, width=78, height=min(24, text.count("\n") + 3), wrap="word",
                    bg=theme.PANEL, fg=theme.TEXT, bd=0, padx=12, pady=10,
                    font=(theme.FONT, 10))
        for line in text.splitlines():
            tag = "bad" if ("✗" in line or "PROBLEM" in line) else (
                "ok" if ("✓" in line or line.endswith(": OK")) else "")
            t.insert("end", line + "\n", tag)
        for st, (_bg, fg) in theme.CHECK.items():
            t.tag_configure("bad" if st == "bad" else st, foreground=fg)
        t.configure(state="disabled")
        t.pack(fill="both", expand=True)
        ttk.Button(w, text="Close", command=w.destroy).pack(pady=(0, 10))

    def _console_notes(self) -> dict:
        """Why a source may be quiet, for the traffic console's top bar."""
        notes = {}
        role = self.team.role() if self.team is not None else "off"
        if role == "slave":
            notes["RBN"] = notes["SSB"] = "via the team master"
            if self.team.master_paused():
                notes["RBN"] = "paused - the whole team on SSB"
        elif getattr(self, "_rbn_pause_on", False):
            notes["RBN"] = ("paused - SSB contest" if self._phone_contest()
                            else "paused - every radio on SSB")
        elif not self._rbn_clients:
            notes["RBN"] = "not connected"
        if role != "slave":
            ssb = (self.ssb_status or "").lower()
            if ssb.startswith("via n1mm"):
                notes["SSB"] = "spots come from N1MM"
            elif ssb in ("", "off"):
                notes["SSB"] = "cluster off - no radio on SSB"
        if self._http_feed is None:
            notes["HTTP"] = "backup off - RBN telnet works"
        if not self.cfg.web_enabled:
            notes["WEB"] = "phone page off"
        return notes

    def _on_close(self) -> None:
        """Window closed (X / File > Exit): remember it, then quit."""
        self._save_window()
        if getattr(self, "_console", None) is not None:
            self._console.close()
        if getattr(self, "pskr", None) is not None:
            self.pskr.stop()
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def _open_path(self, path: str) -> None:
        if not path:
            self.status_label.configure(text="No autostart folder ([autostart] folder is empty)")
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)
            else:
                import webbrowser
                webbrowser.open("file://" + path)
        except OSError as exc:
            self.status_label.configure(text=f"Could not open {path}: {exc}")

    def _restart_now(self) -> None:
        """Menu File > Restart / Settings window: start POUNCer again."""
        self._save_window()
        self.status_label.configure(text="Restarting...")
        self.root.update_idletasks()
        self.updater.restart(before_exit=self._shutdown)

    def _shutdown_and_quit(self) -> None:
        """File > Exit: close the window; run() then shuts everything down."""
        self._on_close()

    def _diag_facts(self) -> dict:
        import platform
        f = {"version": __version__, "frozen": bool(getattr(sys, "frozen", False)),
             "platform": platform.platform(), "python": platform.python_version(),
             "config": self.cfg.config_path, "time": time.strftime("%Y-%m-%d %H:%M:%S %z")}
        try:
            f["radios"] = {nr: {k: v for k, v in (self.state.snapshot(nr) or {}).items()
                                if k in ("freq_khz", "band", "mode", "age", "entry_window_hwnd")}
                           for nr in self.state.radio_numbers()}
            f["chosen radio"] = self.radio
            f["n1mm running"] = self._n1mm_running()
            f["n1mm app info"] = self.state.app()
            f["log"] = f"{self.worked.db_name} ({self.worked.status}, source {self.worked.contest_source})"
            f["team"] = self.team.role() if self.team is not None else "off"
            f["rbn"] = self.rbn_status
            f["phone page"] = self._local_web_url() or "off"
            f["last tap"] = getattr(self, "_last_tap", None)
            f["autostart"] = getattr(getattr(self, "_autostart", None), "results", None)
            f["checks"] = [(i["name"], i["state"], i["text"])
                           for i in checksmod.build(self._check_facts())]
        except Exception as exc:  # noqa: BLE001 - the zip still helps
            f["facts error"] = repr(exc)
        return f

    def _save_diagnostics(self) -> None:
        """Help > Save diagnostics: a zip next to config.ini, folder opened."""
        folder = os.path.dirname(self.cfg.config_path) if self.cfg.config_path else os.getcwd()
        out = os.path.join(folder, diagmod.zip_name())
        try:
            diagmod.build_zip(out, self.cfg.config_path, self.cfg.run_history_dir or "cache",
                              self._diag_facts())
        except OSError as exc:
            self.status_label.configure(text=f"Diagnostics failed: {exc}")
            return
        log.info("diagnostics saved: %s", out)
        self.status_label.configure(text=f"Diagnostics saved: {out} (passwords removed)")
        self._open_path(folder)

    def _about(self) -> None:
        from tkinter import messagebox
        messagebox.showinfo("About POUNCer", (
            f"POUNCer {__version__}\n"
            "Search & pounce helper for N1MM Logger+: RBN / DX-cluster spots for "
            "your band, one tap to tune radio A or B and put the call in.\n\n"
            f"Settings: {self.cfg.config_path or 'config.ini'}\n{GITHUB_URL}"), parent=self.root)

    @staticmethod
    def _wrap_label(parent, text: str, muted: bool = False, bg: str | None = None):
        """A full-width info line that wraps onto more lines when the window
        is narrow; **...** in its text is shown bold (the values). ``muted``:
        small grey text with the values in dark bold."""
        from .richlabel import RichLabel   # needs the real Tk (tests stub it)
        if muted:
            label = RichLabel(parent, text, bg=bg, fg=theme.MUTED, bold_fg=theme.TEXT, size=9)
        else:
            label = RichLabel(parent, text, bg=bg)
        label.pack(fill="x")
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
            btn.configure(text=self._sort_labels[col] + arrow,
                          style="SortOn.TButton" if col == self.sort_col else "Sort.TButton")

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

    def _phone_radio(self, radio: int | None = None) -> bool:
        """This radio is on USB / LSB / SSB (N1MM RadioInfo mode) - and not in
        an FT8 / FT4 window: 14074 on USB is FT8; 7074 - 7077 (inside the SSB
        segment) is phone only while an SSB contest is open in N1MM."""
        snap = self.state.snapshot(radio) if radio is not None or self.state.radio_numbers() else None
        if not snap or (snap.get("mode") or "").upper() not in ("USB", "LSB", "SSB", "AM", "FM"):
            return False
        khz = snap.get("freq_khz") or 0
        if digital_sure(khz):
            return False
        if digital_overlap(khz):
            return self._phone_contest() if hasattr(self, "_phone_contest") else False
        return True

    def _effective_modes(self, radio: int | None = None) -> tuple[str, ...]:
        """Modes of the spots to show for a radio: SSB while it is on
        USB / LSB (follow_radio_mode), else the configured ones (CW)."""
        r = radio or getattr(self, "radio", None)
        if getattr(self.cfg, "follow_radio_mode", False) and self._phone_radio(r):
            return ("SSB",)
        return tuple(self.cfg.follow_modes)

    def _ssb_wanted(self) -> bool:
        mode = getattr(self.cfg, "ssb_mode", "off")
        if mode == "off" or not getattr(self.cfg, "ssb_host", ""):
            return False
        if self.team is not None and (time.time() - self.team.started < 3
                                      or self.team.role() == "slave"):
            return False            # the master passes SSB spots on
        if mode == "always":
            return True
        if any(self._phone_radio(nr) for nr in self.state.radio_numbers()):
            self._ssb_last_needed = time.time()
            return True
        if self.team is not None and self.team.role() == "master":
            return True             # for the team's SSB stations
        # keep it a while after the radio went back to CW
        return time.time() - getattr(self, "_ssb_last_needed", 0) < 15 * 60

    N1MM_SPOT_START_S = 0      # N1MM's first spot: our cluster closes at once
    N1MM_SPOT_START_N = 1
    N1MM_SPOT_BREAK_S = 300    # a gap this long ends N1MM's stream (start counting again)
    N1MM_SPOT_QUIET_S = 20     # no N1MM spot this long = N1MM stopped: our own cluster again ...
    N1MM_SPOT_QUIET_X = 4      # ... or 4 x its usual gap on a slow band (at most BREAK_S)
    N1MM_SPOT_HOLD_S = 60      # then our own cluster at least this long (one login a minute at most)

    def _n1mm_quiet_limit(self) -> float:
        """20 s without an N1MM spot = stopped; on a slow band (usual gap over
        5 s) 4 x the usual gap, so a quiet evening doesn't switch back and forth."""
        gap = getattr(self, "_n1mm_gap", None) or 0.0
        return min(self.N1MM_SPOT_BREAK_S, max(self.N1MM_SPOT_QUIET_S, self.N1MM_SPOT_QUIET_X * gap))

    def _on_n1mm_spot(self, pkt: dict) -> None:
        """A <spot> packet from N1MM (listener thread). Any spot shows N1MM's
        telnet is alive; SSB ones go to the list while N1MM is the SSB source."""
        now = time.time()
        if self._n1mm_spot_first is None or now - self._n1mm_spot_last > self.N1MM_SPOT_BREAK_S:
            self._n1mm_spot_first = now
            self._n1mm_streak = 0
            self._n1mm_gap = None
            log.info("N1MM sends its spots (Broadcast Data > Spots)")
        else:     # the usual gap between its spots (smoothed)
            g = now - self._n1mm_spot_last
            old = getattr(self, "_n1mm_gap", None)
            self._n1mm_gap = g if old is None else 0.8 * old + 0.2 * g
        self._n1mm_spot_last = now
        self._n1mm_spots += 1
        if pkt.get("action") in ("", "add") and hasattr(self, "_self_spots"):
            self._note_self_spot(pkt.get("call", ""), pkt.get("spotter", ""),
                                 pkt.get("freq_khz") or 0.0, pkt.get("comment", ""))
        self._n1mm_streak = getattr(self, "_n1mm_streak", 0) + 1
        if not self._ssb_via_n1mm or pkt.get("action") not in ("", "add"):
            return
        spot = spot_from_n1mm(pkt, now)
        if spot.mode != "SSB":
            return                       # CW: our RBN feed has every skimmer
        if self._n1mm_filter is None:
            self._n1mm_filter = ContinentFilter(lambda call: self._continent(call),
                                                lambda: self.home_cont, self.cfg.ssb_spotters)
        try:
            self._on_raw_spot(spot)
        except Exception:  # noqa: BLE001
            pass
        if not self._n1mm_filter.accept(spot.spotter):
            return
        try:
            self.spot_queue.put_nowait(spot)
        except queue.Full:
            pass

    def _n1mm_spots_ok(self) -> bool:
        """N1MM sends spots (from its first one) and hasn't gone quiet."""
        if getattr(self.cfg, "ssb_use_n1mm", "auto") == "off":
            return False
        first = getattr(self, "_n1mm_spot_first", None)
        now = time.time()
        if first is None or now - getattr(self, "_n1mm_spot_last", 0.0) > self._n1mm_quiet_limit():
            return False
        # once switched, stay until it goes quiet; before, wait for a steady
        # stream - and after N1MM went quiet, keep our own cluster a while
        if getattr(self, "_ssb_via_n1mm", False):
            return True
        if now - getattr(self, "_ssb_back_at", 0.0) < self.N1MM_SPOT_HOLD_S:
            return False
        return (now - first >= self.N1MM_SPOT_START_S
                and getattr(self, "_n1mm_streak", 0) >= self.N1MM_SPOT_START_N)

    def _ensure_ssb_client(self) -> None:
        want = self._ssb_wanted()
        via = want and self._n1mm_spots_ok()
        if via != getattr(self, "_ssb_via_n1mm", False):
            if not via and want:
                self._ssb_back_at = time.time()    # N1MM quiet: own cluster for a while
            self._ssb_via_n1mm = via
            log.info("SSB spots %s", "from N1MM (own cluster connection closed)" if via
                     else (f"from our own cluster (no spot from N1MM for "
                           f"{time.time() - getattr(self, '_n1mm_spot_last', 0.0):.0f} s)")
                     if want else "off")
        client = getattr(self, "_ssb_client", None)
        if client is not None and (not want or via):
            client.stop()
            self._ssb_client = None
        if via:
            self.ssb_status = "via N1MM"
            return
        if not want:
            self.ssb_status = "off"
            return
        if client is None and want:
            self._ssb_client = RBNClient(
                self.cfg.ssb_host, self.cfg.ssb_port,
                self.cfg.ssb_login or self.cfg.rbn_callsign,
                ContinentFilter(lambda call: self._continent(call), lambda: self.home_cont,
                                self.cfg.ssb_spotters),
                self.spot_queue,
                on_status=self._ssb_status_put,
                history=self.cfg.ssb_history,
                backup_commands=self.cfg.ssb_commands,
                kind="cluster", modes=("SSB",), name="ssb",
                on_raw=self._on_raw_spot,
            )
            # cluster commands are sent like the backup's (after login)
            self._ssb_client.backup_commands = tuple(self.cfg.ssb_commands)
            self._ssb_client.start()
            log.info("SSB cluster client started on %s:%s", self.cfg.ssb_host, self.cfg.ssb_port)

    def _ssb_status_put(self, text: str) -> None:
        self._ssb_status_q.put(text)

    def _rbn_healthy(self) -> bool:
        """The telnet RBN feed (or its telnet backup) is logged in and lines
        are coming (any skimmer, so even a quiet band counts)."""
        client = self._rbn_clients.get(self.cfg.rbn_port)
        if client is None:
            return False
        if not self.rbn_status.lower().startswith("logged in"):
            return False
        return time.monotonic() - getattr(client, "_last_dx", 0) < 120

    def _ensure_http_feed(self) -> None:
        """Start the HTTP backup after a minute without a working telnet
        feed; stop it after a minute of telnet working again."""
        url = getattr(self.cfg, "rbn_http_backup", "")
        if not url or not self._rbn_clients:    # off, or a team slave
            self._stop_http_feed()
            return
        now = time.time()
        if self._rbn_healthy():
            self._rbn_bad_since = None
            self._rbn_good_since = self._rbn_good_since or now
            if self._http_feed is not None and now - self._rbn_good_since > 60:
                self._stop_http_feed()
                log.info("RBN telnet works again: HTTP backup stopped")
            return
        self._rbn_good_since = None
        self._rbn_bad_since = self._rbn_bad_since or now
        if self._http_feed is None and now - self._rbn_bad_since > 60:
            self._http_feed = RBNHttpFeed(
                url, self.spotter_filter, self.spot_queue,
                on_status=lambda t: log.info("RBN HTTP: %s", t),
                modes=tuple(m for m in self.cfg.follow_modes if m.upper() in ("CW", "RTTY"))
                or ("CW",), on_raw=self._on_raw_spot)
            self._http_feed.start()
            log.info("RBN telnet down / silent: HTTP backup %s started", url)

    def _stop_http_feed(self) -> None:
        if self._http_feed is not None:
            self._http_feed.stop()
            self._http_feed = None

    RBN_PAUSE_S = 60   # every radio on SSB this long before the RBN connection closes

    def _phone_contest(self) -> bool:
        w = getattr(self, "worked", None)
        return is_phone_contest(getattr(w, "contest_name", "") or "")

    def _rbn_needed_here(self) -> bool:
        """This POUNCer needs RBN: a radio on CW (or not on SSB for
        RBN_PAUSE_S yet). Not needed: every radio on SSB / AM / FM for a
        minute, or an SSB contest is open in N1MM (at once - also before
        N1MM reports a radio, so RBN isn't even started)."""
        if not getattr(self.cfg, "rbn_pause_on_phone", False):
            self._phone_since = None
            return True
        nrs = self.state.radio_numbers()
        if nrs and not all(self._phone_radio(nr) for nr in nrs):
            self._phone_since = None
            return True                   # a radio on CW (also in an SSB contest)
        if self._phone_contest():
            return False
        if not nrs:
            self._phone_since = None
            return True
        now = time.time()
        if getattr(self, "_phone_since", None) is None:
            self._phone_since = now
        return now - self._phone_since < self.RBN_PAUSE_S

    def _rbn_paused(self) -> bool:
        """RBN closed: not needed here and by no other team POUNCer (each
        tells the team, "cw" in its hello). Any radio back on CW = at once."""
        if self._rbn_needed_here():
            return False
        if self.team is not None and self.team.peers_need_rbn():
            return False                  # another POUNCer is on CW: keep feeding it
        return True

    def _ensure_rbn_clients(self) -> None:
        self._ensure_ssb_client()
        desired = self._desired_rbn_ports()
        paused = self._rbn_paused()
        if paused != getattr(self, "_rbn_pause_on", False):
            self._rbn_pause_on = paused
            log.info("RBN %s", ("paused: SSB contest / every radio on SSB" +
                                (", in the whole team" if self.team is not None
                                 and self.team.alive_peers() else ""))
                     if paused else "on: a radio is on CW")
            self.rbn_status = "paused: SSB, not needed" if paused else "starting"
        if paused:
            desired = set()
        if self.team is not None:
            # Team: only the master talks to RBN; give discovery 3 s at start
            # so a later POUNCer doesn't log in just to hand over at once.
            if time.time() - self.team.started < 3 or self.team.role() == "slave":
                desired = set()
        for port in [p for p in self._rbn_clients if p not in desired]:
            self._rbn_clients.pop(port).stop()
        self._ensure_http_feed()
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
                history=self.cfg.rbn_history,
                # the backup server passes on the CW feed only
                backup=((self.cfg.rbn_backup_host, self.cfg.rbn_backup_port)
                        if port == self.cfg.rbn_port and self.cfg.rbn_backup_host else None),
                backup_commands=self.cfg.rbn_backup_commands,
                on_raw=self._on_raw_spot,
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

    def _wait_for_qsy(self, nr: int, khz: float) -> bool:
        """Wait until N1MM reports radio ``nr`` on ``khz`` (it may wipe the
        callsign when the radio moves - typing before that lost the call),
        then a little more. False if it never came (we type anyway)."""
        wait = getattr(self.cfg, "qsy_wait_ms", 0) / 1000
        settle = getattr(self.cfg, "qsy_settle_ms", 0) / 1000
        end = time.time() + wait
        while True:
            snap = self.state.snapshot(nr)
            f = snap.get("freq_khz") if snap else None
            if f is not None and abs(f - khz) <= 0.15:
                if settle:
                    time.sleep(settle)
                return True
            if time.time() >= end:
                return wait == 0
            time.sleep(0.05)

    def _settext(self) -> bool:
        return getattr(self.cfg, "type_mode", "settext") == "settext"

    def _enter_text(self, hwnd, text: str) -> bool:
        """Put text into the focused N1MM box: in one go (settext - nothing
        can get mixed up), or cleared + typed key by key. True if the box
        was set / cleared."""
        try:   # the log tells which window got the call (call shown twice?)
            fg = win32mod.foreground_window()
        except Exception:  # noqa: BLE001
            fg = 0
        if self._settext() and hwnd and set_focused_edit_text(hwnd, text):
            log.info("call %s -> set in Entry Window %s (front window %s)", text, hwnd, fg)
            return True
        cleared = clear_focused_edit(hwnd) if hwnd else False
        type_text(text)
        log.info("call %s -> typed as keys, Entry Window %s cleared=%s (front window %s)",
                 text, hwnd, cleared, fg)
        return cleared

    def _check_call(self, hwnd, call: str) -> str:
        """The call really in the callsign box? If not (e.g. N1MM's wipe
        came late), put it in again - up to 3 times. Only with settext: with
        typed keys a slow PC may still be typing, and typing again would mix
        the two ("PHB9CBR/P")."""
        if not hwnd or not self._settext():
            return ""
        want = call.strip().upper()
        for attempt in range(3):
            time.sleep(0.15)
            got = focused_edit_text(hwnd)
            if got is None or got.strip().upper() == want:
                return " [call put in again]" if attempt else ""
            set_focused_edit_text(hwnd, call)
        return " [call not confirmed in N1MM]"

    def _guard_call(self, hwnd, call: str, nr: int, khz: float) -> bool:
        """Radio B when N1MM hadn't reported the new frequency yet: keep the
        call in the box a little longer - if N1MM's QSY (and its wipe of the
        callsign) comes late, put the call in again. True = the report came."""
        if not hwnd or not self._settext():
            return False
        settle = getattr(self.cfg, "qsy_settle_ms", 0) / 1000
        want = call.strip().upper()
        end = time.time() + GUARD_CALL_S
        landed_at = None
        while time.time() < end:
            snap = self.state.snapshot(nr)
            f = snap.get("freq_khz") if snap else None
            if landed_at is None and f is not None and abs(f - khz) <= 0.15:
                landed_at = time.time()
                end = min(end, landed_at + settle + 0.2)
            got = focused_edit_text(hwnd)
            if got is not None and got.strip().upper() != want:
                set_focused_edit_text(hwnd, call)
            time.sleep(0.05)
        return landed_at is not None

    def _restore_call(self, hwnd, call: str) -> bool:
        """After a late N1MM QSY: the callsign box empty (N1MM wiped it)?
        Put the call in again. Anything else in the box is left alone."""
        if not hwnd or not self._settext():
            return False
        try:
            got = focused_edit_text(hwnd)
        except Exception:  # noqa: BLE001 - window gone
            return False
        if got is None or got.strip():
            return False
        set_focused_edit_text(hwnd, call)
        log.info("tap %s: call put in again (N1MM wiped it with its late QSY)", call)
        return True

    def _snapshot_focus(self):
        """SO2R: where the cursor is before we go to the other radio's window."""
        if not getattr(self.cfg, "return_focus", False) or not self._so2r():
            return None
        try:
            return focus_snapshot()
        except Exception:   # never let this stop a QSY
            return None

    def _after_radio(self, nr: int, snap, target_hwnd) -> str:
        """Where the cursor goes after a tap / tune: radio B with "focus
        back to A" on -> Entry Window A, always; else back where it was."""
        if nr == 2 and self._so2r() and getattr(self.cfg, "focus_back_to_a", False):
            return self._focus_a(target_hwnd)
        return self._return_focus(snap, target_hwnd)

    def _focus_a(self, b_hwnd) -> str:
        """SO2R / SO2V: after radio B, bring Entry Window A to the front -
        the keyboard is on radio A again (N1MM's focus = A)."""
        a_hwnd = self.state.entry_window_hwnd(1)
        if not a_hwnd or a_hwnd == b_hwnd:
            return ""
        time.sleep(0.1)
        try:
            ok = force_foreground(a_hwnd)
        except Exception:   # never let this stop a QSY
            ok = False
        return " [focus back on A]" if ok else " [focus back on A failed]"

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

        t0 = time.time()
        self._late_qsy = None             # a new tap: stop watching the last one
        # the clicked station keeps its row in the window list until it is
        # logged (leaves the list) or PIN_S passed
        key = (spot.dxcall.upper(), band_from_khz(spot.freq_khz))
        idx = next((i for i, x in enumerate(getattr(self, "displayed", []) or [])
                    if (x.dxcall.upper(), band_from_khz(x.freq_khz)) == key), 0)
        self._pin = (key, idx, time.time())
        if not self._qsy(nr, spot.freq_khz, hwnd, focused):
            _note_tap(self, spot, nr, False, self.status_label.cget("text") if hasattr(
                self.status_label, "cget") else "QSY failed")
            return
        self.taps.record(spot.dxcall, spot.freq_khz)   # for the contest summary
        # wait until N1MM reports the radio on the new frequency (it wipes
        # the callsign when the radio moves), radio B too
        t_wait = time.time()
        landed = self._wait_for_qsy(nr, spot.freq_khz)
        msg_retry = ""
        if not landed and not self._types_qsy(nr) and self.cfg.qsy_wait_ms:
            # N1MM didn't report the new frequency: send the QSY once more and
            # wait again (only the UDP command - typing the frequency again
            # would go into the callsign box)
            log.info("tap %s: no report of %.1f after %.1f s - QSY sent again", spot.dxcall,
                     spot.freq_khz, time.time() - t_wait)
            if self._qsy(nr, spot.freq_khz, hwnd, focused):
                landed = self._wait_for_qsy(nr, spot.freq_khz)
                if landed:
                    msg_retry = " [QSY sent again]"
        reported = f"N1MM reported it after {time.time() - t_wait:.1f} s" if landed else \
            f"N1MM didn't report it within {self.cfg.qsy_wait_ms / 1000:.1f} s"

        msg = f"QSY {self._radio_tag(nr)}{spot.freq_khz:.1f} kHz -> {spot.dxcall}"
        msg += msg_retry
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
                    if not self._settext():
                        wipe_callsign()   # Alt+W key: only when typing keys
                    time.sleep(0.2)  # let N1MM finish the wipe before we type
                how = self._enter_text(hwnd, spot.dxcall)
                msg += " (call typed, no TX)" + ("" if how else " [box not cleared]")
                msg += self._check_call(hwnd, spot.dxcall)
                if not landed:
                    # N1MM's QSY (and its wipe of the callsign) may still come:
                    # keep the call in the box a little longer - every radio
                    landed = self._guard_call(hwnd, spot.dxcall, nr, spot.freq_khz)
                if not landed:
                    msg += " [N1MM didn't report the new frequency]"
            else:
                msg += " (call skipped: N1MM not focused)"
        if self.cfg.focus_n1mm:
            msg += " [N1MM focused]" if focused else " [N1MM focus failed]"
        msg += self._after_radio(nr, snap, hwnd)
        msg += f" ({time.time() - t0:.1f} s; {reported})"
        self.status_label.configure(text=msg)
        log.info("tap %s: %s", spot.dxcall, msg)
        if landed:
            short = f"radio {self._radio_tag(nr)}on {spot.freq_khz:.1f}"
        else:
            # not yet: N1MM is often just slow to report - watched a while
            # longer in _tick (_check_late_qsy), the phone then gets the result
            short = f"waiting for N1MM to report {spot.freq_khz:.1f}\u2026"
            self._late_qsy = {"spot": spot, "nr": nr, "t0": t_wait, "typed": "call typed" in msg,
                              "hwnd": hwnd}
        if "call typed" in msg:
            short += " \u00b7 call in"
        elif "call skipped" in msg:
            short += " \u00b7 call NOT typed (N1MM not focused)"
        _note_tap(self, spot, nr, bool(landed), short, msg)
        if not landed:
            self._last_tap["wait"] = True          # the phone keeps listening

    LATE_QSY_S = 8.0     # after the wait: keep watching for N1MM's report this long

    def _check_late_qsy(self) -> None:
        """A tap whose new frequency N1MM hadn't reported in time: did the
        report come after all? Then say so (the phone turns its message into
        ✓); after LATE_QSY_S the warning stands."""
        w = getattr(self, "_late_qsy", None)
        if not w:
            return
        spot, nr = w["spot"], w["nr"]
        snap = self.state.snapshot(nr)
        f = snap.get("freq_khz") if snap else None
        took = time.time() - w["t0"]
        tail = " \u00b7 call in" if w["typed"] else ""
        if f is not None and abs(f - spot.freq_khz) <= 0.15:
            self._late_qsy = None
            log.info("tap %s: N1MM reported %.1f late, after %.1f s", spot.dxcall,
                     spot.freq_khz, took)
            if w["typed"] and w.get("hwnd"):
                # N1MM wipes the callsign when its (late) QSY lands: put the
                # call back - only into an empty box, never over your typing
                for ms in (300, 800, 1500):
                    self.root.after(ms, lambda h=w["hwnd"], c=spot.dxcall: self._restore_call(h, c))
            _note_tap(self, spot, nr, True, f"radio {self._radio_tag(nr)}on {spot.freq_khz:.1f} "
                      f"(N1MM reported it late, {took:.1f} s){tail}")
        elif took > self.cfg.qsy_wait_ms / 1000 + self.LATE_QSY_S:
            self._late_qsy = None
            log.info("tap %s: N1MM never reported %.1f (radio %s at %s)", spot.dxcall,
                     spot.freq_khz, self._radio_tag(nr).strip() or "A", f)
            _note_tap(self, spot, nr, False, f"N1MM never reported {spot.freq_khz:.1f} - "
                      f"check the radio{tail}")

    def _tick(self) -> None:
        """The refresh tick, timed for the perf log (a slow tick = a slow window)."""
        t0 = time.perf_counter()
        try:
            self._tick_body()
        finally:
            ms = (time.perf_counter() - t0) * 1000
            p = getattr(self, "_perf", None)
            if p is not None:
                p["ticks"] += 1
                p["tick_ms"] += ms
                p["tick_max"] = max(p["tick_max"], ms)

    def _perf_stats(self) -> dict:
        """POUNCer's own numbers for perf.csv - rates since the last row."""
        p = self._perf
        now = time.monotonic()
        mins = max(1e-6, (now - p["t"]) / 60)
        req, phones = self.web.load() if self.web is not None else (0, 0)
        out = {"spots": len(self.spots), "spots_in_per_min": round(p["spots_in"] / mins),
               "rbn_per_min": round(p["raw"] / mins), "queue": self.spot_queue.qsize(),
               "ticks_per_min": round(p["ticks"] / mins),
               "tick_avg_ms": round(p["tick_ms"] / p["ticks"], 1) if p["ticks"] else "",
               "tick_max_ms": round(p["tick_max"], 1), "rebuilds_per_min": round(p["rebuilds"] / mins),
               "web_req_per_min": round(req / mins), "phones": phones,
               "cty_cache": len(self._cty_cache), "taps": len(getattr(self.taps, "_taps", []))}
        p.update(spots_in=0, raw=0, ticks=0, tick_ms=0.0, tick_max=0.0, rebuilds=0, t=now)
        return out

    def _tick_body(self) -> None:
        self.updater.maybe_check()
        self._drain_status()
        if hasattr(self, "_self_spot_q"):
            self._drain_self_spots()
        self._check_late_qsy()
        self._drain_spots()
        self._drain_wpm()
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
        self._maybe_rate_line()
        if self._dirty:
            self._rebuild()
            self._dirty = False
            self._perf["rebuilds"] += 1
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
        self._last_weak = 0
        real_band = self._current_band(radio)
        if view_band == ALL_BANDS:
            # all bands (hunting mults): new mults first so none is cut off
            spots = sorted(
                self._visible_spots(radio, view_band),
                key=lambda s: (self._mults(s), s.snr if s.snr is not None else -999),
                reverse=True,
            )[:WEB_MAX_SPOTS_ALL]
        else:
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
            # N1MM's CW speed per radio (read from its Entry Window), if known
            "wpm": {str(nr): w for nr, w in (self.cwspeed.wpm.items() if self.cwspeed else ())},
            "wpm_popup": bool(self.cfg.cw_speed_popup),
            # minimum SNR of the list and how many weaker spots it hides
            "min_snr": self.cfg.min_snr,
            # band open? {band: {state open/local/closed, dx, calls, spots}}
            "bandopen": ({b: dict(v, bg=self._band_chip_color(v)[0], fg=self._band_chip_color(v)[1])
                          for b, v in self._band_info().items()} if self.cfg.band_lights else {}),
            # pre-contest check (the phone warns when it isn't all fine)
            "checks": {"state": checksmod.worst(self._checks) if self._checks else "ok",
                       "items": [c for c in self._checks if c["state"] in ("warn", "bad")]},
            "snr_hidden": getattr(self, "_last_weak", 0),
            # phone CW keyboard (hold ENTER): allowed, open now, last problem
            "cw_keyboard": bool(self.cfg.web_cw_keyboard),
            "cw_open": self._cw_win is not None,
            "cw_error": self._cw_error,
            "cw_error_n": self._cw_error_n,
            "rbands": {str(nr): self._current_band(nr) for nr in self.state.radio_numbers()},
            # the last tap's real result (radio moved? call typed?) for the toast
            "tap": {k: v for k, v in (getattr(self, "_last_tap", None) or {}).items() if k != "full"},
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
            "modes": list(self._effective_modes(radio)),
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
            "fkeys": [{"key": k, "label": v,
                       "half": k.upper() in getattr(self.cfg, "fkeys_half", ())}
                      for k, v in self.cfg.fkeys],
            "spots": [
                {
                    "call": s.dxcall, "freq": s.freq_khz, "mode": s.mode,
                    "snr": s.snr, "wpm": s.wpm, "time": s.time_utc,
                    "cont": self._continent(s.dxcall),
                    "country": self._country(s.dxcall),
                    "dx": self._is_dx(s.dxcall),
                    "mult": self._mults(s),
                    "busted": bool(self.scp is not None and self.scp.busted(s.dxcall)),
                    **self._tried_fields(s),
                    **self._run_fields(s),
                    "cq_min": self._cq_minutes(s),
                    "first": self._first_heard_utc(s),
                    "age": int(time.time() - s.received),
                }
                for s in spots
            ],
        }

    def _explain_spot(self, call: str, freq: float) -> dict:
        """Phone debug (hold a tile > Debug): everything POUNCer knows about
        this station on this band and why the tile looks as it does - the
        facts behind the colours and numbers, to check they are right."""
        call = (call or "").strip().upper()
        band = band_from_khz(freq)
        same = [s for s in list(self.spots.values())
                if s.dxcall.upper() == call and band_from_khz(s.freq_khz) == band]
        if not same:
            return {"ok": False, "error": f"{call} is no longer in the spot list"}
        now = time.time()
        s = max(same, key=lambda x: x.received)
        ent = self._entity(s.dxcall)
        zone = (ent.itu if self.worked.zone_is_itu else ent.cq) if ent else None
        run = self._run_fields(s)
        tried = self._tried(s)
        radio_khz = self._radio_khz()
        out = {
            "ok": True, "call": s.dxcall, "band": band, "freq": s.freq_khz, "mode": s.mode,
            "type": s.spot_type, "snr": s.snr, "wpm": s.wpm, "spotter": s.spotter,
            "time": s.time_utc, "age_s": int(now - s.received),
            "fade_s": self.cfg.fade_minutes * 60, "max_age_s": self.cfg.max_age_minutes * 60,
            "first": self._first_heard_utc(s), "cq_min": self._cq_minutes(s),
            "entity": ({"country": ent.name, "prefix": ent.prefix, "cont": ent.continent,
                        "cq": ent.cq, "itu": ent.itu} if ent else None),
            "home_cont": self.home_cont, "dx": self._is_dx(s.dxcall),
            "wpx": wpx_prefix(s.dxcall),
            "contest": self.worked.contest_name, "log_open": self.worked.log_open,
            "worked": self.worked.is_worked(s.dxcall, s.freq_khz, s.mode),
            "zone_kind": "ITU" if self.worked.zone_is_itu else "CQ",
            "mults": self.worked.mult_detail(s.freq_khz, ent.prefix if ent else None, zone,
                                             wpx_prefix(s.dxcall), call=s.dxcall,
                                             continent=ent.continent if ent else None,
                                             mode=s.mode),
            "mult_rules": self.worked.mult_summary(),
            "mult": self._mults(s),
            "run": run, "rare_run": RARE_RUN_PCT, "rare_hours": RARE_HOURS,
            "scp": None if self.scp is None else not self.scp.busted(s.dxcall),
            "tried": {"times": tried[0], "minutes": tried[1]} if tried else None,
            "tried_window_min": self.cfg.tried_minutes,
            "radio_khz": radio_khz,
            "min_snr": self.cfg.min_snr,
            # the exact spot, every field as received (newest on this band)
            "raw": dict(dataclasses.asdict(s), received_utc=time.strftime(
                "%Y-%m-%d %H:%M:%SZ", time.gmtime(s.received))),
            "skimmers": sorted(
                ({"spotter": x.spotter, "freq": x.freq_khz, "snr": x.snr,
                  "age_s": int(now - x.received)} for x in same),
                key=lambda d: d["age_s"])[:12],
        }
        return out

    def _web_spots(self, radio: int | None = None, band: str | None = None) -> dict:
        """Phone list: the radio's band, or ``band`` from the band menu (the
        radio isn't moved - tapping a station there QSYs it)."""
        cache = self._web_cache
        base = cache.get(radio or self.radio) or cache.get(self.radio) or cache.get(1) or {}
        if not band or (band not in CONTEST_BANDS and band != ALL_BANDS) \
                or band == base.get("band"):
            return base
        key = (base.get("radio") or radio or self.radio, band)
        self._web_views[key] = time.time()        # the next tick builds it
        got = self._web_view_cache.get(key)
        return got if got is not None else {**base, "pending": band}

    def _web_qsy(self, call: str, freq: float, mode: str, radio: int | None = None) -> None:
        self._web_queue.put(("qsy", call, float(freq), mode, radio))

    def _web_key(self, key: str, radio: int | None = None) -> None:
        self._web_queue.put(("key", key, radio))

    def _web_cw(self, op: str, text: str = "", n: int = 0, radio: int | None = None) -> None:
        """Phone CW keyboard: open / text / back / done / stop (in order)."""
        self._web_queue.put(("cw", op, text, n, radio))

    # ---- CW keyboard (phone: hold ENTER) -> N1MM's Ctrl+K window ------------

    def _cw(self, op: str, text: str = "", n: int = 0, radio: int | None = None) -> None:
        nr = self._radio_nr(radio)
        if op == "open":
            self._cw_open(nr)
        elif op == "text":
            if text and self._cw_ready(nr):
                type_text(text.upper())
        elif op == "back":
            if n > 0 and self._cw_ready(nr):
                win32mod.send_backspace(n)
        elif op in ("done", "stop"):
            self._cw_close(stop=(op == "stop"))

    def _cw_open(self, nr: int) -> bool:
        """Ctrl+K in the radio's Entry Window: N1MM opens its CW window and
        sends what is typed there. Only types once that window has the
        keyboard - never into the callsign box."""
        win = self._cw_win
        if win and win32mod.is_window(win) and self._cw_radio == nr:
            force_foreground(win)
            return True
        if win:
            self._cw_close(stop=False)
        hwnd = self.state.entry_window_hwnd(nr)
        if not hwnd or not force_foreground(hwnd):
            return self._cw_fail("N1MM's Entry Window could not be brought to the front")
        time.sleep(0.15)
        win32mod.send_ctrl_k()
        fg = 0
        for _ in range(10):              # wait for N1MM's CW window (max 0.5 s)
            time.sleep(0.05)
            fg = win32mod.foreground_window()
            if fg and fg != hwnd:
                break
        if not fg or fg == hwnd or window_pid(fg) != window_pid(hwnd):
            return self._cw_fail("N1MM's CW window (Ctrl+K) did not open")
        self._cw_win, self._cw_radio, self._cw_error = fg, nr, ""
        self.status_label.configure(
            text=f"CW keyboard open{' (radio ' + 'AB'[nr - 1] + ')' if self._so2r() else ''}"
                 " - typing from the phone is sent as CW")
        if not self._cw_pumping:
            self._cw_pumping = True
            self.root.after(CW_PUMP_MS, self._cw_pump)
        return True

    def _cw_ready(self, nr: int) -> bool:
        win = self._cw_win
        if not win or self._cw_radio != nr or not win32mod.is_window(win):
            self._cw_win = None
            return self._cw_open(nr)
        if win32mod.foreground_window() != win:
            if not force_foreground(win):
                return self._cw_fail("N1MM's CW window could not be brought to the front")
            time.sleep(0.05)
        return True

    def _cw_close(self, stop: bool) -> None:
        """Done: Enter (N1MM sends what's left, closes its CW window).
        Stop: Esc (stops at once)."""
        win, self._cw_win = self._cw_win, None
        if not win or not win32mod.is_window(win):
            if stop:                     # stop anyway: Esc in N1MM stops sending
                hwnd = self.state.entry_window_hwnd(self._cw_radio or self._radio_nr())
                if hwnd and force_foreground(hwnd):
                    win32mod.send_escape()
            return
        if win32mod.foreground_window() != win:
            force_foreground(win)
            time.sleep(0.05)
        if stop:
            win32mod.send_escape()
        else:
            send_enter()
        self.status_label.configure(
            text="CW keyboard: stopped (Esc)" if stop else "CW keyboard closed (rest is sent)")

    def _cw_fail(self, why: str) -> bool:
        self._cw_win = None
        self._cw_error = why
        self._cw_error_n += 1
        self.status_label.configure(text=f"CW keyboard: {why}")
        return False

    def _cw_pump(self) -> None:
        """While the CW keyboard is open: handle the phone's typing every
        80 ms, not only every half second."""
        if self._cw_win is None:
            self._cw_pumping = False
            return
        self._drain_web()
        self.root.after(CW_PUMP_MS, self._cw_pump)

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
            if item[0] == "status":
                self.status_label.configure(text=item[1])
                continue
            if item[0] == "minsnr":
                self._set_min_snr(item[1])
                continue
            if item[0] == "cw":
                self._cw(*item[1:])
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
            if item[0] == "restart":       # settings page: start POUNCer again
                self._restart_now()
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
            log.info("phone tap %s %.1f %s radio %s", call, freq, mode, radio or "-")
            spot = Spot(dxcall=call, freq_khz=freq, mode=mode or "CW",
                        snr=None, wpm=None, spot_type="", spotter="",
                        time_utc="", received=time.time())
            self._act_on_spot(spot, radio)

    @staticmethod
    def _key_text(key: str, label: str, sep: str) -> str:
        """Button text: key name (F2 / ENTER) plus its label, e.g. "F2 RPRT"."""
        if key == "SpotIt":
            return label or "SPOTit"           # N1MM Spot It (Alt+P)
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
        self._update_compact_bar()
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
        if key in ("PgUp", "PgDn") and self.cwspeed is not None:
            self.cwspeed.expect(self._radio_nr(radio), 1 if key == "PgUp" else -1)
        ok_key = self._send_key(key)
        if not ok_key:
            self.status_label.configure(text=f"Unknown key: {key}")
            return
        msg = f"Sent {key} to N1MM"
        if key == "PgUp":
            msg = "CW speed up (PgUp)"
        elif key == "PgDn":
            msg = "CW speed down (PgDn)"
        elif key == "SpotIt":
            msg = "Spot It (Alt+P) sent to N1MM - it spots the Entry Window call to the cluster"
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
        if key == "SpotIt":
            return send_alt_p()
        return False

    def _drain_status(self) -> None:
        while True:
            try:
                status = self.status_queue.get_nowait()
            except queue.Empty:
                break
            self.rbn_status = status
        while True:
            try:
                status = self._ssb_status_q.get_nowait()
            except queue.Empty:
                break
            if not getattr(self, "_ssb_via_n1mm", False):   # a stopped client's last words
                self.ssb_status = status

    def _drain_spots(self) -> None:
        got = 0
        while got < 500:
            try:
                spot = self.spot_queue.get_nowait()
            except queue.Empty:
                break
            if not spot.time_utc:     # N1MM / some cluster spots carry no time
                spot.time_utc = time.strftime("%H%MZ", time.gmtime(spot.received))
            self._note_own(spot)
            self._check_overlap(spot)
            self.spots[spot.key] = spot
            self._track_on_freq(spot)
            got += 1
            self._perf["spots_in"] += 1
            self._last_own_spot = time.time()
            if self.team is not None and self.team.is_master():
                self.team.send_spot(self._spot_dict(spot))
        # spots passed on by the team master (slave): use, don't pass on
        while got < 1000:
            try:
                spot = self._team_queue.get_nowait()
            except queue.Empty:
                break
            if not spot.time_utc:
                spot.time_utc = time.strftime("%H%MZ", time.gmtime(spot.received))
            self._note_own(spot)
            self._check_overlap(spot)
            self.spots[spot.key] = spot
            self._track_on_freq(spot)
            got += 1
        self._relabel_overlap()
        if got:
            self._dirty = True

    def _check_overlap(self, spot: Spot) -> None:
        """An SSB spot in 7074 - 7077 or 1840 - 1843: ask PSK Reporter -
        not in an SSB contest (there everything in the segment is SSB)."""
        if (self.pskr is None or spot.mode != "SSB" or not digital_overlap(spot.freq_khz)
                or self._phone_contest()):
            return
        # PSK Reporter may already know (a re-spot): use that at once
        m = self.pskr.digital(spot.dxcall, spot.freq_khz)
        if m:
            spot.mode = m
            return
        self.pskr.ask(spot.dxcall, spot.freq_khz)
        self._overlap_keys.add(spot.key)

    def _relabel_overlap(self) -> None:
        """PSK Reporter has him on FT8 / FT4 there: the spot is FT8 / FT4."""
        if not self._overlap_keys or self.pskr is None:
            return
        for key in list(self._overlap_keys):
            s = self.spots.get(key)
            if s is None or s.mode != "SSB":
                self._overlap_keys.discard(key)
                continue
            m = self.pskr.digital(s.dxcall, s.freq_khz)
            if m:
                s.mode = m
                self._overlap_keys.discard(key)
                self._dirty = True
                log.info("PSK Reporter: %s %.1f is on %s, not SSB", s.dxcall, s.freq_khz, m)
            elif time.time() - s.received > self.pskr.watch_s + 30:
                self._overlap_keys.discard(key)      # no FT8 report: SSB it is

    OWN_HEARD_S = 600   # ★ = our own skimmer heard him within this time

    def _note_own(self, spot: Spot) -> None:
        own = self.spotter_filter.primary
        if own and (spot.spotter or "").upper().startswith(own):
            d = self._own_heard
            if len(d) > 5000:
                cut = time.time() - self.OWN_HEARD_S
                for k in [k for k, v in d.items() if v[0] < cut]:
                    del d[k]
            d[(spot.dxcall.upper(), band_from_khz(spot.freq_khz))] = (
                time.time(), spot.spotter.replace("-#", ""))

    def _own_mark(self, s: Spot) -> str:
        """'TK0C-1' when our own skimmer heard this station lately - only when
        other skimmers are listed too (or the backup is on): with S53WW
        alone every spot is its own."""
        f = self.spotter_filter
        if not (getattr(f, "multi", False) or f.backup_active()):
            return ""
        got = getattr(self, "_own_heard", {}).get((s.dxcall.upper(), band_from_khz(s.freq_khz)))
        if not got or time.time() - got[0] > self.OWN_HEARD_S:
            return ""
        return got[1]

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
        self._last_team_spot = time.time()
        sw = getattr(self, "skimwatch", None)
        if sw is not None:
            sw.seen(spot.spotter)
        self._team_queue.put(spot)

    def _rbn_ok(self) -> bool:
        """Our own RBN connection works (logged in or spots lately) - or is
        closed on purpose (paused: the master stays master)."""
        return (getattr(self, "_rbn_pause_on", False)
                or self.rbn_status.startswith("logged in")
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
                get_bands=self.bandact.summary,
                needs_rbn=self._rbn_needed_here,
                rbn_paused=lambda: bool(getattr(self, "_rbn_pause_on", False)),
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

    def _update_top_bar(self, snap) -> None:
        """Big frequency + band / mode / CW speed (the chosen radio)."""
        if snap and snap.get("freq_khz"):
            big = f"{snap['freq_khz']:.1f}"
            parts = ["kHz", snap.get("band") or "?", snap.get("mode") or ""]
            wpm = self._wpm(self._radio_nr()) if getattr(self, "cwspeed", None) else None
            if wpm:
                parts.append(f"{wpm} WPM")
            if self._so2r():
                parts.append(f"radio {'AB'[self._radio_nr() - 1]}")
                other = 2 if self._radio_nr() == 1 else 1
                okhz = self._radio_khz(other)
                if okhz:
                    parts.append(f"{'AB'[other - 1]} {okhz:.1f}")
            sub = "   ".join(p for p in parts if p)
        else:
            big, sub = "----.-", "no radio info from N1MM"
        if getattr(self, "_view_band", None):
            sub += f"      \u25b6 list: {self._view_band} (click it again: back)"
        if self.freq_big.cget("text") != big:
            self.freq_big.configure(text=big)
        if self.freq_sub.cget("text") != sub:
            self.freq_sub.configure(text=sub)

    def _toggle_check_details(self) -> None:
        self._check_details = not self._check_details
        self._update_check_line()

    def _update_team_label(self) -> None:
        """Team badge in the top bar + window title: MASTER / SLAVE / alone."""
        role = self.team.role() if self.team is not None else "off"
        n = len(self.team.alive_peers()) + 1 if self.team is not None else 1
        text, bg = {
            "master": (f"MASTER \u00b7 {n} POUNCers", "#2e7d32"),
            "slave": (f"SLAVE of {self.team.master_ip() if self.team else ''} \u00b7 {n}", "#1565c0"),
            "solo": ("TEAM: ALONE", "#546e7a"),
        }.get(role, ("TEAM OFF", "#455a64"))
        note = self._version_note()
        if note and role in ("master", "slave"):
            text, bg = f"{text} \u00b7 VERSIONS DIFFER", "#ef6c00"
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
        return f"{f.label()} quiet - using {which}"

    def _version_note(self) -> str:
        """'' when the whole team runs this version; else who differs."""
        if self.team is None:
            return ""
        diff = self.team.version_mismatch()
        if not diff:
            return ""
        who = ", ".join(f"{ip} runs {ver}" for ip, ver in diff)
        return f"VERSIONS DIFFER: this one is {__version__}, {who} - update all to the same"

    @staticmethod
    def _bold_warm(text: str) -> str:
        """'CW 46044 + SSB 60917 stations known ...' -> both counts bold."""
        import re
        out = re.sub(r"\b(CW|SSB|RTTY|PH) (\d[\d,.]*)", r"\1 **\2**", text)
        return out if out != text else re.sub(r"(\d[\d,.]*)", r"**\1**", text, count=1)

    @staticmethod
    def _bold_count(text: str) -> str:
        """'SCP: 51234 calls (...)' -> the number bold."""
        import re
        return re.sub(r"(\d[\d,.]*)", r"**\1**", text, count=1)

    @staticmethod
    def _bold_team(text: str) -> str:
        """Window only: role, IP and count bold ('team: slave of **10.0.0.5** ...')."""
        import re
        text = re.sub(r"\b(master|slave|alone)\b", r"**\1**", text, count=1)
        text = re.sub(r"(\d+\.\d+\.\d+\.\d+)", r"**\1**", text)
        return re.sub(r"\((\d+) POUNCers\)", r"(**\1** POUNCers)", text)

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
            call=s.dxcall, continent=ent.continent if ent else None, mode=s.mode,
        )

    def _run_fields(self, s: Spot) -> dict:
        """From the call's past logs (same contest + mode): % of QSOs made
        running - on this spot's band when known ("run_band") - hours on the
        air, and whether the log was reconstructed."""
        info = None
        if self.runhist is not None:
            info = self.runhist.get(s.dxcall, self.worked.contest_name, s.mode,
                                    band=band_from_khz(s.freq_khz))
        info = info or {}
        return {"run": info.get("run"), "hours": info.get("hours"),
                "rec": bool(info.get("rec")), "run_band": info.get("band")}

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
        return nr == 2 and self._so2r() and getattr(self.cfg, "radio_b_qsy", "udp-left") == "type"

    def _freq_text(self, khz: float) -> str:
        """kHz as N1MM reads it when typed: 14000.1 -> "14000,1" with a
        comma decimal sign (Windows setting), "14001" without decimals."""
        text = f"{khz:.3f}".rstrip("0").rstrip(".")
        sep = getattr(self.cfg, "freq_decimal", "auto")
        if sep == "auto":
            sep = decimal_separator()
        return text.replace(".", sep if sep in (".", ",") else ".")

    def _qsy(self, nr: int, khz: float, hwnd, focused: bool) -> bool:
        """QSY radio ``nr``. Radio A: N1MM's UDP command. SO2R radio B: type
        the frequency in kHz into its Entry Window and press Enter - N1MM
        then tunes the radio of that window (no transmit). False = failed
        (status line says why)."""
        if self._types_qsy(nr):
            if not (hwnd and focused):
                self.status_label.configure(
                    text="QSY B failed: Entry Window B not in front (N1MM focus failed)")
                log.warning("QSY B %.1f failed: Entry Window B not in front", khz)
                return False
            # typed key by key, as up to v0.7.23 when B worked well (N1MM
            # reacts to typed keys; only the call goes in in one go)
            clear_focused_edit(hwnd)
            type_text(self._freq_text(khz))   # e.g. 14025,3 on a Slovenian PC
            send_enter()
            time.sleep(0.3)   # let N1MM tune and clear the box before the call
            return True
        # radio B by UDP: "udp-left" = radio 2 with mouse button Left (as
        # SP_Helper does; "Right" moved VFO A here), "udp" = Right (N1MM docs)
        button = ("Left" if nr == 2 and self._so2r()
                  and getattr(self.cfg, "radio_b_qsy", "udp-left") == "udp-left" else None)
        try:
            self.controller.set_frequency(khz, mousebutton=button, radio_nr=nr)
        except OSError as exc:
            self.status_label.configure(text=f"N1MM QSY failed: {exc}")
            log.warning("QSY %.1f radio %s failed: %s", khz, nr, exc)
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
        note = self._after_radio(nr, snap, hwnd) if focused else ""
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

    def _update_radio_switch(self) -> None:
        """Radio A / B switch only when N1MM reports two radios (SO2R / SO2V)
        - or B is still chosen; with one radio there is nothing to switch."""
        show = self._so2r() or self.radio == 2
        flow = getattr(self, "_flow", None)
        btn = getattr(self, "radio_btn", None)
        if flow is not None and btn is not None:
            flow.set_visible(btn, show)
        cbtn = getattr(self, "_compact_btn", None)
        if cbtn is not None and show != bool(cbtn.winfo_manager()):
            if show:
                cbtn.pack(side="left", before=self._compact_info)
            else:
                cbtn.pack_forget()

    def _update_labels(self) -> None:
        self._update_radio_switch()
        self._update_compact_bar()
        # Min SNR changed elsewhere (Settings, phone): show it in the box
        box = getattr(self, "_min_snr_box", None)
        if box is not None and self.root.focus_get() is not box \
                and self.min_snr_var.get() != str(self.cfg.min_snr):
            self.min_snr_var.set(str(self.cfg.min_snr))
        snap = self.state.snapshot(self.radio)
        self._update_top_bar(snap)
        if snap and snap.get("freq_khz"):
            self.n1mm_label.configure(
                text=(
                    f"N1MM{' radio **' + 'AB'[self._radio_nr() - 1] + '**' if self._so2r() else ''}:"
                    f" **{snap['freq_khz']:.2f}** kHz"
                    f"   radio info **{snap['age']:.0f} s** ago"
                    f"{'   **' + self._wpm_text() + '**' if self._wpm_text() else ''}"
                )
            )
        else:
            self.n1mm_label.configure(
                text="N1MM: **no radio info** (enable Config > Broadcast Data > Radio to **127.0.0.1:12060**)"
            )
        band = self._current_band()
        if self.worked.log_open or self.worked.log_state == "unknown":
            src = {"N1MM": "from N1MM", "ini": "from N1MM Logger.ini",
                   "latest QSO": "newest QSO's contest"}.get(self.worked.contest_source, "")
            worked = f"Log: **{self.worked.db_name or '-'}**" + (f" ({src})" if src else "") + \
                f"   station: **{self.own_call() or '?'}**" \
                f" (**{self.home_cont}**)   worked: **{self.worked.status}**"
            if self.worked.log_open:
                per_band = " ".join(f"{b} **{n}**" for b, n in self.worked.band_qsos())
                worked += f"   QSOs: {per_band or '-'} (total **{self.worked.qso_count}**)"
                rules = self.worked.mult_summary()
                worked += f"   mults: **{rules or 'none known'}**"
        else:
            worked = f"LOG: **{self.worked.log_message}**"
        self.rbn_label.configure(
            text=(
                f"RBN: **{('paused - SSB, not needed' if getattr(self, '_rbn_pause_on', False) else self.rbn_status) if not (self.team and self.team.role() == 'slave') else 'via team master'}**"
                f"   {self._bold_team(self._team_text())}   spotter=**{self.cfg.rbn_spotter or 'ALL'}**"
                f"{'   BACKUP: **' + self._backup_text() + '**' if self._backup_text() else ''}"
                f"   mode=**{','.join(self.cfg.follow_modes)}**"
                f"   band=**{band or 'ALL'}**   spots=**{len(self.displayed)}**/{len(self.spots)}"
                f"   hidden=**{self._worked_hidden}**"
                f"{'   weak (<' + str(self.cfg.min_snr) + ' dB)=**' + str(self._snr_hidden) + '**' if self.cfg.min_snr else ''}"
                f"{'   HTTP backup: **' + str(self._http_feed.spots_in) + '** spots' if self._http_feed else ''}"
                f"{'   warm-up: ' + self._bold_warm(self.runhist.warm_status) if self.runhist and self.runhist.warm_status else ''}"
                f"{'   ' + self._bold_count(self.scp.status) if self.scp is not None else ''}"
            )
        )
        self.log_label.configure(text=worked)
        self._update_check_line()
        self._update_band_lights()
        self._update_team_label()
        self._update_lights(snap)

    # ---- pre-contest check ------------------------------------------------

    def _skimmers_quiet(self, role: str) -> str:
        """'' or 'TK0C sends nothing (4 min)': listed skimmers that stopped while
        the feed itself works (paused / feed down = not checked here)."""
        sw = getattr(self, "skimwatch", None)
        if sw is None or not sw.calls or getattr(self, "_rbn_pause_on", False):
            return ""
        if role == "slave":
            alive = (not self.team.master_paused()
                     and time.time() - getattr(self, "_last_team_spot", 0.0) < 120)
        else:
            alive = self._rbn_healthy() or (self._http_feed is not None
                                            and bool(self._http_feed.spots_in))
        text = sw.text(sw.quiet(alive))
        if text != getattr(self, "_skim_quiet_text", ""):
            if text:
                log.warning("own skimmer: %s", text)
            elif self._skim_quiet_text:
                log.info("own skimmer spotting again")
            self._skim_quiet_text = text
        return text

    def _check_facts(self) -> dict:
        snap = self.state.snapshot(self.radio)
        role = self.team.role() if self.team is not None else "off"
        if role == "slave":
            rbn = "paused" if self.team.master_paused() else "team"
        elif getattr(self, "_rbn_pause_on", False):
            rbn = "paused"
        elif self._rbn_healthy():
            rbn = "ok"
        elif self._http_feed is not None and self._http_feed.spots_in:
            rbn = "http"
        else:
            rbn = self.rbn_status or "no spots"
        cty_days = scp_days = None
        cty_path = getattr(self.cty, "path", "") if self.cty else ""
        try:
            if cty_path:
                cty_days = (time.time() - os.path.getmtime(cty_path)) / 86400
        except OSError:
            pass
        scp_src = getattr(self.scp, "source", "") if self.scp is not None else ""
        try:
            if scp_src:
                scp_days = (time.time() - os.path.getmtime(scp_src)) / 86400
        except OSError:
            pass
        return {
            "n1mm_age": snap["age"] if snap and snap.get("freq_khz") else None,
            "n1mm_freq": (snap or {}).get("freq_khz"),
            "log_open": self.worked.log_open, "log_name": self.worked.db_name,
            "log_source": self.worked.contest_source,
            "log_stale_nr": self.worked.ini_stale_nr,
            "log_msg": self.worked.log_message,
            "call": self.own_call(),
            "rbn": rbn,
            "skimmers": (None if not getattr(getattr(self, "skimwatch", None), "calls", ())
                         or rbn == "paused" else
                         {"names": ", ".join(self.skimwatch.calls),
                          "quiet": self._skimmers_quiet(role)}),
            "cty_loaded": bool(self.cty), "cty_days": cty_days,
            "scp_enabled": self.scp is not None,
            "scp_loaded": bool(self.scp is not None and self.scp.loaded), "scp_days": scp_days,
            "web": ("ok" if self.web is not None else "bad") if self.cfg.web_enabled else "off",
            "clock_offset": self.clock.offset,
            "clock_source": self.clock.source,
        }

    def _update_check_line(self) -> None:
        self._checks = checksmod.build(self._check_facts())
        lbl = getattr(self, "check_label", None)
        if lbl is None:
            return
        if not self.cfg.check_line and lbl.winfo_manager():   # switched off in Settings
            lbl.pack_forget()
        state = checksmod.worst(self._checks)
        badge = getattr(self, "ready_badge", None)
        if badge is not None:
            if not self.cfg.check_line:
                if badge.winfo_manager():
                    badge.pack_forget()
            else:
                if not badge.winfo_manager():
                    badge.pack(side="right", before=self.team_label)
                word = {"ok": "\u2713 READY", "warn": "\u26a0 CHECK", "bad": "\u2717 NOT READY"}[state]
                b_bg, b_fg = theme.READY_COLORS[state]
                if badge.cget("text") != word:
                    badge.configure(text=word, bg=b_bg, fg=b_fg)
        if not self.cfg.check_line:
            return
        # the items: shown when something is wrong, or when the badge was clicked
        want = state != "ok" or self._check_details
        if want != bool(lbl.winfo_manager()):
            if want:
                lbl.pack(fill="x", after=self._top)
            else:
                lbl.pack_forget()
        segs = checksmod.segments(self._checks)
        if segs != getattr(self, "_check_segs", None):
            self._check_segs = segs
            lbl.configure(state="normal")
            lbl.delete("1.0", "end")
            for n, (text, st) in enumerate(segs):
                if n:
                    lbl.insert("end", "   ")
                lbl.insert("end", text, st)
            lbl.configure(state="disabled")
            self._fit_check_line()

    def _fit_check_line(self) -> None:
        """Check line height = its wrapped rows."""
        lbl = getattr(self, "check_label", None)
        if lbl is None:
            return
        try:
            rows = lbl.count("1.0", "end", "displaylines")
            rows = rows[0] if isinstance(rows, tuple) else rows
        except tk.TclError:
            rows = 1
        rows = max(1, min(4, int(rows or 1)))
        if int(lbl.cget("height")) != rows:
            lbl.configure(height=rows)

    # ---- band open lights ---------------------------------------------------

    BAND_COLORS = {"open": ("#2e7d32", "white"), "local": ("#f9a825", "black"),
                   "closed": ("#bdbdbd", "#424242")}

    def _band_open(self) -> dict[str, dict]:
        """{band: {spots, calls, dx, state}} - ours, or the team master's."""
        if self.team is not None and self.team.role() == "slave":
            got = self.team.master_bands()
            if isinstance(got, dict):
                return {b: dict(v, state=state_of(v), pct=dx_pct(v))
                        for b, v in got.items() if isinstance(v, dict)}
            return {}
        return self.bandact.summary()

    def _on_raw_spot(self, spot) -> None:
        """Every live spot of the whole feed (before the list filters)."""
        p = getattr(self, "_perf", None)
        if p is not None:
            p["raw"] += 1
        sw = getattr(self, "skimwatch", None)
        if sw is not None:
            sw.seen(spot.spotter)
        if hasattr(self, "_self_spots"):
            self._note_self_spot(spot.dxcall, spot.spotter, spot.freq_khz)
        self.bandact.add(spot)
        zm = getattr(self, "zonemap", None)
        if zm is not None:
            try:
                zm.add(spot)
            except Exception as exc:  # noqa: BLE001 - never stop the feed
                log.debug("zones: %s", exc)

    def _timeline(self, hours: int = 12) -> dict:
        """/api/timeline: spots per band and 10 minutes (skimmers on our continent)."""
        out = self.timeline.grid(hours)
        out.update(home=self.home_cont, team=self.team.role() if self.team is not None else "off")
        return out

    def _zones(self, minutes: int = 15) -> dict:
        """/api/zones: who hears me / what I hear per band and CQ zone, over
        the last ``minutes`` (15 by default; the page offers 30 / 60)."""
        minutes = max(5, min(60, int(minutes or 15)))
        own = self.own_call()
        ent = self._entity(own) if own else None
        out = self.zonemap.table(minutes * 60)
        out.update(own=own, my_zone=getattr(ent, "cq", None), home=self.home_cont,
                   minutes=minutes, bands=list(CONTEST_BANDS),
                   team=self.team.role() if self.team is not None else "off")
        return out

    def _mults_waiting(self) -> dict[str, dict]:
        """{band: {"mults": stations that are a new multiplier there, "mm":
        of them double}} - from the spots in the list (all bands, the shown
        modes, not removed, not ourselves). Worked out every 3 s at most."""
        now = time.time()
        cache = getattr(self, "_mw_cache", None)
        if cache and now - cache[0] < 3:
            return cache[1]
        modes = {m.upper() for m in self._effective_modes(None)}
        own = self.own_call()
        best: dict[tuple[str, str], int] = {}
        for sp in list(self.spots.values()):
            band = band_from_khz(sp.freq_khz)
            if band not in CONTEST_BANDS or sp.mode.upper() not in modes:
                continue
            key = (sp.dxcall.upper(), band)
            if key in best or key in self._removed or same_station(sp.dxcall, own):
                continue
            if self.cfg.hide_beacons and is_beacon(sp):
                continue
            best[key] = self._mults(sp)
        out = {b: {"mults": 0, "mm": 0} for b in CONTEST_BANDS}
        for (_call, band), m in best.items():
            if m:
                out[band]["mults"] += 1
                out[band]["mm"] += 1 if m >= 2 else 0
        self._mw_cache = (now, out)
        return out

    def _band_info(self) -> dict[str, dict]:
        """Band open counts plus the new multipliers spotted per band."""
        data = {b: dict(v) for b, v in self._band_open().items()}
        try:
            waiting = self._mults_waiting()
        except Exception as exc:  # noqa: BLE001 - the lights still work without
            log.debug("mults waiting: %s", exc)
            waiting = {}
        for b, w in waiting.items():
            data.setdefault(b, {"state": "closed", "dx": 0, "calls": 0, "spots": 0}).update(w)
        return data

    def _band_chip_text(self, band: str, c: dict) -> str:
        """'40m: 34 DX 70%' (open) / '80m: EU only' (only our continent) /
        '160m: -' (nothing). The % = DX stations of all heard (the colour).
        '· 6 mults' = new multipliers spotted on that band now."""
        m = c.get("mults") or 0
        more = f" \u00b7 {m} mult{'s' if m != 1 else ''}" if m else ""
        if c.get("state") == "closed" or not c.get("calls"):
            return f"{band}: {m} mult{'s' if m != 1 else ''}" if m else f"{band}: \u2013"
        if c.get("dx"):                    # some DX: how many, and their share (= the colour)
            return f"{band}: {c.get('dx', 0)} DX {dx_pct(c)}%" + more
        return f"{band}: {self.home_cont} only" + more

    @staticmethod
    def _band_chip_color(c: dict) -> tuple[str, str]:
        """Grey when nothing is heard, else orange (our continent) -> green
        (DX) in proportion to the DX share."""
        if c.get("state") == "closed" or not c.get("calls"):
            return "#bdbdbd", "#424242"
        return share_color(dx_pct(c))

    def _view_band_now(self) -> str | None:
        """The band the window's list shows instead of the radio's - until
        the radio changes band (then it follows the radio again)."""
        if self._view_band and self._current_band() != self._view_from:
            self._view_band = self._view_from = None
            self._bands_last = None
            self._dirty = True
        return self._view_band

    def _on_band_chip(self, band: str) -> None:
        """Band chip clicked: show that band's spots (the radio stays where
        it is). Clicked again, or the radio's own band: follow the radio."""
        here = self._current_band()
        if band == self._view_band or band == here:
            self._view_band = self._view_from = None
            msg = f"List follows the radio again ({here or 'all bands'})"
        else:
            self._view_band, self._view_from = band, here
            msg = (f"List shows {band} (the radio stays on {here or '?'}) - click a spot to QSY "
                   f"there; click {band} again to go back")
        self._bands_last = None
        self._dirty = True
        self.status_label.configure(text=msg)
        self._update_band_lights()
        self._update_top_bar(self.state.snapshot(self.radio))

    def _update_band_lights(self) -> None:
        if self.bands_frame is None:         # made with the lights (same row)
            return
        if not self.cfg.band_lights:
            if self._band_lights:
                for w in self.bands_frame.winfo_children():
                    w.destroy()
                self._band_lights = {}
                self._bands_last = None
            return
        data = self._band_info()
        here = self._current_band()
        key = (tuple(sorted((b, v.get("dx"), v.get("calls"), v.get("state"), v.get("mults"))
                            for b, v in data.items())),
               here, self._view_band, bool(self.compact_var.get()))
        if key == self._bands_last:
            return
        self._bands_last = key
        self._update_compact_bands(data, here)
        if not self._band_lights:
            tk.Label(self.bands_frame, text="Band open (DX heard, 10 min; new mults spotted):",
                     bg=theme.BG,
                     fg=theme.MUTED,
                     font=(theme.FONT, 9)).pack(side="left", padx=(0, 4))
            for band in CONTEST_BANDS:
                lbl = tk.Label(self.bands_frame, text=band, padx=6, pady=1,
                               font=(theme.FONT, 9, "bold"), relief="flat", cursor="hand2")
                lbl.pack(side="left", padx=1)
                lbl.bind("<Button-1>", lambda _e, b=band: self._on_band_chip(b))
                self._band_lights[band] = lbl
        for band, lbl in self._band_lights.items():
            c = data.get(band) or {"state": "closed", "dx": 0, "spots": 0}
            bg, fg = self._band_chip_color(c)
            viewed = band == self._view_band
            lbl.configure(text=("\u25b6 " if viewed else "") + self._band_chip_text(band, c),
                          bg=bg, fg=fg,
                          relief="solid" if band in (here, self._view_band) else "flat",
                          bd=2 if viewed else (1 if band == here else 0))

    def _light_states(self, snap) -> list[tuple[str, str]]:
        role = self.team.role() if self.team is not None else "off"
        n = len(self.team.alive_peers()) + 1 if self.team is not None else 1
        spot_age = time.time() - self._last_own_spot if self._last_own_spot else None
        return [
            lights.rbn_light("paused" if getattr(self, "_rbn_pause_on", False) or (
                role == "slave" and self.team.master_paused()) else self.rbn_status,
                             spot_age, role,
                             http=self._http_feed is not None and bool(self._http_feed.spots_in)),
            lights.n1mm_light(snap["age"] if snap and snap.get("freq_khz") else None),
            lights.log_light(self.worked.log_open, self.worked.log_state),
            lights.team_light(role, n, self.team.version_mismatch() if self.team else None),
            lights.web_light(self.cfg.web_enabled, self.web is not None),
            lights.ssb_light(self.ssb_status, role),
            lights.http_light(bool(getattr(self.cfg, "rbn_http_backup", "")) and role != "slave",
                              self._http_feed is not None,
                              self._http_feed is not None and bool(self._http_feed.spots_in)),
            lights.n1mm_spots_light(
                getattr(self.cfg, "ssb_use_n1mm", "auto") != "off",
                getattr(self, "_n1mm_spot_first", None) is not None,
                getattr(self, "_ssb_via_n1mm", False),
                (time.time() - self._n1mm_spot_last) if getattr(self, "_n1mm_spot_last", 0) else None,
                self._n1mm_quiet_limit()),
            lights.warm_light(self.runhist is not None and self.cfg.warmup,
                              self.runhist.warm_status if self.runhist else ""),
            lights.update_light(self.updater.state, self.updater.text()),
        ]

    def _update_lights(self, snap) -> None:
        """Coloured dot per connection / stream: green working, yellow on hold,
        amber connecting, red not working, grey off."""
        states = self._light_states(snap)
        if states == self._lights_last:
            return
        self._lights_last = states
        bg = theme.BG
        while len(self._lights) < len(states):
            box = tk.Frame(self.lights_frame, bg=bg)
            d = theme.px(11)      # a drawn circle: sharp at any scaling, any font
            dot = tk.Canvas(box, width=d + 2, height=d + 2, bg=bg, highlightthickness=0, bd=0)
            dot.create_oval(1, 1, d, d, fill=lights.COLORS[lights.OFF], outline="", tags="dot")
            dot.pack(side="left", padx=(0, 4))
            name = tk.Label(box, text="", font=(theme.FONT, 9, "bold"), bg=bg, fg=theme.TEXT)
            name.pack(side="left")
            self.lights_frame.add(box)
            self._lights.append((dot, name))
        if self.bands_frame is None:         # band open chips: same row, after the lights
            self.bands_frame = tk.Frame(self.lights_frame, bg=bg)
            self.lights_frame.add(self.bands_frame, gap=10)
            self._bands_last = None
        for (dot, name), (state, text) in zip(self._lights, states):
            dot.itemconfigure("dot", fill=lights.COLORS[state])
            name.configure(text=text)
        # the Update light (last) is a button: click = check GitHub now
        if self._lights and not getattr(self, "_update_light_bound", False):
            for w in self._lights[-1]:
                w.configure(cursor="hand2")
                w.bind("<Button-1>", lambda _e: self._on_update_click())
            self._update_light_bound = True
        if self.updater.state == "ready" and self._restart_btn is None:
            self._restart_btn = tk.Button(
                self.lights_frame, text=f"Restart -> {self.updater.new_version}",
                bg=lights.COLORS[lights.NEW], fg="#ffffff", relief="flat",
                padx=8, command=self._restart_for_update)
            self.lights_frame.add(self._restart_btn)

    def _save_setting(self, key: str, value: str) -> dict:
        """Settings page (web thread): write config.ini, update the config."""
        return settingsmod.save(self.cfg, self.cfg.config_path, key, value)

    def _web_restart(self) -> None:
        self._web_queue.put(("restart",))

    def _on_update_click(self) -> None:
        """Update light clicked: restart into a downloaded version, open the
        Releases page (macOS), or ask GitHub for a new version right now."""
        u = self.updater
        if u.state == "ready":
            self._restart_for_update()
            return
        if u.state == "available":
            import webbrowser
            webbrowser.open("https://github.com/s55oo/POUNCer/releases/latest", new=2)
            self.status_label.configure(text=f"{u.new_version}: opened the Releases page")
            return
        if u.state == "downloading":
            self.status_label.configure(text=f"Already downloading {u.new_version}...")
            return
        if not u.check_in_background():
            self.status_label.configure(
                text="Update check works only in the built POUNCer (exe / macOS app)")
            return
        self.status_label.configure(text="Checking GitHub for a new version...")
        self._lights_last = None   # redraw the light ("checking")

    def _restart_for_update(self) -> None:
        """Restart button: quit and start the new version right away."""
        self._save_window()
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
        modes = {m.upper() for m in self._effective_modes(radio)}
        own = self.own_call()
        every = band == ALL_BANDS          # phone band menu > ALL: every contest band
        items = [
            s for s in self.spots.values()
            if (band is None or band_from_khz(s.freq_khz) == band
                or (every and band_from_khz(s.freq_khz) in CONTEST_BANDS))
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
        # Min SNR: weaker spots out (spots without SNR - SSB cluster - stay;
        # new mults stay unless min_snr_keep_mults is off)
        weak = 0
        if self.cfg.min_snr > 0:
            kept = []
            for s in items:
                if (s.snr is not None and s.snr < self.cfg.min_snr
                        and not (self.cfg.min_snr_keep_mults and self._mults(s))):
                    weak += 1
                else:
                    kept.append(s)
            items = kept
        self._last_weak = weak
        if radio is None:
            self._snr_hidden = weak
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
        items = self._visible_spots(band=self._view_band_now())
        if hasattr(self, "_apply_pin"):
            items = self._apply_pin(items)
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

        # keep the selected spot selected (arrow keys + Space) across redraws
        sel = self.tree.selection()
        keep = None
        if sel:
            try:
                keep = self._row_key(self._row_spots[int(sel[0])])
            except (ValueError, IndexError, AttributeError):
                keep = None
        self.tree.delete(*self.tree.get_children())
        self.displayed = items
        self._row_spots = items
        reselect = None
        alert = []
        tile_items = []
        want_tiles = getattr(self, "tiles_var", None) is not None and self.tiles_var.get()
        fade_s = getattr(self.cfg, "fade_minutes", 5) * 60 if hasattr(self, "cfg") else 300
        now = time.time()
        for idx, s in enumerate(items):
            mults = self._mults(s)
            worked = self.worked.is_worked(s.dxcall, s.freq_khz, s.mode)
            run = self._run_fields(s)
            rare = (run.get("run") is not None and run["run"] < RARE_RUN_PCT) or (
                run.get("hours") is not None and run["hours"] < RARE_HOURS)
            busted = self.scp is not None and self.scp.busted(s.dxcall)
            if rare and mults and not worked:
                alert.append(s)
            # one text colour: worked (grey) > new mult (red, bold) > strong
            if worked:
                tags: tuple = ("worked",)
            elif mults:
                tags = ("mult",)
            elif s.snr is not None and s.snr >= 20:
                tags = ("strong",)
            else:
                tags = ()
            # one background: tuned to it > DX
            if s is tuned:
                tags += ("onfreq",)
            elif self._is_dx(s.dxcall):
                tags += ("dx",)
            elif idx % 2:
                tags += ("stripe",)               # calm zebra rows
            if keep is not None and self._row_key(s) == keep:
                reselect = str(idx)
            call_text = ("\u26a1 " if rare else "") + s.dxcall + (" ?" if busted else "")
            tried = self._tried(s)
            if tried:
                call_text += f"   \u21bb{tried[1]}m" + (f"\u00d7{tried[0]}" if tried[0] > 1 else "")
            if want_tiles:
                tile_items.append(self._tile_item(s, s is tuned, mults, worked, run, rare, busted,
                                                  tried, now - s.received >= fade_s))
            self.tree.insert(
                "", "end", iid=str(idx), tags=tags,
                values=(
                    s.time_utc,
                    f"{s.freq_khz:.1f}",
                    "" if s.snr is None else s.snr,
                    "" if s.wpm is None else s.wpm,
                    call_text,
                    self._continent(s.dxcall) or "",
                    {0: "", 1: "M", 2: "MM"}.get(self._mults(s), ""),
                    s.mode,
                    s.spot_type,
                    (f"\u2605 {own}" if (own := (self._own_mark(s) if hasattr(self, "_own_mark")
                                                    else "")) else s.spotter),
                ),
            )
        if reselect is not None:
            self.tree.selection_set(reselect)
            self.tree.focus(reselect)
        if getattr(self, "tiles_var", None) is not None and self.tiles_var.get():
            self.tiles.set_items(tile_items, int(reselect) if reselect is not None else None)
        check = getattr(self, "_check_alerts", None)
        if check is not None and getattr(self, "_alerts", None) is not None:
            try:
                check(alert)
            except Exception as exc:  # noqa: BLE001 - an alert must never break the list
                log.info("alert: %s", exc)

    def _tile_item(self, s, onfreq, mults, worked, run, rare, busted, tried, stale) -> dict:
        """What one tile shows (tiles.TileList), like the phone's tile."""
        line1 = f"{s.freq_khz:.1f}" + (f" {s.mode}" if s.mode and s.mode.upper() != "CW" else "")
        if run.get("run") is not None:
            line1 += f"  R{run['run']}%" + ("*" if run.get("rec") else "")
        if run.get("hours") is not None:
            line1 += f"  ~{round(run['hours'])}h"
        cq = self._cq_minutes(s) if hasattr(self, "_on_freq") else None
        line2 = (self._first_heard_utc(s) if hasattr(self, "_on_freq") else s.time_utc or "")
        line2 += "  " + ("--" if s.snr is None else f"{s.snr}dB")
        if cq is not None:
            line2 += f"  cq{cq}m"
        return {"call": s.dxcall, "line1": line1, "line2": line2,
                "strong": s.snr is not None and s.snr >= 20, "dx": bool(self._is_dx(s.dxcall)),
                "mult": int(mults or 0), "worked": bool(worked), "rare": bool(rare),
                "busted": bool(busted), "onfreq": bool(onfreq), "stale": bool(stale),
                "tried": (f"\u21bb{tried[1]}m" + (f"\u00d7{tried[0]}" if tried[0] > 1 else ""))
                if tried else "", "own": bool(self._own_mark(s)) if hasattr(self, "_own_mark") else False}

    def _tile_tap(self, i: int) -> None:
        """A tile clicked (or Space on the selected one): like clicking the row."""
        try:
            spot = self._row_spots[i]
        except (IndexError, AttributeError):
            return
        self._act_on_spot(spot)

    def _show_tiles(self) -> None:
        """Show the tiles or the table in the list's place."""
        on = bool(self.tiles_var.get())
        if on:
            self.tree.pack_forget()
            self._tree_scroll.pack_forget()
            self.tiles.pack(fill="both", expand=True)
        else:
            self.tiles.pack_forget()
            self.tree.pack(side="left", fill="both", expand=True)
            self._tree_scroll.pack(side="right", fill="y")
        self._dirty = True

    def _toggle_tiles(self, flip: bool) -> str:
        """View > Spot list as tiles / Ctrl+T: saved in config.ini."""
        if flip:
            self.tiles_var.set(not self.tiles_var.get())
        self._show_tiles()
        try:
            self._save_setting("ui.spot_tiles", "true" if self.tiles_var.get() else "false")
        except Exception as exc:  # noqa: BLE001
            log.info("tiles setting not saved: %s", exc)
        return "break"

    def _apply_pin(self, items: list) -> list:
        """The clicked / tapped station stays on its row (``PIN_S``)."""
        pin = getattr(self, "_pin", None)
        if not pin:
            return items
        key, idx, at = pin
        if time.time() - at > PIN_S:
            self._pin = None
            return items
        j = next((i for i, x in enumerate(items)
                  if (x.dxcall.upper(), band_from_khz(x.freq_khz)) == key), -1)
        if j < 0:                       # logged / gone: let go
            self._pin = None
            return items
        w = getattr(self, "worked", None)
        if w is not None and w.is_worked(items[j].dxcall, items[j].freq_khz, items[j].mode):
            self._pin = None            # logged (and shown grey): let go
            return items
        to = min(idx, len(items) - 1)
        if j != to:
            items = list(items)
            items.insert(to, items.pop(j))
        return items

    @staticmethod
    def _row_key(s: Spot) -> tuple:
        return (s.dxcall.upper(), band_from_khz(s.freq_khz))

    def _on_tree_key(self, event) -> str:
        """Spot list keys: Space = tap the selected spot (QSY + call), like
        a click. Arrow keys move the selection (Treeview's own)."""
        sel = self.tree.selection()
        if not sel:
            first = self.tree.get_children()
            if first:
                self.tree.selection_set(first[0])
                self.tree.focus(first[0])
            return "break"
        try:
            spot = self.displayed[int(sel[0])]
        except (ValueError, IndexError):
            return "break"
        self._act_on_spot(spot)
        return "break"

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
            qsos = summarymod.read_qsos(path, w.contest_nr, w.mult_slots or w.mult_types)
        except Exception as exc:   # locked / gone
            return {"ok": False, "error": f"Could not read the log: {exc}"}
        out = summarymod.build(qsos, self.taps.snapshot(), w.mult_slots or w.mult_types)
        out.update(log=w.db_name, contest=w.contest_name,
                   station=w.station_call or self.own_call())
        return out

    def _set_look(self) -> None:
        """View > Look: saved in config.ini, used from the next start (the
        whole window is built in it) - offer the restart now."""
        look = self.look_var.get()
        try:
            self._save_setting("ui.look", look)
        except Exception as exc:  # noqa: BLE001
            log.info("look not saved: %s", exc)
        from tkinter import messagebox
        if self.updater.exe and messagebox.askyesno(
                "POUNCer", f"Look: {look}. Restart POUNCer now to use it?", parent=self.root):
            self._restart_now()
        else:
            self.status_label.configure(text=f"Look {look}: used from the next start")

    @staticmethod
    def _app_browser() -> list[str] | None:
        """Edge or Chrome, to open a page as an app window (no tabs / bars)."""
        import shutil
        if sys.platform == "win32":
            pf = [os.environ.get(k, "") for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
            for base in pf:
                for rel in (r"Microsoft\Edge\Application\msedge.exe",
                            r"Google\Chrome\Application\chrome.exe"):
                    p = os.path.join(base, rel)
                    if base and os.path.exists(p):
                        return [p]
            return None
        if sys.platform == "darwin":
            for app in ("/Applications/Google Chrome.app", "/Applications/Microsoft Edge.app"):
                if os.path.exists(app):
                    return ["open", "-na", app, "--args"]
            return None
        for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
            p = shutil.which(name)
            if p:
                return [p]
        return None

    def _open_app_window(self) -> None:
        """View > Phone page as an app window: the phone page on the PC in
        its own window (Edge / Chrome app mode: no tabs, no address bar)."""
        url = self._local_web_url()
        if not url:
            self.status_label.configure(
                text="The app window needs the phone view: [web] enabled = true")
            return
        cmd = self._app_browser()
        if not cmd:
            self._open_browser()          # no Edge / Chrome: the normal browser
            return
        import subprocess
        try:
            subprocess.Popen(cmd + [f"--app={url}", "--window-size=480,900"])
            self.status_label.configure(text=f"Opened the phone page as an app window ({url})")
        except OSError as exc:
            log.info("app window: %s", exc)
            self._open_browser()

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
            self.cfg.n1mm_listen_ip, self.cfg.n1mm_listen_port, self.state,
            on_spot=self._on_n1mm_spot,
        )
        self._listener.start()
        # your other programs (Settings > Autostart) - not again after a restart.
        # N1MM listed first and not running yet: start it and wait for it
        # before the rest of POUNCer (and the other programs) starts.
        restarted = os.environ.pop(autostartmod.RESTART_ENV, "") == "1"
        waiting = False
        if self.cfg.autostart and not restarted:
            waiting = self._start_autostart(defer_pouncer=True)
        if not waiting:
            self._start_services()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        try:
            self.root.mainloop()
        finally:
            self._shutdown()

    def _start_services(self) -> None:
        """Everything that needs N1MM or the network: phone page, team, RBN,
        clock check, CW speed, SCP, and the refresh tick (reads the log)."""
        if getattr(self, "_services_started", False):
            return
        self._services_started = True
        if self.cfg.web_enabled:
            self._start_web()
            if self.cfg.touch_view:
                self.root.after(1500, self._open_browser)
        else:
            self.web_label.configure(text="Phone view: **disabled** ([web] enabled = false)")
        if self.cfg.team:
            self._start_team()
        self._ensure_rbn_clients()
        self.clock.start()              # PC clock check (check line), every 30 min
        if self.cwspeed is not None:
            self.cwspeed.start()        # N1MM's CW speed box, read once a second
        if self.scp is not None:
            self.scp.start()            # load MASTER.SCP, ask the server for a newer one (304 = current)
        self.perf.start()             # cache/perf.csv: CPU / memory / disk, a row a minute
        self.root.after(REFRESH_MS, self._tick)

    def _start_web(self) -> None:
        try:
            self.web = WebServer(
                self.cfg.web_host, self.cfg.web_port,
                self._web_spots, self._web_qsy, self._web_key,
                self._web_reconnect, self._web_band, self._web_hide,
                self._web_tune, self._web_filter, self._web_clear,
            )
            self.web.set_summary_provider(self._summary)
            self.web.set_zones_provider(self._zones)
            self.web.set_explain_provider(self._explain_spot)
            self.web.set_timeline_provider(self._timeline)
            if self.cfg.web_cw_keyboard:
                self.web.set_cw_handler(self._web_cw)
            self.web.set_min_snr_handler(self._web_min_snr)
            self.web.set_settings_provider(
                lambda: {"items": settingsmod.current(self.cfg), "path": self.cfg.config_path},
                self._save_setting,
                restart=(self._web_restart if self.updater.exe else None),
                remote=self.cfg.web_remote_settings,
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
                text=f"Phone view: **FAILED** to listen on port **{self.cfg.web_port}** "
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
                f"Phone view: **{self.web_url}**   ({network})"
                f"   listening on **{self.cfg.web_host or '0.0.0.0'}:{port}**"
            )
        )
        self._web_addr_checked = time.time()

    def _shutdown(self) -> None:
        if getattr(self, "perf", None) is not None:
            self.perf.stop()
        rh = getattr(self, "runhist", None)
        if rh is not None and hasattr(rh, "flush"):
            try:
                rh.flush()               # the last learnt run histories (saved every 30 s)
            except Exception as exc:  # noqa: BLE001
                log.info("run history not saved: %s", exc)
        if getattr(self, "timeline", None) is not None:
            self.timeline.save()
        if self.team is not None:
            self.team.stop()
        if self.web is not None:
            self.web.stop()
            self.web = None
        for client in self._rbn_clients.values():
            client.stop()
        self._rbn_clients.clear()
        self._stop_http_feed()
        if getattr(self, "cwspeed", None) is not None:
            self.cwspeed.stop()
        if getattr(self, "clock", None) is not None:
            self.clock.stop()
        if getattr(self, "_ssb_client", None) is not None:
            self._ssb_client.stop()
            self._ssb_client = None
        if self._listener is not None:
            self._listener.stop()
        self.controller.close()
