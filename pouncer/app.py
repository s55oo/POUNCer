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
from .bands import CONTEST_BANDS, DEFAULT_BAND_KHZ, band_from_khz, normalize_band_view
from .config import Config
from .flow import flow_positions
from . import lights
from . import traffic
from .cty import CtyDB, Entity, default_cty_paths, load_cty, wpx_prefix
from .ctyupdate import CtyUpdater, cache_name as cty_cache_name, newest as newest_file
from .n1mm import (
    N1MMController, RadioListener, RadioState, cat_macro, next_tune_khz,
)
from .rbn import (
    ContinentFilter, OnFreqTracker, PeopleFilter, RBNClient, Spot, SpotterFilter, SpottedFilter,
    beacon_why,
    is_beacon,
    is_phone_contest,
    ports_for_modes, spot_from_n1mm, digital_sure, digital_overlap, digital_mode,
)
from .runhist import RARE_RUN_PCT, RunHistory
from .urgency import classify as classify_urgency, format_hours
from .rbnhttp import RBNHttpFeed
from .scp import ScpList
from .cwspeed import SpeedReader
from . import checks as checksmod
from .bandopen import BandActivity, dx_pct, share_color, state_of
from .zones import ZoneMap
from .timeline import Timeline
from .perf import PerfMonitor
from .netwatch import NetWatch, minutes_text
from .alerts import AlertWatch, SelfSpotWatch, base_call as alert_base_call, beep as alert_beep
from .panels import TimelineWindow, ZonesWindow
from .console import TrafficConsole
from .skimwatch import SkimmerWatch
from . import stationtest
from .pskr import PskChecker
from .tiles import TileList
from .rowlist import RowList
from . import theme
from . import autostart as autostartmod
from . import diag as diagmod
from . import win32 as win32mod
from . import summary as summarymod
from . import settings as settingsmod
from . import winstate
from .settings_window import SettingsWindow
from .team import Team, derive_key
from .clusterserver import ClusterServer, parse_dx
from .hamqth import HamQTHFeed
from .config import has_source, parse_sources
from .winfw import add_firewall_rules, firewall_rule_exists
from .settings import set_ini_value
from .update import Updater
from .web import phone_addresses, WebServer, pick_web_address
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


def _drive(steps):
    """Run a steps generator (yields seconds to wait) right here, sleeping -
    for threads and tests. Its return value."""
    try:
        while True:
            time.sleep(next(steps))
    except StopIteration as stop:
        return stop.value
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


def _keyboard_ready(app, hwnd, *, allow_focus=True) -> bool:
    """Confirm the actual keyboard target; optionally bring it to the front.

    With focus disabled, the operator must already have selected that window.
    A successful API return alone is insufficient after the settling delay.
    """
    if not hwnd:
        return False
    try:
        if win32mod.foreground_window() == hwnd:
            return True
        if not allow_focus or not getattr(getattr(app, "cfg", None), "focus_n1mm", True):
            return False
        if not force_foreground(hwnd):
            return False
        time.sleep(0.15)
        return win32mod.foreground_window() == hwnd
    except Exception:  # a window may disappear between the two checks
        return False


def _cw_keyboard_ready(app, hwnd, nr: int, *, allow_focus=True) -> bool:
    """A CW input must be distinct from every reported Entry Window."""
    entry = app.state.entry_window_hwnd(nr)
    entries = {app.state.entry_window_hwnd(r) for r in app.state.radio_numbers()}
    if not hwnd or hwnd in entries or not win32mod.is_window(hwnd):
        return False
    pid = window_pid(entry) if entry else 0
    return bool(pid and window_pid(hwnd) == pid and
                _keyboard_ready(app, hwnd, allow_focus=allow_focus) and
                win32mod.focused_multiline_edit(hwnd))


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
        # ... and one that is destroyed (the band chips are rebuilt when the
        # layout changes) leaves the row: measuring it raised "bad window
        # path name" and the row stopped laying out (v0.7.188)
        widget.bind("<Destroy>", lambda e, w=widget: self._forget(w) if e.widget is w else None,
                    add="+")
        self._schedule()
        return widget

    def _forget(self, widget) -> None:
        self._items = [(w, g) for w, g in self._items if w is not widget]
        self._hidden.discard(widget)
        self._last = None
        try:
            self._schedule()
        except tk.TclError:                 # the row itself is going away too
            pass

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
        try:
            width = max(self.winfo_width(), 1)
        except tk.TclError:                 # the row itself was destroyed
            return
        self._items = [(w, g) for w, g in self._items if w.winfo_exists()]
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
DESKTOP_REFRESH_S = 5  # quiet-feed age/fade/CQ/pin updates, without a redraw every tick

class PouncerApp:
    def __init__(self, config: Config) -> None:
        self.cfg = config
        self._tap_async = True      # taps run as steps between window updates
        self._tap_gen = None
        self._dxc = None              # POUNCer's own DX cluster server ([cluster] enabled)
        self._app_started = time.time()
        self._fw_ok = None            # POUNCer's firewall rules there: True / False / None
        # internet up / down: one clear red bar instead of every feed failing alone
        self.net = (NetWatch(on_change=lambda up: self._web_queue.put(("net", up)))
                    if getattr(config, "internet_check", True) else None)
        self._net_bar_text = ""
        self.spot_queue: "queue.Queue[Spot]" = queue.Queue(maxsize=20000)
        self.status_queue: "queue.Queue[str]" = queue.Queue()
        self.spots: dict[tuple, Spot] = {}
        # How long each station has been calling on one frequency, from RBN
        self._on_freq = OnFreqTracker(ON_FREQ_STAY_KHZ, ON_FREQ_GAP_S)
        self.displayed: list[Spot] = []
        self.state = RadioState(profile_override=config.worked_profile)
        self.worked = WorkedLog(
            config.worked_db, config.n1mm_ini, config.worked_refresh_seconds,
            n1mm_running=self._n1mm_running, profile=config.worked_profile,
        )
        self._worked_hidden = 0
        self._snr_hidden = 0
        # the open log / contest as N1MM broadcasts it (AppInfo) wins over the ini
        self.worked.app_info = self.state.app
        self.worked.contact_events = self.state.contact_events
        self.worked.background = True   # read the log in a thread (v0.7.176)
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
        # Callsign -> country / continent / zone for EU / DX marking and mults.
        # v0.7.187: POUNCer's own download (S53M's cty.dat, every Friday) is
        # used, not N1MM's file - N1MM's wl_cty.dat only before the first
        # download ever (or with [cty] file = ... / auto_update = false).
        self.cty = None
        if getattr(config, "cty_auto_update", False) and not config.cty_file:
            cache_dir = config.run_history_dir or "cache"
            for cache_cty in dict.fromkeys([
                    os.path.join(cache_dir, cty_cache_name(config.cty_url)),
                    os.path.join(cache_dir, "wl_cty.dat")]):      # an older download
                if not os.path.exists(cache_cty):
                    continue
                try:
                    self.cty = CtyDB.load(cache_cty)
                    log.info("loaded %d cty entries from %s (POUNCer's download)",
                             len(self.cty), cache_cty)
                    break
                except Exception as exc:  # noqa: BLE001
                    log.warning("cty file %s unreadable: %s", cache_cty, exc)
        if self.cty is None:
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
        self.spotter_filter.entity_of = self._entity
        self._apply_filter(log_it=False)
        self.spotted_filter = SpottedFilter(entity_of=self._entity)
        self._apply_spotted(log_it=False)
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
        self._overlap_since: dict = {}     # key -> first asked (the alert waits a while)
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
        self._sort_labels = {"time": "Time", "freq": "Freq", "snr": "SNR", "mult": "Mult"}
        self._sort_buttons: dict[str, ttk.Button] = {}
        self.sort_col = config.sort if config.sort in self.columns and config.sort != "wpm" else "snr"
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
        # a failing button / timer callback goes to pouncer.log (no console in the exe)
        def _cb_failed(et, ev, tb) -> None:
            log.error("window callback failed", exc_info=(et, ev, tb))
            self._note_error("a button / timer", ev)
        self.root.report_callback_exception = _cb_failed
        self.root.title(f"POUNCer {__version__} - RBN band follower for N1MM")
        theme.apply(self.root, getattr(self.cfg, "look", "classic"))   # first: SCALE, colours
        self.root.geometry(f"{theme.px(940)}x{theme.px(520)}")
        # Narrow windows are fine: text lines wrap and the buttons flow onto
        # more rows instead of being cut off at the right edge.
        self.root.minsize(theme.px(420), theme.px(300))
        self.compact_var = tk.BooleanVar(value=False)
        self.tiles_var = tk.BooleanVar(value=bool(getattr(self.cfg, "tiles", False)))
        # clean (spots first) / detailed (every status line): View > Detailed layout
        self.detailed_var = tk.BooleanVar(value=getattr(self.cfg, "layout", "clean") == "detailed")
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
        # Tools > CQ zones / Band opening timeline: windows of their own
        self._zones_win = ZonesWindow(self.root, self._zones,
                                      browser=lambda: self._open_browser("zones"))
        self._timeline_win = TimelineWindow(self.root, self._timeline,
                                            browser=lambda: self._open_browser("timeline"))
        # Tools > Traffic console: the taps only work while it is open
        self._console = TrafficConsole(self.root, notes=self._console_notes, info=self._console_info)
        self.root.bind_all("<Control-Shift-D>", lambda _e: self._console.open())
        self.root.bind_all("<Control-Shift-d>", lambda _e: self._console.open())

        header = ttk.Frame(self.root, padding=0)
        header.pack(fill="x")
        self._header = header
        # compact mode (View > Compact window): one line with A / B instead
        # of the header, sort row, F-keys and status lines
        self._compact_bar = ttk.Frame(self.root, padding=(6, 4, 6, 2))
        self._compact_btn = ttk.Button(self._compact_bar, text="", style="Radio.TButton",
                                       command=self._toggle_radio)
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
        # this PC's address for the phone page: ZeroTier, else LAN - IP:port
        # (click = open the phone page here)
        self.addr_label = tk.Label(top, text="", bg=theme.TOP_BG, fg=theme.TOP_MUTED,
                                   font=(theme.FONT, 11), padx=6, cursor="hand2")
        self.addr_label.pack(side="left", pady=(6, 0))
        self.addr_label.bind("<Button-1>", lambda _e: self._show_phone_qr())
        # Team role: MASTER (talks to RBN) / SLAVE / alone - a coloured badge
        self.team_label = tk.Label(top, text="", font=(theme.FONT, 9, "bold"), padx=8, pady=2)
        self.team_label.pack(side="right", padx=(8, 0))
        self.team_label.bind("<Button-1>", lambda _e: self._become_master())
        # clear the spots: of the band in the list / of every band (like the badges)
        for text, cmd in (("CLR ALL", self._clear_spots), ("CLR BAND", self._clear_band)):
            b = tk.Label(top, text=text, font=(theme.FONT, 9, "bold"), padx=8, pady=2,
                         bg="#455a64", fg="#ffffff", cursor="hand2")
            b.pack(side="right", padx=(8, 0))
            b.bind("<Button-1>", lambda _e, c=cmd: c())
            b.bind("<Enter>", lambda _e, w=b: w.configure(bg="#607d8b"))
            b.bind("<Leave>", lambda _e, w=b: w.configure(bg="#455a64"))
        self._clr_band_hint = None
        # Pre-contest check: READY / CHECK / NOT READY (click: details on / off)
        self.ready_badge = tk.Label(top, text="", font=(theme.FONT, 10, "bold"), padx=10,
                                    pady=3, cursor="hand2")
        self.ready_badge.pack(side="right")
        self.ready_badge.bind("<Button-1>", lambda e: self._toggle_check_details())
        self._check_details = False
        # SPOTTED bar inside the window (always drawn, unlike a floating
        # pop-up on some PCs): shown under the top bar until clicked
        self.spotted_bar = tk.Label(header, text="", anchor="w", justify="left",
                                    bg="#ffd600", fg="#212121", padx=12, pady=4,
                                    font=(theme.FONT, 11, "bold"), cursor="hand2")
        self.spotted_bar.bind("<Button-1>", lambda _e: self._hide_spotted_bar())
        # internet down: a red bar under the top bar while it lasts
        self.net_bar = tk.Label(header, text="", anchor="w", justify="left",
                                bg="#c62828", fg="white", padx=12, pady=4,
                                font=(theme.FONT, 11, "bold"))
        self._spotted_after = None
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
        # clean layout: one sentence when something is wrong (click = details)
        self.problem_label = tk.Label(self.lights_frame, text="", bg=theme.BG,
                                      font=(theme.FONT, 9, "bold"), cursor="hand2")
        self.problem_label.bind("<Button-1>", lambda _e: self._toggle_check_details())
        self._problem_added = False
        self._lights_last: list | None = None
        self._restart_btn: tk.Button | None = None
        # Session state must remain visible when clean layout hides _info.
        self.session_label = self._wrap_label(header, "", muted=True)
        self.session_label.pack_forget()
        info = ttk.Frame(header, padding=(12, 2, 12, 2))
        info.pack(fill="x")
        self._info = info
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
        for col in ("time", "freq", "snr", "mult"):
            btn = flow.add(ttk.Button(
                flow, text=self._sort_labels[col], style="Sort.TButton", width=self.BTN_W,
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
        self._src_buttons = {}
        # the other spot connections, on the first row
        cn = ttk.Frame(flow)
        for key, label in (("mycall", "MyCall"), ("relay", "Relay")):
            b = ttk.Button(cn, text=label, style="Sort.TButton", takefocus=False,
                           width=self.BTN_W, command=lambda k=key: self._set_source(k))
            b.pack(side="left")
            self._src_buttons[key] = b
            self._source_hover(b, key)
        flow.add(cn, gap=16)
        # where the spots come from and which spotters: menus Spots / Spotters;
        # here only one short line saying what is in use
        src = ttk.Frame(flow)
        self._src_sum = ttk.Label(src, text="", style="Muted.TLabel", cursor="hand2")
        self._src_sum.pack(side="left")
        self._src_sum.bind("<Button-1>", lambda _e: self._show_connections())
        self._src_note = ttk.Label(src, text="", style="Muted.TLabel")
        self._src_note.pack(side="left", padx=(6, 0))
        self._src_box = src
        flow.add(src, gap=16)
        self._src_last = None
        self._flt_last = None
        # clean layout: the rate, short (the full rate line in the detailed layout)
        self.rate_short = ttk.Label(flow, text="", style="Muted.TLabel")

        # (the options and actions are in the menu bar: File / View /
        # Settings / Help)
        # SO2R: which radio (N1MM Entry Window A / B) clicks, F-keys and the
        # band follow go to; with one radio only A is used.
        # It sits in the lights row, right after the Update light (v0.7.180) -
        # the sort row keeps its room for the band numbers. Blue = A, orange
        # = B, like the phone page's key. Added there in _update_lights.
        self.radio_btn = tk.Label(self.lights_frame, text=self._radio_btn_text(),
                                  font=(theme.FONT, 10, "bold"), padx=8, pady=1,
                                  fg="#ffffff", cursor="hand2")
        self.radio_btn.bind("<Button-1>", lambda _e: self._toggle_radio())
        self._radio_btn_added = False
        self._paint_radio_btn()

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
        # clean layout: readable rows (call big, colour only where it means something)
        self.rows = RowList(tree_frame, on_tap=self._tile_tap, on_sort=self._on_sort)
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
        self.tree.tag_configure("offline", foreground=theme.T_WORKED)   # internet down: old
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
        # red line when something failed (v0.7.176): the refresh, a button,
        # reading the N1MM log - until now only pouncer.log knew. Click =
        # open pouncer.log, and the line goes away.
        self.err_label = tk.Label(bottom, text="", anchor="w", justify="left", cursor="hand2",
                                  bg="#c62828", fg="white", padx=6,
                                  font=(theme.FONT, 9, "bold"))
        self.err_label.bind("<Button-1>", lambda _e: self._open_error_log())
        self._err = None
        self.status_label = self._wrap_label(bottom, "", bg=theme.BAR_BG)
        self.web_label = self._wrap_label(bottom, "Phone view: starting...", bg=theme.BAR_BG,
                                          muted=True)
        self._apply_layout()

    def _build_menu(self) -> None:
        """Menu bar (v0.7.189), grouped by what you do:
        File (program) / N1MM / View (the window) / Spots (sources, Hide worked,
        Clear) / Spotters / Spotted (one click in the contest) / Tools (pages and
        windows) / Team / Settings... / Help. Sort, A / B and the F-keys stay
        buttons: they're used all contest long."""
        bar = tk.Menu(self.root)
        mac = sys.platform == "darwin"
        # -- File: the program itself ----------------------------------------------
        file = tk.Menu(bar, tearoff=False, postcommand=self._refresh_menu_states)
        file.add_command(label="Open the config folder", command=self._open_config_folder)
        auto = tk.Menu(file, tearoff=False, postcommand=self._refresh_menu_states)
        auto.add_command(label="Start autostart programs now", command=self._start_autostart)
        auto.add_command(label="Don't wait for N1MM (start the rest now)",
                         command=self._stop_waiting_n1mm)
        auto.add_command(label="Open the autostart folder",
                         command=lambda: self._open_path(self._autostart_folder()))
        file.add_cascade(label="Autostart", menu=auto)
        self._auto_menu = auto
        file.add_separator()
        file.add_command(label="Allow POUNCer in Windows Firewall\u2026", command=self._allow_firewall,
                         state="normal" if sys.platform == "win32" else "disabled")
        self._fw_menu = (file, file.index("end"))
        file.add_separator()
        file.add_command(label="Restart POUNCer", command=self._restart_now,
                         state="normal" if self.updater.exe else "disabled")
        file.add_command(label="Exit", command=self._shutdown_and_quit,
                         accelerator="Cmd+Q" if mac else "")
        bar.add_cascade(label="File", menu=file)
        # -- N1MM: how POUNCer works with N1MM -----------------------------------------
        n1mm = tk.Menu(bar, tearoff=False)
        n1mm.add_command(label="Reconnect log", command=self._reconnect_log)
        n1mm.add_checkbutton(label="Also enter callsign in N1MM", variable=self.set_call_var)
        n1mm.add_separator()
        n1mm.add_command(label="Station test (QSY + callsign, each radio)",
                         command=self._station_test)
        bar.add_cascade(label="N1MM", menu=n1mm)
        # -- View: this window ----------------------------------------------------------
        view = tk.Menu(bar, tearoff=False)
        view.add_checkbutton(label="Compact window (beside N1MM)", variable=self.compact_var,
                             command=self._apply_compact, accelerator="Ctrl+M")
        view.add_checkbutton(label="Always on top", variable=self.topmost_var,
                             command=self._apply_topmost)
        view.add_separator()
        view.add_checkbutton(label="Detailed layout (every status line, the old table)",
                             variable=self.detailed_var, command=self._toggle_layout)
        view.add_checkbutton(label="Spot list as tiles (like the phone)",
                             variable=self.tiles_var, accelerator="Ctrl+T",
                             command=lambda: self._toggle_tiles(flip=False))
        self.rate_var = tk.BooleanVar(value=bool(getattr(self.cfg, "rate_line", True)))
        view.add_checkbutton(label="Rate / QSOs / mults per band line", variable=self.rate_var,
                             command=self._toggle_rate_line)
        view.add_separator()
        looks = tk.Menu(view, tearoff=False)
        self.look_var = tk.StringVar(value=getattr(self.cfg, "look", "classic"))
        for value, text in (("dark", "Dark (Windows 11 style)"), ("light", "Light (Windows 11 style)"),
                            ("classic", "Classic")):
            looks.add_radiobutton(label=text, value=value, variable=self.look_var,
                                  command=self._set_look)
        view.add_cascade(label="Look (from the next start)", menu=looks)
        bar.add_cascade(label="View", menu=view)
        # -- Spots: where they come from (each on / off), the list's own switches.
        # The first entries are relabelled by position (SPOT_MENU): add after them.
        spots = tk.Menu(bar, tearoff=False, postcommand=self._refresh_spot_menus)
        self._src_vars = {}
        for key in ("rbn", "telnet", "http", "cluster"):
            v = tk.BooleanVar(value=has_source(self.cfg, key))
            self._src_vars[key] = v
            spots.add_checkbutton(label=key, variable=v,
                                  command=lambda k=key: self._menu_source(k))
        spots.add_separator()
        for key in ("telnetbck", "hamqth"):
            v = tk.BooleanVar(value=bool(getattr(self.cfg, self.CONN_KEYS[key][1], False)))
            self._src_vars[key] = v
            spots.add_checkbutton(label=key, variable=v,
                                  command=lambda k=key: self._menu_source(k))
        spots.add_separator()
        spots.add_checkbutton(label="Hide worked (same band + mode)", accelerator="Ctrl+H",
                              variable=self.hide_worked_var, command=self._on_toggle_worked)
        spots.add_command(label="Clear spots", command=self._clear_spots, accelerator="Ctrl+L")
        spots.add_separator()
        spots.add_command(label="The connections now\u2026", command=self._show_connections)
        bar.add_cascade(label="Spots", menu=spots)
        self._spots_menu = spots
        # -- Spotters: the one filter for every source -------------------------------------
        spotters = tk.Menu(bar, tearoff=False, postcommand=self._refresh_spot_menus)
        self._flt_var = tk.StringVar(value=getattr(self.cfg, "filter_mode", "calls"))
        for key in ("calls", "dxcc", "zones", "cont", "ww"):
            spotters.add_radiobutton(label=key, value=key, variable=self._flt_var,
                                     command=lambda k=key: self._menu_filter(k))
        spotters.add_separator()
        spotters.add_command(label="Edit the lists\u2026 (Settings > Spots)",
                             command=lambda: self._settings_win.open("Spots", "Spotters - who heard it"))
        bar.add_cascade(label="Spotters", menu=spotters)
        self._spotters_menu = spotters
        # -- Spotted: which spotted stations (the DX) - separate from Spotters ---------------
        spotted = tk.Menu(bar, tearoff=False, postcommand=self._refresh_spot_menus)
        self._spd_var = tk.StringVar(value=getattr(self.cfg, "spotted_mode", "ww"))
        for key in ("calls", "dxcc", "zones", "cont", "ww"):
            spotted.add_radiobutton(label=key, value=key, variable=self._spd_var,
                                    command=lambda k=key: self._menu_spotted(k))
        spotted.add_separator()
        spotted.add_command(label="Edit the lists\u2026 (Settings > Spots)",
                            command=lambda: self._settings_win.open("Spots", "Spotted - who was heard"))
        bar.add_cascade(label="Spotted", menu=spotted)
        self._spotted_menu = spotted
        # -- Tools: pages and windows ---------------------------------------------------------
        tools = tk.Menu(bar, tearoff=False)
        tools.add_command(label="Contest summary", command=lambda: self._open_browser("summary"))
        tools.add_command(label="CQ zones: who hears me", command=lambda: self._zones_win.open())
        tools.add_command(label="Band opening timeline", command=lambda: self._timeline_win.open())
        tools.add_separator()
        phone = tk.Menu(tools, tearoff=False)
        phone.add_command(label="In the browser", command=self._open_browser)
        phone.add_command(label="QR code\u2026 (scan with the phone)", command=self._show_phone_qr)
        phone.add_command(label="As an app window", command=self._open_app_window)
        tools.add_cascade(label="Phone page", menu=phone)
        # history hint + new mult alert: pop-up and sound, each on its own
        self.alert_popup_var = tk.BooleanVar(value=bool(self.cfg.alert_popup))
        self.alert_sound_var = tk.BooleanVar(value=bool(self.cfg.alert_sound))
        alerts = tk.Menu(tools, tearoff=False)
        alerts.add_checkbutton(label="Pop-up (history hint + new mult)",
                               variable=self.alert_popup_var,
                               command=lambda: self._set_alert("popup"))
        alerts.add_checkbutton(label="Sound", variable=self.alert_sound_var,
                               command=lambda: self._set_alert("sound"))
        tools.add_cascade(label="Alerts", menu=alerts)
        tools.add_separator()
        tools.add_command(label="Traffic console (raw data in / out)", accelerator="Ctrl+Shift+D",
                          command=lambda: self._console.open())
        bar.add_cascade(label="Tools", menu=tools)
        # -- Team: POUNCers on several PCs ----------------------------------------------------
        team = tk.Menu(bar, tearoff=False, postcommand=self._refresh_menu_states)
        team.add_command(label=self._team_pw_label(), command=self._team_password)
        self._pw_menu = (team, team.index("end"))
        team.add_separator()
        team.add_command(label="Become team master (take it back)", command=self._become_master)
        self.team_master_var = tk.BooleanVar(value=bool(getattr(self.cfg, "team_master", False)))
        team.add_checkbutton(label="This PC is the team master (take it back after a restart)",
                             variable=self.team_master_var, command=self._toggle_team_master)
        team.add_separator()
        team.add_command(label="Update all POUNCers in the team (from the master)",
                         command=self._update_team)
        bar.add_cascade(label="Team", menu=team)
        self._team_menu = team
        # -- Settings: on the bar (Windows); on the Mac in the POUNCer menu (Cmd+,) -------------
        if mac:
            app_menu = tk.Menu(bar, name="apple", tearoff=False)
            app_menu.add_command(label=f"About POUNCer {__version__}", command=self._about)
            bar.add_cascade(menu=app_menu)
            try:
                self.root.createcommand("tk::mac::ShowPreferences", self._settings_win.open)
            except tk.TclError:
                pass
            # the pages also in the POUNCer menu
            app_menu.add_separator()
            for page, _parts in settingsmod.PAGES:
                app_menu.add_command(label=f"Settings: {page}\u2026",
                                     command=lambda p=page: self._settings_win.open(p))
        else:
            # Settings: all of it (Ctrl+,) or straight to one page
            setm = tk.Menu(bar, tearoff=False)
            setm.add_command(label="All settings\u2026", accelerator="Ctrl+,",
                             command=self._settings_win.open)
            setm.add_separator()
            for page, _parts in settingsmod.PAGES:
                setm.add_command(label=f"{page}\u2026",
                                 command=lambda p=page: self._settings_win.open(p))
            bar.add_cascade(label="Settings", menu=setm)
            self._settings_menu = setm
        # -- Help --------------------------------------------------------------------------------
        helpm = tk.Menu(bar, tearoff=False, postcommand=self._refresh_menu_states)
        helpm.add_command(label="What every button does", command=lambda: self._open_browser("help"))
        helpm.add_command(label="POUNCer on GitHub", command=lambda: self._open_url(GITHUB_URL))
        helpm.add_separator()
        versions = tk.Menu(helpm, tearoff=False)
        versions.add_command(label="Check for a new version", command=self._on_update_click)
        versions.add_command(label="Go back to the previous version", command=self._rollback,
                             state="normal" if self.updater.exe else "disabled")
        versions.add_command(label="Switch version\u2026 (any released version)",
                             command=self._switch_version,
                             state="normal" if self.updater.exe else "disabled")
        helpm.add_cascade(label="Versions", menu=versions)
        trouble = tk.Menu(helpm, tearoff=False)
        trouble.add_command(label="Save diagnostics (zip)", command=self._save_diagnostics)
        trouble.add_command(label="Open the log file", command=lambda: self._open_path(
            diagmod.log_path(self.cfg.run_history_dir or "cache")))
        trouble.add_command(label="Open the performance log (CPU / memory / disk)",
                            command=lambda: self._open_path(self.perf.path))
        helpm.add_cascade(label="Troubleshooting", menu=trouble)
        helpm.add_separator()
        helpm.add_command(label=f"About POUNCer {__version__}", command=self._about)
        bar.add_cascade(label="Help", menu=helpm)
        self.root.configure(menu=bar)
        self.menubar = bar
        # shortcuts (never while typing in a box - Ctrl+H is backspace there)
        for seq, fn in (("<Control-h>", self._key_hide_worked), ("<Control-H>", self._key_hide_worked),
                        ("<Control-l>", lambda: self._clear_spots()),
                        ("<Control-L>", lambda: self._clear_spots()),
                        ("<Control-comma>", self._settings_win.open)):
            self.root.bind_all(seq, lambda e, f=fn: self._menu_key(e, f))

    @staticmethod
    def _menu_key(event, fn) -> str | None:
        """A menu shortcut - but not while typing in an entry / text box."""
        try:
            if event.widget.winfo_class() in ("Entry", "TEntry", "Text", "Spinbox", "TSpinbox",
                                              "TCombobox"):
                return None
        except (AttributeError, tk.TclError):
            pass
        fn()
        return "break"

    def _key_hide_worked(self) -> None:
        """Ctrl+H: Hide worked on / off (as Spots > Hide worked)."""
        self.hide_worked_var.set(not self.hide_worked_var.get())
        self._on_toggle_worked()

    def _refresh_menu_states(self) -> None:
        """Before a menu opens: grey out what can't do anything now."""
        def state(menu, label, on):
            if menu is None:
                return
            try:
                for i in range(menu.index("end") + 1):
                    if menu.type(i) != "separator" and menu.entrycget(i, "label").startswith(label):
                        menu.entryconfigure(i, state="normal" if on else "disabled")
            except (tk.TclError, TypeError):
                pass
        team = getattr(self, "team", None)
        role = team.role() if team is not None and hasattr(team, "role") else "off"
        tm = getattr(self, "_team_menu", None)
        state(tm, "Become team master", team is not None and role != "master")
        state(tm, "This PC is the team master", team is not None)
        state(tm, "Update all POUNCers", role == "master" and bool(self.updater.exe))
        state(getattr(self, "_auto_menu", None), "Don't wait for N1MM",
              getattr(self, "_n1mm_wait_rest", None) is not None)

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
            self._show_tiles()                     # the compact window lists in the table
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
            self._show_tiles()
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

    # ---- history hint + new mult alert (window) --------------------------------
    ALERT_MS = 8000

    def _set_alert(self, which: str) -> None:
        """Tools > Alerts > Pop-up / Alert sound: used at once, saved in config.ini."""
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
        except tk.TclError as exc:
            log.warning("alert pop-up could not be made: %s", exc)
            self._alert_pop = None

    def _check_alerts(self, candidates: list) -> None:
        """Rows that are a rare CQ and a new mult now: alert the new ones. A
        spot PSK Reporter is still asked about (7074 - 7077: FT8 or SSB?) waits
        ALERT_HOLD_S - an FT8 station shouldn't pop up before the answer."""
        since = getattr(self, "_overlap_since", {})
        keys = getattr(self, "_overlap_keys", set())
        now = time.time()
        # (a CW spot asked about isn't held: a CW mult pops up at once)
        candidates = [s for s in candidates
                      if not (s.key in keys and s.key in since
                              and now - since[s.key] < self.ALERT_HOLD_S)]
        fresh = self._alerts.fresh([(self._row_key(s), s) for s in candidates])
        if not fresh:
            return
        s = fresh[0]
        more = f"  (+{len(fresh) - 1} more)" if len(fresh) > 1 else ""
        log.info("alert: history hint + new mult %s %.1f%s", s.dxcall, s.freq_khz, more)
        if self.cfg.alert_popup:
            self._show_alert(s, f"\u26a1 history hint + new mult: {s.dxcall}  {s.freq_khz:.1f}{more}"
                                "\nclick = QSY and call")
        if self.cfg.alert_sound:
            alert_beep(self.root)

    def _is_my_call(self, spot) -> bool:
        me = self.own_call() if hasattr(self, "own_call") else ""
        return bool(me) and alert_base_call(spot.dxcall) == alert_base_call(me)

    def _note_self_spot(self, dxcall: str, spotter: str, freq_khz: float,
                        comment: str = "", from_team: bool = False) -> None:
        """Any thread: a spot of MY call by a person (not a skimmer). The
        master passes it on to the team; a slave takes the master's word for
        whose call it is (its own N1MM may name another station call)."""
        if not getattr(self.cfg, "self_spot_alert", True):
            return
        me = dxcall if from_team else self.own_call()
        if me and alert_base_call(dxcall) == alert_base_call(me):
            log.info("spot of my call: %s by %s on %.1f", dxcall, spotter, float(freq_khz or 0))
        if self._self_spots.check(dxcall, spotter, freq_khz, me):
            self._self_spot_q.put((dxcall, spotter, float(freq_khz), comment or "",
                                   time.strftime("%H%MZ", time.gmtime())))
            team = getattr(self, "team", None)
            if not from_team and team is not None and team.is_master():
                try:
                    team.send_self({"call": dxcall, "spotter": spotter, "khz": float(freq_khz),
                                    "comment": comment or ""})
                except Exception:  # noqa: BLE001
                    pass

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
            if not self._on_my_band(khz):
                # my call spotted on a band none of this PC's radios is on:
                # log + status line only (the PC on that band pops up)
                log.info("self spot on %s: no pop-up here (radios on %s)", band_from_khz(khz),
                         ", ".join(sorted(self._my_bands())) or "-")
                continue
            # a floating pop-up and a bar in the window, SELF_SPOT_MS each (the
            # status line keeps it)
            self._show_alert(None, text, ms=self.SELF_SPOT_MS)
            self._show_spotted_bar(text.replace("\n", "   "))
            if self.cfg.alert_sound:
                alert_beep(self.root)

    def _my_bands(self) -> set:
        out = set()
        for nr in self.state.radio_numbers():
            snap = self.state.snapshot(nr) or {}
            b = band_from_khz(snap.get("freq_khz")) if snap.get("freq_khz") else None
            if b:
                out.add(b)
        return out

    def _on_my_band(self, khz: float) -> bool:
        """The spot is on a band one of this PC's radios is on - or we don't
        know the radios' bands (then: pop up), or the option is off."""
        if not getattr(self.cfg, "self_spot_band_only", True):
            return True
        bands = self._my_bands()
        return not bands or band_from_khz(khz) in bands

    def _show_spotted_bar(self, text: str) -> None:
        bar = getattr(self, "spotted_bar", None)
        if bar is None:
            return
        try:
            bar.configure(text=text)
            if not bar.winfo_manager():
                bar.pack(fill="x", after=self._top)
            if self._spotted_after is not None:
                self.root.after_cancel(self._spotted_after)
            self._spotted_after = self.root.after(self.SELF_SPOT_MS, self._hide_spotted_bar)
        except tk.TclError as exc:
            log.warning("SPOTTED bar: %s", exc)

    def _hide_spotted_bar(self) -> None:
        self._spotted_after = None
        bar = getattr(self, "spotted_bar", None)
        if bar is not None and bar.winfo_manager():
            bar.pack_forget()
        self._hide_alert()

    def _check_alert_place(self, x: int, y: int) -> None:
        """The pop-up really where it should be? (On one PC it stayed off
        screen, at its hiding place.) If not: put it there again, and log it."""
        pop = getattr(self, "_alert_pop", None)
        if pop is None or not getattr(self, "_alert_shown", False):
            return
        try:
            rx, ry = pop.winfo_rootx(), pop.winfo_rooty()
            if abs(rx - x) > 50 or abs(ry - y) > 50:
                log.warning("alert pop-up is at %d,%d, not %d,%d - placing it again", rx, ry, x, y)
                pop.geometry(f"+{x}+{y}")
                pop.update_idletasks()
                pop.attributes("-topmost", True)
                pop.lift()
                log.info("alert pop-up now at %d,%d", pop.winfo_rootx(), pop.winfo_rooty())
        except tk.TclError as exc:
            log.warning("alert pop-up check: %s", exc)

    ALERT_HOLD_S = 30   # ~two FT8 cycles: PSK Reporter's answer comes first
    SELF_SPOT_MS = 3_000     # the SPOTTED pop-up (and its bar) for 3 s (click closes it sooner)

    def _show_alert(self, spot, text: str, ms: int | None = None) -> None:
        pop = getattr(self, "_alert_pop", None)
        if pop is None:
            log.warning("alert pop-up not shown: it could not be made at start (%s)",
                        text.splitlines()[0])
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
            pop.update_idletasks()                 # move it NOW (Windows applies it at idle)
            pop.attributes("-topmost", True)
            pop.attributes("-alpha", 0.96)
            pop.lift()
            self.root.after(150, lambda: self._check_alert_place(x, y))
            self._alert_shown = True
            log.info("alert pop-up shown at %d,%d (really at %d,%d; %dx%d; window %s at %d,%d "
                     "%dx%d; screen %dx%d)", x, y, pop.winfo_rootx(), pop.winfo_rooty(),
                     w, h, self.root.state(), self.root.winfo_rootx(),
                     self.root.winfo_rooty(), self.root.winfo_width(), self.root.winfo_height(),
                     self.root.winfo_screenwidth(), self.root.winfo_screenheight())
            if self._alert_after is not None:
                self.root.after_cancel(self._alert_after)
            self._alert_after = self.root.after(ms or self.ALERT_MS, self._hide_alert)
        except tk.TclError as exc:
            log.warning("alert pop-up failed: %s", exc)

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
        if s is None and getattr(self, "spotted_bar", None) is not None:
            self._hide_spotted_bar()          # SPOTTED: closing the pop-up closes the bar
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
            x = max(0, min(x, self.root.winfo_screenwidth() - w))
            pop.geometry(f"+{x}+{y}")
            pop.update_idletasks()          # move it NOW (Windows applies it at idle)
            pop.attributes("-topmost", True)
            pop.attributes("-alpha", 0.94)
            pop.lift()
            self._wpm_shown = True
            self.root.after(150, lambda: self._check_wpm_place(x, y))
            if self._wpm_pop_after is not None:
                self.root.after_cancel(self._wpm_pop_after)
            self._wpm_pop_after = self.root.after(1500, self._hide_wpm_popup)
        except tk.TclError:
            pass

    def _check_wpm_place(self, x: int, y: int) -> None:
        """Same check as the alert pop-up: still at its hiding place? Place it again."""
        pop = getattr(self, "_wpm_pop", None)
        if pop is None or not getattr(self, "_wpm_shown", False):
            return
        try:
            rx, ry = pop.winfo_rootx(), pop.winfo_rooty()
            if abs(rx - x) > 50 or abs(ry - y) > 50:
                log.warning("CW speed pop-up is at %d,%d, not %d,%d - placing it again",
                            rx, ry, x, y)
                pop.geometry(f"+{x}+{y}")
                pop.update_idletasks()
                pop.attributes("-topmost", True)
                pop.lift()
        except tk.TclError as exc:
            log.warning("CW speed pop-up check: %s", exc)

    def _hide_wpm_popup(self) -> None:
        self._wpm_pop_after = None
        self._wpm_shown = False
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
            "" if done else "   (File > Autostart > Don't wait for N1MM)"))
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
        """File > Autostart > Don't wait for N1MM: go on now."""
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
        short = getattr(self, "_rate_short_text", None)
        rs = getattr(self, "rate_short", None)
        if short is not None and rs is not None and rs.cget("text") != short:
            rs.configure(text=short)
        if self._rate_busy or time.time() - self._rate_at < self.RATE_EVERY_S:
            return
        self._rate_at = time.time()
        self._rate_busy = True

        def work():
            try:
                summary = self._summary()
                self._rate_text = summarymod.rate_line(summary)
                self._rate_short_text = summarymod.rate_short(summary)
            except Exception as exc:  # noqa: BLE001
                log.debug("rate line: %s", exc)
            finally:
                self._rate_busy = False
        threading.Thread(target=work, name="rate-line", daemon=True).start()

    def _toggle_rate_line(self) -> None:
        on = bool(self.rate_var.get())
        self.cfg.rate_line = on
        if on and not self._clean():
            self.rate_label.pack(fill="x", padx=12, pady=(0, 2), after=self.lights_frame)
        else:
            self.rate_label.pack_forget()
        if on:
            self._rate_at = 0.0
        if getattr(self, "_rate_short_added", False):
            self._flow.set_visible(self.rate_short, on and self._clean())
        self._save_setting("ui.rate_line", "true" if on else "false")

    def _station_test(self) -> None:
        """N1MM > Station test: per radio QSY 0.1 kHz up / back and a test call
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

    def _console_info(self, group: str) -> str:
        """Traffic console hover: what a source is (first line) and the
        settings it runs with - read now, so a change shows at once. Never a
        password."""
        c = self.cfg
        g = getattr
        role = self.team.role() if self.team is not None else "off"
        call = g(c, "rbn_callsign", "") or (self.own_call() if hasattr(self, "own_call") else "") or "(from N1MM)"
        onoff = lambda v: "on" if v else "off"  # noqa: E731
        if group == "RBN":
            return ("RBN - CW skimmer spots from the Reverse Beacon Network (telnet)\n"
                    f"Server: {g(c, 'rbn_host', '')}:{g(c, 'rbn_port', '')}, login {call}\n"
                    f"Spots switched on: {g(c, 'rbn_source', '') or 'none'} (Spots buttons)\n"
                    f"Own skimmer: {g(c, 'rbn_spotter', '') or '-'}; min SNR {g(c, 'min_snr', 0)} dB\n"
                    f"Pause while every radio is on SSB: {onoff(g(c, 'rbn_pause_on_phone', True))}\n"
                    f"Team: {role}" + (" - the master talks to RBN" if role == "slave" else ""))
        if group == "TELNET":
            return ("TELNET - the telnet server's skimmer spots ('Spots: Telnet') and the "
                    "spots-of-my-call feed\n"
                    f"Server: {g(c, 'rbn_backup_host', '')}:{g(c, 'rbn_backup_port', '')} - "
                    f"{'on' if has_source(c, 'telnet') else 'off'}\n"
                    f"My-call feed: {g(c, 'self_spot_host', '')}:{g(c, 'self_spot_port', '')} as "
                    f"{self._self_feed_login() if hasattr(self, '_self_feed_login') else '-'}, "
                    f"filter '{g(c, 'self_spot_filter', '')}'\n"
                    f"SPOTTED pop-up: {onoff(g(c, 'self_spot_alert', True))}")
        if group == "SSB":
            return ("SSB - 'Spots: Cluster': the DX cluster's spots of people (every mode)\n"
                    f"Cluster: {g(c, 'ssb_host', '')}:{g(c, 'ssb_port', '')}, login "
                    f"{g(c, 'ssb_login', '') or call} - "
                    f"{'on' if has_source(c, 'cluster') else 'off'}"
                    f"{' (the same connection as Telnet)' if self._cluster_merged() else ''}\n"
                    f"Spotters: {g(c, 'ssb_spotters', 'EU')}; N1MM's spots instead: "
                    f"{g(c, 'ssb_use_n1mm', 'auto')}")
        if group == "HTTP":
            return ("HTTP - RBN's skimmer spots over HTTP ('Spots: HTTP')\n"
                    f"URL: {g(c, 'rbn_http_backup', '') or '(off)'}")
        if group == "N1MM":
            return ("N1MM - UDP from N1MM Logger+ (Config > Broadcast Data): radio, "
                    "frequency, mode, call, spots\n"
                    f"Listening on: {g(c, 'n1mm_listen_ip', '')}:{g(c, 'n1mm_listen_port', '')} UDP\n"
                    "To N1MM: UDP 13064 (frequency), keys / text to its Entry Window\n"
                    f"Log read: {g(c, 'worked_db', '') and 'set' or 'auto (N1MM database)'}, "
                    f"every {g(c, 'worked_refresh_seconds', 10)} s")
        if group == "TEAM":
            peers = ", ".join(g(c, "team_peers", ()) or ()) or "-"
            return ("TEAM - the other POUNCers on the network (UDP broadcast)\n"
                    f"Team: {onoff(g(c, 'team', True))}, UDP {g(c, 'team_port', 12079)}, role {role}\n"
                    f"Extra peers: {peers}\n"
                    f"Take master back after a restart: {onoff(g(c, 'team_master', False))} "
                    "(click the SLAVE badge: become master now)\n"
                    f"Password: {'set' if g(c, 'team_key', '') else 'off'}")
        if group == "WEB":
            return ("WEB - the phone page (HTTP server; requests from phones / browsers)\n"
                    f"Page: {onoff(g(c, 'web_enabled', True))}, "
                    f"{g(c, 'web_host', '')}:{g(c, 'web_port', 8080)} TCP\n"
                    f"Settings from other devices: {onoff(g(c, 'web_remote_settings', False))}; "
                    f"CW keyboard: {onoff(g(c, 'web_cw_keyboard', True))}; "
                    f"phones may control N1MM: {g(c, 'web_phone_control', 'phone')}")
        if group == "PSKR":
            on = g(c, "ssb_pskreporter", True)
            return ("PSKR - PSK Reporter (MQTT): is a 40 / 160 m spot in the FT8 window "
                    "really FT8?\n"
                    f"Server: mqtt.pskreporter.info:1883, asked: {onoff(on)} ([ssb] pskreporter)\n"
                    "Asked only for an SSB spot on 7074 - 7077 or 1840 - 1843 kHz (FT8 inside "
                    "the SSB segment) with no mode word: that one call is watched for 2 min "
                    "(max 8 at once). A FT8 / FT4 report of him within 3.5 kHz (5 min) = the "
                    "spot becomes FT8 / FT4 and leaves the SSB list; none = stays SSB.\n"
                    "CW: a CW spot in any FT8 / FT4 window (3573, 3575.5, 7047.5, 14074, "
                    "14080, 21074, 21140, 28074, 28180 ... + 3 kHz) is asked the same way: "
                    "FT8 / FT4 report = leaves the CW list; none = real CW, stays (alert not "
                    "delayed).\n"
                    "Never asked - FT8 at once: a cluster spot with no mode word in a window "
                    "below the SSB segment (14074, 21074, 3573 ...), or FT8 / FT4 / DIGI / "
                    "JT65 / PSK ... in the comment.\n"
                    "Your radio on USB at 7074 - 7077: SSB only while an SSB contest is open "
                    "in N1MM; at 14074 etc. never SSB.")
        if group == "BEACON":
            on = g(c, "hide_beacons", True)
            return ("BEACON - beacon spots POUNCer keeps out (v0.7.177)\n"
                    "dropped: one of the 18 NCDXF beacons (4U1UN, W6WX, OH2B, 4X6TU ...) "
                    "within 0.5 kHz of 14100 / 18110 / 21150 / 24930 / 28200 - the same call "
                    "elsewhere is a real station and stays; a spot of type BEACON / NCDXF; "
                    "a call ending in /B. Not in the list, not on port 10000.\n"
                    + ("Hide beacons: on ([rbn] hide_beacons)." if on else
                       "Hide beacons is OFF ([rbn] hide_beacons): beacons are listed and served."))
        if group == "FT8":
            want = ",".join(sorted({m.upper() for m in g(c, "follow_modes", ("CW",))}
                                   & {"FT8", "FT4"}))
            return ("FT8 - FT8 / FT4 spots POUNCer keeps out (v0.7.174)\n"
                    "dropped (RBN / telnet / cluster / HTTP / HamQTH): an FT8 / FT4 spot "
                    "of a feed whose Spots modes don't have it - not in the list, not on "
                    "port 10000.\n"
                    "server: an FT8 / FT4 spot (a cluster spot of a person with FT8 in the "
                    "comment or at 14074 ...) not sent to port 10000.\n"
                    "PSKR: a CW / SSB spot in an FT8 window is held from port 10000 until "
                    "PSK Reporter answers: 'not served' (he is on FT8) or 'served now'.\n"
                    + (f"FT8 / FT4 in the Spots modes ({want}): those ARE served."
                       if want else "Spots modes without FT8 / FT4: none is served."))
        if group == "SERVER":
            dxc = g(self, "_dxc", None)
            return ("SERVER - POUNCer as a DX cluster for N1MM's Telnet window (port 10000)\n"
                    f"Server: {onoff(g(c, 'cluster_server', False))}, TCP {g(c, 'cluster_port', 10000)}"
                    + (f", {dxc.client_count()} client(s)" if dxc is not None else "") + "\n"
                    f"Relay: {onoff(g(c, 'cluster_relay', True))} - on = the spots of the "
                    "switched-on sources go out; the clients' commands go over POUNCer's "
                    "telnet connection (Telnet / Cluster)")
        return ""

    def _console_notes(self) -> dict:
        """Why a source may be quiet, for the traffic console's top bar."""
        notes = {}
        role = self.team.role() if self.team is not None else "off"
        if role == "slave":
            notes["RBN"] = notes["SSB"] = "via the team master"
            notes["TELNET"] = "via the team master"
            if self.team.master_paused():
                notes["RBN"] = "paused - the whole team on SSB"
        elif getattr(self, "_rbn_pause_on", False):
            notes["RBN"] = ("paused - SSB contest" if self._phone_contest()
                            else "paused - every radio on SSB")
        elif not has_source(self.cfg, "rbn"):
            notes["RBN"] = "off - Spots: RBN"
        elif not self._rbn_clients:
            notes["RBN"] = "not connected"
        dxc = getattr(self, "_dxc", None)
        if dxc is None:
            notes["SERVER"] = ("off - Settings > Cluster server" if not getattr(
                self.cfg, "cluster_server", False) else "not running - port busy?")
        elif not dxc.client_count():
            notes["SERVER"] = f"TCP {dxc.port}: no client connected"
        if role != "slave" and not has_source(self.cfg, "telnet"):
            notes["TELNET"] = ("off - Spots: Telnet"
                               + ("; the spots-of-my-call feed is here"
                                  if getattr(self, "_self_feed", None) else ""))
        if role != "slave":
            ssb = (self.ssb_status or "").lower()
            if ssb.startswith("via n1mm"):
                notes["SSB"] = "Cluster spots come from N1MM"
            elif ssb in ("", "off"):
                notes["SSB"] = "off - Spots: Cluster"
        if self._http_feed is None:
            notes["HTTP"] = "off - Spots: HTTP"
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
        if getattr(self, "_self_feed", None) is not None:
            self._self_feed.stop()
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
        """Help > Troubleshooting > Save diagnostics: a zip next to config.ini, folder opened."""
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

    UNSORTED = ("wpm",)     # shown, but sorting by them says nothing

    def _on_sort(self, col: str) -> None:
        if col in self.UNSORTED:
            return
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
        rows = getattr(self, "rows", None)
        if rows is not None:
            rows.set_sort(self.sort_col, self.sort_reverse)

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
            return lambda s: s.received
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
        sl = getattr(self, "status_label", None)
        if sl is not None:
            sl.configure(text=f"{len(self._cleared)} spots cleared (every band)")

    def _clear_band(self) -> None:
        """CLR BAND: forget the spots of the band in the list (the radio's,
        or the band chip clicked); the others stay. Undo brings them back."""
        band = self._view_band_now() if hasattr(self, "_view_band_now") else None
        band = band or self._current_band()
        if not band:
            self.status_label.configure(text="CLR BAND: no band yet (N1MM sends no frequency)")
            return
        gone = {k: s for k, s in self.spots.items() if band_from_khz(s.freq_khz) == band}
        for k in gone:
            del self.spots[k]
        self._cleared = gone
        self._dirty = True
        self.status_label.configure(text=f"{len(gone)} spots of {band} cleared")

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
        self.worked.maybe_reload(wait=True)
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

    SSB_GRACE_S = 60           # SSB cluster stays this long after the last radio left USB/LSB

    def _ssb_wanted(self) -> bool:
        """The 'Cluster' source (spots of people from the DX cluster) is on
        - not on a team slave (the master passes the spots on)."""
        if not has_source(self.cfg, "cluster") or not getattr(self.cfg, "ssb_host", ""):
            return False
        if self.team is not None and (time.time() - self.team.started < 3
                                      or self.team.role() == "slave"):
            return False
        return True

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

    def _upstream_client(self):
        """POUNCer's own telnet connection to the cluster (Telnet, else
        Cluster) - where the port 10000 clients' commands go."""
        for c in (getattr(self, "_telnet_client", None), getattr(self, "_ssb_client", None)):
            if c is not None and c.connected():
                return c
        return None

    def _relay_send(self, line: str) -> str:
        """A port 10000 client's line (DX spot, sh/dx ...) -> over POUNCer's
        telnet connection; '' = sent, else why not."""
        c = self._upstream_client()
        if c is None:
            return "no telnet connection (switch on Telnet or Cluster)"
        return "" if c.send_line(line) else "the telnet connection is down"

    def _relay_back(self, line: str) -> None:
        """A non-spot line from POUNCer's telnet connection (answers,
        announcements) -> the port 10000 clients."""
        dxc = getattr(self, "_dxc", None)
        if dxc is not None:
            dxc.to_clients(line)

    def _on_n1mm_spot(self, pkt: dict, via: str = "n1mm") -> None:
        """A <spot> packet from N1MM (listener thread). Any spot shows N1MM's
        telnet is alive; people's spots go to the list while N1MM (connected
        to the cluster itself) is the Cluster source."""
        dxc = getattr(self, "_dxc", None)
        if via == "n1mm" and dxc is not None:
            if dxc.echo(str(pkt.get("call") or ""), float(pkt.get("freq_khz") or 0)):
                return  # our own spot back from N1MM's telnet (connected to us): not N1MM's
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
        if (spot.spotter or "").endswith("-#"):
            return                       # skimmers come from RBN / Telnet / HTTP
        if self._n1mm_filter is None:
            self._n1mm_filter = PeopleFilter(self.spotter_filter)
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

    def _cluster_merged(self) -> bool:
        """Telnet and Cluster are both on and on the same node: one
        connection (one login) carries both."""
        c = self.cfg
        return (has_source(c, "telnet") and bool(c.rbn_backup_host)
                and (c.rbn_backup_host.lower(), int(c.rbn_backup_port))
                == (str(c.ssb_host).lower(), int(c.ssb_port)))

    def _people_filter(self):
        return PeopleFilter(self.spotter_filter)     # the Spotters filter, live

    def _ensure_ssb_client(self) -> None:
        """The 'Cluster' source: spots of people from the DX cluster - on the
        Telnet connection when that is on the same node, else a connection
        of its own; while N1MM (connected to the cluster itself) sends its
        spots, from N1MM's broadcast instead (no second login)."""
        want = self._ssb_wanted()
        via = want and self._n1mm_spots_ok()
        if via != getattr(self, "_ssb_via_n1mm", False):
            if not via and want:
                self._ssb_back_at = time.time()    # N1MM quiet: own cluster for a while
            self._ssb_via_n1mm = via
            log.info("Cluster spots %s", "from N1MM (own cluster connection closed)" if via
                     else (f"from our own cluster (no spot from N1MM for "
                           f"{time.time() - getattr(self, '_n1mm_spot_last', 0.0):.0f} s)")
                     if want else "off")
        merged = want and not via and self._cluster_merged()
        tc = getattr(self, "_telnet_client", None)
        if tc is not None:
            tc.people = self._people_filter() if merged else None
        client = getattr(self, "_ssb_client", None)
        if client is not None and (not want or via or merged):
            client.stop()
            self._ssb_client = None
            client = None
        if via:
            self.ssb_status = "via N1MM"
            return
        if not want:
            self.ssb_status = "off"
            return
        if merged:
            self.ssb_status = getattr(tc, "status_text", "") or "starting"
            return
        if client is None:
            self._ssb_client = RBNClient(
                self.cfg.ssb_host, self.cfg.ssb_port,
                self.cfg.ssb_login or self.cfg.rbn_callsign,
                self._people_filter(),
                self.spot_queue,
                on_status=self._ssb_status_put,
                history=self.cfg.ssb_history,
                backup_commands=self.cfg.ssb_commands,
                kind="cluster", modes=(), name="cluster",
                on_raw=self._on_raw_spot,
            )
            self._ssb_client.skimmers = False      # people only; skimmers: RBN / Telnet / HTTP
            self._ssb_client.backup_commands = tuple(self.cfg.ssb_commands)
            self._ssb_client.on_line = self._relay_back
            self._ssb_client.start()
            log.info("Cluster client started on %s:%s", self.cfg.ssb_host, self.cfg.ssb_port)

    def _ssb_status_put(self, text: str) -> None:
        self._ssb_status_q.put(text)

    def _rbn_healthy(self) -> bool:
        """The telnet RBN feed (or its telnet backup) is logged in and lines
        are coming (any skimmer, so even a quiet band counts)."""
        client = self._rbn_clients.get(self.cfg.rbn_port)
        if client is None:
            return False
        st = self.rbn_status.lower()
        if not (st.startswith("logged in") or st.startswith("via n1mm")):   # (shared login)
            return False
        return time.monotonic() - getattr(client, "_last_dx", 0) < 120

    def _ensure_http_feed(self) -> None:
        """The 'HTTP' source: the HTTP feed runs while it is switched on
        (not on a team slave, not while RBN is paused for SSB)."""
        url = getattr(self.cfg, "rbn_http_backup", "")
        team = getattr(self, "team", None)
        slave = team is not None and (time.time() - team.started < 3 or team.role() == "slave")
        want = (bool(url) and has_source(self.cfg, "http") and not slave
                and not getattr(self, "_rbn_pause_on", False))
        if not want:
            if self._http_feed is not None:
                log.info("HTTP feed stopped")
            self._stop_http_feed()
            return
        if self._http_feed is None:
            self._start_http_feed(url)
            log.info("HTTP feed %s started", url)

    def _start_http_feed(self, url: str) -> None:
        self._http_feed = RBNHttpFeed(
            url, self.spotter_filter, self.spot_queue,
            on_status=lambda t: log.info("RBN HTTP: %s", t),
            modes=tuple(m for m in self.cfg.follow_modes if m.upper() in ("CW", "RTTY"))
            or ("CW",), on_raw=self._on_raw_spot)
        self._http_feed.began = time.time()
        self._http_feed.start()

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

    def _self_feed_login(self) -> str:
        """<call>-77 ([ssb] self_spot_ssid): its own SSID, so this connection
        never pushes N1MM's login or the RBN backup off the node."""
        base = (self.cfg.rbn_callsign or self.own_call() or "").split("-")[0]
        if not base:
            return ""
        ssid = str(getattr(self.cfg, "self_spot_ssid", "77") or "").strip().lstrip("-")
        return f"{base}-{ssid}" if ssid else base

    SELF_FEED_OK_S = 600       # this long with only my call: "the node filter works"
    SELF_FEED_BAD_N = 20       # this many spots of other calls: it doesn't

    def _on_self_feed_spot(self, s) -> None:
        """A spot on the self-spot feed (its thread): the SPOTTED check, and
        is the node's filter (accept/spots call ...) really applied? Logged
        once: works / doesn't (then POUNCer filters itself - still right,
        just more traffic)."""
        self._note_self_spot(s.dxcall, s.spotter, s.freq_khz)
        c = getattr(self, "_self_feed_check", None)
        if not c or c["said"]:
            return
        me = getattr(getattr(self, "_self_feed", None), "_for_call", "")
        mine = alert_base_call(s.dxcall) == alert_base_call(me)
        c["mine" if mine else "other"] += 1
        if c["other"] >= self.SELF_FEED_BAD_N:
            c["said"] = True
            mins = max(1 / 60, (time.time() - c["t"]) / 60)
            log.warning("self-spot feed: the node does NOT apply the filter - %d spots of other "
                        "calls in %.0f min (about %.0f a minute); POUNCer drops them itself. "
                        "Check [ssb] self_spot_filter for this node.", c["other"], mins,
                        c["other"] / mins)

    def _check_self_feed_filter(self) -> None:
        """From _tick: 10 min on the feed and nothing but my call - say so."""
        c = getattr(self, "_self_feed_check", None)
        if c and not c["said"] and time.time() - c["t"] >= self.SELF_FEED_OK_S:
            c["said"] = True
            log.info("self-spot feed: the node filter works - only spots of my call in %d min "
                     "(%d of them)", self.SELF_FEED_OK_S // 60, c["mine"])

    def _ensure_self_feed(self) -> None:
        """SPOTTED pop-up: N1MM never passes on spots of your own call, so a
        cluster connection of its own with a node filter that lets only spots
        of my call through (DXSpider: accept/spots call S55OO) - nothing else
        arrives on it. [ssb] self_spot_host / port / filter."""
        team = getattr(self, "team", None)
        slave = team is not None and team.role() == "slave"   # the master passes it on
        want = bool(getattr(self.cfg, "self_spot_alert", True) and not slave
                    and getattr(self.cfg, "self_spot_host", "") and self.own_call())
        feed = getattr(self, "_self_feed", None)
        me = self.own_call().upper() if want else ""
        if feed is not None and (not want or getattr(feed, "_for_call", "") != me):
            feed.stop()
            self._self_feed = feed = None
        if feed is not None or not want:
            return
        login = self._self_feed_login()
        cmds = [c.strip().replace("{call}", me) for c in
                (getattr(self.cfg, "self_spot_filter", "") or "").split(";") if c.strip()]

        class _All:            # every spotter (the node filter does the work)
            primary = ""

            @staticmethod
            def accept(_sp):
                return True
        feed = RBNClient(self.cfg.self_spot_host, self.cfg.self_spot_port, login, _All(),
                         queue.Queue(maxsize=50), history=0, backup_commands=cmds,
                         kind="cluster", name="self-spot",
                         on_raw=self._on_self_feed_spot)
        feed.backup_commands = tuple(cmds)
        feed._for_call = me
        self._self_feed_check = {"t": time.time(), "mine": 0, "other": 0, "said": False}
        feed.start()
        self._self_feed = feed
        log.info("self-spot feed: %s:%s as %s, filter %s", self.cfg.self_spot_host,
                 self.cfg.self_spot_port, login, "; ".join(cmds) or "(none)")

    def _ensure_rbn_clients(self) -> None:
        self._ensure_self_feed()
        desired = self._desired_rbn_ports() if has_source(self.cfg, "rbn") else set()
        paused = self._rbn_paused()
        if paused != getattr(self, "_rbn_pause_on", False):
            self._rbn_pause_on = paused
            log.info("RBN %s", ("paused: SSB contest / every radio on SSB" +
                                (", in the whole team" if self.team is not None
                                 and self.team.alive_peers() else ""))
                     if paused else "on: a radio is on CW")
            self.rbn_status = "paused: SSB, not needed" if paused else "starting"
        telnet = has_source(self.cfg, "telnet") and bool(self.cfg.rbn_backup_host)
        if not telnet and desired and self._telnet_backup_due():
            telnet = True                     # TelBck: RBN down, the telnet server meanwhile
        if paused:
            desired, telnet = set(), False
        if self.team is not None:
            # Team: only the master talks to RBN; give discovery 3 s at start
            # so a later POUNCer doesn't log in just to hand over at once.
            if time.time() - self.team.started < 3 or self.team.role() == "slave":
                desired, telnet = set(), False
        for port in [p for p in self._rbn_clients if p not in desired]:
            self._rbn_clients.pop(port).stop()
        self._ensure_telnet_client(telnet)
        self._ensure_ssb_client()
        self._ensure_http_feed()
        self._ensure_hamqth(paused)
        if not paused and not desired and not telnet:
            self.rbn_status = ("standby - the HTTP feed delivers" if self._http_feed is not None
                               or getattr(self.cfg, "rbn_hamqth_backup", False)
                               else "off - no skimmer source on")
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
                on_raw=self._on_raw_spot,
            )
            client.start()
            self._rbn_clients[port] = client
            log.info("RBN client started on %s:%s", self.cfg.rbn_host, port)

    BACKUP_AFTER_S = 60      # TelBck: RBN down / silent this long -> the telnet server
    BACKUP_BACK_S = 60       # ... and RBN working this long -> telnet closed again

    def _telnet_backup_due(self) -> bool:
        """TelBck on, RBN on, Telnet off: RBN down or silent for a minute
        -> True (until RBN has worked for a minute again)."""
        if not (getattr(self.cfg, "rbn_telnet_backup", False) and self.cfg.rbn_backup_host
                and has_source(self.cfg, "rbn")):
            self._rbn_bad_since = self._rbn_good_since = None
            self._bck_active = False
            return False
        now = time.time()
        active = getattr(self, "_bck_active", False)
        if self._rbn_healthy():
            self._rbn_bad_since = None
            self._rbn_good_since = self._rbn_good_since or now
            if active and now - self._rbn_good_since > self.BACKUP_BACK_S:
                active = False
                log.info("TelBck: RBN works again - telnet server closed")
        else:
            self._rbn_good_since = None
            self._rbn_bad_since = self._rbn_bad_since or now
            if not active and now - self._rbn_bad_since > self.BACKUP_AFTER_S:
                active = True
                log.info("TelBck: RBN down / silent - %s:%s meanwhile",
                         self.cfg.rbn_backup_host, self.cfg.rbn_backup_port)
        self._bck_active = active
        return active

    HQ_AFTER_S = 60     # HamQTH: a telnet connection down this long -> HamQTH meanwhile
    HQ_BACK_S = 60      # ... and working this long again -> HamQTH stopped

    def _skimmer_telnet_ok(self) -> bool:
        """A skimmer telnet connection (RBN or Telnet) is logged in and lines
        are coming."""
        now = time.monotonic()
        clients = list(self._rbn_clients.values()) + [getattr(self, "_telnet_client", None)]
        return any(c is not None and c.connected() and now - getattr(c, "_last_dx", 0) < 120
                   for c in clients)

    def _cluster_telnet_ok(self) -> bool:
        if getattr(self, "_ssb_via_n1mm", False):
            return True
        c = getattr(self, "_telnet_client", None) if self._cluster_merged() else \
            getattr(self, "_ssb_client", None)
        return c is not None and c.connected()

    def _hq_state(self, kind: str, need: bool, ok: bool) -> bool:
        """Down HQ_AFTER_S -> True, until working HQ_BACK_S again."""
        st = self.__dict__.setdefault("_hq_st", {}).setdefault(
            kind, {"bad": None, "good": None, "on": False})
        now = time.time()
        if not need:
            st.update(bad=None, good=None, on=False)
            return False
        if ok:
            st["bad"] = None
            st["good"] = st["good"] or now
            if st["on"] and now - st["good"] > self.HQ_BACK_S:
                st["on"] = False
        else:
            st["good"] = None
            st["bad"] = st["bad"] or now
            if not st["on"] and now - st["bad"] > self.HQ_AFTER_S:
                st["on"] = True
        return st["on"]

    def _ensure_hamqth(self, paused: bool = False) -> None:
        """The HamQTH button: HamQTH.com's RBN API while the skimmer telnet
        connections are down, its DX cluster API while the Cluster one is."""
        c = self.cfg
        team = getattr(self, "team", None)
        slave = team is not None and (time.time() - team.started < 3 or team.role() == "slave")
        on = bool(getattr(c, "rbn_hamqth_backup", False)) and not slave
        telnet_rbn = has_source(c, "rbn") or has_source(c, "telnet")
        telnet_cl = self._ssb_wanted()
        # with its telnet source on: only while that one is down (a minute);
        # with it off: HamQTH is the source itself, at once
        need = {"rbn": on and not paused and telnet_rbn,
                "spots": on and telnet_cl}
        ok = {"rbn": self._skimmer_telnet_ok() if need["rbn"] else True,
              "spots": self._cluster_telnet_ok() if need["spots"] else True}
        alone = {"rbn": on and not paused and not telnet_rbn, "spots": on and not telnet_cl}
        feeds = self.__dict__.setdefault("_hq_feeds", {"rbn": None, "spots": None})
        for kind in ("rbn", "spots"):
            want = self._hq_state(kind, need[kind], ok[kind]) or alone[kind]
            f = feeds.get(kind)
            if f is not None and not want:
                f.stop()
                feeds[kind] = None
                log.info("HamQTH %s stopped (telnet works again / switched off)", kind)
            elif f is None and want:
                filt = self.spotter_filter if kind == "rbn" else self._people_filter()
                f = HamQTHFeed(kind, filt, self.spot_queue, modes=tuple(c.follow_modes),
                               on_raw=self._on_raw_spot, version=__version__)
                f.start()
                feeds[kind] = f
                log.info("HamQTH %s started: the telnet connection is down (data provided by "
                         "HamQTH.com)", kind)

    def _ensure_telnet_client(self, want: bool) -> None:
        """The 'Telnet' source: the skimmer spots of the telnet server
        (backup_host, azure.s53m.com:8000) - and its spots of people when
        'Cluster' is on too (same node, one login)."""
        tc = getattr(self, "_telnet_client", None)
        if not want:
            if tc is not None:
                tc.stop()
                self._telnet_client = None
                log.info("Telnet client stopped")
            return
        if tc is not None:
            return

        def status(text, self=self):
            if not has_source(self.cfg, "rbn"):
                self.status_queue.put(text)       # the RBN light shows this connection
            if self._cluster_merged() and self._ssb_wanted():
                self._ssb_status_put(text)
        tc = RBNClient(self.cfg.rbn_backup_host, self.cfg.rbn_backup_port, self.cfg.rbn_callsign,
                       self.spotter_filter, self.spot_queue, on_status=status,
                       history=self.cfg.rbn_history, name="telnet",
                       backup_commands=self.cfg.rbn_backup_commands, on_raw=self._on_raw_spot)
        tc.backup_commands = tuple(self.cfg.rbn_backup_commands)
        tc.on_line = self._relay_back
        tc.start()
        self._telnet_client = tc
        log.info("Telnet client started on %s:%s", self.cfg.rbn_backup_host,
                 self.cfg.rbn_backup_port)

    FILTER_NAMES = {"calls": "Calls", "dxcc": "DXCC", "zones": "Zones", "cont": "Cont",
                    "ww": "WW"}
    BTN_W = 7          # every Sort / Spots / Spotters button: room for six letters (HamQTH)

    @staticmethod
    def _list(text: str) -> list[str]:
        return [t.strip() for t in str(text or "").replace(";", ",").split(",") if t.strip()]

    def _apply_filter(self, log_it: bool = True) -> None:
        """The Spotters filter ([filter] mode + its list) -> the one
        SpotterFilter every source uses (skimmers and people)."""
        from .rbn import parse_spotters
        c = self.cfg
        calls, zones_in_calls = parse_spotters(getattr(c, "rbn_spotter", ""))
        zones = set(zones_in_calls)
        for t in self._list(getattr(c, "filter_zones", "")):
            digits = "".join(ch for ch in t if ch.isdigit())
            if digits and 1 <= int(digits) <= 40:
                zones.add(int(digits))
        conts = [t.upper() for t in self._list(getattr(c, "filter_conts", ""))]
        self.spotter_filter.configure(getattr(c, "filter_mode", "calls"), calls=calls,
                                      dxcc=self._list(getattr(c, "filter_dxcc", "")),
                                      zones=sorted(zones), conts=conts)
        sw = getattr(self, "skimwatch", None)
        if sw is not None:
            sw.calls = tuple(self.spotter_filter.calls)
        self._flt_last = None
        if log_it:
            log.info("spotters filter: %s", self.spotter_filter.label() or "all")

    def _apply_spotted(self, log_it: bool = True) -> None:
        """The Spotted filter ([spotted]) -> which spotted stations are kept."""
        c = self.cfg
        zones = []
        for t in self._list(getattr(c, "spotted_zones", "")):
            digits = "".join(ch for ch in t if ch.isdigit())
            if digits and 1 <= int(digits) <= 40:
                zones.append(int(digits))
        self.spotted_filter.configure(
            getattr(c, "spotted_mode", "ww"), calls=self._list(getattr(c, "spotted_calls", "")),
            dxcc=self._list(getattr(c, "spotted_dxcc", "")), zones=zones,
            conts=self._list(getattr(c, "spotted_conts", "")))
        self._src_last = None
        if log_it:
            log.info("spotted filter: %s", self.spotted_filter.label())

    def _set_spotted_filter(self, mode: str) -> None:
        """Spotted menu: keep spots of these stations (saved)."""
        if mode not in self.FILTER_NAMES:
            return
        lists = {"calls": "spotted_calls", "dxcc": "spotted_dxcc", "zones": "spotted_zones",
                 "cont": "spotted_conts"}
        if mode in lists and not self._list(getattr(self.cfg, lists[mode], "")):
            self.status_label.configure(text=f"Spotted {self.FILTER_NAMES[mode]}: the list is "
                                             "empty - fill it in Settings > Spots > Spotted")
            return
        self._save_setting("spotted.mode", mode)      # applies it (_apply_spotted)
        self.status_label.configure(text=f"Spots of: {self.spotted_filter.label()}")

    def _menu_spotted(self, mode: str) -> None:
        self._set_spotted_filter(mode)
        self._refresh_spot_menus()

    def _set_spotter_filter(self, mode: str) -> None:
        """A Spotters button: that filter from now on (saved)."""
        if mode not in self.FILTER_NAMES:
            return
        empty = {"calls": "rbn_spotter", "dxcc": "filter_dxcc", "zones": "filter_zones",
                 "cont": "filter_conts"}
        if mode in empty and not self._list(getattr(self.cfg, empty[mode], "")):
            self.status_label.configure(text=f"Spotters {self.FILTER_NAMES[mode]}: the list is "
                                             "empty - fill it in Settings > Spots > Spotters")
            return
        self._save_setting("filter.mode", mode)      # applies it (_apply_filter)
        self.status_label.configure(text=f"Spots from: {self.spotter_filter.label()}")

    def _update_filter_buttons(self) -> None:
        btns = getattr(self, "_flt_buttons", None)
        if not btns:
            return
        mode = getattr(self.cfg, "filter_mode", "calls")
        if mode == getattr(self, "_flt_last", None):
            return
        self._flt_last = mode
        for key, b in btns.items():
            b.configure(style="SortOn.TButton" if key == mode else "Sort.TButton")

    def _filter_tip(self, mode: str) -> str:
        c = self.cfg
        on = getattr(c, "filter_mode", "calls") == mode
        lists = {"calls": getattr(c, "rbn_spotter", ""), "dxcc": getattr(c, "filter_dxcc", ""),
                 "zones": getattr(c, "filter_zones", ""), "cont": getattr(c, "filter_conts", "")}
        what = {"calls": "only these skimmers / spotters (prefix); while all are quiet: the "
                         "other skimmers on your continent; people's spots: these calls' continent",
                "dxcc": "spotters in these countries", "zones": "spotters in these CQ zones",
                "cont": "spotters on these continents", "ww": "every spotter, worldwide"}[mode]
        head = f"Spotters: {self.FILTER_NAMES[mode]} - {'ON' if on else 'click = use this'}"
        body = f"{lists.get(mode) or ('-' if mode != 'ww' else 'all')}\n{what}\n" \
               "For every source: RBN, Telnet, HTTP, HamQTH, Cluster. Lists: Settings > Spots > Spotters"
        if on and mode != "ww":
            sf = self.spotter_filter
            body += "\nNow: " + ("backup skimmers (the listed ones are quiet)"
                                  if sf.backup_active() else "the listed ones")
        return head + "\n" + body

    SOURCE_NAMES = {"rbn": "RBN", "telnet": "Telnet", "http": "HTTP", "cluster": "Cluster"}

    def _set_source(self, src: str, from_team: bool = False) -> None:
        """A Spots button: switch that source on / off. A team slave asks the
        master (only it is connected); otherwise switch now, save."""
        if src in self.CONN_KEYS:
            self._toggle_conn(src)
            return
        if src not in self.SOURCE_NAMES:
            return
        team = getattr(self, "team", None)
        if team is not None and not from_team and team.alive_peers() and not team.is_master():
            team.send_src(src)
            self.status_label.configure(
                text=f"Asked the team master to switch {self.SOURCE_NAMES[src]} on / off")
            return
        need = {"telnet": ("rbn_backup_host", "No telnet server set ([rbn] backup_host)"),
                "http": ("rbn_http_backup", "No HTTP feed set ([rbn] http_backup)"),
                "cluster": ("ssb_host", "No DX cluster set ([ssb] host)")}
        cur = set(filter(None, str(self.cfg.rbn_source or "").split(",")))
        if src not in cur and src in need and not getattr(self.cfg, need[src][0], ""):
            self.status_label.configure(text=need[src][1])
            return
        cur ^= {src}
        value = parse_sources(",".join(cur))
        self._save_setting("rbn.sources", value)     # applies it too (_apply_source)
        names = " + ".join(self.SOURCE_NAMES[x] for x in value.split(",") if x) or "nothing"
        self.status_label.configure(text=f"Spots come in from: {names}"
                                         + (" (asked by a team member)" if from_team else ""))
        self._src_last = None

    # the other spot connections on the Spots line: own settings, this PC only
    CONN_KEYS = {"telnetbck": ("rbn.telnet_backup", "rbn_telnet_backup", "TelBck"),
                 "hamqth": ("rbn.hamqth_backup", "rbn_hamqth_backup", "HamQTH"),
                 "mycall": ("ui.self_spot_alert", "self_spot_alert", "MyCall"),
                 "relay": ("cluster.relay", "cluster_relay", "Relay")}

    def _toggle_conn(self, key: str) -> None:
        """MyCall (the spots-of-my-call connection + SPOTTED pop-up) / Relay
        (N1MM's cluster through POUNCer): on / off, saved."""
        setting, attr, name = self.CONN_KEYS[key]
        new = not bool(getattr(self.cfg, attr, False))
        self._save_setting(setting, new)
        extra = ""
        if key == "relay" and new and not getattr(self.cfg, "cluster_server", False):
            extra = " (the cluster server is off: Settings > Cluster server)"
        self.status_label.configure(text=f"{name} {'on' if new else 'off'}{extra}")
        self._src_last = None

    def _apply_source(self) -> None:
        """The sources changed: start / stop the connections now."""
        self._src_last = None
        if hasattr(self, "_rbn_clients"):
            self._ensure_rbn_clients()

    def _update_source_buttons(self) -> None:
        """Each source that is on filled blue; in a team slave the master's
        choice."""
        box = getattr(self, "_src_box", None)
        if box is None:
            return
        team = getattr(self, "team", None)
        slave = team is not None and team.alive_peers() and not team.is_master()
        chosen = (team.master_src() or self.cfg.rbn_source) if slave else self.cfg.rbn_source
        on = set(filter(None, str(chosen or "").split(",")))
        note = "master's choice" if slave else (
            "nothing on - no spots" if not on and not getattr(self.cfg, "rbn_hamqth_backup", False)
            else "")
        if not slave and getattr(self, "_bck_active", False):
            note = "TelBck: RBN down, telnet server meanwhile"
        hq = [n for n, k in (("skimmers", "rbn"), ("cluster", "spots"))
              if getattr(self, "_hq_feeds", {}).get(k) is not None]
        if not slave and hq:
            on_telnet = has_source(self.cfg, "rbn") or has_source(self.cfg, "telnet") or \
                has_source(self.cfg, "cluster")
            note = (f"HamQTH: telnet down - {' + '.join(hq)} from HamQTH.com meanwhile"
                    if on_telnet else f"{' + '.join(hq)} from HamQTH.com")
        for key, (_s, attr, _n) in self.CONN_KEYS.items():
            if getattr(self.cfg, attr, False):
                on.add(key)
        flt = self.spotter_filter.label() or "all"
        spd = self.spotted_filter.label() if getattr(self, "spotted_filter", None) else "all"
        sig = (tuple(sorted(on)), note, flt, spd)
        if sig == self._src_last:
            return
        self._src_last = sig
        for key, b in self._src_buttons.items():
            b.configure(style="SortOn.TButton" if key in on else "Sort.TButton")
        names = [self.SOURCE_NAMES[k] for k in ("rbn", "telnet", "http", "cluster") if k in on]
        extra = [n for k, n in (("telnetbck", "TelBck"), ("hamqth", "HamQTH")) if k in on]
        summ = "Spots: " + (" + ".join(names) or "none")
        if extra:
            summ += " (backup: " + ", ".join(extra) + ")"
        summ += f"   Spotters: {flt}"
        if spd != "all":
            summ += f"   Spotted: {spd}"
        sl = getattr(self, "_src_sum", None)
        if sl is not None:
            sl.configure(text=summ)
        self._src_note.configure(text=note)

    SPOT_MENU = (("rbn", "RBN - {c.rbn_host}:{c.rbn_port}"),
                 ("telnet", "Telnet - {c.rbn_backup_host}:{c.rbn_backup_port} (its skimmer spots)"),
                 ("http", "HTTP - RBN over HTTP (azure)"),
                 ("cluster", "Cluster - spots of people, {c.ssb_host}:{c.ssb_port}"),
                 (None, None),
                 ("telnetbck", "TelBck - the telnet server while RBN is down"),
                 ("hamqth", "HamQTH - its APIs while the telnet is down (or alone)"))

    def _refresh_spot_menus(self) -> None:
        """Before a menu opens: labels with the lists, ticks from the settings."""
        c = self.cfg
        m = getattr(self, "_spots_menu", None)
        if m is not None:
            for idx, (key, text) in enumerate(self.SPOT_MENU):
                if key is None:
                    continue
                try:
                    m.entryconfigure(idx, label=text.format(c=c))
                except (tk.TclError, AttributeError, KeyError):
                    pass
                on = has_source(c, key) if key in self.SOURCE_NAMES else \
                    bool(getattr(c, self.CONN_KEYS[key][1], False))
                self._src_vars[key].set(on)
        m = getattr(self, "_spotters_menu", None)
        if m is not None:
            lists = {"calls": ("Calls", getattr(c, "rbn_spotter", "")),
                     "dxcc": ("DXCC", getattr(c, "filter_dxcc", "")),
                     "zones": ("Zones", getattr(c, "filter_zones", "")),
                     "cont": ("Continents", getattr(c, "filter_conts", "")),
                     "ww": ("WW - everyone", "")}
            for idx, key in enumerate(("calls", "dxcc", "zones", "cont", "ww")):
                name, lst = lists[key]
                text = f"{name}: {lst}" if key != "ww" else name
                if key != "ww" and not str(lst).strip():
                    text = f"{name}: (empty - fill the list in Settings)"
                try:
                    m.entryconfigure(idx, label=text)
                except tk.TclError:
                    pass
            self._flt_var.set(getattr(c, "filter_mode", "calls"))
        m = getattr(self, "_spotted_menu", None)
        if m is not None:
            lists = {"calls": ("Calls", getattr(c, "spotted_calls", "")),
                     "dxcc": ("DXCC", getattr(c, "spotted_dxcc", "")),
                     "zones": ("Zones", getattr(c, "spotted_zones", "")),
                     "cont": ("Continents", getattr(c, "spotted_conts", "")),
                     "ww": ("WW - every station", "")}
            for idx, key in enumerate(("calls", "dxcc", "zones", "cont", "ww")):
                name, lst = lists[key]
                text = f"{name}: {lst}" if key != "ww" else name
                if key != "ww" and not str(lst).strip():
                    text = f"{name}: (empty - fill the list in Settings)"
                try:
                    m.entryconfigure(idx, label=text)
                except tk.TclError:
                    pass
            self._spd_var.set(getattr(c, "spotted_mode", "ww"))

    def _menu_source(self, key: str) -> None:
        self._set_source(key)
        self._refresh_spot_menus()

    def _menu_filter(self, mode: str) -> None:
        self._set_spotter_filter(mode)
        self._refresh_spot_menus()

    def _show_connections(self) -> None:
        """Spots > The connections now: every source's connection and state."""
        text = "\n\n".join(self._source_tip(k) for k in
                            ("rbn", "telnet", "http", "cluster", "telnetbck", "hamqth",
                             "mycall", "relay"))
        text += "\n\nSpotters: " + (self.spotter_filter.label() or "all")
        sf = getattr(self, "spotted_filter", None)
        text += "\nSpotted: " + (sf.label() if sf is not None else "all")
        win = getattr(self, "_conn_win", None)
        try:
            if win is not None and win.winfo_exists():
                win.destroy()
        except tk.TclError:
            pass
        win = tk.Toplevel(self.root)
        win.title("POUNCer - spot connections")
        t = tk.Text(win, width=92, height=34, wrap="word", font=(theme.FONT, 9), bd=0,
                    padx=10, pady=8)
        t.insert("1.0", text)
        t.configure(state="disabled")
        t.pack(fill="both", expand=True)
        self._conn_win = win

    def _source_tip(self, key: str) -> str:
        """Hover over a Spots button: the connection behind it, now."""
        c = self.cfg
        if key.startswith("flt:"):
            return self._filter_tip(key[4:])
        if key in self.CONN_KEYS:
            return self._conn_tip(key)
        on = has_source(c, key)
        call = c.rbn_callsign or (self.own_call() if hasattr(self, "own_call") else "") or "?"
        head = f"{self.SOURCE_NAMES.get(key, key)} - {'ON' if on else 'off'} (click = switch " \
               f"{'off' if on else 'on'})"
        team = getattr(self, "team", None)
        if team is not None and team.alive_peers() and not team.is_master():
            return head + "\nTeam slave: the master is connected; a click asks the master."

        def st(client):
            if client is None:
                return "not connected"
            return getattr(client, "status_text", "") or "starting"
        if key == "rbn":
            cl = self._rbn_clients.get(c.rbn_port)
            return (f"{head}\n{c.rbn_host}:{c.rbn_port} (telnet), login {call}\n"
                    f"Skimmer spots; skimmers: {self.spotter_filter.label() or 'all'}\n"
                    f"Now: {st(cl) if on else 'off'}")
        if key == "telnet":
            tc = getattr(self, "_telnet_client", None)
            return (f"{head}\n{c.rbn_backup_host}:{c.rbn_backup_port} (telnet), login {call}\n"
                    f"Its skimmer spots ('-#'); skimmers: {self.spotter_filter.label() or 'all'}\n"
                    f"Now: {st(tc) if on else 'off'}")
        if key == "http":
            f = self._http_feed
            if f is None:
                now = "off" if not on else "not running"
            else:
                age = time.time() - f.last_ok if f.last_ok else None
                now = (f"{f.spots_in} spots, last answer {age:.0f} s ago" if age is not None
                       else "waiting for the first answer")
            return f"{head}\n{c.rbn_http_backup or '(no URL)'}\nSkimmer spots over HTTP\nNow: {now}"
        if key == "cluster":
            merged = self._cluster_merged()
            cl = getattr(self, "_telnet_client", None) if merged else getattr(self, "_ssb_client", None)
            now = ("from N1MM's spots (N1MM is connected to the cluster itself)"
                   if getattr(self, "_ssb_via_n1mm", False) else (st(cl) if on else "off"))
            return (f"{head}\n{c.ssb_host}:{c.ssb_port} (telnet), login "
                    f"{(c.rbn_callsign if merged else c.ssb_login) or call}"
                    f"{' - the same connection as Telnet' if merged else ''}\n"
                    f"Spots of people (no '-#'), spotters: {c.ssb_spotters}\nNow: {now}")
        return head

    def _conn_tip(self, key: str) -> str:
        c = self.cfg
        _s, attr, name = self.CONN_KEYS[key]
        on = bool(getattr(c, attr, False))
        head = f"{name} - {'ON' if on else 'off'} (click = switch {'off' if on else 'on'})"
        if key == "telnetbck":
            tc = getattr(self, "_telnet_client", None)
            if not on:
                now = "off - nothing takes over when RBN is down"
            elif has_source(c, "telnet"):
                now = "not needed - Telnet is on anyway"
            elif not has_source(c, "rbn"):
                now = "not used - RBN is off"
            elif getattr(self, "_bck_active", False):
                now = ("ACTIVE - RBN down / silent; " +
                       ((getattr(tc, "status_text", "") or "connecting") if tc else "starting"))
            else:
                bad = getattr(self, "_rbn_bad_since", None)
                now = (f"RBN not working for {time.time() - bad:.0f} s (takes over after "
                       f"{self.BACKUP_AFTER_S} s)" if bad else "standby - RBN works")
            return (f"{head}\n{c.rbn_backup_host}:{c.rbn_backup_port} (telnet), login "
                    f"{c.rbn_callsign or '?'} - its skimmer spots\nTakes over when RBN has been "
                    f"down or silent for {self.BACKUP_AFTER_S} s, closes after RBN works "
                    f"{self.BACKUP_BACK_S} s again\nNow: {now}")
        if key == "hamqth":
            feeds = getattr(self, "_hq_feeds", {})
            st = getattr(self, "_hq_st", {})

            def one(kind, name):
                f = feeds.get(kind)
                if f is not None:
                    return f"{name}: ACTIVE - {f.status_text}"
                if kind == "rbn" and getattr(self, "_rbn_pause_on", False):
                    return f"{name}: paused (every radio on SSB)"
                bad = (st.get(kind) or {}).get("bad")
                if bad:
                    return f"{name}: telnet down {time.time() - bad:.0f} s (HamQTH after {self.HQ_AFTER_S} s)"
                return f"{name}: standby - telnet works"
            now = ("off - nothing takes over" if not on else
                   one("rbn", "skimmers") + "; " + one("spots", "cluster"))
            return (f"{head}\nhttps://www.hamqth.com/api/v2/rbn (skimmers) and /api/v2/spots "
                    f"(cluster), every 20 s, no login\nWith RBN / Telnet (skimmers) or Cluster "
                    f"on: only while that telnet is down ({self.HQ_AFTER_S} s), stops "
                    f"{self.HQ_BACK_S} s after it works again; with them off: the source itself. "
                    f"Data provided by HamQTH.com\nNow: {now}")
        if key == "mycall":
            feed = getattr(self, "_self_feed", None)
            team = getattr(self, "team", None)
            slave = team is not None and team.role() == "slave"
            login = self._self_feed_login() if hasattr(self, "_self_feed_login") else "?"
            now = ("the team master is connected and passes them on" if slave and on else
                   (getattr(feed, "status_text", "") or "starting") if feed is not None else
                   ("not connected" if on else "off"))
            return (f"{head}\n{c.self_spot_host}:{c.self_spot_port} (telnet), login {login}\n"
                    f"Spots of your own call only (node filter '{c.self_spot_filter}') for the "
                    f"SPOTTED pop-up\nNow: {now}")
        dxc = getattr(self, "_dxc", None)
        up = self._upstream_client()
        if not getattr(c, "cluster_server", False):
            now = "the cluster server is off (Settings > Cluster server)"
        elif dxc is None:
            now = "the cluster server is not running"
        elif not on:
            now = f"off - nothing goes out on TCP {dxc.port}"
        else:
            now = (f"{dxc.client_count()} client(s); commands go over "
                   + (f"{up.host}:{up.port}" if up else "nothing (Telnet and Cluster off)"))
        return (f"{head}\nOpens no connection: hands out the spots of the switched-on sources "
                f"(RBN, Telnet, HTTP, Cluster) to POUNCer's cluster server, TCP "
                f"{c.cluster_port} (N1MM's Telnet window)\nWhat N1MM sends (DX spots, sh/dx) "
                f"goes over POUNCer's telnet connection (Telnet / Cluster)\nNow: {now}")

    def _source_hover(self, button, key: str) -> None:
        def show(e, key=key):
            hide()
            tip = tk.Toplevel(self.root)
            tip.wm_overrideredirect(True)
            try:
                tip.attributes("-topmost", True)
            except tk.TclError:
                pass
            text = self._source_tip(key)
            head, _, body = text.partition("\n")
            fr = tk.Frame(tip, bg="#263238", bd=1, relief="solid", padx=8, pady=6)
            fr.pack()
            tk.Label(fr, text=head, bg="#263238", fg="#ffffff", justify="left",
                     font=(theme.FONT, 9, "bold")).pack(anchor="w")
            tk.Label(fr, text=body, bg="#263238", fg="#eceff1", justify="left",
                     font=(theme.FONT, 9), wraplength=theme.px(440)).pack(anchor="w")
            tip.geometry(f"+{e.x_root + 12}+{e.y_root + 18}")
            self._src_tip = tip

        def hide(_e=None):
            tip = getattr(self, "_src_tip", None)
            if tip is not None:
                try:
                    tip.destroy()
                except tk.TclError:
                    pass
                self._src_tip = None
        button.bind("<Enter>", show, add="+")
        button.bind("<Leave>", hide, add="+")
        button.bind("<ButtonPress>", hide, add="+")

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
        return _drive(self._wait_for_qsy_steps(nr, khz))

    def _wait_for_qsy_steps(self, nr: int, khz: float):
        """_wait_for_qsy as steps: yields the seconds to wait (a tap runs
        between window updates - the window doesn't freeze)."""
        wait = getattr(self.cfg, "qsy_wait_ms", 0) / 1000
        settle = getattr(self.cfg, "qsy_settle_ms", 0) / 1000
        end = time.time() + wait
        while True:
            snap = self.state.snapshot(nr)
            f = snap.get("freq_khz") if snap else None
            if f is not None and abs(f - khz) <= 0.15:
                if settle:
                    yield settle
                return True
            if time.time() >= end:
                return wait == 0
            yield 0.05

    def _settext(self) -> bool:
        return getattr(self.cfg, "type_mode", "settext") == "settext"

    def _enter_text(self, hwnd, text: str) -> bool | None:
        """Put text into the focused N1MM box: in one go (settext - nothing
        can get mixed up), or cleared + typed key by key. True if the box
        was set / cleared; None if the keyboard target could not be verified."""
        if not _keyboard_ready(self, hwnd):
            log.warning("call %s not typed: Entry Window is not the keyboard target", text)
            return None
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
        return _drive(self._check_call_steps(hwnd, call))

    def _check_call_steps(self, hwnd, call: str):
        if not hwnd or not self._settext():
            return ""
        want = call.strip().upper()
        for attempt in range(3):
            yield 0.15
            if not _keyboard_ready(self, hwnd, allow_focus=False):
                return " [call check stopped: Entry Window focus changed]"
            got = focused_edit_text(hwnd)
            if got is None or got.strip().upper() == want:
                return " [call put in again]" if attempt else ""
            set_focused_edit_text(hwnd, call)
        return " [call not confirmed in N1MM]"

    def _guard_call(self, hwnd, call: str, nr: int, khz: float) -> bool:
        """Radio B when N1MM hadn't reported the new frequency yet: keep the
        call in the box a little longer - if N1MM's QSY (and its wipe of the
        callsign) comes late, put the call in again. True = the report came."""
        return _drive(self._guard_call_steps(hwnd, call, nr, khz))

    def _guard_call_steps(self, hwnd, call: str, nr: int, khz: float):
        if not hwnd or not self._settext():
            return False
        settle = getattr(self.cfg, "qsy_settle_ms", 0) / 1000
        want = call.strip().upper()
        end = time.time() + GUARD_CALL_S
        landed_at = None
        while time.time() < end:
            if not _keyboard_ready(self, hwnd, allow_focus=False):
                return False
            snap = self.state.snapshot(nr)
            f = snap.get("freq_khz") if snap else None
            if landed_at is None and f is not None and abs(f - khz) <= 0.15:
                landed_at = time.time()
                end = min(end, landed_at + settle + 0.2)
            got = focused_edit_text(hwnd)
            if got is not None and got.strip().upper() != want:
                set_focused_edit_text(hwnd, call)
            yield 0.05
        return landed_at is not None

    def _restore_call(self, hwnd, call: str) -> bool:
        """After a late N1MM QSY: the callsign box empty (N1MM wiped it)?
        Put the call in again. Anything else in the box is left alone."""
        if not hwnd or not self._settext():
            return False
        if not _keyboard_ready(self, hwnd, allow_focus=False):
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
        """A tap / click: QSY, wait for N1MM, type the call. In the app it
        runs as steps between window updates (no 2-3 s freeze while N1MM
        tunes); a new tap stops the one still running."""
        steps = self._tap_steps(spot, radio)
        if getattr(self, "_tap_async", False):
            self._run_steps(steps)
        else:
            _drive(steps)

    def _run_steps(self, gen) -> None:
        old = getattr(self, "_tap_gen", None)
        if old is not None:
            old.close()
        self._tap_gen = gen

        def step():
            if self._tap_gen is not gen:
                return
            try:
                wait = next(gen)
            except StopIteration:
                self._tap_gen = None
                return
            except Exception:  # noqa: BLE001 - a tap must never break the window
                log.exception("tap failed")
                self._tap_gen = None
                return
            self.root.after(max(1, int(wait * 1000)), step)
        step()

    def _tap_steps(self, spot: Spot, radio: int | None = None):
        nr = self._radio_nr(radio)
        hwnd = self.state.entry_window_hwnd(nr)  # that radio's Entry Window (A / B)
        snap = self._snapshot_focus()
        focused = False
        if self.cfg.focus_n1mm and hwnd:
            focused = force_foreground(hwnd)
            if focused:
                yield 0.15

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
        landed = yield from self._wait_for_qsy_steps(nr, spot.freq_khz)
        msg_retry = ""
        if not landed and not self._types_qsy(nr) and self.cfg.qsy_wait_ms:
            # N1MM didn't report the new frequency: send the QSY once more and
            # wait again (only the UDP command - typing the frequency again
            # would go into the callsign box)
            log.info("tap %s: no report of %.1f after %.1f s - QSY sent again", spot.dxcall,
                     spot.freq_khz, time.time() - t_wait)
            if self._qsy(nr, spot.freq_khz, hwnd, focused):
                landed = yield from self._wait_for_qsy_steps(nr, spot.freq_khz)
                if landed:
                    msg_retry = " [QSY sent again]"
        reported = f"N1MM reported it after {time.time() - t_wait:.1f} s" if landed else \
            f"N1MM didn't report it within {self.cfg.qsy_wait_ms / 1000:.1f} s"

        msg = f"QSY {self._radio_tag(nr)}{spot.freq_khz:.1f} kHz -> {spot.dxcall}"
        msg += msg_retry
        if self.set_call_var.get():
            can_type = _keyboard_ready(self, hwnd)
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
                    yield 0.2  # let N1MM finish the wipe before we type
                how = self._enter_text(hwnd, spot.dxcall)
                if how is None:
                    msg += " (call skipped: Entry Window not focused)"
                else:
                    msg += " (call typed, no TX)" + ("" if how else " [box not cleared]")
                    msg += yield from self._check_call_steps(hwnd, spot.dxcall)
                if how is not None and not landed:
                    # N1MM's QSY (and its wipe of the callsign) may still come:
                    # keep the call in the box a little longer - every radio
                    landed = yield from self._guard_call_steps(hwnd, spot.dxcall, nr, spot.freq_khz)
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

    def _note_error(self, where: str, exc: object) -> None:
        """Show a failure in the red status line (count, last time, what)."""
        e = getattr(self, "_err", None)
        what = (f"{type(exc).__name__}: {exc}" if isinstance(exc, BaseException)
                else str(exc)).strip()[:120]
        if e is None or e["where"] != where or e["what"] != what:
            e = self._err = {"where": where, "what": what, "n": 0, "first": time.time()}
        e["n"] += 1
        e["last"] = time.time()
        self._show_error()

    def _show_error(self) -> None:
        e = getattr(self, "_err", None)
        lbl = getattr(self, "err_label", None)
        if lbl is None:
            return
        try:
            if e is None:
                lbl.pack_forget()
                return
            at = time.strftime("%H:%M", time.gmtime(e["last"]))
            times = f" ({e['n']}x since {time.strftime('%H:%M', time.gmtime(e['first']))})" \
                if e["n"] > 1 else ""
            lbl.configure(text=f"\u26a0 Error in {e['where']} at {at}z{times}: {e['what']}"
                               "  -  click: open pouncer.log")
            if not lbl.winfo_ismapped():
                lbl.pack(fill="x", before=self.status_label, pady=(0, 2))
        except tk.TclError:
            pass

    def _open_error_log(self) -> None:
        self._err = None
        self._show_error()
        self._open_path(diagmod.log_path(self.cfg.run_history_dir or "cache"))

    def _tick(self) -> None:
        """The refresh tick, timed for the perf log (a slow tick = a slow window)."""
        t0 = time.perf_counter()
        self._tick_scheduled = False
        try:
            self._tick_body()
        except Exception as exc:  # noqa: BLE001 - log it, and keep the window refreshing
            if time.time() - getattr(self, "_tick_err_t", 0) > 30:
                log.exception("refresh tick failed (the window keeps refreshing)")
                self._tick_err_t = time.time()
            try:
                self._note_error("the refresh", exc)
            except Exception:  # noqa: BLE001 - the marker must never stop the tick
                pass
        finally:
            if not self._tick_scheduled:
                try:
                    self.root.after(REFRESH_MS, self._tick)
                except Exception:  # noqa: BLE001 - window closing
                    pass
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
        zo_mode = bool(getattr(self.cfg, "zo_mode", False))
        if zo_mode != getattr(self, "_zo_mode_seen", False):
            self._zo_mode_seen = zo_mode
            self._show_tiles()
        self.updater.maybe_check()
        self._check_auto_restart()
        self._drain_status()
        if hasattr(self, "_self_spot_q"):
            self._drain_self_spots()
        self._check_self_feed_filter()
        self._update_net_bar()
        self._update_source_buttons()
        self._update_filter_buttons()
        self._check_late_qsy()
        self._drain_spots()
        self._drain_wpm()
        self._ensure_rbn_clients()
        try:
            self._reload_worked()
        except Exception as exc:  # noqa: BLE001 - never stop the tick (the window would freeze)
            if time.time() - getattr(self, "_worked_err_t", 0) > 60:
                log.exception("worked: log read failed")
                self._worked_err_t = time.time()
            self._note_error("reading the N1MM log", exc)
        self._prune()
        self._remember_band_freq()
        # Retuning moves the "tuned to" highlight, so redraw when it changes.
        khz = self._radio_khz()
        if khz != self._last_radio_khz:
            self._last_radio_khz = khz
            self._dirty = True
        self._update_labels()
        self._maybe_rate_line()
        revision = getattr(getattr(self, "runhist", None), "revision", None)
        if revision is not None and revision != getattr(self, "_history_revision", None):
            self._history_revision = revision
            self._dirty = True
            self._web_built = 0.0
        if (getattr(self, "spots", None) and time.monotonic() -
                getattr(self, "_desktop_refreshed_at", 0) >= DESKTOP_REFRESH_S):
            self._dirty = True
        if self._dirty:
            self._rebuild()
            self._dirty = False
            self._desktop_refreshed_at = time.monotonic()
            self._perf["rebuilds"] += 1
        self._drain_web()
        if self.web is not None and self._web_due():
            self._refresh_web_payloads()
            # ZeroTier can come up after POUNCer starts: re-check every minute.
            if time.time() - self._web_addr_checked >= 60:
                self._refresh_web_address()
        self.root.after(REFRESH_MS, self._tick)
        self._tick_scheduled = True

    def _reload_worked(self) -> None:
        """Refresh clock-driven eligibility as well as database-driven changes."""
        if self.worked.maybe_reload():
            self._dirty = True
            self._web_built = 0.0
            if getattr(self.worked, "profile", None):
                self._rate_at = 0.0
            if str(self.worked.status).startswith("read failed"):
                self._note_error("reading the N1MM log", self.worked.status[13:])

    def _refresh_web_payloads(self) -> None:
        # one payload per radio: each phone shows its own radio's band
        radios = set(self.state.radio_numbers()) | {1, self.radio}
        self._web_cache = {r: self._build_web_payload(r) for r in sorted(radios)}
        # bands phones look at without the radio being there (asked < 10 s ago)
        now = time.time()
        views = {k: t for k, t in dict(self._web_views).items() if now - t < WEB_VIEW_S}
        self._web_views = views
        self._web_view_cache = {k: self._build_web_payload(*k) for k in views}

    def _build_web_payload(self, radio: int | None = None, view_band: str | None = None) -> dict:
        radio = radio or self.radio
        self._last_weak = 0
        real_band = self._current_band(radio)
        if view_band == ALL_BANDS or (view_band and "," in view_band):
            # combined bands (hunting mults): new mults first before the list limit
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
            "net": self._net_text(),
            "contest_bands": list(CONTEST_BANDS),
            "tune_step": self.cfg.tune_step_hz,
            "zo_mode": bool(getattr(self.cfg, "zo_mode", False)),
            "rare_run": RARE_RUN_PCT,
            "filter": self._filter_width,
            # "cat": NAR/WIDE set the filter (always send); "toggle": Alt+'
            "filter_mode": (
                "cat" if self.cfg.filter_narrow_cat and self.cfg.filter_wide_cat
                else "toggle"
            ),
            "modes": list(self._effective_modes(radio)),
            "worked": self.worked.status,
            "session": getattr(self.worked, "session_text", ""),
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
                    "received": s.received,
                    "cont": self._continent(s.dxcall),
                    "country": self._country(s.dxcall),
                    "dx": self._is_dx(s.dxcall),
                    "mult": self._mults(s),
                    "worked": bool(getattr(self.worked, "profile", None)
                                   and self.worked.is_worked(s.dxcall, s.freq_khz, s.mode)),
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
            "worked_read": dict(getattr(self.worked, "last_read", {}),
                                from_n1mm=sorted(map(list, getattr(self.worked, "_extra", ()))),
                                deleted=sorted(map(list, getattr(self.worked, "_gone", ())))),
            "worked": self.worked.is_worked(s.dxcall, s.freq_khz, s.mode),
            "zone_kind": "ITU" if self.worked.zone_is_itu else "CQ",
            "mults": self.worked.mult_detail(s.freq_khz, ent.prefix if ent else None, zone,
                                             wpx_prefix(s.dxcall), call=s.dxcall,
                                             continent=ent.continent if ent else None,
                                             mode=s.mode),
            "mult_rules": self.worked.mult_summary(),
            "mult": self._mults(s),
            "run": run, "rare_run": RARE_RUN_PCT,
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

    WEB_IDLE_S = 20      # no phone asked this long: build its list every WEB_IDLE_EVERY_S only
    WEB_IDLE_EVERY_S = 5

    def _web_due(self) -> bool:
        """Build the phone lists this tick? Every tick while a phone looks
        (asked < WEB_IDLE_S ago), else every WEB_IDLE_EVERY_S - about half a
        tick's work saved when nobody has the phone page open."""
        now = time.time()
        if now - getattr(self, "_web_asked", 0.0) < self.WEB_IDLE_S or \
                now - getattr(self, "_web_built", 0.0) >= self.WEB_IDLE_EVERY_S:
            self._web_built = now
            return True
        return False

    def _web_spots(self, radio: int | None = None, band: str | None = None) -> dict:
        """Phone list: the radio's band, or ``band`` from the band menu (the
        radio isn't moved - tapping a station there QSYs it)."""
        self._web_asked = time.time()       # a phone looks: lists every tick again
        cache = self._web_cache
        base = cache.get(radio or self.radio) or cache.get(self.radio) or cache.get(1) or {}
        band = normalize_band_view(band)
        if not band or band == base.get("band"):
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
            return _cw_keyboard_ready(self, win, nr) or self._cw_fail("N1MM's CW input window could not be verified")
        if win:
            self._cw_close(stop=False)
            if self._cw_win is not None:
                return False
        hwnd = self.state.entry_window_hwnd(nr)
        if not _keyboard_ready(self, hwnd):
            return self._cw_fail("N1MM's Entry Window could not be brought to the front")
        win32mod.send_ctrl_k()
        fg = 0
        for _ in range(10):              # wait for N1MM's CW window (max 0.5 s)
            time.sleep(0.05)
            fg = win32mod.foreground_window()
            if _cw_keyboard_ready(self, fg, nr, allow_focus=False):
                break
        if not _cw_keyboard_ready(self, fg, nr, allow_focus=False):
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
        return _cw_keyboard_ready(self, win, nr) or self._cw_fail("N1MM's CW input window could not be verified")

    def _cw_close(self, stop: bool) -> None:
        """Done: Enter (N1MM sends what's left, closes its CW window).
        Stop: Esc (stops at once)."""
        win = self._cw_win
        if not win or not win32mod.is_window(win):
            self._cw_win = None
            if stop:                     # stop anyway: Esc in N1MM stops sending
                hwnd = self.state.entry_window_hwnd(self._cw_radio or self._radio_nr())
                if _keyboard_ready(self, hwnd):
                    win32mod.send_escape()
            return
        if not _cw_keyboard_ready(self, win, self._cw_radio or self._radio_nr()):
            self._cw_error = "N1MM's CW input window could not be verified; close/stop was not sent"
            self._cw_error_n += 1
            self.status_label.configure(text="CW keyboard: " + self._cw_error)
            return
        self._cw_win = None
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
            if item[0] == "net":
                self._on_net_change(item[1])
                continue
            if item[0] == "cty":                  # a new wl_cty.dat downloaded: use it now
                try:
                    self.cty = CtyDB.load(item[1])
                    self._cty_cache.clear()
                    self._dirty = True
                    self.status_label.configure(text=f"New country file: {len(self.cty)} entries "
                                                     "(country-files.com)")
                except Exception as exc:  # noqa: BLE001
                    log.warning("new cty file unreadable: %s", exc)
                continue
            if item[0] == "fw":                   # the firewall check is done
                self._update_fw_menu()
                continue
            if item[0] == "src":                  # a slave's Spots button (team)
                self._set_source(item[1], from_team=True)
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
            if item[0] == "team-update":   # the master: update now
                self._start_team_update(from_master=True)
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

    def _paint_radio_btn(self) -> None:
        """The A / B badge's text and colour: blue = A, orange = B."""
        btn = getattr(self, "radio_btn", None)
        if btn is not None:
            btn.configure(text=self._radio_btn_text(),
                          bg="#ef6c00" if self.radio == 2 else "#1565c0")

    def _toggle_radio(self) -> None:
        """Main window A / B button: act on / follow the other radio."""
        self.radio = 2 if self.radio == 1 else 1
        self._paint_radio_btn()
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
        if not _keyboard_ready(self, hwnd):
            self.status_label.configure(text=f"{key} not sent: N1MM Entry Window is not focused")
            return
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

    MERGE_S = 60      # the same call / frequency / spotter from two sources within this = once

    def _merged_dup(self, spot) -> bool:
        """RBN, Telnet and HTTP pass on the same skimmer spots: keep the first."""
        seen = getattr(self, "_merge_seen", None)
        if seen is None:
            seen = self._merge_seen = {}
        now = time.time()
        key = (spot.dxcall.upper(), int(round(spot.freq_khz * 10)),
               (spot.spotter or "").upper().replace("-#", ""))
        if now - seen.get(key, 0.0) < self.MERGE_S:
            return True
        seen[key] = now
        # sweep old keys at most every 10 s (v0.7.182): above 20000 live keys
        # (heavy RBN) the sweep ran on EVERY spot - 1.25 ms each, seconds a minute
        if len(seen) > 20000 and now - getattr(self, "_merge_swept", 0.0) > 10:
            self._merge_swept = now
            for k in [k for k, t in seen.items() if now - t > self.MERGE_S]:
                del seen[k]
        return False

    def _drain_spots(self) -> None:
        got = 0
        while got < 500:
            try:
                spot = self.spot_queue.get_nowait()
            except queue.Empty:
                break
            if not spot.time_utc:     # N1MM / some cluster spots carry no time
                spot.time_utc = time.strftime("%H%MZ", time.gmtime(spot.received))
            if self._is_my_call(spot):
                # a spot of MY call (live, or the last spots after a cluster
                # login, up to 15 min old): the SPOTTED pop-up, not a row
                if time.time() - spot.received < 900:
                    self._note_self_spot(spot.dxcall, spot.spotter, spot.freq_khz)
                continue
            if self._beacon_out(spot):
                continue                  # a beacon: not in the list, not on port 10000
            sf = getattr(self, "spotted_filter", None)
            if sf is not None and not sf.accept(spot.dxcall):
                continue                  # the Spotted filter: not one of these stations
            if self._merged_dup(spot):
                continue                  # the same spot from another source: once
            self._note_own(spot)
            self._check_overlap(spot)
            self.spots[spot.key] = spot
            self._track_on_freq(spot)
            got += 1
            if getattr(self, "net", None) is not None:
                self.net.alive()          # a spot from the internet: it's up
            self._perf["spots_in"] += 1
            self._last_own_spot = time.time()
            if self.team is not None and self.team.is_master():
                self.team.send_spot(self._spot_dict(spot))
            self._serve(spot)                 # N1MM's telnet clients (never N1MM's own)
        # spots passed on by the team master (slave): use, don't pass on
        while got < 1000:
            try:
                spot = self._team_queue.get_nowait()
            except queue.Empty:
                break
            if not spot.time_utc:
                spot.time_utc = time.strftime("%H%MZ", time.gmtime(spot.received))
            if self._is_my_call(spot):
                continue                  # (the pop-up came with it: _team_spot)
            if self._beacon_out(spot):
                continue
            sf = getattr(self, "spotted_filter", None)
            if sf is not None and not sf.accept(spot.dxcall):
                continue
            self._note_own(spot)
            self._check_overlap(spot)
            self._serve(spot)
            self.spots[spot.key] = spot
            self._track_on_freq(spot)
            got += 1
        self._relabel_overlap()
        if got:
            self._dirty = True

    def _beacon_out(self, spot: Spot) -> bool:
        """[rbn] hide_beacons: a beacon spot (an NCDXF beacon on its
        frequency, type BEACON / NCDXF, a .../B call) is dropped as it comes
        in - not in the list, not on port 10000 - and is a line in the traffic
        console (BEACON). v0.7.177."""
        if not getattr(self.cfg, "hide_beacons", True):
            return False
        why = beacon_why(spot)
        if not why:
            return False
        traffic.kept_out("BEACON", getattr(spot, "source", "") or "spot", spot,
                         f"dropped: {why} - not in the list, not on port "
                         f"{getattr(self.cfg, 'cluster_port', 10000)}")
        return True

    def _digi_wanted(self) -> set[str]:
        """FT8 / FT4 in the Spots modes ([rbn] follow_modes): those are served."""
        return {m.upper() for m in getattr(self.cfg, "follow_modes", ("CW",))} & {"FT8", "FT4"}

    def _serve(self, spot: Spot) -> None:
        """A spot to the cluster server (port 10000, N1MM's Telnet window) -
        but no FT8 / FT4 spot unless FT8 / FT4 is in the Spots modes, and a
        CW / SSB spot in an FT8 window only once PSK Reporter said it is not
        FT8 (v0.7.174). Each one kept out or held is a line in the traffic
        console (FT8)."""
        dxc = getattr(self, "_dxc", None)
        if dxc is None:
            return
        mode = (spot.mode or "").upper()
        if mode in traffic.DIGITAL and mode not in self._digi_wanted():
            if getattr(spot, "source", "") != "n1mm" and dxc.relay():
                traffic.digi_out("server", spot, "not served to port "
                                 f"{dxc.port}: {mode} spot (Spots modes: "
                                 f"{','.join(self.cfg.follow_modes)})")
            return
        if spot.key in getattr(self, "_overlap_keys", ()):
            held = getattr(self, "_serve_held", None)
            if held is None:
                held = self._serve_held = {}
            held[spot.key] = spot
            traffic.digi_out("PSKR", spot, f"held from port {dxc.port}: FT8 window - "
                             "asking PSK Reporter if he is on FT8 / FT4")
            return
        dxc.publish(spot)

    def _serve_verdict(self, key, digital: str = "") -> None:
        """PSK Reporter's answer for a held spot: FT8 / FT4 = not served;
        none = served now."""
        held = getattr(self, "_serve_held", None)
        spot = held.pop(key, None) if held else None
        if spot is None:
            return
        dxc = getattr(self, "_dxc", None)
        if digital:
            traffic.digi_out("PSKR", spot, f"not served: PSK Reporter has him on {digital}")
        elif dxc is not None:
            if dxc.publish(spot):
                traffic.digi_out("PSKR", spot, "served now: no FT8 / FT4 report of him")

    def _check_overlap(self, spot: Spot) -> None:
        """Ask PSK Reporter (MQTT) about ONE call when its spot sits in an FT8 /
        FT4 window and might really be FT8 / FT4:
        - an SSB spot in 7074 - 7077 or 1840 - 1843 (FT8 inside the SSB
          segment; also in an SSB contest - it only ever confirms FT8);
        - a CW spot in any FT8 / FT4 window (3573, 7047.5, 14074, 14080,
          21074, 28074 ... + 3 kHz): a skimmer's false decode or a "CW" word
          on an FT8 spot. A real CW station there stays CW (no FT8 report of
          him), and its alert is not held back."""
        if self.pskr is None:
            return
        if spot.mode == "SSB":
            if not digital_overlap(spot.freq_khz):
                return
        elif spot.mode != "CW" or not digital_mode(spot.freq_khz):
            return
        # PSK Reporter may already know (a re-spot): use that at once
        m = self.pskr.digital(spot.dxcall, spot.freq_khz)
        if m:
            spot.mode = m
            return
        self.pskr.ask(spot.dxcall, spot.freq_khz)
        self._overlap_keys.add(spot.key)
        since = getattr(self, "_overlap_since", None)
        if since is not None and spot.mode == "SSB":
            since.setdefault(spot.key, time.time())

    def _relabel_overlap(self) -> None:
        """PSK Reporter has him on FT8 / FT4 there: the spot is FT8 / FT4 (and
        leaves the SSB / CW list)."""
        if not self._overlap_keys or self.pskr is None:
            return
        for key in list(self._overlap_keys):
            s = self.spots.get(key)
            if s is None or s.mode not in ("SSB", "CW"):
                self._overlap_keys.discard(key)
                getattr(self, "_overlap_since", {}).pop(key, None)
                self._serve_verdict(key, s.mode if s is not None and
                                    s.mode in traffic.DIGITAL else "")
                continue
            m = self.pskr.digital(s.dxcall, s.freq_khz)
            if m:
                was = s.mode
                s.mode = m
                self._overlap_keys.discard(key)
                getattr(self, "_overlap_since", {}).pop(key, None)
                self._dirty = True
                log.info("PSK Reporter: %s %.1f is on %s, not %s", s.dxcall, s.freq_khz, m, was)
                self._serve_verdict(key, m)
            elif time.time() - s.received > self.pskr.watch_s + 30:
                self._overlap_keys.discard(key)      # no FT8 report: SSB / CW it is
                getattr(self, "_overlap_since", {}).pop(key, None)
                self._serve_verdict(key)

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
        """Relative age keeps startup history old even when peer clocks differ.

        Older peers ignore this extra field. New peers accept their legacy
        messages without age as fresh, matching the previous wire behavior.
        """
        d = dataclasses.asdict(spot)
        d.pop("received", None)
        d["age_s"] = max(0.0, time.time() - spot.received)
        return d

    def _team_spot(self, d: dict) -> None:
        """A spot passed on by the team master (slave side; any thread)."""
        import math
        names = {f.name for f in dataclasses.fields(Spot)} - {"received"}
        try:
            age = d.get("age_s", 0)
            if isinstance(age, bool) or not isinstance(age, (int, float)) or not math.isfinite(age) or age < 0:
                return
            freq = d.get("freq_khz")
            if isinstance(freq, bool):
                return
            spot = Spot(**{k: d.get(k) for k in names}, received=time.time() - age)
            spot.freq_khz = float(spot.freq_khz)
            if (not math.isfinite(spot.freq_khz * 10) or spot.freq_khz <= 0
                    or not isinstance(spot.dxcall, str) or not spot.dxcall):
                return
            for field in ("mode", "spot_type", "spotter", "time_utc", "source"):
                value = getattr(spot, field)
                if value is None:
                    setattr(spot, field, "")  # older peers can omit newer text fields
                elif not isinstance(value, str):
                    return
            for value in (spot.snr, spot.wpm):
                if value is not None and (isinstance(value, bool)
                        or not isinstance(value, (int, float)) or not math.isfinite(value)):
                    return
        except (TypeError, ValueError, OverflowError):
            return
        self._last_team_spot = time.time()
        sw = getattr(self, "skimwatch", None)
        if sw is not None:
            sw.seen(spot.spotter)
        if hasattr(self, "_self_spots"):      # my call spotted - also when the master saw it
            self._note_self_spot(spot.dxcall, spot.spotter, spot.freq_khz)
        self._team_queue.put(spot)

    def _rbn_ok(self) -> bool:
        """Our own RBN connection works (logged in or spots lately) - or is
        closed on purpose (paused: the master stays master)."""
        return (getattr(self, "_rbn_pause_on", False)
                or self.rbn_status.startswith(("logged in", "via N1MM"))
                or time.time() - self._last_own_spot < 120)

    def _start_team(self) -> None:
        try:
            self.team = Team(
                key=bytes.fromhex(self.cfg.team_key) if self.cfg.team_key else b"",
                web_port=self.cfg.web_port if self.cfg.web_enabled else 0,
                on_spot=self._team_spot,
                on_runhist=(self.runhist.merge if self.runhist else None),
                rbn_ok=self._rbn_ok,
                port=self.cfg.team_port,
                version=__version__,
                peers=list(self.cfg.team_peers),
                get_bands=self.bandact.summary,
                needs_rbn=self._rbn_needed_here,
                on_phone=lambda: any(self._phone_radio(nr) for nr in self.state.radio_numbers()),
                on_update=lambda: self._web_queue.put(("team-update",)),
                on_self=lambda d: self._note_self_spot(
                    str(d.get("call", "")), str(d.get("spotter", "")),
                    float(d.get("khz") or 0), str(d.get("comment", "")), from_team=True),
                rbn_paused=lambda: bool(getattr(self, "_rbn_pause_on", False)),
                get_src=lambda: self.cfg.rbn_source,
                on_src=lambda v: self._web_queue.put(("src", v)),
                auto_master=bool(getattr(self.cfg, "team_master", False)),
                get_pc=lambda: self.cfg.web_phone_control,
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

    def _update_session_label(self) -> None:
        """A wrapping session/countdown line outside the optional details panel."""
        label = getattr(self, "session_label", None)
        if label is None:
            return
        text = getattr(self.worked, "session_text", "") if self.worked.log_open else ""
        if label.cget("text") != text:
            label.configure(text=text)
        if text:
            if not label.winfo_manager():
                label.pack(fill="x", padx=12, pady=(0, 2), after=self.lights_frame)
        elif label.winfo_manager():
            label.pack_forget()

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
            if self._clean():              # '20m · CW · 28 WPM' (the big number is kHz)
                sub = " \u00b7 ".join(p for p in parts[1:] if p)
            else:
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

    def _phone_control(self) -> str:
        """Phones controlling N1MM here: a team slave follows the master's
        [web] phone_control, everyone else its own."""
        team = self.team
        if team is not None and team.role() == "slave":
            got = team.master_phone_control()
            if got:
                return got
        return self.cfg.web_phone_control

    def _apply_phone_control(self) -> None:
        """Give the web server the mode in force (only when it changes)."""
        if getattr(self, "web", None) is None:
            return
        mode = self._phone_control()
        if mode != getattr(self, "_pc_applied", None):
            self.web.set_phone_control(mode)
            if getattr(self, "_pc_applied", None) is not None:
                log.info("phones may control N1MM: %s%s", mode,
                         " (the team master's choice)" if mode != self.cfg.web_phone_control else "")
            self._pc_applied = mode

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

    NET_BACK_SHOW_S = 8     # "Internet back after 14 min" stays this long

    def _net_down_since(self) -> float | None:
        net = getattr(self, "net", None)
        return net.down_since if net is not None and not net.up else None

    def _net_text(self) -> str:
        """'Internet down since 12:03 (14 min)' while it's down, else ''."""
        down = self._net_down_since()
        if down is None:
            return ""
        return (f"Internet down since {time.strftime('%H:%M', time.localtime(down))} "
                f"({minutes_text(time.time() - down)})")

    def _update_net_bar(self) -> None:
        """The red bar (from _tick): down, or 'back' for a few seconds."""
        bar = getattr(self, "net_bar", None)
        net = getattr(self, "net", None)
        if bar is None or net is None:
            return
        text, bg = self._net_text(), "#c62828"
        if text:
            text = "\u26a0 " + text + " - the last spots stay, greyed; reconnecting by itself"
        elif net.back_at and time.time() - net.back_at < self.NET_BACK_SHOW_S:
            text, bg = "\u2714 Internet back after " + minutes_text(net.back_after), "#2e7d32"
        if text == self._net_bar_text:
            return
        self._net_bar_text = text
        try:
            if text:
                bar.configure(text=text, bg=bg)
                if not bar.winfo_manager():
                    bar.pack(fill="x", after=self._top)
            elif bar.winfo_manager():
                bar.pack_forget()
        except tk.TclError as exc:
            log.warning("internet bar: %s", exc)

    def _on_net_change(self, up: bool) -> None:
        """From NetWatch (via the web queue, so in the Tk thread)."""
        self._dirty = True                    # grey / not grey
        if not up:
            self.status_label.configure(text=self._net_text() + " - reconnecting by itself")
            return
        net = self.net
        self.status_label.configure(text="Internet back after " + minutes_text(net.back_after))
        # every feed: try now, not after its backoff (up to a minute)
        clients = list(self._rbn_clients.values()) + [
            getattr(self, "_ssb_client", None), getattr(self, "_self_feed", None)]
        for c in clients:
            if c is not None and hasattr(c, "kick"):
                c.kick()

    def _prune(self) -> None:
        # internet down: keep what was fresh when it went (shown greyed),
        # instead of an empty list after max_age_minutes
        down = self._net_down_since()
        cutoff = (down if down is not None else time.time()) - self.cfg.max_age_minutes * 60
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
                "hours_unrounded": info.get("hours_unrounded") is True,
                "rec": bool(info.get("rec")), "run_band": info.get("band"),
                **classify_urgency(info, self.worked.contest_name,
                                   getattr(self.cfg, "urgency_contest_minutes", 0))}

    def own_call(self) -> str:
        """Our call as N1MM has it now: <mycall> in its RadioInfo (Config >
        Change Your Station Data), else the open log's station call, else
        [rbn] callsign / N1MM's ini."""
        st = getattr(self, "state", None)
        try:
            snap = st.snapshot() if st is not None and st.radio_numbers() else None
        except Exception:  # noqa: BLE001
            snap = None
        mycall = (snap or {}).get("mycall") or ""
        return mycall or self.worked.station_call or self.cfg.rbn_callsign

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
            if not _keyboard_ready(self, hwnd):
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
        if not _keyboard_ready(self, hwnd):
            self.status_label.configure(text="Filter not changed: N1MM Entry Window is not focused")
            return
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
        flow = getattr(self, "lights_frame", None)
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
                text="N1MM: **no radio info** (enable Config > Broadcast Data > Radio to "
                     f"**127.0.0.1:{getattr(self.cfg, 'n1mm_listen_port', 12060)}**)"
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
        self._update_session_label()
        self._update_check_line()
        self._update_band_lights()
        self._update_team_label()
        self._apply_phone_control()
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
            "team_note": self._team_note(role),
        }

    TEAM_SILENT_S = 30     # after start: no other POUNCer heard - say so (when one is expected)

    def _team_note(self, role: str) -> str | None:
        """The check item 'Team': a team password that doesn't fit, or no
        other POUNCer heard although one is expected (peers set, or one was
        here before) - then probably the firewall."""
        team = getattr(self, "team", None)
        if team is None:
            return None
        auth = team.auth_problem()
        if auth:
            return auth
        if role != "solo":
            self._team_seen = True
            return None
        expected = bool(self.cfg.team_peers) or getattr(self, "_team_seen", False)
        started = getattr(self, "_app_started", None)
        if started is None:
            started = self._app_started = time.time()
        if not expected or time.time() - started < self.TEAM_SILENT_S:
            return None
        if getattr(self, "_fw_ok", None) is False:
            return ("no other POUNCer heard - firewall? File > Allow POUNCer in Windows "
                    "Firewall\u2026")
        return "no other POUNCer heard - check that PC's POUNCer and its firewall"

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
        self._update_problem()
        # the items: shown when something is wrong, or when the badge was clicked
        # (clean layout: only when clicked - the lights row has the one sentence)
        want = (state != "ok" and not self._clean()) or self._check_details
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

    def _update_problem(self) -> None:
        """Clean layout: '⚠ Log: N1MM Logger.ini not found (+1 more)' at the
        end of the lights row - click it (or the badge) for every item."""
        lbl = getattr(self, "problem_label", None)
        if lbl is None:
            return
        text, state = checksmod.problem(getattr(self, "_checks", []) or [])
        show = self._clean() and bool(self.cfg.check_line) and bool(text)
        if show:
            mark = "\u2717" if state == "bad" else "\u26a0"
            hint = "  \u2013 click: hide details" if self._check_details else "  \u2013 click: details"
            full = f"{mark} {text}{hint}"
            fg = theme.CHECK.get(state, ("", theme.TEXT))[1]
            if lbl.cget("text") != full or lbl.cget("fg") != fg:
                lbl.configure(text=full, fg=fg)
        if not self._problem_added:
            if not getattr(self, "_lights", None):
                return                       # after the lights (they come first)
            self.lights_frame.add(lbl, gap=16)
            self._problem_added = True
        self.lights_frame.set_visible(lbl, show)

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

    @staticmethod
    def _band_chip_short(band: str, c: dict) -> str:
        """Clean layout: '20 · 7M' (7 new mults spotted there) / '160' - the
        colour still says open / our continent only / nothing."""
        m = c.get("mults") or 0
        return band.rstrip("m") + (f" \u00b7 {m}M" if m else "")

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
        clean = self._clean()
        if not self._band_lights:
            if not clean:
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
            text = (self._band_chip_short(band, c) if clean else self._band_chip_text(band, c))
            lbl.configure(text=("\u25b6 " if viewed else "") + text,
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
            lights.http_light(bool(getattr(self.cfg, "rbn_http_backup", "")) and role != "slave"
                              and has_source(self.cfg, "http"),
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
        # the A / B badge right after the last light (Update), before the
        # band chips (detailed layout) and the problem sentence
        btn = getattr(self, "radio_btn", None)
        if btn is not None and not getattr(self, "_radio_btn_added", True):
            self.lights_frame.add(btn, gap=10)
            self._radio_btn_added = True
        clean = self._clean()
        if self.bands_frame is None:
            if clean:                         # clean: on the sort row, after Min SNR
                self.bands_frame = tk.Frame(self._flow, bg=bg)
                self._flow.add(self.bands_frame, gap=16)
            else:                             # detailed: same row, after the lights
                self.bands_frame = tk.Frame(self.lights_frame, bg=bg)
                self.lights_frame.add(self.bands_frame, gap=10)
            self._bands_last = None
        if not self._problem_added:
            self._update_problem()           # the sentence after the lights
        for (dot, name), (state, text) in zip(self._lights, states):
            dot.itemconfigure("dot", fill=lights.COLORS[state])
            name.configure(text=text)
            # clean: a light that is simply off (grey) isn't shown
            self.lights_frame.set_visible(dot.master, not (clean and state == lights.OFF))
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

    def _save_setting_web(self, key: str, value: str) -> dict:
        """A change from the web page: never the autostart programs / folder
        (they start programs) - those only in the exe's Settings window."""
        if settingsmod.app_only(key):
            log.warning("web: change of %s refused (autostart only in the app)", key)
            return {"ok": False, "error": "Autostart can be changed only in POUNCer's own "
                                          "Settings window (the exe)"}
        return self._save_setting(key, value)

    def _save_setting(self, key: str, value: str) -> dict:
        """Settings window / page (web thread): write config.ini, update the
        config - and log what changed (old -> new)."""
        item = settingsmod.BY_KEY.get(key, {})
        old = getattr(self.cfg, item.get("attr", ""), None) if item else None
        res = settingsmod.save(self.cfg, self.cfg.config_path, key, value)
        if res.get("ok"):
            new = res.get("value")
            show = (lambda v: "***") if any(w in key.lower() for w in ("pass", "token", "secret")) \
                else (lambda v: ", ".join(v) if isinstance(v, (list, tuple)) else repr(v))
            if old != new:
                log.info("setting changed: %s (%s): %s -> %s%s", item.get("label", key), key,
                         show(old), show(new), "" if res.get("live") else "  [after a restart]")
            if key.startswith("filter.") or key == "rbn.spotter":
                self._apply_filter()
            if key.startswith("spotted."):
                self._apply_spotted()
            if key == "rbn.sources":
                self._apply_source()
                self._src_last = None
            if key.startswith("cluster."):
                self._apply_cluster_server()
            if key == "web.phone_control":
                self._apply_phone_control()
            if key == "ui.urgency_contest_minutes":
                self._dirty = True
                self._web_built = 0.0
        else:
            log.info("setting NOT saved: %s = %r: %s", key, value, res.get("error"))
        return res

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

    def _update_team(self) -> None:
        """Team > Update all POUNCers in the team: only on the master - every
        team POUNCer (this one too) gets the newest version and restarts."""
        from tkinter import messagebox
        role = self.team.role() if self.team is not None else "off"
        if role == "slave":
            messagebox.showinfo("POUNCer", "Only the team master can update the team.\n"
                                "Do it on the master PC.", parent=self.root)
            return
        if not self.updater.enabled or not self.updater.exe:
            messagebox.showinfo("POUNCer", "Updates work only in POUNCer.exe with auto-update on.",
                                parent=self.root)
            return
        n = len(self.team.alive_peers()) if self.team is not None else 0
        if not messagebox.askyesno(
                "POUNCer", f"Update this POUNCer and {n} other(s) in the team to the newest "
                "version? Each one restarts by itself when it has it.", parent=self.root):
            return
        if self.team is not None:
            self.team.send_update()
        log.info("team update started by this master (%d other POUNCer(s))", n)
        self._start_team_update(from_master=False)

    def _start_team_update(self, from_master: bool) -> None:
        """Fetch the newest version; restart into it as soon as it is ready
        (``_check_auto_restart`` in the tick). Already current: nothing."""
        if not self.updater.enabled or not self.updater.exe:
            log.info("team update: updates are off here")
            return
        log.info("team update%s: checking for a new version", " (asked by the master)" if from_master else "")
        self._auto_restart = True
        self._auto_restart_at = time.time()
        if self.updater.state == "ready":
            return                           # downloaded already: the tick restarts
        if not self.updater.check_in_background():
            self.status_label.configure(text="Team update: already downloading ...")
        else:
            self.status_label.configure(text="Team update: getting the newest version ...")

    def _check_auto_restart(self) -> None:
        if not getattr(self, "_auto_restart", False):
            return
        st = self.updater.state
        if st == "ready":
            self._auto_restart = False
            log.info("update: restarting with %s", self.updater.new_version)
            self._restart_for_update()
        elif st in ("current", "failed") and time.time() - self._auto_restart_at > 5:
            self._auto_restart = False
            self.status_label.configure(text="Update: " + (
                "already the newest version" if st == "current" else
                f"failed - {self.updater.error or 'see the log'}"))

    def _switch_version(self) -> None:
        """Help > Versions > Switch version...: the published versions (newest first);
        install the one picked - newer or older - checked like an update
        (two SHA-256 sums), the running one kept in versions\\, then restart."""
        from tkinter import messagebox
        if not self.updater.exe:
            messagebox.showinfo("POUNCer", "Switching versions works in POUNCer.exe only.",
                                parent=self.root)
            return
        old = getattr(self, "_switch_win", None)
        if old is not None:
            try:
                old.destroy()
            except tk.TclError:
                pass
        win = tk.Toplevel(self.root)
        self._switch_win = win
        win.title("POUNCer - switch version")
        win.transient(self.root)
        ttk.Label(win, text=f"This one: v{__version__}. Pick a version to install "
                            "(newer or older):").pack(padx=12, pady=(10, 4), anchor="w")
        lb = tk.Listbox(win, height=12, width=44, font=(theme.FONT, 10), activestyle="dotbox")
        lb.pack(padx=12, fill="both", expand=True)
        lb.insert("end", "loading the list from GitHub ...")
        note = ttk.Label(win, text="", style="Muted.TLabel")
        note.pack(padx=12, pady=(4, 0), anchor="w")
        bar = ttk.Frame(win)
        bar.pack(padx=12, pady=10, fill="x")
        releases: list[dict] = []

        def install() -> None:
            sel = lb.curselection()
            if not sel or not releases:
                return
            r = releases[sel[0]]
            if r["tag"].lstrip("v") == __version__:
                note.configure(text="That is the version running now.")
                return
            if not messagebox.askyesno("POUNCer", f"Install {r['tag']} ({r['date']}) and restart "
                                       "POUNCer?\n\nThe running version is kept in versions\\ "
                                       "(Help > Versions > Go back to the previous version).", parent=win):
                return
            self.updater.install_release(r["release"])
            self._auto_restart = True                    # _check_auto_restart restarts it
            self._auto_restart_at = time.time()
            self.status_label.configure(text=f"Switching to {r['tag']} ...")
            win.destroy()

        ttk.Button(bar, text="Install and restart", command=install).pack(side="left")
        ttk.Button(bar, text="Close", command=win.destroy).pack(side="right")
        lb.bind("<Double-Button-1>", lambda _e: install())
        win.bind("<Escape>", lambda _e: win.destroy())

        def load() -> None:
            try:
                got = self.updater.list_releases()
                err = ""
            except Exception as exc:  # noqa: BLE001 - offline, GitHub down
                got, err = [], str(exc)
            self.root.after(0, lambda: show(got, err))

        def show(got: list[dict], err: str) -> None:
            try:
                lb.delete(0, "end")
                if err or not got:
                    lb.insert("end", "no list - offline?" if err else "no versions found")
                    note.configure(text=err[:80])
                    return
                releases.extend(got)
                for r in got:
                    mark = "   \u2190 running now" if r["tag"].lstrip("v") == __version__ else ""
                    lb.insert("end", f"{r['tag']}   {r['date']}{mark}")
                note.configure(text="Checked like an update: GitHub's SHA-256 and "
                                    "SHA256SUMS.txt must agree.")
            except tk.TclError:
                pass                                      # the window was closed

        threading.Thread(target=load, name="release-list", daemon=True).start()

    def _rollback(self) -> None:
        """Help > Versions > Go back to the previous version (kept in versions\\)."""
        from tkinter import messagebox
        prev = self.updater.previous_versions() if self.updater.exe else []
        if not prev:
            messagebox.showinfo("POUNCer", "No earlier version kept yet (versions\\ folder next "
                                "to POUNCer.exe - each update from v0.7.127 on keeps the one it "
                                "replaces).", parent=self.root)
            return
        ver, path = prev[0]
        if not messagebox.askyesno("POUNCer", f"Go back to v{ver} and restart now?",
                                   parent=self.root):
            return
        try:
            self.updater.rollback(path)
        except OSError as exc:
            messagebox.showerror("POUNCer", f"Could not go back: {exc}", parent=self.root)
            return
        self._restart_for_update()

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
        selected = set(band.split(",")) if band else set()
        items = [
            s for s in self.spots.values()
            if (band is None or band_from_khz(s.freq_khz) in selected
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
        key = self._sort_key(self.sort_col)
        present = [s for s in items if key(s) is not None]
        missing = [s for s in items if key(s) is None]
        present.sort(key=key, reverse=self.sort_reverse)
        ordered = present + missing
        return self._apply_pin(ordered) if hasattr(self, "_apply_pin") else ordered

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
        kind = self._list_kind() if hasattr(self, "_list_kind") else "tree"
        sel = self.tree.selection()
        if kind in ("rows", "tiles"):
            w = self.rows if kind == "rows" else self.tiles
            sel = (str(w.selected),) if w.selected is not None else ()
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
        row_items = []
        want_tiles = kind == "tiles"
        want_rows = kind == "rows"
        fade_s = getattr(self.cfg, "fade_minutes", 5) * 60 if hasattr(self, "cfg") else 300
        net_down = self._net_down_since() if hasattr(self, "_net_down_since") else None
        now = time.time()
        for idx, s in enumerate(items):
            mults = self._mults(s)
            worked = self.worked.is_worked(s.dxcall, s.freq_khz, s.mode)
            run = self._run_fields(s)
            rare = bool(run.get("rare"))
            busted = self.scp is not None and self.scp.busted(s.dxcall)
            if rare and mults and not worked:
                alert.append(s)
            # one text colour: internet down (old spot, grey) > worked (grey) >
            # new mult (red, bold) > strong
            offline = net_down is not None and s.received < net_down
            if offline:
                tags: tuple = ("offline",)
            elif worked:
                tags = ("worked",)
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
                                                  tried, offline or now - s.received >= fade_s))
            if want_rows:
                row_items.append(self._row_item(s, s is tuned, mults, worked, run, rare, busted,
                                                tried, now - s.received >= fade_s, offline, now))
            if kind != "tree":
                continue
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
        if reselect is not None and kind == "tree":
            # only the table has rows to select (v0.7.173): with tiles / the
            # clean rows the table is empty, and selecting in it raised an
            # error that stopped the refresh - the list froze after a click
            self.tree.selection_set(reselect)
            self.tree.focus(reselect)
        if want_tiles:
            self.tiles.zo_mode = bool(getattr(self.cfg, "zo_mode", False))
            self.tiles.set_items(tile_items, int(reselect) if reselect is not None else None)
        if want_rows:
            self.rows.set_items(row_items, int(reselect) if reselect is not None else None)
        check = getattr(self, "_check_alerts", None)
        if check is not None and getattr(self, "_alerts", None) is not None:
            try:
                check(alert)
            except Exception as exc:  # noqa: BLE001 - an alert must never break the list
                log.info("alert: %s", exc)

    @staticmethod
    def _age_text(seconds: float) -> str:
        """'now' / '3 min' / '1 h 05' - since the last spot of that station."""
        m = int(max(0, seconds) // 60)
        if m < 1:
            return "now"
        if m < 60:
            return f"{m} min"
        return f"{m // 60} h {m % 60:02d}"

    @staticmethod
    def run_explain(call: str, run: dict, rare: bool) -> str:
        """The hover text of Runs: what 'R26% ~18h' means for this station."""
        pct, hours = run.get("run"), run.get("hours")
        if pct is None and hours is None and not run.get("urgency"):
            return ""
        lines = []
        if run.get("urgency"):
            lines.append("ZO priority: " + run["urgency"].title() + " · " + run.get("urgency_confidence", ""))
            lines.extend(run.get("urgency_reasons", []))
            lines.append("History contests: " + (", ".join(run.get("history_contests", [])) or "unverified") +
                         (" (matching)." if run.get("history_matches") else " (different or unverified)."))
        band = f" on {run['run_band']}" if run.get("run_band") else ""
        if pct is not None:
            lines.append(f"R{pct}%{'*' if run.get('rec') else ''} - in {call}'s historical logs, "
                         f"{pct} % of measurable QSOs{band} were classified as running "
                         "from frequency history (an estimate).")
        if hours is not None:
            unrounded = run.get("hours_unrounded") is True
            label = format_hours(hours, unrounded=unrounded)
            span = (f"{hours:g} hours (rounded)" if hours < 1 and not unrounded else
                    f"{round(hours * 60)} minutes" if hours < 1 else f"{round(hours)} hours")
            lines.append(f"{label} - on the air about {span} in those "
                         "historical logs (may be other contests).")
        if run.get("rec"):
            lines.append("* - a reconstructed log (put together from other stations' logs), "
                         "not its own.")
        if rare:
            lines.append("\u26a1 Historical opportunity hint: " + (" ".join(run.get("rare_reasons", [])) or "Historical run/participation pattern."))
        elif pct is not None or hours is not None:
            lines.append("No historical opportunity flag from the available evidence.")
        if run.get("urgency_note"):
            lines.append(run["urgency_note"])
        lines.append("Historical heuristic only; no probability or departure time is estimated.")
        lines.append("From the public contest-log archive (S53ZO).")
        return "\n".join(lines)

    def _row_item(self, s, onfreq, mults, worked, run, rare, busted, tried, stale, offline,
                  now) -> dict:
        """What one row of the clean list shows (rowlist.RowList)."""
        runs = ""
        if run.get("run") is not None:
            runs = f"R{run['run']}%" + ("*" if run.get("rec") else "")
        if run.get("hours") is not None:
            runs += (" " if runs else "") + format_hours(
                run["hours"], unrounded=run.get("hours_unrounded") is True)
        own = self._own_mark(s) if hasattr(self, "_own_mark") else ""
        return {"freq": f"{s.freq_khz:.1f}", "call": s.dxcall,
                "snr": "" if s.snr is None else str(s.snr),
                "wpm": "" if s.wpm is None else str(s.wpm),
                "mult": int(mults or 0), "run": runs, "rare": bool(rare),
                "run_tip": self.run_explain(s.dxcall, run, bool(rare)) if runs else "",
                "age": self._age_text(now - s.received),
                "spotter": f"\u2605 {own}" if own else s.spotter,
                "mode": s.mode or "", "type": s.spot_type or "",
                "strong": s.snr is not None and s.snr >= 20, "dx": bool(self._is_dx(s.dxcall)),
                "worked": bool(worked), "offline": bool(offline), "busted": bool(busted),
                "onfreq": bool(onfreq), "stale": bool(stale), "own": bool(own),
                "tried": (f"\u21bb{tried[1]}m" + (f"\u00d7{tried[0]}" if tried[0] > 1 else ""))
                if tried else ""}

    def _tile_item(self, s, onfreq, mults, worked, run, rare, busted, tried, stale) -> dict:
        """What one tile shows (tiles.TileList), like the phone's tile."""
        line1 = f"{s.freq_khz:.1f}" + (f" {s.mode}" if s.mode and s.mode.upper() != "CW" else "")
        if run.get("run") is not None:
            line1 += f"  R{run['run']}%" + ("*" if run.get("rec") else "")
        if run.get("hours") is not None:
            line1 += "  " + format_hours(run["hours"], unrounded=run.get("hours_unrounded") is True)
        cq = self._cq_minutes(s) if hasattr(self, "_on_freq") else None
        line2 = (self._first_heard_utc(s) if hasattr(self, "_on_freq") else s.time_utc or "")
        line2 += "  " + ("--" if s.snr is None else f"{s.snr}dB")
        if cq is not None:
            line2 += f"  cq{cq}m"
        return {"call": s.dxcall, "line1": line1, "line2": line2,
                "urgency": run.get("urgency", "unknown"),
                "run_tip": self.run_explain(s.dxcall, run, bool(rare)),
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

    def _clean(self) -> bool:
        """The clean layout (spots first) - not View > Detailed layout."""
        var = getattr(self, "detailed_var", None)
        return var is not None and not var.get()

    def _list_kind(self) -> str:
        """Which list is shown: 'tiles', 'rows' (clean layout) or 'tree'
        (detailed layout, and the compact window)."""
        if (bool(getattr(getattr(self, "cfg", None), "zo_mode", False))
                or (getattr(self, "tiles_var", None) is not None and self.tiles_var.get())):
            return "tiles"
        compact = getattr(self, "compact_var", None) is not None and self.compact_var.get()
        if self._clean() and not compact and getattr(self, "rows", None) is not None:
            return "rows"
        return "tree"

    def _show_tiles(self) -> None:
        """Show the tiles, the rows or the table in the list's place."""
        kind = self._list_kind()
        self.tree.pack_forget()
        self._tree_scroll.pack_forget()
        self.tiles.pack_forget()
        rows = getattr(self, "rows", None)
        if rows is not None:
            rows.pack_forget()
        if kind == "tiles":
            self.tiles.pack(fill="both", expand=True)
        elif kind == "rows":
            rows.set_sort(self.sort_col, self.sort_reverse)
            rows.pack(fill="both", expand=True)
        else:
            self.tree.pack(side="left", fill="both", expand=True)
            self._tree_scroll.pack(side="right", fill="y")
        self._dirty = True

    def _toggle_layout(self) -> None:
        """View > Detailed layout: used at once, saved in config.ini."""
        self._apply_layout()
        try:
            self._save_setting("ui.layout", "detailed" if self.detailed_var.get() else "clean")
        except Exception as exc:  # noqa: BLE001
            log.info("layout setting not saved: %s", exc)

    def _apply_layout(self) -> None:
        """Clean: the top bar, ONE row of lights (one sentence when something
        is wrong), sort + band chips + rate on one row, the F-keys and the
        readable list. Detailed: every status line as before."""
        clean = self._clean()
        try:
            self.freq_big.configure(font=(theme.FONT, 30 if clean else 20, "bold"))
            # the address: beside the badges (clean), after the mode (detailed)
            self.addr_label.pack_forget()
            if clean:
                self.addr_label.configure(font=(theme.FONT, 9))
                self.addr_label.pack(side="right", padx=(0, 10), pady=(6, 0))
            else:
                self.addr_label.configure(font=(theme.FONT, 11))
                self.addr_label.pack(side="left", pady=(6, 0), after=self.freq_sub)
            # status lines under the lights
            for w in (self.rate_label, self._info):
                w.pack_forget()
            if not clean:
                if getattr(self.cfg, "rate_line", True):
                    self.rate_label.pack(fill="x", padx=12, pady=(0, 2), after=self.lights_frame)
                self._info.pack(fill="x", after=self.rate_label if getattr(
                    self.cfg, "rate_line", True) else self.lights_frame)
            # the phone address in the status bar: the top bar has it (clean)
            if clean:
                self.web_label.pack_forget()
            elif not self.web_label.winfo_manager():
                self.web_label.pack(fill="x", after=self.status_label)
            # the rate, short, on the sort row (clean)
            if not getattr(self, "_rate_short_added", False):
                self._flow.add(self.rate_short, gap=16)
                self._rate_short_added = True
            self._flow.set_visible(self.rate_short, clean and getattr(self.cfg, "rate_line", True))
        except (tk.TclError, AttributeError) as exc:
            log.debug("layout: %s", exc)
        # band chips: rebuilt in the right row (lights row / sort row)
        old = getattr(self, "bands_frame", None)
        if old is not None:
            try:
                old.destroy()
            except tk.TclError:
                pass
            self.bands_frame = None
            self._band_lights = {}
            self._bands_last = None
        self._lights_last = None          # redraw the lights (off ones hidden or not)
        self._check_segs = None
        self._show_tiles()
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
        session_data = w.session_summary() if getattr(w, "profile", None) else None
        if session_data is not None:
            qsos, slots, session = session_data
            taps = [t for t in self.taps.snapshot() if session.contains(t[0])]
            out = summarymod.build(qsos, taps, slots, now=session.now)
        else:
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
        """Tools > Phone page > As an app window: the phone page on the PC in
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

    def _phone_urls(self) -> list[tuple[str, str]]:
        """[(label, url)] of the phone page: LAN, plus ZeroTier if present."""
        web = getattr(self, "web", None)
        if web is None:
            return []
        try:
            addrs = phone_addresses(getattr(self.cfg, "web_host", "0.0.0.0"))
        except Exception:  # noqa: BLE001
            addrs = []
        return [(label, f"http://{ip}:{web.port}/") for label, ip in addrs]

    # ---- Windows Firewall (File > Allow POUNCer in Windows Firewall...) ------

    FW_TCP_RULE = "POUNCer phone page TCP"
    FW_UDP_RULE = "POUNCer team UDP"
    FW_CLUSTER_RULE = "POUNCer cluster TCP"

    def _fw_rules(self) -> list[tuple[str, str, int]]:
        """(rule name, protocol, port): the phone page (TCP) and the team (UDP)."""
        rules = []
        if self.cfg.web_enabled:
            rules.append((self.FW_TCP_RULE, "TCP", int(self.cfg.web_port)))
        if self.cfg.team:
            rules.append((self.FW_UDP_RULE, "UDP", int(self.cfg.team_port)))
        if getattr(self.cfg, "cluster_server", False):
            rules.append((self.FW_CLUSTER_RULE, "TCP", int(self.cfg.cluster_port)))
        return rules

    def _apply_cluster_server(self) -> None:
        """Start / stop / move POUNCer's DX cluster server ([cluster])."""
        old = getattr(self, "_dxc", None)
        want = bool(getattr(self.cfg, "cluster_server", False))
        if old is not None and want and old.port == int(self.cfg.cluster_port):
            return
        if old is not None:
            old.stop()
            self._dxc = None
            log.info("cluster server stopped")
        if not want:
            return
        srv = ClusterServer(port=self.cfg.cluster_port, node="POUNCER", version=__version__,
                            relay=lambda: bool(getattr(self.cfg, "cluster_relay", True)),
                            send_upstream=self._relay_send,
                            spots_out=lambda: bool(getattr(self.cfg, "cluster_spots_out", True)))
        if srv.start():
            self._dxc = srv
            self.status_label.configure(text=f"Cluster server on TCP {srv.port}: in N1MM's "
                                             f"Telnet window add this PC, port {srv.port}")
        else:
            self.status_label.configure(text=f"Cluster server: {srv.error}")

    def _check_firewall(self) -> None:
        """In the background: are POUNCer's rules there? -> self._fw_ok and
        the menu item ('Windows Firewall: POUNCer allowed ✔')."""
        if sys.platform != "win32":
            return

        def work():
            found = [firewall_rule_exists(name) for name, _p, _n in self._fw_rules()]
            self._fw_ok = None if None in found else all(found)
            self._web_queue.put(("fw",))

        threading.Thread(target=work, name="firewall-check", daemon=True).start()

    def _update_fw_menu(self) -> None:
        menu, idx = getattr(self, "_fw_menu", (None, None))
        if menu is None:
            return
        label = ("Windows Firewall: POUNCer allowed \u2714  (add the rules again\u2026)"
                 if getattr(self, "_fw_ok", None) else "Allow POUNCer in Windows Firewall\u2026")
        try:
            menu.entryconfigure(idx, label=label)
        except tk.TclError:
            pass

    def _allow_firewall(self) -> None:
        """Add the inbound rules (Windows asks for administrator rights):
        TCP phone page + UDP team, private and domain networks only; an older
        rule with the same name is replaced."""
        from tkinter import messagebox
        rules = self._fw_rules()
        if not rules:
            messagebox.showinfo("POUNCer", "Phone page and team are both off - nothing to allow.",
                                parent=self.root)
            return
        what = ", ".join(f"{p} {n} ({'team' if p == 'UDP' else 'cluster server' if name == self.FW_CLUSTER_RULE else 'phone page'})"
                         for name, p, n in rules)
        if not messagebox.askyesno(
                "POUNCer - Windows Firewall",
                f"Allow incoming {what} on private and domain networks?\n\nWindows asks for "
                "administrator rights. Older rules with the same names are replaced.",
                parent=self.root):
            return
        ok, detail = add_firewall_rules(rules)
        if ok:
            self.root.after(3000, self._check_firewall)
            messagebox.showinfo("POUNCer - Windows Firewall",
                                "Firewall rules added: " + ", ".join(n for n, _p, _q in rules)
                                + ".\n\nIf the VPN / network adapter is set to Public in "
                                "Windows, set it to Private - the rules don't apply to Public.",
                                parent=self.root)
            log.info("firewall rules added: %s", what)
        else:
            messagebox.showwarning("POUNCer - Windows Firewall",
                                   f"The rules were not added ({detail}).", parent=self.root)

    # ---- taking the master role back -----------------------------------------
    def _become_master(self) -> None:
        """SLAVE badge / Team menu: this POUNCer becomes the team master
        (e.g. the radio PC after a restart, when another one took over)."""
        from tkinter import messagebox
        team = self.team
        if team is None or team.role() != "slave":
            messagebox.showinfo("POUNCer", {"master": "This POUNCer is already the team master.",
                                            "solo": "No other POUNCer on the network - "
                                                    "nothing to take over."}.get(
                team.role() if team else "", "The team is off ([team] enabled)."),
                parent=self.root)
            return
        if not messagebox.askyesno(
                "POUNCer - become master",
                f"Take the master role from {team.master_ip()}?\n\n"
                "This POUNCer then keeps the spot connections and feeds the others; "
                f"{team.master_ip()} becomes a slave.", parent=self.root):
            return
        if team.claim():
            log.info("team: master role taken back by hand")
        self._update_team_label()

    def _toggle_team_master(self) -> None:
        on = bool(self.team_master_var.get())
        self.cfg.team_master = on
        if self.team is not None:
            self.team.auto_master = on
        try:
            set_ini_value(self.cfg.config_path, "team", "master", "on" if on else "off")
        except OSError as exc:
            log.warning("[team] master not saved: %s", exc)

    # ---- team password (Team > Team password...) -----------------------------

    def _team_pw_label(self) -> str:
        return "Team password\u2026  (" + ("ON \u2714" if self.cfg.team_key else "off") + ")"

    def _team_password(self) -> None:
        """Set / change / remove the team password: typed twice; only a key
        made from it is saved ([team] key), never the password itself. Every
        POUNCer of the team needs the same one."""
        from tkinter import messagebox, simpledialog
        pw = simpledialog.askstring(
            "POUNCer - team password",
            "Team password (the same on every POUNCer of the team).\n"
            "Empty = no password (off).", show="*", parent=self.root)
        if pw is None:
            return
        if pw:
            again = simpledialog.askstring("POUNCer - team password", "Once more:", show="*",
                                           parent=self.root)
            if again != pw:
                messagebox.showwarning("POUNCer", "The two passwords differ - nothing changed.",
                                       parent=self.root)
                return
        key = derive_key(pw)
        self.cfg.team_key = key.hex()
        try:
            set_ini_value(self.cfg.config_path, "team", "key", key.hex())
        except OSError as exc:
            messagebox.showwarning("POUNCer", f"Not saved: {exc}", parent=self.root)
        if self.team is not None:
            self.team.key = key
        log.info("team password %s", "set" if key else "removed")
        menu, idx = getattr(self, "_pw_menu", (None, None))
        if menu is not None:
            menu.entryconfigure(idx, label=self._team_pw_label())
        messagebox.showinfo(
            "POUNCer", ("Team password set. Set the same one on every POUNCer of the team - "
                        "until then they don't hear each other (the check line says so)."
                        if key else "Team password off."), parent=self.root)

    def _show_phone_qr(self) -> None:
        """A window with a QR code per address (LAN, ZeroTier): scan it with
        the phone's camera and the page opens - no typing. Click a code to
        open it in this PC's browser. Like N1MMPingPong's phone lamp."""
        from tkinter import messagebox
        from . import qr
        if getattr(self, "web", None) is None:
            messagebox.showinfo("POUNCer", "The phone page is off ([web] enabled = false, "
                                "or its port is busy).", parent=self.root)
            return
        urls = self._phone_urls()
        if not urls:
            messagebox.showinfo("POUNCer", "No network address found yet - try again in a "
                                "minute.", parent=self.root)
            return
        old = getattr(self, "_qr_window", None)
        if old is not None:
            try:
                old.destroy()
            except tk.TclError:
                pass
        win = tk.Toplevel(self.root, bg="white")
        self._qr_window = win
        win.title("POUNCer \u2013 phone page")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        tk.Label(win, text="Scan with the phone's camera to open the POUNCer page",
                 bg="white", font=(theme.FONT, 10, "bold")).pack(padx=12, pady=(10, 4))
        row = tk.Frame(win, bg="white")
        row.pack(padx=8)
        import webbrowser
        for label, url in urls:
            cell = tk.Frame(row, bg="white")
            cell.pack(side="left", padx=8, pady=4)
            matrix = qr.qr_matrix(url)
            box, quiet = theme.px(6), 4
            side = (len(matrix) + 2 * quiet) * box
            canvas = tk.Canvas(cell, width=side, height=side, bg="white", highlightthickness=0,
                               cursor="hand2")
            canvas.pack()
            for r, line in enumerate(matrix):
                for c, dark in enumerate(line):
                    if dark:
                        x, y = (c + quiet) * box, (r + quiet) * box
                        canvas.create_rectangle(x, y, x + box, y + box, fill="black", width=0)
            canvas.bind("<Button-1>", lambda _e, u=url: webbrowser.open(u, new=2))
            tk.Label(cell, text=f"{label}\n{url[7:].rstrip('/')}", bg="white",
                     font=(theme.FONT, 9)).pack()
        tk.Label(win, text="The phone must be on the same network (or ZeroTier).\n"
                           "Click a code to open the page here. Other devices need TCP "
                           f"{self.web.port} allowed in the Windows Firewall.",
                 bg="white", fg="#555555", font=(theme.FONT, 8), justify="center").pack(pady=(2, 4))
        tk.Button(win, text="Close", command=win.destroy).pack(pady=(0, 10))
        win.bind("<Escape>", lambda _e: win.destroy())

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
        # your other programs (Settings > Startup & updates > Autostart) - not again after a restart.
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
        self._apply_cluster_server()           # [cluster] enabled: N1MM's telnet connects here
        self._check_firewall()                 # are POUNCer's firewall rules there?
        if getattr(self, "net", None) is not None:
            self.net.start()
        if self.cfg.web_enabled:
            self._start_web()
            if self.cfg.touch_view:
                self.root.after(1500, self._open_browser)
        else:
            self.web_label.configure(text="Phone view: **disabled** ([web] enabled = false)")
            self._show_addr(None)
        if self.cfg.team:
            self._start_team()
        self._ensure_rbn_clients()
        self.clock.start()              # PC clock check (check line), every 30 min
        if self.cwspeed is not None:
            self.cwspeed.start()        # N1MM's CW speed box, read once a second
        if self.scp is not None:
            self.scp.start()            # load MASTER.SCP, ask the server for a newer one (304 = current)
        if getattr(self.cfg, "cty_auto_update", False) and not self.cfg.cty_file:
            n1mm_cty = next((p for p in ([os.path.join(os.path.dirname(self.cfg.n1mm_ini),
                                                       "SupportFiles", "wl_cty.dat")]
                                         if self.cfg.n1mm_ini else []) + default_cty_paths()
                             if p.endswith("wl_cty.dat") and os.path.exists(p)), "")
            self._cty_up = CtyUpdater(self.cfg.run_history_dir or "cache", n1mm_path=n1mm_cty,
                                      url=self.cfg.cty_url, check_days=self.cfg.cty_check_days,
                                      update_n1mm=self.cfg.cty_update_n1mm,
                                      on_new=lambda p: self._web_queue.put(("cty", p)))
            self._cty_up.start()        # S53M's cty.dat every Friday (If-Modified-Since)
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
            self.web.set_phone_control(self.cfg.web_phone_control)
            self._pc_applied = self.cfg.web_phone_control
            self.web.set_settings_provider(
                lambda: {"items": settingsmod.current(self.cfg), "path": self.cfg.config_path},
                self._save_setting_web,
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
            self._show_addr(None)
            return
        self._refresh_web_address()
        log.info("phone view: %s", self.web_url)

    @staticmethod
    def addr_text(ip: str, network: str, port: int | None) -> str:
        """Top bar: 'ZeroTier 10.66.66.5:8080' / 'LAN 192.168.1.20:8080';
        no phone page: the IP only."""
        net = "ZeroTier" if network == "ZeroTier" else ("LAN" if network == "LAN" else "")
        where = f"{ip}:{port}" if port else f"{ip} (phone page off)"
        return f"{net} {where}".strip()

    def _show_addr(self, port, ip: str | None = None, network: str = "") -> None:
        """This PC's address next to the frequency (ZeroTier first, else LAN)."""
        lbl = getattr(self, "addr_label", None)
        if lbl is None:
            return
        if ip is None:
            try:
                ip, network = pick_web_address(getattr(self.cfg, "web_host", "0.0.0.0"))
            except Exception:  # noqa: BLE001
                ip, network = "", ""
        try:
            lbl.configure(text=self.addr_text(ip, network, port) if ip else "")
        except tk.TclError:
            pass

    def _refresh_web_address(self) -> None:
        """Work out the URL to show for the phone view (ZeroTier, else LAN)."""
        if self.web is None:
            return
        port = self.web.port
        ip, network = pick_web_address(self.cfg.web_host)
        self.web_url = f"http://{ip}:{port}"
        self._show_addr(port, ip, network)
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
        if getattr(self, "net", None) is not None:
            self.net.stop()
        if getattr(self, "_dxc", None) is not None:
            self._dxc.stop()
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
        for f in getattr(self, "_hq_feeds", {}).values():
            if f is not None:
                f.stop()
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
