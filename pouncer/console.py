"""Traffic console (Tools > Traffic console): what comes in and goes out.

Raw lines of every source as they arrive - RBN and the Telnet #1 - #5 (telnet
lines, and the commands sent), N1MM (UDP packets in, QSY / call commands
out), the HTTP backup feed, the team, the phone page's requests. The taps
(``traffic.emit``) only work while this window is open; closed = no work.
The red / green / yellow colour and the PT note (below) need ``spot_key`` to
recognize the line - the telnet "DX de ..." format (any source), or, for the
HTTP group only, HamQTH's bare line or the HTTP feed's JSON (v0.7.234); a
format it does not recognize keeps the group's own colour, never red.

**Colour is the publish outcome, nothing else** (v0.7.225): every spot line
starts **red** - not (yet) sent to port 10000. The moment the collector
actually sends the station out (a ``server >`` line), every spot line of
that station that came in at or before that moment turns **green**; one
that comes in **after** turns **yellow** ("merged into" its ``PT`` note -
see below - because another, earlier line is the one that was actually
sent). A line stays red forever when it is never sent at all: filtered out,
held, a repeat within 180 s, Relay off ... (hover a source, or the ``EXCL``
reason below, says why). This replaces the earlier scheme (yellow = matches
your filters, v0.7.203; orange = via the backup skimmers, v0.7.206; green =
a yellow line that got sent, v0.7.222) - whether a spot is sent now matters
more than whether it is "primary" or "backup", and a spot that is still
being decided (``SHOWN_WAIT_S`` / PSK Reporter) can still turn green the
moment it is sent, even before that decision is in.

Sources can be ticked off (they keep being counted; a line in the view marks
the switch), a filter word (a call, "DX de", "RadioInfo" ...) shows matching
lines only, Pause freezes the view (lines keep counting), Clear empties it.
The top bar shows per source: lines so far, "hidden", how long it has been
quiet, and why (``notes()`` from the app: RBN paused on SSB, no Telnet on,
via the team master ...). At most ``KEEP`` lines are kept in the window.

**EXCL** (v0.7.221): a spot line your filters keep out (and so will stay
red) gets the reason at its end, in grey - ``EXCL SPOTTER`` / ``EXCL
SPOTTED`` (the Spotters / Spotted filter), ``EXCL FT8`` / ``EXCL FT4`` (its
mode, an unmarked one in the FT8 window, or PSK Reporter has him there),
``EXCL BCN`` (beacon), ``EXCL WARC``, ``EXCL PEOPLE`` (a person's spot with
no Telnet on). With its own group unticked (``hide_group_for``), EXCL BCN /
WARC / FT8 / FT4 remove the whole raw line instead (v0.7.228 / 229 / 231) -
a WARC spot has no separate "kept out" summary line at all (unlike BEACON /
FT8), so its checkbox only ever works this way; the FT8 checkbox (shown as
"FT8/FT4") covers every FT8 / FT4 EXCL variant (plain, PSK Reporter, an
unmarked one in the FT8 window).

**PT** (published time; v0.7.218 as PB): when a spot goes out on port 10000 (a ``server >`` line),
every spot line of that station that came in shortly before (the same call
within ``PB_KHZ``, the last ``PB_LINK_S``) gets ``PT 09:48:55 +1s`` at its
end - the publish time (UTC) and how long after it came in; ``as G4ZFE-#`` =
the spot went out under another skimmer (merged), ``as SQ6OEP`` = under a
corrected call. A line that comes in after its station went out gets ``merged
into PT 09:48:55`` (that line turns yellow). No PT = not (yet) sent: filtered,
held, or one of a group still being collected (that line stays red). The
``PT`` note always keeps its own light orange colour, on every line colour -
green or yellow only reach up to where the note starts (v0.7.224; in
v0.7.222 green painted the note too, hiding it). A shown line that never
gets a PT at all - no new publish ever matched it, and nothing else ever
will - gets ``  not sent (likely a recent repeat)`` at its end instead,
once ``UNSENT_NOTE_S`` has passed with nothing (v0.7.230): the most common
real cause is the same station already published moments before this
console's own memory of it, or within the collector's / cluster server's
own repeat window, so a red line is never silently unexplained forever. An
already-EXCL'd line is left alone - that reason already explains the red.
"""

from __future__ import annotations

import json
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
NOT_SENT_COLOR = "#e53935"  # red: not (yet) sent to port 10000 - the default
SENT_COLOR = "#4caf50"      # green: this line's station went out (now or already)
MERGED_COLOR = "#ffeb3b"    # yellow: this line's station already went out via another line
PB_COLOR = "#ffcc80"       # "PT 09:48:55" - published to port 10000
EXCL_COLOR = "#9e9e9e"     # "EXCL WARC" - kept out by your filters
PB_LINK_S = 120            # a spot line links to a publish of its station this long after it
# ... and a line that comes in AFTER the publish is "merged into" it this long:
# the collector's own repeat window ([spot_processing] repeat_seconds, 180 s)
# when merge is on - the console asks the app (TrafficConsole ``merge_s``), so a
# repeat the collector held back 120 - 180 s after the send is yellow, not red
MERGE_LINK_S = 180
# when to give up waiting for a PT and say "not sent" (v0.7.230): well past
# PSK_WAIT_S, so the filter decision (EXCL or not) has always landed first -
# PB_LINK_S itself stays the merge-matching window only (pb_note), unrelated
# to how long a line is kept waiting for one
UNSENT_NOTE_S = PSK_WAIT_S + 20
PB_KHZ = 1.0               # ... within this much of the frequency
PB_BUSTED_KHZ = 0.2        # same skimmer, other call this close: a corrected call
_SPOT_RE = re.compile(r"DX de\s+([^\s:]+):?\s+(\d+(?:\.\d+)?)\s+(\S+)", re.IGNORECASE)


def spot_key(text: str, src: str = ""):
    """(call, kHz, spotter) of a spot line - a telnet 'DX de ...' line (any
    source), or, for the HTTP group only (v0.7.234): HamQTH's bare
    ``spotter freq call ...`` line, or the HTTP feed's JSON (``dxCall`` /
    ``freqKHz`` / ``spotterRaw`` or ``spotter``, matching ``rbnhttp.to_spot``)
    - else None. Without this, HamQTH / HTTP spots never got a PT note or a
    colour at all: ``spot_key`` (text only, no ``src``) never recognized
    them, so they never entered ``_waiting_pb`` in the first place."""
    m = _SPOT_RE.search(text or "")
    if m:
        try:
            khz = float(m.group(2))
        except ValueError:
            return None
        return m.group(3).upper(), khz, m.group(1).upper()
    if traffic.group_of(src) != "HTTP":
        return None
    t = (text or "").strip()
    if t.startswith("{"):
        try:
            d = json.loads(t)
            call = str(d.get("dxCall") or "").strip()
            khz = float(d["freqKHz"])
            spotter = str(d.get("spotterRaw") or d.get("spotter") or "").strip()
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            return None
        return (call.upper(), khz, spotter.upper()) if call and spotter else None
    parts = t.split()
    if len(parts) < 4:
        return None
    try:
        khz = float(parts[1])
    except ValueError:
        return None
    return parts[2].upper(), khz, parts[0].upper()


def pb_note(line_key, line_t: float, pb_key, pb_t: float,
            merge_s: float = PB_LINK_S) -> str:
    """The text a spot line gets when its station went out at ``pb_t``; ''
    when the publish is not about this line. A line before the publish
    links within ``PB_LINK_S``; one after it within ``merge_s`` (the
    collector's repeat window, so its held-back repeats read "merged into")."""
    call, khz, spotter = line_key
    pcall, pkhz, pspotter = pb_key
    if not (0 <= pb_t - line_t <= PB_LINK_S or 0 <= line_t - pb_t <= merge_s):
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
          "WARC": "#8d6e63", "OUT": "#ffeb3b"}
# a group's checkbox / chip text, where it differs from the GROUPS key itself
GROUP_LABEL = {"FT8": "FT8/FT4"}
# EXCL reason -> the group whose checkbox, unticked, removes that raw line
# too (not only its own "kept out" summary line, if it has one) (v0.7.228/229)
HIDE_WITH_GROUP = {"EXCL BCN": "BEACON", "EXCL WARC": "WARC", "EXCL 60M": "WARC"}
# EXCL FT8 / EXCL FT4 have several variants ("EXCL FT8", "EXCL FT4
# (unmarked, FT8 window)", "EXCL FT8 (PSK Reporter)" ...) - all of them
# mean the FT8/FT4 group (v0.7.231)
FT8_EXCL_PREFIXES = ("EXCL FT8", "EXCL FT4")


def hide_group_for(reason: str) -> str | None:
    """The group whose checkbox, unticked, removes a line with this EXCL
    reason - or None (it only ever gets the reason at its end)."""
    if reason.startswith(FT8_EXCL_PREFIXES):
        return "FT8"
    return HIDE_WITH_GROUP.get(reason)


class TrafficConsole:
    def __init__(self, root, notes=None, info=None, shown=None, merge_s=None) -> None:
        self.root = root
        self.merge_s = merge_s        # () -> seconds a later line counts as merged into a send
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
            cb = ttk.Checkbutton(bar, text=GROUP_LABEL.get(g, g), variable=self.show[g],
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
            # could not be read); only the lines below are coloured (v0.7.202)
            ttk.Label(bar, text="green = sent to port 10000, yellow = merged into "
                                "another spot's send (still has its own PT time), "
                                "red = never sent, PT = published to port 10000 (UTC), "
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
            lbl = ttk.Label(box, text=GROUP_LABEL.get(g, g), font=(theme.FONT, 9))
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
        # the publish outcome, nothing else (v0.7.225): red by default, green
        # once sent, yellow once merged into another line's send
        self.text.tag_configure("notsent", foreground=NOT_SENT_COLOR)
        self.text.tag_configure("merged", foreground=MERGED_COLOR, font=("Consolas", 9, "bold"))
        self.text.tag_configure("sent", foreground=SENT_COLOR, font=("Consolas", 9, "bold"))
        self.text.tag_configure("pb", foreground=PB_COLOR)
        self.text.tag_configure("excl", foreground=EXCL_COLOR)
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

    def _hidden_by_group(self, how) -> bool:
        """An EXCL reason whose own group is unticked (EXCL WARC / 60M with
        WARC, EXCL BCN with BEACON, EXCL FT8 ... with FT8/FT4): the line is
        not shown at all - before v0.7.246 it showed for SHOWN_WAIT_S first."""
        if how is None or how or not isinstance(how, str) or not str(how):
            return False
        group = hide_group_for(str(how))
        var = self.show.get(group) if group else None
        return var is not None and not var.get()

    def _drop_excluded_lines(self, g: str) -> None:
        """Group g unticked: remove the lines already shown with its EXCL
        reason (EXCL WARC / 60M, EXCL BCN, EXCL FT8 ...)."""
        reasons = [r for r, grp in HIDE_WITH_GROUP.items() if grp == g]
        if g == "FT8":
            reasons += list(FT8_EXCL_PREFIXES)
        if not reasons:
            return
        line = int(self.text.index("end-1c").split(".")[0])
        while line >= 1:
            body = self.text.get(f"{line}.0", f"{line}.end")
            if any(("  " + r) in body for r in reasons):
                self.text.delete(f"{line}.0", f"{line + 1}.0")
            line -= 1

    def _switched(self, g: str) -> None:
        """A source ticked on / off: say so in the view (else it looks stuck)."""
        on = self.show[g].get()
        if not on:
            try:
                self._drop_excluded_lines(g)
            except tk.TclError:
                pass
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
            self._sweep_unsent(time.time())    # real wall clock - add()'s own lines
                                                # may use a test's synthetic one (v0.7.230)
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
        """'EXCL WARC' ... at the end of a line the filters keep out (grey).
        With its own group unticked (``hide_group_for``), EXCL BCN / WARC /
        FT8 / FT4 remove the whole raw line instead (v0.7.228 / 229 / 231) -
        unticking BEACON or FT8 otherwise only hid the separate "kept out"
        summary line (``traffic.kept_out`` / ``digi_out``), not the raw line
        under its real source (telnet / rbn ...); WARC has no summary line
        at all, so its button only ever worked this way."""
        if how or not isinstance(how, str) or not str(how):
            return
        reason = str(how)
        group = hide_group_for(reason)
        if group:
            var = self.show.get(group)
            if var is not None and not var.get():
                try:
                    self.text.delete(f"{mark} linestart", f"{mark} linestart +1 line")
                except tk.TclError:
                    pass
                return
        try:
            self.text.insert(f"{mark} lineend", "  " + reason, "excl")
        except tk.TclError:
            pass

    def _mark_not_sent(self, mark: str) -> None:
        """Red by default - every spot line starts here (v0.7.225); a later
        ``_note_pb`` overrides it with green or yellow, in either order."""
        try:
            self.text.tag_add("notsent", f"{mark} linestart", f"{mark} lineend")
        except tk.TclError:
            pass

    def _color_end(self, mark: str) -> str:
        """Where to stop painting a line red / yellow / green: right before
        its own ``PT ...`` note, if it already has one - that note keeps its
        own light orange ("pb") colour instead of disappearing into it
        (v0.7.224)."""
        start, end = f"{mark} linestart", f"{mark} lineend"
        pb = self.text.tag_nextrange("pb", start, end)
        return pb[0] if pb else end

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
        """A ``PT ...`` note means this line's station went out: green - or
        yellow when it is "merged into" another line's send (v0.7.225)."""
        try:
            self.text.insert(f"{mark} lineend", note, "pb")
        except tk.TclError:
            pass
        try:
            tag = "merged" if note.strip().startswith("merged into") else "sent"
            self.text.tag_add(tag, f"{mark} linestart", self._color_end(mark))
        except tk.TclError:
            pass

    def _note_unsent(self, mark: str) -> None:
        """A spot line that got no PT at all within ``UNSENT_NOTE_S``: say
        so (v0.7.230), unless it already has its own EXCL reason - that
        already explains the red, and this would just be noise on top of
        it. Most likely cause: the same station, already sent moments
        before this console's own ``PB_LINK_S`` memory of it, or within the
        collector's / cluster server's own repeat window - a real
        duplicate, not a fresh one, even though nothing here proves which."""
        try:
            if "EXCL" not in self.text.get(f"{mark} linestart", f"{mark} lineend"):
                self.text.insert(f"{mark} lineend", "  not sent (likely a recent repeat)",
                                 "excl")
        except tk.TclError:
            pass

    def _published(self, key, t: float) -> None:
        """A spot went out on port 10000: PB on the lines of that station."""
        keep = []
        for mark, lkey, lt in self._waiting_pb:
            note = pb_note(lkey, lt, key, t) if lt <= t else ""
            if note:
                self._note_pb(mark, note)
            elif t - lt > UNSENT_NOTE_S:
                self._note_unsent(mark)
            if note or t - lt > UNSENT_NOTE_S:
                try:
                    self.text.mark_unset(mark)
                except tk.TclError:
                    pass
                continue
            keep.append((mark, lkey, lt))
        self._waiting_pb = keep
        keep_s = max(PB_LINK_S, self._merge_window())
        self._recent_pb = [(k, pt) for k, pt in self._recent_pb if t - pt <= keep_s]
        self._recent_pb.append((key, t))
        del self._recent_pb[:-500]

    def _merge_window(self) -> float:
        """How long after a send a later line of that station is "merged
        into" it: the app's answer (the collector's repeat window while
        merge is on), else ``PB_LINK_S``."""
        try:
            got = float(self.merge_s()) if self.merge_s is not None else PB_LINK_S
        except Exception:  # noqa: BLE001 - never break the console over it
            got = PB_LINK_S
        return got if got > 0 else PB_LINK_S

    def _sweep_unsent(self, now: float) -> None:
        """Lines that got no PT within ``UNSENT_NOTE_S`` and never will,
        because no later publish happened to trigger ``_published``'s own
        sweep either (v0.7.230) - called every tick, so this is the only
        thing that ever resolves a station nothing else ever publishes
        again."""
        if not self._waiting_pb:
            return
        keep = []
        for mark, lkey, lt in self._waiting_pb:
            if now - lt > UNSENT_NOTE_S:
                self._note_unsent(mark)
                try:
                    self.text.mark_unset(mark)
                except tk.TclError:
                    pass
                continue
            keep.append((mark, lkey, lt))
        self._waiting_pb = keep

    def _spot_in(self, mark: str, key, t: float) -> None:
        """A spot line came in: already sent (merged into) - or wait for its PB."""
        for pkey, pt in reversed(self._recent_pb):
            note = pb_note(key, t, pkey, pt, self._merge_window()) if t >= pt else ""
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
        """Spot lines not decided yet: shown by now? (until SHOWN_WAIT_S).
        Colour never depends on this (v0.7.225) - only whether it is kept
        out gets a reason here (EXCL); a line can turn green or yellow
        (``_note_pb``) at any time, decided or not."""
        if not self._pending:
            return
        now = time.time() if now is None else now
        keep = []
        for mark, src, txt, until in self._pending:
            how = self._is_shown(src, txt)
            if how and how != "wait":
                pass
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
            yes = None if out else self._is_shown(src, txt)
            if self._hidden_by_group(yes):
                continue                   # e.g. EXCL WARC with WARC unticked: never shown
            self.text.insert("end", time.strftime("%H:%M:%S ", time.gmtime(t)), "time")
            self.text.insert("end", f"{src:<16} ", "OUT" if out else g)
            self.text.insert("end", txt + "\n")
            shown += 1
            if not out:
                key = spot_key(txt, src)
                if key:
                    line = int(self.text.index("end-1c").split(".")[0]) - 1
                    pbm = f"pb{id(txt)}_{t}_{shown}"
                    self.text.mark_set(pbm, f"{line}.0")
                    self.text.mark_gravity(pbm, "left")
                    self._mark_not_sent(pbm)     # red by default (v0.7.225)
                    self._spot_in(pbm, key, t)
                if yes is not None:
                    line = int(self.text.index("end-1c").split(".")[0]) - 1
                    mark = f"sp{id(txt)}_{t}_{shown}"
                    self.text.mark_set(mark, f"{line}.0")
                    if yes and yes != "wait":
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
            elif g in ("FT8", "BEACON", "WARC"):   # nothing kept out lately: fine, not "dead"
                st = "why"
            elif notes.get(g):
                st = "why"
            else:
                st = "quiet" if quiet < DEAD_S else "dead"
            text = f"{GROUP_LABEL.get(g, g)} {self.counts.get(g, 0)}" + (
                f" ({', '.join(extra)})" if extra else "")
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
