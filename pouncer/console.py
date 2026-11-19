"""Traffic console (Tools > Traffic console): what comes in and goes out.

Raw lines of every source as they arrive - RBN and the Telnet #1 - #4 (telnet
lines, and the commands sent), N1MM (UDP packets in, QSY / call commands
out), the HTTP backup feed, the team, the phone page's requests. The taps
(``traffic.emit``) only work while this window is open; closed = no work.

Spot lines - of every source - that match the filters (the Spotters filter:
skimmers / zones, and the Spotted filter; not a dropped beacon) are drawn in
**yellow** (``shown`` from the app, v0.7.203), so the filtering can be
watched; a line that doesn't is looked at again for ``SHOWN_WAIT_S`` (the
backup skimmers can switch on meanwhile). A spot taken only because the
chosen skimmers are quiet and the backup skimmers are in use is **orange**
(v0.7.206). Once a yellow (primary, non-backup) line actually goes out on
port 10000 it turns **green** (v0.7.222) - backup (orange) lines keep their
colour even when they are the one that gets forwarded, so orange still means
"via the backup skimmers" at a glance.

Sources can be ticked off (they keep being counted; a line in the view marks
the switch), a filter word (a call, "DX de", "RadioInfo" ...) shows matching
lines only, Pause freezes the view (lines keep counting), Clear empties it.
The top bar shows per source: lines so far, "hidden", how long it has been
quiet, and why (``notes()`` from the app: RBN paused on SSB, no Telnet on,
via the team master ...). At most ``KEEP`` lines are kept in the window.

**EXCL** (v0.7.221): a spot line your filters keep out gets the reason at
its end, in grey - ``EXCL SPOTTER`` / ``EXCL SPOTTED`` (the Spotters /
Spotted filter), ``EXCL FT8`` / ``EXCL FT4`` (its mode, an unmarked one in the
FT8 window, or PSK Reporter has him there), ``EXCL BCN`` (beacon), ``EXCL
WARC``, ``EXCL PEOPLE`` (a person's spot with no Telnet on).

**PT** (published time; v0.7.218 as PB): when a spot goes out on port 10000 (a ``server >`` line),
every spot line of that station that came in shortly before (the same call
within ``PB_KHZ``, the last ``PB_LINK_S``) gets ``PT 09:48:55 +1s`` at its
end - the publish time (UTC) and how long after it came in; ``as G4ZFE-#`` =
the spot went out under another skimmer (merged), ``as SQ6OEP`` = under a
corrected call. A line that comes in after its station went out gets ``merged
into PT 09:48:55``. No PT = not (yet) sent: filtered, held, or one of a group
still being collected.
"""

from __future__ import annotations

import logging
import re
import time
import tkinter as tk
from tkinter import ttk

from . import theme, traffic

log = logging.getLogger(__name__)

KEEP = 3000
EVERY_MS = 250
QUIET_S = 5          # no line this long = "quiet" (amber dot)
DEAD_S = 30          # this long = red dot (unless the app says why: grey)
SHOWN_WAIT_S = 5     # a spot line: look again this long whether POUNCer shows it
PSK_WAIT_S = 160     # ... or this long when the app says "wait" (asking PSK Reporter)
SHOWN_COLOR = "#ffeb3b"
BACKUP_COLOR = "#ff9800"   # taken via the backup skimmers (chosen ones quiet)
PUBLISHED_COLOR = "#4caf50"  # a primary (non-backup) spot that went out on port 10000
PB_COLOR = "#ffcc80"       # "PT 09:48:55" - published to port 10000
EXCL_COLOR = "#9e9e9e"     # "EXCL WARC" - kept out by your filters
PB_LINK_S = 120            # a spot line links to a publish of its station this long after it
PB_KHZ = 1.0               # ... within this much of the frequency
PB_BUSTED_KHZ = 0.2        # same skimmer, other call this close: a corrected call
_SPOT_RE = re.compile(r"DX de\s+([^\s:]+):?\s+(\d+(?:\.\d+)?)\s+(\S+)", re.IGNORECASE)


def spot_key(text: str):
    """(call, kHz, spotter) of a 'DX de ...' line, else None."""
    m = _SPOT_RE.search(text or "")
    if not m:
        return None
    try:
        khz = float(m.group(2))
    except ValueError:
        return None
    return m.group(3).upper(), khz, m.group(1).upper()


def pb_note(line_key, line_t: float, pb_key, pb_t: float) -> str:
    """The text a spot line gets when its station went out at ``pb_t``; ''
    when the publish is not about this line."""
    call, khz, spotter = line_key
    pcall, pkhz, pspotter = pb_key
    if not (0 <= pb_t - line_t <= PB_LINK_S or 0 <= line_t - pb_t <= PB_LINK_S):
        return ""
    stamp = time.strftime("%H:%M:%S", time.gmtime(pb_t))
    if call == pcall and abs(khz - pkhz) <= PB_KHZ:
        if line_t > pb_t:
            return f"  merged into PT {stamp}"
        delay = f" +{pb_t - line_t:.0f}s"
        return f"  PT {stamp}{delay}" + ("" if spotter == pspotter else f" as {pspotter}")
    if spotter == pspotter and abs(khz - pkhz) <= PB_BUSTED_KHZ and line_t <= pb_t:
        return f"  PT {stamp} +{pb_t - line_t:.0f}s as {pcall}"      # a corrected call
    return ""
DOT = {"live": "#43a047", "quiet": "#f9a825", "dead": "#e53935", "why": "#78909c"}
GROUPS = traffic.GROUPS
group_of = traffic.group_of
COLORS = {"RBN": "#81c784", "TELNET": "#80cbc4", "HTTP": "#4fc3f7", "N1MM": "#ce93d8",
          "TEAM": "#90caf9", "WEB": "#b0bec5", "PSKR": "#f48fb1", "SERVER": "#ffcc80", "FT8": "#ff8a65", "BEACON": "#b0bec5",
          "OUT": "#ffeb3b"}


class TrafficConsole:
    def __init__(self, root, notes=None, info=None, shown=None) -> None:
        self.root = root
        self.notes = notes            # () -> {group: "paused - radio on SSB", ...}
        self.shown = shown            # (source, line) -> True / False / None (not a spot)
        self._pending: list = []      # [(mark, source, line, until)] not yet shown
        self._waiting_pb: list = []   # [(mark, (call, kHz, spotter), t)] spot lines not sent yet
        self._recent_pb: list = []    # [((call, kHz, spotter), t)] spots sent to port 10000
        self.info_text = info         # (group) -> what it is + its settings (hover)
        self._tip: tk.Toplevel | None = None
        self.win: tk.Toplevel | None = None
        self._after = None
        self.counts: dict[str, int] = {g: 0 for g in GROUPS}
        self.last: dict[str, float] = {}
        self.opened = time.time()
        self.paused = False

    def is_open(self) -> bool:
        try:
            return self.win is not None and bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def open(self) -> None:
        if self.is_open():
            self.win.deiconify()
            self.win.lift()
            return
        traffic.start()
        self.opened = time.time()
        win = tk.Toplevel(self.root)
        self.win = win
        win.title("POUNCer - traffic console")
        win.geometry(f"{theme.px(900)}x{theme.px(420)}")
        win.protocol("WM_DELETE_WINDOW", self.close)
        win.after(10, lambda w=win: theme.title_bar(w, True))
        bar = ttk.Frame(win, padding=(6, 4))
        bar.pack(fill="x")
        self.show = {g: tk.BooleanVar(value=g != "WEB") for g in GROUPS}
        for g in GROUPS:
            cb = ttk.Checkbutton(bar, text=g, variable=self.show[g],
                                 command=lambda g=g: self._switched(g))
            cb.pack(side="left", padx=(0, 6))
            self._hoverable(cb, g)
        # untick all sources at once (v0.7.175) - then tick only the one to
        # watch; with all off the button ticks them all again
        self.all_btn = ttk.Button(bar, text="None", width=5, command=self._toggle_all)
        self.all_btn.pack(side="left", padx=(0, 6))
        # filter and buttons on a row of their own: with ten sources one row
        # was wider than the window and cut the buttons off (v0.7.175)
        bar = ttk.Frame(win, padding=(6, 0, 6, 4))
        bar.pack(fill="x")
        ttk.Label(bar, text="filter").pack(side="left")
        self.filter = tk.StringVar()
        ttk.Entry(bar, textvariable=self.filter, width=14).pack(side="left", padx=(4, 8))
        self.pause_btn = ttk.Button(bar, text="Pause", command=self._toggle_pause)
        self.pause_btn.pack(side="left", padx=2)
        ttk.Button(bar, text="Clear", command=self._clear).pack(side="left", padx=2)
        ttk.Button(bar, text="Reset counters", command=self.reset_counts).pack(side="left", padx=2)
        if self.shown is not None:
            # the look's own text colour (yellow on the light look's grey bar
            # could not be read); only the lines below are yellow (v0.7.202)
            ttk.Label(bar, text="yellow = spots that match your filters, "
                                "green = those forwarded to port 10000, "
                                "orange = via backup skimmers, PT = published to port 10000 (UTC), "
                                "EXCL = why not",
                      font=(theme.FONT, 9)).pack(side="left", padx=(10, 0))
        # one chip per source: coloured dot (green = lines now, amber = quiet,
        # red = quiet long, grey = quiet for a known reason) + count / why
        self.info = ttk.Frame(win, padding=(8, 0, 8, 4))
        self.info.pack(fill="x")
        self.chips = {}
        d = theme.px(10)
        for n, g in enumerate(GROUPS):
            box = ttk.Frame(self.info)
            box.grid(row=n // 3, column=n % 3, sticky="w", padx=(0, 18))
            dot = tk.Canvas(box, width=d + 2, height=d + 2, highlightthickness=0, bd=0,
                            bg=theme.BG or "#1c1c1c")
            dot.create_oval(1, 1, d, d, fill=DOT["why"], outline="", tags="dot")
            dot.pack(side="left", padx=(0, 4))
            dot.state = "why"
            lbl = ttk.Label(box, text=g, font=(theme.FONT, 9))
            lbl.pack(side="left")
            self.chips[g] = (dot, lbl)
            for w in (dot, lbl):
                self._hoverable(w, g)
        for col in range(3):
            self.info.columnconfigure(col, weight=1)
        self.paused_lbl = ttk.Label(self.info, text="", font=(theme.FONT, 9, "bold"))
        self.paused_lbl.grid(row=0, column=3, sticky="e")
        # shown when the view is scrolled up and new lines came in below it
        self.below = tk.Label(win, text="", bg="#1565c0", fg="#ffffff", cursor="hand2",
                              font=(theme.FONT, 9, "bold"), pady=2)
        self.below.bind("<Button-1>", lambda e: self._follow())
        self._below_n = 0
        frame = tk.Frame(win, bg="#101418")
        self._frame = frame
        frame.pack(fill="both", expand=True)
        self.text = tk.Text(frame, bg="#101418", fg="#cfd8dc", insertbackground="#cfd8dc",
                            font=("Consolas", 9), wrap="none", bd=0, padx=6, pady=4)
        ys = ttk.Scrollbar(frame, orient="vertical", command=self.text.yview)
        xs = ttk.Scrollbar(frame, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        ys.pack(side="right", fill="y")
        xs.pack(side="bottom", fill="x")
        self.text.pack(fill="both", expand=True)
        for g, col in COLORS.items():
            self.text.tag_configure(g, foreground=col)
        self.text.tag_configure("time", foreground="#607d8b")
        self.text.tag_configure("mark", foreground="#607d8b", font=("Consolas", 9, "italic"))
        self.text.tag_configure("shown", foreground=SHOWN_COLOR, font=("Consolas", 9, "bold"))
        self.text.tag_configure("backup", foreground=BACKUP_COLOR, font=("Consolas", 9, "bold"))
        self.text.tag_configure("pb", foreground=PB_COLOR)
        self.text.tag_configure("excl", foreground=EXCL_COLOR)
        # highest priority (configured last): a primary spot that was actually
        # forwarded to port 10000 turns green, on top of its yellow "shown" tag
        self.text.tag_configure("published", foreground=PUBLISHED_COLOR, font=("Consolas", 9, "bold"))
        self._pending = []
        self._waiting_pb = []
        self._recent_pb = []
        self._tick()

    # ---- hover: what a source is and its settings ---------------------------------
    def tip_text(self, g: str) -> str:
        """The hover text of a source: the app's description + settings, and
        why it is quiet now (notes)."""
        text = ""
        if self.info_text is not None:
            try:
                text = self.info_text(g) or ""
            except Exception:  # noqa: BLE001 - a tooltip never breaks the console
                text = ""
        note = ""
        if self.notes is not None:
            try:
                note = (self.notes() or {}).get(g, "")
            except Exception:  # noqa: BLE001
                note = ""
        n = self.counts.get(g, 0)
        last = self.last.get(g)
        seen = (f"{n} lines, the last {time.time() - last:.0f} s ago" if last
                else f"{n} lines since the console opened")
        return "\n".join(x for x in (text, f"Now: {note}" if note else "", seen) if x)

    def _hoverable(self, widget, g: str) -> None:
        widget.bind("<Enter>", lambda e, g=g: self._show_tip(e, g), add="+")
        widget.bind("<Leave>", lambda e: self._hide_tip(), add="+")
        widget.bind("<ButtonPress>", lambda e: self._hide_tip(), add="+")

    def _show_tip(self, event, g: str) -> None:
        self._hide_tip()
        text = self.tip_text(g)
        if not text or self.win is None:
            return
        tip = tk.Toplevel(self.win)
        tip.wm_overrideredirect(True)
        try:
            tip.attributes("-topmost", True)
        except tk.TclError:
            pass
        head, _, body = text.partition("\n")
        frame = tk.Frame(tip, bg="#263238", bd=1, relief="solid", padx=8, pady=6)
        frame.pack()
        tk.Label(frame, text=head, bg="#263238", fg=COLORS.get(g, "#ffffff"), justify="left",
                 font=(theme.FONT, 9, "bold"), wraplength=theme.px(460)).pack(anchor="w")
        if body:
            tk.Label(frame, text=body, bg="#263238", fg="#eceff1", justify="left",
                     font=(theme.FONT, 9), wraplength=theme.px(460)).pack(anchor="w")
        tip.update_idletasks()
        x, y = event.x_root + 12, event.y_root + 18
        sw = tip.winfo_screenwidth()
        if x + tip.winfo_width() > sw:
            x = max(0, sw - tip.winfo_width() - 4)
        tip.geometry(f"+{x}+{y}")
        self._tip = tip

    def _hide_tip(self) -> None:
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None

    def _toggle_all(self) -> None:
        """None: untick every source (still counted). All: tick them all."""
        on = not any(v.get() for v in self.show.values())
        for v in self.show.values():
            v.set(on)
        self.text.insert("end", time.strftime("%H:%M:%S ", time.gmtime())
                         + ("--- all sources shown ---" if on
                            else "--- all sources hidden (still counted above) - "
                                 "tick the ones to watch ---") + "\n", "mark")
        if self._at_end():
            self.text.see("end")
        self._sync_all_btn()
        self._draw_chips()

    def _sync_all_btn(self) -> None:
        btn = getattr(self, "all_btn", None)
        if btn is not None:
            try:
                btn.configure(text="None" if any(v.get() for v in self.show.values())
                              else "All")
            except tk.TclError:
                pass

    def _switched(self, g: str) -> None:
        """A source ticked on / off: say so in the view (else it looks stuck)."""
        on = self.show[g].get()
        note = (self.notes() if self.notes else {}).get(g, "") if on else ""
        self.text.insert("end", time.strftime("%H:%M:%S ", time.gmtime())
                         + (f"--- {g} shown again" + (f" ({note})" if note else "") if on
                            else f"--- {g} hidden (still counted above)") + " ---\n", "mark")
        if self._at_end():
            self.text.see("end")
        self._sync_all_btn()
        self._draw_chips()

    def close(self) -> None:
        self._hide_tip()
        traffic.stop()                     # the taps go quiet again
        if self._after is not None:
            try:
                self.root.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None
        if self.win is not None:
            try:
                self.win.destroy()
            except tk.TclError:
                pass
        self.win = None

    def _toggle_pause(self) -> None:
        self.paused = not self.paused
        self.pause_btn.configure(text="Resume" if self.paused else "Pause")

    def _clear(self) -> None:
        self.text.delete("1.0", "end")
        self._drop_pending()
        self._drop_pb()
        self._follow()

    def reset_counts(self) -> None:
        """Counters back to 0 (the view stays); "quiet" counts from now."""
        self.counts = {g: 0 for g in GROUPS}
        self.last = {}
        self.opened = time.time()
        self.text.insert("end", time.strftime("%H:%M:%S ", time.gmtime())
                         + "--- counters reset ---\n", "mark")
        if self._at_end():
            self.text.see("end")
        self._draw_chips()

    def _at_end(self) -> bool:
        if not self.text.winfo_ismapped():     # not drawn yet: nothing is "below"
            return True
        return self.text.yview()[1] >= 0.999

    def _follow(self) -> None:
        """Back to the newest lines (the blue bar, or Clear)."""
        self.text.see("end")
        self._below_n = 0
        if self.below.winfo_manager():
            self.below.pack_forget()

    def _show_below(self, n: int) -> None:
        self._below_n += n
        self.below.configure(text=f"{self._below_n} new line"
                             f"{'' if self._below_n == 1 else 's'} below - click to follow")
        if not self.below.winfo_manager():
            self.below.pack(fill="x", side="bottom", before=self._frame)

    def _tick(self) -> None:
        self._after = None
        if not self.is_open():
            return
        if not traffic.active():           # someone stopped the tap: start it again
            traffic.start()
        try:
            self.add(traffic.drain())
        except Exception as exc:  # noqa: BLE001 - one bad line must not stop the console
            log.exception("traffic console")
            try:
                self.text.insert("end", f"--- console error: {exc} (it goes on) ---\n", "mark")
            except tk.TclError:
                pass
        finally:
            if self.is_open():
                self._after = self.root.after(EVERY_MS, self._tick)

    # ---- yellow: the spots POUNCer shows ---------------------------------------
    def _is_shown(self, src: str, txt: str):
        if self.shown is None:
            return None
        try:
            return self.shown(src, txt)
        except Exception:  # noqa: BLE001 - colour only: never stops the console
            return None

    def _note_excl(self, mark: str, how) -> None:
        """'EXCL WARC' ... at the end of a line the filters keep out (grey)."""
        if how or not isinstance(how, str) or not str(how):
            return
        try:
            self.text.insert(f"{mark} lineend", "  " + str(how), "excl")
        except tk.TclError:
            pass

    def _mark_shown(self, mark: str, how=True) -> None:
        """Yellow (matches the filters) or orange (``how == "backup"``); green
        instead of yellow when this line has already been noted as published
        to port 10000 (the PT note can arrive before or after this, v0.7.222)."""
        try:
            backup = how == "backup"
            self.text.tag_add("backup" if backup else "shown",
                              f"{mark} linestart", f"{mark} lineend")
            if not backup and self.text.tag_nextrange(
                    "pb", f"{mark} linestart", f"{mark} lineend"):
                self.text.tag_add("published", f"{mark} linestart", f"{mark} lineend")
        except tk.TclError:
            pass

    def _mark_published(self, mark: str) -> None:
        """The line a ``PT ...`` note was just added to: green, but only when
        it is a primary (non-backup) spot already marked yellow - a backup
        (orange) line keeps its colour, and a line not yet decided picks up
        green itself once ``_mark_shown`` runs (v0.7.222)."""
        try:
            if (self.text.tag_nextrange("shown", f"{mark} linestart", f"{mark} lineend")
                    and not self.text.tag_nextrange(
                        "backup", f"{mark} linestart", f"{mark} lineend")):
                self.text.tag_add("published", f"{mark} linestart", f"{mark} lineend")
        except tk.TclError:
            pass

    def _drop_pending(self) -> None:
        for mark, *_ in self._pending:
            try:
                self.text.mark_unset(mark)
            except tk.TclError:
                pass
        self._pending = []

    # ---- PB: when a spot went out on port 10000 ----------------------------------
    def _drop_pb(self) -> None:
        for mark, *_ in getattr(self, "_waiting_pb", []):
            try:
                self.text.mark_unset(mark)
            except tk.TclError:
                pass
        self._waiting_pb = []

    def _note_pb(self, mark: str, note: str) -> None:
        try:
            self.text.insert(f"{mark} lineend", note, "pb")
        except tk.TclError:
            pass
        self._mark_published(mark)

    def _published(self, key, t: float) -> None:
        """A spot went out on port 10000: PB on the lines of that station."""
        keep = []
        for mark, lkey, lt in self._waiting_pb:
            note = pb_note(lkey, lt, key, t) if lt <= t else ""
            if note:
                self._note_pb(mark, note)
            if note or t - lt > PB_LINK_S:
                try:
                    self.text.mark_unset(mark)
                except tk.TclError:
                    pass
                continue
            keep.append((mark, lkey, lt))
        self._waiting_pb = keep
        self._recent_pb = [(k, pt) for k, pt in self._recent_pb if t - pt <= PB_LINK_S]
        self._recent_pb.append((key, t))
        del self._recent_pb[:-500]

    def _spot_in(self, mark: str, key, t: float) -> None:
        """A spot line came in: already sent (merged into) - or wait for its PB."""
        for pkey, pt in reversed(self._recent_pb):
            note = pb_note(key, t, pkey, pt) if t >= pt else ""
            if note and pkey[0] == key[0]:
                self._note_pb(mark, note)
                self.text.mark_unset(mark)
                return
        if len(self._waiting_pb) >= 3000:
            old = self._waiting_pb.pop(0)
            try:
                self.text.mark_unset(old[0])
            except tk.TclError:
                pass
        self._waiting_pb.append((mark, key, t))

    def recheck(self, now: float | None = None) -> None:
        """Spot lines not yellow yet: shown by now? (until SHOWN_WAIT_S)."""
        if not self._pending:
            return
        now = time.time() if now is None else now
        keep = []
        for mark, src, txt, until in self._pending:
            how = self._is_shown(src, txt)
            if how and how != "wait":
                self._mark_shown(mark, how)
            elif now < until:
                keep.append((mark, src, txt, until))
                continue
            else:
                self._note_excl(mark, how)    # still kept out: say why
            try:
                self.text.mark_unset(mark)
            except tk.TclError:
                pass
        self._pending = keep

    def add(self, lines: list) -> None:
        """Show drained (time, source, text) lines - filtered, at the end."""
        want = self.filter.get().strip().upper()
        at_end = self._at_end()
        if at_end and self._below_n:          # scrolled down by hand: following again
            self._follow()
        shown = 0
        for t, src, txt in lines:
            if src == "server >":          # out on port 10000: PB on its lines (even if
                key = spot_key(txt)        # SERVER is unticked or filtered away)
                if key:
                    self._published(key, t)
            g = group_of(src)
            self.counts[g] = self.counts.get(g, 0) + 1
            self.last[g] = t
            var = self.show.get(g)
            if self.paused or (var is not None and not var.get()):
                continue
            if want and want not in txt.upper() and want not in src.upper():
                continue
            out = src.endswith(">")
            self.text.insert("end", time.strftime("%H:%M:%S ", time.gmtime(t)), "time")
            self.text.insert("end", f"{src:<16} ", "OUT" if out else g)
            self.text.insert("end", txt + "\n")
            shown += 1
            if not out:
                key = spot_key(txt)
                if key:
                    line = int(self.text.index("end-1c").split(".")[0]) - 1
                    pbm = f"pb{id(txt)}_{t}_{shown}"
                    self.text.mark_set(pbm, f"{line}.0")
                    self.text.mark_gravity(pbm, "left")
                    self._spot_in(pbm, key, t)
                yes = self._is_shown(src, txt)
                if yes is not None:
                    line = int(self.text.index("end-1c").split(".")[0]) - 1
                    mark = f"sp{id(txt)}_{t}_{shown}"
                    self.text.mark_set(mark, f"{line}.0")
                    if yes and yes != "wait":
                        self._mark_shown(mark, yes)
                        self.text.mark_unset(mark)
                    elif len(self._pending) < 500:
                        wait = PSK_WAIT_S if yes == "wait" else SHOWN_WAIT_S
                        self._pending.append((mark, src, txt, time.time() + wait))
                    else:
                        self._note_excl(mark, yes)
                        self.text.mark_unset(mark)
        self.recheck()
        if shown:
            n = int(self.text.index("end-1c").split(".")[0])
            if n > KEEP:
                self.text.delete("1.0", f"{n - KEEP}.0")
                self._drop_pending()          # their lines may be gone
                self._drop_pb()
            if at_end:
                self.text.see("end")
            else:
                self._show_below(shown)
        self._draw_chips()

    def states(self, now: float | None = None) -> dict:
        """{group: (dot state, text)}: live / quiet / dead / why."""
        now = time.time() if now is None else now
        notes = {}
        if self.notes is not None:
            try:
                notes = self.notes() or {}
            except Exception:  # noqa: BLE001
                notes = {}
        out = {}
        for g in GROUPS:
            extra = []
            var = getattr(self, "show", {}).get(g)
            if var is not None and not var.get():
                extra.append("hidden")
            quiet = now - self.last.get(g, self.opened)
            if quiet >= QUIET_S:
                extra.append(f"quiet {int(quiet // 60)}m" if quiet >= 120 else f"quiet {int(quiet)}s")
            if notes.get(g):
                extra.append(notes[g])
            if quiet < QUIET_S:
                st = "live"
            elif g in ("FT8", "BEACON"):    # nothing kept out lately: fine, not "dead"
                st = "why"
            elif notes.get(g):
                st = "why"
            else:
                st = "quiet" if quiet < DEAD_S else "dead"
            text = f"{g} {self.counts.get(g, 0)}" + (f" ({', '.join(extra)})" if extra else "")
            out[g] = (st, text)
        return out

    def _draw_chips(self) -> None:
        for g, (st, text) in self.states().items():
            dot, lbl = self.chips[g]
            if dot.state != st:
                dot.state = st
                dot.itemconfigure("dot", fill=DOT[st])
            if lbl.cget("text") != text:
                lbl.configure(text=text)
        self.paused_lbl.configure(text="PAUSED" if self.paused else "")

    def summary(self, now: float | None = None) -> str:
        """'RBN 120   SSB 0 (quiet 40s, spots come from N1MM) ...' (as the chips)."""
        return "   ".join(t for _, t in self.states(now).values())
