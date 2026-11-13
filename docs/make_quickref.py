"""POUNCer quick reference card: 2 pages A4 (PC window, phone page).

Remake it after a release (needs reportlab and the DejaVu fonts):
    python docs/make_quickref.py docs/POUNCer-quick-reference.pdf
The version is read from pouncer/__init__.py; a second argument overrides it.
"""
import sys
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate, Paragraph, Table,
                                TableStyle, Spacer, FrameBreak, NextPageTemplate, KeepTogether)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.fonts import addMapping

D = "/usr/share/fonts/truetype/dejavu/"
pdfmetrics.registerFont(TTFont("Sans", D + "DejaVuSansCondensed.ttf"))
pdfmetrics.registerFont(TTFont("SansB", D + "DejaVuSansCondensed-Bold.ttf"))
pdfmetrics.registerFont(TTFont("SansI", D + "DejaVuSansCondensed-Oblique.ttf"))
pdfmetrics.registerFont(TTFont("SansBI", D + "DejaVuSansCondensed-BoldOblique.ttf"))
pdfmetrics.registerFont(TTFont("Mono", D + "DejaVuSansMono.ttf"))
addMapping("Sans", 0, 0, "Sans"); addMapping("Sans", 1, 0, "SansB")
addMapping("Sans", 0, 1, "SansI"); addMapping("Sans", 1, 1, "SansBI")

import os
import re

_init = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pouncer", "__init__.py")
VERSION = (sys.argv[2] if len(sys.argv) > 2 else
           re.search(r'__version__\s*=\s*"([^"]+)"', open(_init, encoding="utf-8").read()).group(1))
OUT = sys.argv[1] if len(sys.argv) > 1 else "POUNCer-quick-reference.pdf"

INK = colors.HexColor("#1f2933")
MUTED = colors.HexColor("#5f6b7a")
ACCENT = colors.HexColor("#1565c0")
RULE = colors.HexColor("#d5dbe3")
BAND = colors.HexColor("#eef3fa")
GREEN, YELLOW, AMBER, RED, GREY, BLUE = ("#2e7d32", "#fdd835", "#fb8c00", "#c62828",
                                         "#9e9e9e", "#1565c0")

body = ParagraphStyle("b", fontName="Sans", fontSize=7.4, leading=9.0, textColor=INK)
small = ParagraphStyle("s", parent=body, fontSize=6.6, leading=8.0, textColor=MUTED)
key = ParagraphStyle("k", parent=body, fontName="SansB")
h2 = ParagraphStyle("h2", parent=body, fontName="SansB", fontSize=9.6, leading=11.5,
                    textColor=ACCENT, spaceBefore=5, spaceAfter=2.5)


def P(t, st=body):
    return Paragraph(t, st)


def dot(c):
    return f'<font name="Sans" color="{c}" size="10">●</font>'


def table(rows, w1, total, keycol=True):
    data = [[P(a, key if keycol else body), P(b)] for a, b in rows]
    t = Table(data, colWidths=[w1, total - w1])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2.5), ("RIGHTPADDING", (0, 0), (-1, -1), 2.5),
        ("TOPPADDING", (0, 0), (-1, -1), 1.6), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.6),
        ("LINEBELOW", (0, 0), (-1, -2), 0.3, RULE),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, BAND]),
    ]))
    return t


def section(title, rows, w1, total):
    return KeepTogether([P(title, h2), table(rows, w1, total)])


PAGE_W, PAGE_H = A4
M = 11 * mm
GAP = 6 * mm
COL = (PAGE_W - 2 * M - GAP) / 2
TOP = 19 * mm


def header(title, sub):
    def draw(c, doc):
        c.saveState()
        c.setFillColor(colors.HexColor("#1f2933"))
        c.rect(0, PAGE_H - 15 * mm, PAGE_W, 15 * mm, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont("SansB", 15)
        c.drawString(M, PAGE_H - 9.8 * mm, "POUNCer")
        c.setFont("Sans", 10.5)
        c.drawString(M + 27 * mm, PAGE_H - 9.8 * mm, f"quick reference  ·  {title}")
        c.setFont("Sans", 7.5)
        c.setFillColor(colors.HexColor("#cfd8dc"))
        c.drawRightString(PAGE_W - M, PAGE_H - 9.8 * mm, sub)
        c.setFont("Sans", 6.5)
        c.setFillColor(MUTED)
        c.drawString(M, 6.5 * mm, f"v{VERSION}  ·  every mark and colour: docs/SYMBOLS.md  ·  "
                     "every feature: README  ·  github.com/s55oo/POUNCer")
        c.drawRightString(PAGE_W - M, 6.5 * mm, f"{doc.page} / 2")
        c.restoreState()
    return draw


def frames():
    h = PAGE_H - TOP - 11 * mm
    return [Frame(M, 11 * mm, COL, h, id="l", leftPadding=0, rightPadding=0,
                  topPadding=0, bottomPadding=0),
            Frame(M + COL + GAP, 11 * mm, COL, h, id="r", leftPadding=0, rightPadding=0,
                  topPadding=0, bottomPadding=0)]


doc = BaseDocTemplate(OUT, pagesize=A4, title="POUNCer quick reference",
                      author="S55OO", subject=f"POUNCer v{VERSION}")
doc.addPageTemplates([
    PageTemplate("pc", frames(), onPage=header("the PC window", "Windows / macOS")),
    PageTemplate("phone", frames(), onPage=header("the phone page", "any phone or tablet, port 8080")),
])

W = COL
s = []
# ---------------- page 1: PC ------------------------------------------------
s.append(section("Click", [
    ("a spot", "QSY + the call into N1MM's Entry Window (the station keeps its place 60 s)"),
    ("a band chip", "list that band (▶); click again or change band = follow the radio"),
    ("✓ READY badge", "the pre-contest checks: ✓ good · ⚠ look at it · ✗ not working"),
    ("the address (top bar)", "QR code for the phone, one per address (LAN, ZeroTier)"),
    ("Radio A / B badge", "switch the radio that clicks and F-keys go to (SO2R / SO2V)"),
    ("SLAVE badge", "become the team master (asks first)"),
    ("Update light", "check GitHub now; blue = new version ready → Restart"),
    ("a column heading", "sort by it (▼ / ▲)"),
], 27 * mm, W))

s.append(section("Keys", [
    ("↑ ↓  Space", "select a spot (yellow frame) · Space = tap it"),
    ("Ctrl+M", "compact window beside N1MM (⤢ = back)".replace("⤢", "the arrow")),
    ("Ctrl+T", "spot list as tiles (like the phone)"),
    ("Ctrl+Z", "ZO mode: three columns ⚡ Urgent / Medium / Normal"),
    ("Ctrl+H", "hide worked stations"),
    ("Ctrl+L", "clear spots (CLR ALL); the phone's Undo brings them back"),
    ("Ctrl+,", "Settings (Ctrl+F search · Ctrl+S save · Esc clears the search / closes)"),
    ("Ctrl+Shift+D", "traffic console: raw lines of every source"),
], 19 * mm, W))
s.append(P("Shortcuts do nothing while you type in a box.", small))

s.append(KeepTogether([P("Lights", h2), table([
    (dot(GREEN) + " green", "working: this stream delivers"),
    (dot(YELLOW) + " yellow", "on hold: ready, not needed now (RBN on SSB …)"),
    (dot(AMBER) + " amber", "waiting, connecting, partly working · HamFiles: one file missing"),
    (dot(RED) + " red", "not working · HamFiles NOK: cty.dat and MASTER.DTA both missing"),
    (dot(GREY) + " grey", "switched off (the clean layout hides these)"),
    (dot(BLUE) + " blue", "Update: a new version is downloaded"),
], 15 * mm, W, keycol=False),
    P("RBN · N1MM · Log · Team · Phone · Cluster · HTTP · N1MM spots · Warm-up · "
      "HamFiles · Update - then the <b>Radio A/B</b> badge.", small)]))

s.append(section("Top bar badges", [
    ("✓ READY / ⚠ CHECK / ✗ NOT READY", "all good / look at something / something needed is down"),
    ("MASTER · SLAVE", "team: holds RBN and feeds the others · gets spots from the master"),
    ("ALONE · TEAM OFF", "no other POUNCer found · team switched off"),
    ("orange team badge", "a team POUNCer runs another version (hover: IP + version)"),
    ("CLR BAND · CLR ALL", "forget the spots of this band / of every band"),
], 34 * mm, W))

s.append(FrameBreak())
s.append(section("A spot (list or tile)", [
    ("<font color='#c62828'>K1ABC</font>", "red bold call = new multiplier on this band; <b>M</b> single, <b>MM</b> double (tile: pink bar, thin / thick)"),
    ("<font color='#8a8f98'>K1ABC</font>", "grey = already worked on this band and mode"),
    ("warm row / yellow call", "DX: another continent"),
    ("⚡", "historical opportunity: rarely runs, or few hours in past logs of this contest"),
    ("R84% ~23h", "ran 84 % of its QSOs, ~23 h on the air (its past logs); * = reconstructed"),
    ("?", "not in Super Check Partial: maybe a busted call"),
    ("↻3m×2", "you tapped it 3 min ago, twice"),
    ("★ TK0C-1", "your own skimmer heard it"),
    ("blue frame", "the radio is on this frequency (0.5 kHz)"),
    ("green tile · dark tile", "20 dB or more · not re-spotted lately, probably gone"),
    ("cq3m · 1200Z · 22dB", "calling 3 min · first heard (UTC) · skimmer SNR"),
], 27 * mm, W))

s.append(section("Band chips", [
    ("20m: 34 DX 85%", "34 DX stations heard by your continent's skimmers in 10 min, 85 % of all; 5+ DX = open"),
    ("· 6 mults", "6 spots there would be new multipliers"),
    ("80m: EU only · 160m: –", "only your own continent · nothing heard"),
    ("colour", "orange (own continent) → yellow → green (DX) · grey = nothing"),
    ("thin / thick ▶ frame", "the radio's band / the band listed"),
], 30 * mm, W))

s.append(section("ZO mode (View › ZO mode, Ctrl+Z)", [
    ("⚡ Urgent", "historical hint: catch it now"),
    ("Medium · Normal", "the rest by the same history; unknown history below Normal"),
], 22 * mm, W))
s.append(P("Hints from past logs, not a prediction of who leaves the band.", small))

s.append(section("Menus", [
    ("File", "config folder, Autostart ▸, Windows Firewall, restart, exit"),
    ("N1MM", "reconnect log, also enter callsign, station test"),
    ("View", "compact, always on top, detailed layout, tiles, ZO mode, rate line, look ▸"),
    ("Spots / Spotters / Spotted", "sources on / off · whose spots (skimmers) · which DX"),
    ("Tools", "summary, CQ zones, timeline, phone page ▸ (QR), alerts ▸, traffic console"),
    ("Team", "password, become master, master after restart, update all"),
    ("Settings", "All settings… or straight to a page: Display, Alerts & history, N1MM & radios, Phone page, Spots, Cluster server, Startup & updates"),
    ("Help", "Versions ▸ (check for a new version, go back), Troubleshooting ▸"),
], 25 * mm, W))

s.append(section("Something wrong?", [
    ("Help › Troubleshooting", "Save diagnostics: one zip (log, settings without passwords, perf log, team settings) - send it"),
    ("N1MM › Station test", "QSY and callsign entry, per radio"),
    ("cache\\pouncer.log", "what POUNCer did · cache\\perf.csv: CPU / memory per minute"),
], 29 * mm, W))

# ---------------- page 2: phone ---------------------------------------------
s.append(NextPageTemplate("phone"))
s.append(FrameBreak())
s.append(section("First time", [
    ("Open", "scan the QR code (PC: Tools › Phone page › QR code…), same Wi-Fi or ZeroTier"),
    ("Look only (dimmed keys)", "a phone starts look-only: tap the frequency → band menu → <b>Control N1MM</b>"),
    ("on for all phones", "the PC (team: the master) set Phones may control N1MM = all"),
    ("Reset", "acts oddly in one browser only: <b>? › Reset this page</b>"),
], 22 * mm, W))

s.append(section("Tap and hold", [
    ("tap a tile", "QSY + call into N1MM (yellow ring: keeps its place until logged, or 60 s)"),
    ("hold a tile 2 s", "Remove (off the list) · Debug (why it looks like this)"),
    ("tap the frequency / title", "band menu: pick a band, several bands, ALL bands, alerts on / off"),
    ("hold SNR 1.3 s", "set the minimum SNR (≥10 shown under SNR)"),
    ("hold ENTER 1.3 s", "CW keyboard: type = sent as you type · DONE ⏎ · STOP (Esc)"),
    ("tap ping 23ms", "min / avg / max of the last 10 min (green < 150 ms, amber < 500)"),
], 27 * mm, W))

s.append(section("Keys", [
    ("A / B", "which radio this phone works (blue A, orange B)"),
    ("CLR", "clear all spots; Undo for 5 s"),
    ("Time · Freq · SNR", "sort (green = active)"),
    ("DX · MLT · ⚡", "put DX / new mults / rare CQs on top"),
    ("F-keys · ENTER", "N1MM's keys: they <b>transmit</b> · ENTER logs"),
    ("SPOTit", "N1MM's Spot It (Alt+P), no TX"),
    ("WPM − / +", "CW speed (PgDn / PgUp)"),
    ("−25 Hz / +25 Hz", "move the VFO, no TX"),
    ("NEXT ▶", "the next station: QSY + call, no TX"),
    ("↻ Log", "re-read which log N1MM has open"),
], 24 * mm, W))

s.append(section("CQ zones page", [
    ("blue box (hears me)", "skimmers in that zone spotted you on that band; number = best SNR"),
    ("green box (I hear)", "your continent's skimmers heard stations from that zone"),
    ("brighter · dark", "stronger (3 → 30 dB) · nothing in the time chosen (15 min)"),
    ("yellow column", "your own CQ zone"),
], 26 * mm, W))

s.append(section("Band opening timeline", [
    ("a row · a box", "one band (160 m on top) · 10 min, or 1 hour, UTC left to right"),
    ("orange → green", "only your continent → mostly DX; brighter = more spots"),
    ("dark · yellow frame", "nothing heard · now"),
], 26 * mm, W))

s.append(FrameBreak())
s.append(section("A tile", [
    ("green / grey", "20 dB or more / weaker"),
    ("yellow call", "DX (another continent); white = your continent"),
    ("pink stripe", "new multiplier: thin single, thick double"),
    ("light blue outline", "your radio is on this frequency"),
    ("darker tile", "not re-spotted for 5 min, probably gone"),
    ("⚡ · ? · ↻3m", "rare CQ · not in SCP · you tapped it 3 min ago"),
    ("R6% ~4h", "run share and hours in this contest's past logs"),
    ("1132Z · 22dB · cq3m", "first heard · SNR · calling 3 min"),
], 24 * mm, W))

s.append(section("After a tap", [
    ("✓ radio on 7012.0 · call in", "done: N1MM moved and the call is in"),
    ("waiting for N1MM…", "N1MM slow to confirm; QSY sent again, watching 8 s"),
    ("⚠ never reported", "no confirmation in 10 s: look at the radio"),
    ("⚠ no answer from POUNCer", "no answer in 8 s: is POUNCer running?"),
], 31 * mm, W))

s.append(section("Status line", [
    ("(+12 weak)", "hidden by the minimum SNR"),
    ("calm", "tiles keep their place, re-sorted every 15 s"),
    ("⚠ cty (or another)", "a pre-contest check found something - tap it"),
    ("20m list · ALL bands list", "you picked a band / all bands - tap to follow the radio"),
    ("⚠ backup skimmers", "main skimmer quiet: others on your continent used"),
], 29 * mm, W))

s.append(section("Band menu (tap the frequency)", [
    ("dot by a band", "band open light: orange (own continent) → green (DX), DX count when open"),
    ("pink 6M", "6 spots there would be new multipliers"),
    ("Select multiple bands", "then Show selected bands: their spots on one list"),
    ("ALL bands", "every band on one list, for hunting mults (MLT puts them on top)"),
    ("Control N1MM", "this phone may tune and send keys (off = look only)"),
    ("Alerts", "pop-up and sound for a rare CQ that is a new mult, each on its own"),
], 29 * mm, W))

doc.build(s)
print("written", OUT)
