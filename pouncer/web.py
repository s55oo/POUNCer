"""A tiny local web server: the phone / tablet / browser view of POUNCer.

Standard library only (``http.server``). The heavy lifting stays in the app:
this module just serves a mobile page, a JSON feed of the currently visible
spots (plus whether N1MM has a log open), and endpoints that ask the app to
act on a spot, press a key, or reconnect to the N1MM log. The GUI is not
thread safe, so callbacks are expected to marshal work onto the Tk thread
(the app pushes actions onto a queue drained by its refresh tick).
"""

from __future__ import annotations

import json
import logging
import re
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlparse

from . import __version__
from .bands import CONTEST_BANDS

log = logging.getLogger(__name__)

SpotsProvider = Callable[[], dict]
QSYHandler = Callable[[str, float, str], None]
KeyHandler = Callable[[str], None]
ReconnectHandler = Callable[[], None]
BandHandler = Callable[[str], None]
HideHandler = Callable[[str, float, bool], None]  # (call, freq kHz, hide?)
TuneHandler = Callable[[int], None]  # VFO step in Hz (+/-)
# filter width ("narrow"/"wide"), fix=True: toggle the radio, keep the label
FilterHandler = Callable[[str, bool], None]
ClearHandler = Callable[[bool], None]  # True = undo (bring the cleared spots back)


def is_send_key(key: str) -> bool:
    """True for keys the phone page may send: F1-F12, Enter, PgUp, PgDn."""
    return normalize_key(key) is not None


CW_OPS = ("open", "text", "back", "done", "stop")
_CW_CHARS = re.compile(r"[^A-Z0-9 /?.,=+\-@!():;'\"<>&]")


def clean_cw_text(text: str) -> str:
    """What may go into N1MM's CW window: upper case letters, digits, space
    and CW punctuation (no control keys); at most 200 characters."""
    return _CW_CHARS.sub("", (text or "").upper())[:200]


def normalize_key(key: str) -> str | None:
    """Canonical key name (F4 / Enter / PgUp / PgDn) or None if not allowed."""
    key = (key or "").strip()
    if re.fullmatch(r"F([1-9]|1[0-2])", key.upper()):
        return key.upper()
    low = key.lower()
    if low in ("enter", "return", "cr"):
        return "Enter"
    if low in ("pgup", "pageup", "prior"):
        return "PgUp"
    if low in ("pgdn", "pagedown", "next"):
        return "PgDn"
    return None


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<meta name="theme-color" content="#101418">
<title>POUNCer __VERSION__</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  body { margin: 0; background: #101418; color: #fff;
    font-family: system-ui, "Segoe UI", Roboto, sans-serif; }
  header { position: sticky; top: 0; z-index: 2; background: #0b0f12;
    padding: 10px 14px; border-bottom: 1px solid #223; }
  header .band { font-size: 16px; font-weight: 700; }
  header .meta { font-size: 11px; opacity: .6; margin-top: 2px; }
  .metarow { display: flex; align-items: center; gap: 8px; }
  .metarow .meta { flex: 1 1 auto; min-width: 0; }
  .metarow .help { flex: 0 0 auto; width: 22px; height: 22px; border-radius: 50%;
    background: #263238; color: #cfd8dc; font-size: 13px; font-weight: 700;
    text-align: center; line-height: 22px; text-decoration: none; margin-top: 2px; }
  /* --row: spot tile height, --gap: space between tiles; the key column on
     the right uses them so one key = exactly two spot rows. */
  :root { --row: 84px; --gap: 10px; --kw: 160px; --kfont: 26px; }
  .layout { display: flex; align-items: flex-start; }
  .grid { display: grid; grid-auto-flow: column; gap: var(--gap); padding: var(--gap);
    flex: 1 1 auto; min-width: 0; }
  button.tile { display: flex; flex-direction: column; align-items: center;
    justify-content: center; gap: 2px; min-height: var(--row); border: 0;
    border-radius: 14px; background: #263238; color: #fff; font: inherit;
    padding: 12px 10px; cursor: pointer; }
  button.tile.strong { background: #1b5e20; }
  /* the station you tapped: yellow border until you tap another one (or it
     leaves the list); wins over the blue "tuned to" outline */
  /* selection ring is drawn with box-shadow (always follows the rounded
     corners, also on older iOS where outlines are square) - see the
     combined rules after the mult bars below */
  /* the station the radio is tuned to (spot within 0.5 kHz of the radio) */
  button.tile.onfreq { outline: 3px solid #4fc3f7; outline-offset: -3px;
    box-shadow: 0 0 0 2px #4fc3f7, 0 0 14px rgba(79,195,247,.55); }
  .call { font-size: 32px; font-weight: 800; letter-spacing: .5px; }
  .call.dx { color: #ffeb3b; }  /* DX = outside our continent */
  .detail { font-size: 13px; opacity: .8; }
  /* Phones (single column): half-height tiles, call left / details right,
     so about twice as many spots fit on the screen. */
  @media (max-width: 619px) {
    :root { --row: 46px; --gap: 6px; --kw: 92px; --kfont: 21px; }
    .grid { padding: var(--gap) var(--gap) var(--gap) 8px; }
    button.tile { flex-direction: row; justify-content: space-between;
      padding: 4px 12px; border-radius: 10px; gap: 8px; }
    button.tile .call { font-size: 24px; white-space: nowrap; }
    /* long portable calls (VP2V/W1AW) stay on one line */
    button.tile .call.long { font-size: 19px; }
    button.tile .call.xlong { font-size: 16px; }
    /* details on two short lines (freq mode / SNR time) next to the call */
    button.tile .detail { font-size: 13px; text-align: right; white-space: nowrap;
      line-height: 1.25; }
    button.tile .detail > span { display: block; }
  }
  .empty { padding: 24px; opacity: .6; font-size: 16px; }
  /* footer: CW speed -/+, VFO -/+ step, NEXT station (no TX) */
  .footbar { position: fixed; left: 0; right: 0; bottom: 0; z-index: 3;
    /* thirds: CW speed left, VFO step in the middle, NEXT right */
    display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px;
    padding: 6px 8px calc(6px + env(safe-area-inset-bottom)); background: #0b0f12;
    border-top: 1px solid #223; }
  .footbar .fgroup { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
  .footbar button { border: 0; border-radius: 10px; color: #fff; font: inherit;
    font-size: 16px; font-weight: 700; padding: 12px 0; cursor: pointer;
    white-space: nowrap;
    transition: background .12s ease; }
  .footbar .tune { background: #1565c0; }
  .footbar .wpm { background: #6a1b9a; }
  /* N1MM's CW speed between WPM - and + (read from its Entry Window) */
  .footbar .fgroup.wpmg { grid-template-columns: 1fr auto 1fr; }
  .footbar .wpmval { align-self: center; min-width: 2.4em; text-align: center;
    font-size: 18px; font-weight: 800; color: #ce93d8; }
  .footbar .wpmval[hidden] { display: none; }
  /* NEXT: the next station of the list (QSY + call), one wide button */
  .footbar .next { grid-column: 1 / -1; background: #00897b; letter-spacing: .5px; }
  .footbar button:active, .footbar button.sel { background: #c62828; color: #fff; }
  @media (max-width: 619px) { .footbar button { font-size: 14px; } }
  body { padding-bottom: var(--ftr, 0px); }
  /* pop-up message: level with the ENTER key, over the spot list (set from
     JS, placeToast); faded out when hidden; 5 s, tap to close */
  #toast { position: fixed; left: 50%;
    top: calc(var(--hdr, 0px) + 12px);
    transform: translateX(-50%) translateY(-12px); opacity: 0; visibility: hidden;
    pointer-events: none; max-width: calc(100vw - 32px);
    background: #37474f; color: #fff; padding: 12px 18px; border-radius: 12px;
    font-size: 16px; font-weight: 600; z-index: 10; border: 2px solid #cfd8dc;
    box-shadow: 0 6px 24px rgba(0, 0, 0, .7);
    transition: transform .15s ease, opacity .15s ease, visibility 0s linear .15s; }
  .call .rare { font-size: .8em; margin-left: 4px; }
  .call .busted { color: #ffb74d; margin-left: 3px; font-weight: 700; }
  .detail .fq { font-weight: 800; color: #fff; }  /* spot frequency stands out */
  button.tile.stale { opacity: .42; }  /* not re-spotted lately: probably gone */
  .meta .warn { color: #ffb74d; font-weight: 700; }
  button.tile.tight .call { font-size: 18px; }
  button.tile.tight .call.long, button.tile.tight .call.xlong { font-size: 15px; }
  button.tile.tight .detail { font-size: 11px; }
  /* narrow phones: a bit smaller details + padding so long rows still fit */
  @media (max-width: 380px) {
    button.tile { padding: 4px 8px; gap: 6px; }
    button.tile .detail { font-size: 12px; }
    button.tile .call { font-size: 22px; }
    button.tile .call .rare { margin-left: 2px; }
  }
  #toast[hidden] { display: none !important; }
  #toast.show { transform: translateX(-50%) translateY(0); opacity: 1;
    visibility: visible; pointer-events: auto;
    transition: transform .15s ease, opacity .15s ease, visibility 0s; }
  #toast button { margin-left: 12px; background: #fff; color: #263238; border: 0;
    border-radius: 8px; padding: 6px 12px; font: inherit; font-weight: 700; }
  /* long-press on a tile: no iOS text selection / callout menu */
  button.tile, .footbar button { -webkit-touch-callout: none; -webkit-user-select: none; user-select: none; }
  button.tile.holding { filter: brightness(.7); transition: filter 2s linear; }
  #rmsheet .rm { background: #c62828; }
  .top { display: flex; align-items: center; gap: 10px; }
  /* SO2R radio A / B switch (only shown when N1MM reports two radios) */
  .sorts button.radiosw { background: #1565c0; color: #fff; font-weight: 800; }
  .sorts button.radiosw.b { background: #ef6c00; }
  .sorts button.radiosw[hidden] { display: none; }
  /* SO2R: the A/B key is as wide as CLR; the row gets a bit tighter so the
     frequency still fits (only while the switch is shown) */
  .sorts button.radiosw { min-width: 2.6em; }
  /* SO2R: two frequencies, A over B */
  button.band.ab { padding-top: 2px; padding-bottom: 2px; padding-right: 0; flex: 0 0 auto; }
  button.band.ab::after { display: none; }
  button.band .fl.cur::after { content: " \u25be"; font-size: 11px; opacity: .6; }
  button.band .two { display: flex; flex-direction: column; line-height: 1.15; }
  button.band .fl { font-size: 12px; font-weight: 400; opacity: .55; }
  button.band .fl.cur { font-size: 16px; font-weight: 700; opacity: 1; }
  button.band .fl i { font-style: normal; font-weight: 700; color: #64b5f6; }
  button.band .fl.r2 i { color: #ffb74d; }
  @media (max-width: 480px) { button.band .fl.cur { font-size: 15px; } }
  @media (max-width: 400px) { button.band .fl.cur { font-size: 14px; }
    button.band .fl { font-size: 11px; } }
  @media (max-width: 480px) {
    header.so2r .sorts { gap: 2px; }
    header.so2r .sorts button { padding: 7px 4px; font-size: 13px; }
    header.so2r .sorts button.radiosw { min-width: 2.4em; }
    header.so2r button.band::after { display: none; }
    header.so2r .top { gap: 4px; }
    header.so2r button.band { font-size: 15px; padding-right: 2px; }
  }
  @media (max-width: 400px) {
    header.so2r .sorts button { padding: 7px 3px; }
  }
  @media (max-width: 370px) {
    header.so2r .sorts button { padding: 6px 2px; font-size: 12px; }
  }

  .top .title { flex: 1 1 auto; min-width: 0; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis; }
  .sorts { display: flex; gap: 6px; align-items: center; font-size: 13px; flex-wrap: wrap; }
  button.band { background: none; border: 0; color: inherit; font: inherit;
    font-size: 16px; font-weight: 700; padding: 6px 8px 6px 0; text-align: left;
    cursor: pointer; }
  button.band::after { content: " ▾"; font-size: 12px; opacity: .6; }
  /* Min SNR: "SNR≥10" pill next to the sort keys (teal = on), sheet with presets */
  /* SNR key: hold 1.3 s (fills teal) = minimum SNR sheet; "≥10" under it when on */
  .sorts button#snrsort { position: relative; }
  .sorts button#snrsort .snrbadge { position: absolute; left: 0; right: 0; bottom: 1px;
    font-size: 9px; line-height: 1; font-weight: 800; color: #80cbc4; text-align: center; }
  .sorts button#snrsort.active .snrbadge { color: #e0f2f1; }
  @keyframes snrfill { from { box-shadow: inset 0 0 0 0 #00897b; }
                       to { box-shadow: inset 0 -60px 0 0 #00897b; } }
  .sorts button#snrsort.holding { animation: snrfill 1.3s linear forwards; }
  #snrsheet .bandgrid { grid-template-columns: repeat(4, 1fr); }
  #snrsheet .bandgrid button { font-size: 19px; padding: 13px 0; }
  #snrsheet .snrstep { display: grid; grid-template-columns: 1fr 1.4fr 1fr; gap: 8px;
    margin-top: 8px; align-items: center; }
  #snrsheet .snrstep button { background: #37474f; color: #fff; border: 0; border-radius: 10px;
    font: inherit; font-size: 22px; font-weight: 800; padding: 10px 0; cursor: pointer; }
  #snrsheet .snrval { text-align: center; font-size: 24px; font-weight: 800; color: #80cbc4; }
  .weaktag { color: #80cbc4; }
  /* already tried (tapped): small blue mark after the call */
  button.tile .call .tried { font-size: 13px; font-weight: 700; color: #90caf9;
    margin-left: 5px; vertical-align: middle; }
  /* pre-contest check: shown only when something is wrong (tap = details) */
  .chktag { font-weight: 700; cursor: pointer; }
  .chktag.warn { color: #ffd54f; } .chktag.bad { color: #ff8a80; }
  #bandsheet, #rmsheet, #snrsheet { display: none; position: fixed; inset: 0; z-index: 20;
    background: rgba(0,0,0,.55); align-items: flex-start; justify-content: center; }
  #bandsheet.show, #rmsheet.show, #snrsheet.show { display: flex; }
  #bandsheet .sheet, #rmsheet .sheet, #snrsheet .sheet { margin-top: 64px; width: min(420px, calc(100% - 24px));
    background: #1a2127; border-radius: 14px; padding: 14px; }
  .sheet-title { font-weight: 700; margin-bottom: 10px; }
  .sheet-title span { font-weight: 400; opacity: .6; font-size: 13px; }
  a.helplink { display: block; text-align: center; text-decoration: none; color: #cfd8dc;
    margin-bottom: 8px; }
  .bandgrid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
  .bandgrid button { background: #263238; color: #fff; border: 0; border-radius: 10px;
    font: inherit; font-size: 22px; font-weight: 800; padding: 16px 0; cursor: pointer; }
  .bandgrid button.cur { background: #2e7d32; }
  .bandgrid button { position: relative; }
  /* band open dot: green = open to DX (number = DX stations), amber = only
     our continent, grey = nothing (skimmers on our continent, 10 min) */
  .bopen { position: absolute; top: 5px; right: 6px; min-width: 10px; height: 10px;
    border-radius: 6px; font-size: 11px; line-height: 10px; padding: 2px 4px; font-weight: 800; }
  .bopen.open { background: #43a047; color: #fff; }
  .bopen.local { background: #f9a825; color: #222; padding: 0; min-width: 10px; width: 10px; }
  .bopen.closed { background: #546e7a; padding: 0; min-width: 10px; width: 10px; }
  /* new multipliers spotted on that band now (pink = mult, as the tile stripe) */
  .bmult { position: absolute; bottom: 4px; right: 6px; background: #ec407a; color: #fff;
    border-radius: 8px; padding: 0 5px; font-size: 11px; font-weight: 700; line-height: 15px; }
  .footbar .wpm[hidden] { display: none; }
  .bandgrid button.here { box-shadow: inset 0 0 0 2px #4fc3f7; }
  .viewtag { color: #4fc3f7; font-weight: 700; cursor: pointer; }
  #bandfor i { font-style: normal; color: #64b5f6; } #bandfor i.r2 { color: #ffb74d; }
  .bandgrid button:active { background: #c62828; }
  .sheet-close { margin-top: 10px; width: 100%; background: #37474f; color: #fff;
    border: 0; border-radius: 10px; font: inherit; padding: 12px; cursor: pointer; }
  header .band .khz { font-size: 12px; font-weight: 400; opacity: .7; }
  header .meta b { color: #fff; }
  header .meta .cur { color: #ffeb3b; font-weight: 700; }
  header .meta { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 4px; }
  @media (max-width: 480px) {
    .sorts > span, .sorts .lbl, .band .app, .band .khz, .band .modes, .meta .cnr { display: none; }
    header .sorts { gap: 3px; flex-wrap: nowrap; }
    header .sorts button { padding: 7px 6px; font-size: 14px; }
    header .sorts button.reconnect { margin-left: 2px; padding: 7px 9px; }
    header .top { gap: 6px; }
  }
  @media (max-width: 400px) {
    header .sorts { gap: 2px; }
    header .sorts button { padding: 7px 4px; font-size: 13px; }
    header .sorts button.reconnect { padding: 7px 7px; }
    header .top { gap: 4px; }
  }
  @media (max-width: 370px) {
    button.band::after { display: none; }
    header .sorts button { padding: 6px 3px; font-size: 12px; }
  }
  .sorts > span { opacity: .55; margin-right: 2px; }
  .sorts button { white-space: nowrap; background: #263238; color: #fff; border: 0; padding: 7px 14px;
    border-radius: 8px; font: inherit; cursor: pointer; }
  .sorts button.active { background: #2e7d32; }
  .sorts button.from::after { content: '\u2191'; font-size: 0.8em; }
  button.tile.fresh { background-image: linear-gradient(#ffd54f, #ffd54f);
    background-size: 100% 3px; background-repeat: no-repeat; }
  .calmtag { color: #80cbc4; font-weight: 700; }
  /* DX-on-top toggle: yellow like the DX callsigns when on */
  .sorts button.dxfirst { color: #ffeb3b; font-weight: 700; }
  .sorts button.dxfirst.on { background: #f9a825; color: #1a1a1a; }
  /* MLT toggle and multiplier marks: magenta */
  .sorts button.multfirst { color: #f48fb1; font-weight: 700; }
  .sorts button.multfirst.on { background: #d81b60; color: #fff; }
  .sorts button.rarefirst { color: #ffd54f; font-weight: 700; }
  .sorts button.clr { color: #ef9a9a; font-weight: 700; }
  .sorts button.clr:active { background: #c62828; color: #fff; }
  .sorts button.rarefirst.on { background: #6d4c41; color: #fff; }
  button.tile.m1 { box-shadow: inset 6px 0 0 #ec407a; }
  button.tile.m2 { box-shadow: inset 12px 0 0 #ec407a; }
  button.tile.onfreq.m1 { box-shadow: inset 6px 0 0 #ec407a, 0 0 0 2px #4fc3f7,
    0 0 14px rgba(79,195,247,.55); }
  button.tile.onfreq.m2 { box-shadow: inset 12px 0 0 #ec407a, 0 0 0 2px #4fc3f7,
    0 0 14px rgba(79,195,247,.55); }
  /* the station you tapped: rounded yellow ring (+ mult bar / tuned glow) */
  button.tile.sel { outline: none;
    box-shadow: inset 0 0 0 3px #ffeb3b; }
  button.tile.sel.m1 { box-shadow: inset 6px 0 0 #ec407a, inset 0 0 0 3px #ffeb3b; }
  button.tile.sel.m2 { box-shadow: inset 12px 0 0 #ec407a, inset 0 0 0 3px #ffeb3b; }
  button.tile.sel.onfreq { box-shadow: inset 0 0 0 3px #ffeb3b, 0 0 0 2px #4fc3f7,
    0 0 14px rgba(79,195,247,.55); }
  button.tile.sel.onfreq.m1 { box-shadow: inset 6px 0 0 #ec407a, inset 0 0 0 3px #ffeb3b,
    0 0 0 2px #4fc3f7, 0 0 14px rgba(79,195,247,.55); }
  button.tile.sel.onfreq.m2 { box-shadow: inset 12px 0 0 #ec407a, inset 0 0 0 3px #ffeb3b,
    0 0 0 2px #4fc3f7, 0 0 14px rgba(79,195,247,.55); }
  /* TX / log keys: a column on the right, F2 at the top; each key is two
     spot rows tall. Sticky, so it stays put while the list scrolls. */
  .fkeys { flex: 0 0 var(--kw); width: var(--kw); display: flex;
    flex-direction: column; gap: var(--gap); padding: var(--gap) var(--gap) var(--gap) 0;
    position: sticky; top: var(--hdr, 0px); box-sizing: border-box;
    max-height: calc(100vh - var(--hdr, 0px) - var(--ftr, 0px));
    max-height: calc(100dvh - var(--hdr, 0px) - var(--ftr, 0px)); }
  .fkeys:empty { display: none; }
  /* two spot rows tall; shrinks only if the window is too short for all keys */
  .fkeys button { flex: 0 1 calc(2 * var(--row) + var(--gap)); min-height: 44px; width: 100%;
    white-space: nowrap; background: #2e7d32; color: #fff; border: 0; padding: 0 4px;
    border-radius: 12px; font: inherit; font-size: var(--kfont); font-weight: 700;
    cursor: pointer; transition: background .12s ease; }
  /* red while pressed, and for a moment after the tap (.sel) */
  .fkeys button:active, .fkeys button.sel { background: #c62828; }
  /* ENTER held: fills up in 1.3 s, then the CW keyboard opens */
  @keyframes holdfill { from { box-shadow: inset 0 0 0 0 #f9a825; }
                        to { box-shadow: inset 0 -120px 0 0 #f9a825; } }
  .fkeys button.holding { animation: holdfill 1.3s linear forwards; }
  /* CW keyboard (hold ENTER): at the top, so the phone keyboard can't hide it */
  #cwkb { position: fixed; left: 0; right: 0; top: 0; z-index: 60; padding: 10px;
    background: #263238; box-shadow: 0 4px 16px rgba(0,0,0,.5); }
  #cwkb[hidden] { display: none; }
  #cwkb .cwhead { color: #fff; font-weight: 800; margin-bottom: 6px; display: flex;
    align-items: center; gap: 8px; }
  #cwkb .cwtx { background: #c62828; color: #fff; border-radius: 6px; padding: 1px 8px;
    font-size: 13px; }
  #cwkb .cwhint { color: #b0bec5; font-weight: 400; font-size: 13px; margin-left: auto; }
  #cwin { width: 100%; box-sizing: border-box; font: 700 22px/1.2 ui-monospace, Consolas, monospace;
    padding: 10px; border-radius: 8px; border: 2px solid #f9a825; background: #fff; color: #111;
    text-transform: uppercase; }
  #cwkb .cwbtns { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 8px; }
  #cwkb .cwbtns button { border: 0; border-radius: 10px; color: #fff; font: inherit;
    font-size: 17px; font-weight: 800; padding: 12px 0; cursor: pointer; }
  #cwstop { background: #c62828; }
  #cwdone { background: #2e7d32; }
  .fkeys button { display: flex; flex-direction: column; align-items: center;
    justify-content: center; gap: 2px; line-height: 1.1; }
  .fkeys .fk { font-size: var(--kfont); font-weight: 800; }
  .fkeys .fl { font-size: calc(var(--kfont) * 0.6); font-weight: 600; opacity: .85;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; }
  .fkeys .hint { font-size: 12px; opacity: .55; align-self: center; }
  #logwarn { display: none; margin-top: 8px; padding: 12px 14px; border-radius: 10px;
    background: #ff8f00; color: #1a1a1a; font-size: 17px; font-weight: 700;
    align-items: center; gap: 12px; flex-wrap: wrap; }
  #logwarn.show { display: flex; }
  #logwarn .msg { flex: 1 1 220px; }
  #logwarn button, .sorts button.reconnect { background: #1a1a1a; color: #fff; border: 0;
    padding: 9px 16px; border-radius: 8px; font: inherit; font-weight: 700; cursor: pointer; }
  .sorts button.reconnect { margin-left: 6px; background: #37474f; font-weight: 400; }
</style>
</head>
<body>
<header>
  <div class="top">
  <button class="title band" id="band" title="Change band">POUNCer</button>
  <div class="sorts">
    <span>Sort:</span>
    <button class="radiosw" id="radiosw" hidden title="SO2R: radio A or B - taps, keys and the band list go to this radio (N1MM Entry Window A / B)">A</button>
    <button class="clr" id="clr" title="Clear all spots (e.g. old spots before the contest). Undo for 5 s">CLR</button>
    <button data-sort="time">Time</button>
    <button data-sort="freq">Freq</button>
    <button data-sort="snr" id="snrsort" title="Sort by SNR. Hold 1.3 s: minimum SNR (weaker spots not listed)">SNR</button>
    <button class="dxfirst" id="dxfirst" title="DX (outside your continent) on top">DX</button>
    <button class="multfirst" id="multfirst" title="New multipliers on top: double, then single">MLT</button>
    <button class="rarefirst" id="rarefirst" title="Rare CQ on top: stations that rarely run or are on only a few hours (from past logs)">\u26a1</button>
    <button class="reconnect" id="reconnect" title="Re-read which log N1MM has open">&#x21bb;<span class="lbl"> Log</span></button>
  </div>
  </div>
  <div class="metarow"><div class="meta" id="meta">connecting...</div>
    <a class="help" href="/summary" title="Contest summary: QSOs, rate, mults from spots">&Sigma;</a>
    <a class="help" href="/help" title="What every button does">?</a></div>
  <div id="logwarn"><span class="msg" id="logmsg"></span>
    <button id="logbtn">Reconnect</button></div>
</header>
<main class="layout">
  <div class="grid" id="grid"></div>
  <aside class="fkeys" id="fkeys"></aside>
</main>
<footer class="footbar" id="footbar">
  <div class="fgroup wpmg">
    <button class="wpm" data-key="PgDn" title="CW speed down (N1MM PgDn)">WPM &minus;</button>
    <span class="wpmval" id="wpmval" hidden title="N1MM's CW speed (WPM)"></span>
    <button class="wpm" data-key="PgUp" title="CW speed up (N1MM PgUp)">WPM +</button>
  </div>
  <div class="fgroup">
    <button class="tune" data-tune="-1" title="Move the VFO down (no TX)">&minus;25 Hz</button>
    <button class="tune" data-tune="1" title="Move the VFO up (no TX)">+25 Hz</button>
  </div>
  <div class="fgroup">
    <button class="next" id="next" title="Next station of the list (in the order shown): QSY + call, like tapping its tile. No TX">NEXT &#9654;</button>
  </div>
</footer>
<div id="toast" hidden></div>
<div id="cwkb" hidden>
  <div class="cwhead">CW keyboard <span id="cwradio"></span><span class="cwtx">TX</span>
    <span class="cwhint">sent as you type</span></div>
  <input id="cwin" type="text" autocomplete="off" autocorrect="off" autocapitalize="characters"
    spellcheck="false" enterkeyhint="done" placeholder="type here - N1MM sends it as CW">
  <div class="cwbtns">
    <button id="cwstop" title="Esc in N1MM: stop sending at once">STOP (Esc)</button>
    <button id="cwdone" title="Enter in N1MM: send the rest, close the CW window">DONE &#9166;</button>
  </div>
</div>
<div id="snrsheet"><div class="sheet">
  <div class="sheet-title">Minimum SNR <span>(weaker spots are not listed - here and in the window)</span></div>
  <div class="bandgrid" id="snrpresets"></div>
  <div class="snrstep">
    <button id="snrdown">&minus;1</button>
    <div class="snrval" id="snrval">-</div>
    <button id="snrup">+1</button>
  </div>
  <button class="sheet-close" id="snrclose">Close</button>
</div></div>
<div id="rmsheet" class="sheetwrap"><div class="sheet">
  <div class="sheet-title" id="rmtitle">Remove from the list?</div>
  <div class="bandgrid" style="grid-template-columns: 1fr">
    <button class="rm" id="rmyes">Remove</button>
  </div>
  <button class="sheet-close" id="rmno">Cancel</button>
</div></div>
<div id="bandsheet"><div class="sheet">
  <div class="sheet-title">Band<span id="bandfor"></span> <span>(shows its spots - tap a station to QSY)</span></div>
  <div class="bandgrid" id="bandgrid"></div>
  <button class="sheet-close" id="alertbtn">Alerts: on</button>
  <a class="sheet-close helplink" href="/summary">Contest summary</a>
  <a class="sheet-close helplink" href="/zones">CQ zones: who hears me</a>
  <a class="sheet-close helplink" href="/timeline">Band opening timeline</a>
  <a class="sheet-close helplink" href="/settings">Settings</a>
  <a class="sheet-close helplink" href="/help">Help: what every button does</a>
  <button class="sheet-close" id="bandclose">Cancel</button>
</div></div>
<script>
  const grid = document.getElementById('grid');
  const bandEl = document.getElementById('band');
  const metaEl = document.getElementById('meta');
  const fkeysEl = document.getElementById('fkeys');
  const toastEl = document.getElementById('toast');
  const logWarnEl = document.getElementById('logwarn');
  const logMsgEl = document.getElementById('logmsg');
  let toastTimer = null;
  const PAGE_VERSION = '__VERSION__';   // filled in by POUNCer when it sends the page
  let lastData = {band: '', modes: [], spots: []};
  let lastWpm = {r: 0, v: 0};
  let sortKey = localStorage.getItem('pouncer.sort') || 'snr';
  let dxFirst = false;
  let multFirst = false;
  try { dxFirst = localStorage.getItem('pouncer.dxfirst') === '1'; } catch (e) {}
  try { multFirst = localStorage.getItem('pouncer.multfirst') === '1'; } catch (e) {}
  // SO2R: radio this phone works (1 = A, 2 = B); null = POUNCer's default
  let myRadio = null;
  try { const v = localStorage.getItem('pouncer.radio'); if (v === '1' || v === '2') myRadio = Number(v); } catch (e) {}
  // Band menu: the band whose spots a radio's list shows, without moving the
  // radio ({radio: {band, from}}; from = the radio's band when picked - once
  // the radio changes band, e.g. by tapping a station there, the list
  // follows the radio again). Tapping a station QSYs as always.
  let views = {};
  try { views = JSON.parse(localStorage.getItem('pouncer.view') || '{}') || {}; } catch (e) { views = {}; }
  function saveViews() { try { localStorage.setItem('pouncer.view', JSON.stringify(views)); } catch (e) {} }
  function rp(params) {           // add ?radio= to a request when chosen
    const q = new URLSearchParams(params || {});
    if (myRadio) q.set('radio', myRadio);
    return q;
  }
  let rareFirst = false;
  try { rareFirst = localStorage.getItem('pouncer.rarefirst') === '1'; } catch (e) {}
  // Test options (on / off on the help page; Freq tapped again = freqFrom):
  // freqFrom - Freq sort starts at the radio frequency and goes up the band
  //            (wraps to the bottom), so the top is always the next one up.
  // calm     - tiles keep their place: new stations are added at the end
  //            (marked), the list re-sorts only every CALM_RESORT_MS and
  //            never within CALM_HOLD_MS of a touch / scroll.
  let freqFrom = false, calm = false;
  try { freqFrom = localStorage.getItem('pouncer.freqfrom') === '1'; } catch (e) {}
  try { calm = localStorage.getItem('pouncer.calm') === '1'; } catch (e) {}
  const CALM_RESORT_MS = 15000, CALM_HOLD_MS = 3000;
  let calmOrder = [], calmSig = '', calmSorted = 0, lastTouch = 0;
  let calmNew = new Set();
  const calmKey = s => s.call + '|' + Math.round(s.freq / 1000);

  function esc(s) {
    return String(s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }
  // Pop-up level with the ENTER key (the last key on the right), centred over
  // the spot list so it never covers the keys; no keys: just above the footer.
  function placeToast() {
    const keys = document.querySelectorAll('#fkeys button');
    const grid = document.getElementById('grid').getBoundingClientRect();
    const h = toastEl.offsetHeight;
    let top;
    if (keys.length) {
      const k = keys[keys.length - 1].getBoundingClientRect();
      top = k.top + k.height / 2 - h / 2;
    } else {
      const f = document.getElementById('footbar').getBoundingClientRect();
      top = f.top - h - 12;
    }
    top = Math.max(8, Math.min(top, window.innerHeight - h - 8));
    toastEl.style.top = top + 'px';
    toastEl.style.left = ((grid.left + grid.right) / 2) + 'px';
    toastEl.style.maxWidth = Math.max(160, grid.width - 16) + 'px';
  }
  function toast(msg, undo, ms) {
    toastEl.textContent = msg;
    if (undo) {
      const u = document.createElement('button');
      u.textContent = 'Undo';
      u.onclick = undo;
      toastEl.appendChild(u);
    }
    // Shown: put it back in the page, then fade in on the next frame.
    clearTimeout(toastTimer);
    clearTimeout(toastGone);
    toastEl.hidden = false;
    placeToast();
    void toastEl.offsetWidth;
    toastEl.classList.add('show');
    toastTimer = setTimeout(hideToast, ms || 5000);
  }
  // Hidden: fade out, then take it out of the page completely (display:none),
  // so no box or ghost can stay on screen (iOS kept one before).
  let toastGone = null;
  function hideToast() {
    clearTimeout(toastTimer);
    toastEl.classList.remove('show');
    clearTimeout(toastGone);
    toastGone = setTimeout(() => { toastEl.hidden = true; }, 200);
  }
  toastEl.addEventListener('click', (e) => {
    if (e.target === toastEl) hideToast();
  });
  function columns() {
    return window.innerWidth >= 620 ? 2 : 1;
  }
  function snrVal(s) {
    return s.snr == null ? -999 : s.snr;
  }
  function timeVal(s) {
    const m = /([0-9]{2})([0-9]{2})Z/.exec(s.time || '');
    return m ? parseInt(m[1], 10) * 60 + parseInt(m[2], 10) : -1;
  }
  // Rare CQ: runs under rare_run % of its QSOs, or on the air under
  // rare_hours hours, in its last log of this contest (run history).
  function isRare(s) {
    const d = lastData || {};
    return (s.run != null && s.run < (d.rare_run || 30)) ||
           (s.hours != null && s.hours < (d.rare_hours || 6));
  }
  // \u26a1 sort: rare first, then lowest run %, then fewest hours; hours
  // only (logs without frequencies) after those; unknown last.
  function rareKey(s) {
    if (s.run == null && s.hours == null) return 1e6;
    const h = Math.min(s.hours == null ? 99 : s.hours, 99) / 10;
    if (s.run == null) return (isRare(s) ? 0 : 1e4) + 1000 + h;
    return (isRare(s) ? 0 : 1e4) + s.run * 10 + h;
  }
  // Alert: a rare CQ (\u26a1) that is also a new multiplier appears on the
  // band -> beep (+ vibrate where the phone can) and a pop-up. Once per
  // station and band; nothing for what is already listed when the page opens.
  let alertsOn = true;
  try { alertsOn = localStorage.getItem('pouncer.alerts') !== '0'; } catch (e) {}
  const alerted = new Set();
  let alertsReady = false;
  let audioCtx = null;
  function unlockAudio() {  // browsers allow sound only after a tap
    try {
      if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      if (audioCtx.state === 'suspended') audioCtx.resume();
    } catch (e) {}
  }
  document.addEventListener('pointerdown', unlockAudio, {passive: true});
  function beep() {
    if (!audioCtx) return;
    try {
      [0, 0.22].forEach(t => {
        const o = audioCtx.createOscillator(), g = audioCtx.createGain();
        o.frequency.value = 1000; g.gain.value = 0.15;
        o.connect(g); g.connect(audioCtx.destination);
        o.start(audioCtx.currentTime + t); o.stop(audioCtx.currentTime + t + 0.12);
      });
    } catch (e) {}
  }
  function checkAlerts(spots) {
    const fresh = [];
    spots.forEach(s => {
      if (!(isRare(s) && (s.mult || 0) >= 1)) return;
      const k = s.call + '|' + bandOf(s.freq);
      if (!alerted.has(k)) { alerted.add(k); fresh.push(s); }
    });
    if (!alertsReady) { alertsReady = true; return; }  // page just opened
    if (!fresh.length || !alertsOn) return;
    beep();
    if (navigator.vibrate) { try { navigator.vibrate([120, 60, 120]); } catch (e) {} }
    const s = fresh[0];
    toast('\u26a1 rare CQ + new mult: ' + s.call + ' ' + s.freq.toFixed(1));
  }
  function bandOf(khz) { return Math.round(khz / 1000); }
  function cqText(m) {
    return m < 60 ? m + 'm' : Math.floor(m / 60) + 'h' + String(m % 60).padStart(2, '0');
  }
  function sorted(spots, radioKhz) {
    const a = spots.slice();
    if (sortKey === 'freq' && freqFrom && radioKhz > 0) {
      // from the radio frequency up (the station you're on first), then wrap
      const k = f => f >= radioKhz - 0.3 ? f - radioKhz : f - radioKhz + 1e7;
      a.sort((x, y) => k(x.freq) - k(y.freq));
    }
    else if (sortKey === 'freq') a.sort((x, y) => x.freq - y.freq);
    else if (sortKey === 'time') a.sort((x, y) => timeVal(y) - timeVal(x));
    else a.sort((x, y) => snrVal(y) - snrVal(x));
    // \u26a1 button: rare CQ first (see rareKey), unknown last. DX / MLT still
    // group on top of it.
    if (rareFirst) a.sort((x, y) => rareKey(x) - rareKey(y));
    // DX button: DX spots first, each group keeping the chosen sort order
    // (Array.sort is stable, so this only moves DX ahead of EU).
    if (dxFirst) a.sort((x, y) => (y.dx ? 1 : 0) - (x.dx ? 1 : 0));
    // MLT button: double mults, then single, then the rest (stable, so the
    // chosen order - and DX-first - is kept inside each group).
    if (multFirst) a.sort((x, y) => (y.mult || 0) - (x.mult || 0));
    return a;
  }
  // Calm list: keep the tiles where they were between re-sorts.
  function arrange(data) {
    const full = sorted(data.spots, data.freq);
    const now = Date.now();
    const sig = [sortKey, freqFrom, dxFirst, multFirst, rareFirst, data.band, data.radio].join('|');
    if (!calm || sig !== calmSig || !calmOrder.length ||
        (now - calmSorted >= CALM_RESORT_MS && now - lastTouch >= CALM_HOLD_MS)) {
      calmSig = sig; calmSorted = now; calmNew = new Set();
      calmOrder = full.map(calmKey);
      return full;
    }
    const byKey = new Map();
    full.forEach(s => { if (!byKey.has(calmKey(s))) byKey.set(calmKey(s), s); });
    const kept = calmOrder.filter(k => byKey.has(k));
    const keep = new Set(kept);
    const added = [];
    byKey.forEach((s, k) => { if (!keep.has(k)) { added.push(k); calmNew.add(k); } });
    calmOrder = kept.concat(added);
    return calmOrder.map(k => byKey.get(k));
  }
  function markSort() {
    document.querySelectorAll('.sorts button[data-sort]').forEach(b =>
      b.classList.toggle('active', b.dataset.sort === sortKey));
    document.getElementById('dxfirst').classList.toggle('on', dxFirst);
    document.getElementById('multfirst').classList.toggle('on', multFirst);
    document.getElementById('rarefirst').classList.toggle('on', rareFirst);
    const fb = document.querySelector('.sorts button[data-sort="freq"]');
    fb.classList.toggle('from', freqFrom);
    fb.title = freqFrom ? 'By frequency from your radio up (tap again: whole band from the bottom)'
                        : 'By frequency (tap again: from your radio frequency up)';
  }
  // CLR: clear every spot (e.g. old ones from before the contest started);
  // new spots come in again as RBN sends them. Undo in the pop-up for 5 s.
  async function clearSpots(undo) {
    try {
      const r = await fetch('/api/clear' + (undo ? '?undo=1' : ''));
      const j = await r.json();
      if (!j.ok) { toast('Failed: ' + (j.error || 'error')); return; }
      if (undo) { toast('Spots are back'); }
      else {
        const n = lastData && lastData.spots ? lastData.spots.length : 0;
        toast('Cleared ' + n + ' spot' + (n === 1 ? '' : 's'), () => clearSpots(true));
      }
      setTimeout(refresh, 400);
    } catch (e) {
      toast('Failed: ' + e);
    }
  }
  document.getElementById('clr').onclick = () => clearSpots(false);
  const alertBtn = document.getElementById('alertbtn');
  function labelAlerts() {
    alertBtn.textContent = 'Alerts (rare CQ + new mult): ' + (alertsOn ? 'on' : 'off');
  }
  labelAlerts();
  alertBtn.onclick = () => {
    alertsOn = !alertsOn;
    try { localStorage.setItem('pouncer.alerts', alertsOn ? '1' : '0'); } catch (e) {}
    labelAlerts();
    unlockAudio();
    if (alertsOn) beep();
  };
  document.getElementById('rarefirst').onclick = () => {
    rareFirst = !rareFirst;
    try { localStorage.setItem('pouncer.rarefirst', rareFirst ? '1' : '0'); } catch (e) {}
    render(lastData);
  };
  document.getElementById('multfirst').onclick = () => {
    multFirst = !multFirst;
    try { localStorage.setItem('pouncer.multfirst', multFirst ? '1' : '0'); } catch (e) {}
    render(lastData);
  };
  document.getElementById('dxfirst').onclick = () => {
    dxFirst = !dxFirst;
    try { localStorage.setItem('pouncer.dxfirst', dxFirst ? '1' : '0'); } catch (e) {}
    render(lastData);
  };
  function renderFkeys(fkeys) {
    const list = fkeys || [];
    const sig = JSON.stringify(list);
    if (fkeysEl.dataset.sig === sig) return;
    fkeysEl.dataset.sig = sig;
    fkeysEl.textContent = '';
    list.forEach(f => {
      const b = document.createElement('button');
      // Big key name (F2 / ENTER) with the label underneath in small text,
      // unless the label is just the key name again.
      const big = String(f.key).toUpperCase();
      const small = (f.label && f.label.toUpperCase() !== big) ? f.label : '';
      b.innerHTML = '<span class="fk">' + esc(big) + '</span>' +
        (small ? '<span class="fl">' + esc(small) + '</span>' : '');
      const isEnter = /^enter$/i.test(f.key);
      b.title = isEnter ? 'Enter - log the QSO' : f.key + ' (transmits)';
      if (isEnter) {
        b.classList.add('log');
        armHold(b);
        b.title += ' (hold 1.3 s: CW keyboard)';
      }
      b.onclick = () => {
        if (b.dataset.held === '1') { b.dataset.held = ''; return; }  // was a hold
        sendKey(f.key, b);
      };
      fkeysEl.appendChild(b);
    });
  }
  // ---- CW keyboard: hold ENTER 1.3 s; typing goes to N1MM's Ctrl+K window
  const cwEl = document.getElementById('cwkb');
  const cwIn = document.getElementById('cwin');
  let cwPrev = '', cwChain = Promise.resolve(), cwErrN = 0;
  function armHold(b) {
    let timer = null;
    const end = () => { clearTimeout(timer); b.classList.remove('holding'); };
    b.addEventListener('pointerdown', () => {
      b.dataset.held = '';
      if (!lastData.cw_keyboard) return;
      b.classList.add('holding');
      clearTimeout(timer);
      timer = setTimeout(() => { b.dataset.held = '1'; end(); openCw(); }, 1300);
    });
    ['pointerup', 'pointerleave', 'pointercancel'].forEach(t => b.addEventListener(t, end));
    // phones open their keyboard only from a touch: focus the box when the
    // finger comes up after the hold
    b.addEventListener('pointerup', () => { if (b.dataset.held === '1') cwIn.focus(); });
    b.addEventListener('pointerleave', () => { if (b.dataset.held === '1') b.dataset.held = ''; });
    b.addEventListener('contextmenu', e => e.preventDefault());
  }
  function cwSend(params) {        // in order, one after the other
    const q = rp(params);
    cwChain = cwChain.then(() => fetch('/api/cw?' + q).then(r => r.json()).then(j => {
      if (!j.ok) toast('CW keyboard: ' + (j.error || 'error'));
    })).catch(e => toast('CW keyboard: ' + e));
  }
  function openCw() {
    cwPrev = '';
    cwIn.value = '';
    cwErrN = lastData.cw_error_n || 0;
    const so2r = (lastData.radios || []).length > 1;
    document.getElementById('cwradio').textContent = so2r ? (lastData.radio === 2 ? 'B' : 'A') : '';
    cwEl.hidden = false;
    cwIn.focus();
    cwSend({op: 'open'});
  }
  function closeCw(stop) {
    if (cwEl.hidden) return;
    cwSend({op: stop ? 'stop' : 'done'});
    cwEl.hidden = true;
    cwIn.blur();
    toast(stop ? 'CW stopped' : 'CW keyboard closed', null, 1500);
  }
  cwIn.addEventListener('input', () => {
    const v = cwIn.value.toUpperCase();
    let i = 0;                       // what changed since the last time
    while (i < v.length && i < cwPrev.length && v[i] === cwPrev[i]) i++;
    const back = cwPrev.length - i, add = v.slice(i);
    if (back > 0) cwSend({op: 'back', n: back});
    if (add) cwSend({op: 'text', text: add});
    cwPrev = v;
  });
  cwIn.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); closeCw(false); }
    else if (e.key === 'Escape') { e.preventDefault(); closeCw(true); }
  });
  document.getElementById('cwdone').onclick = () => closeCw(false);
  document.getElementById('cwstop').onclick = () => closeCw(true);
  function cwCheck(data) {          // N1MM's CW window didn't open etc.
    const n = data.cw_error_n || 0;
    if (!cwEl.hidden && n !== cwErrN && data.cw_error) {
      toast('CW keyboard: ' + data.cw_error);
    }
    cwErrN = n;
  }
  async function sendKey(key, btn) {
    if (btn) {
      btn.classList.add('sel');
      setTimeout(() => btn.classList.remove('sel'), 800);
    }
    try {
      const r = await fetch('/api/key?' + rp({key: key}));
      const j = await r.json();
      toast(j.ok ? (/^enter$/i.test(key) ? 'Logged' : ('TX ' + key))
                 : ('Failed: ' + (j.error || 'error')));
    } catch (e) {
      toast('Failed: ' + e);
    }
  }
  function render(data) {
    renderFkeys(data.fkeys);
    cwCheck(data);
    if (data.tune_step && data.tune_step !== tuneStep) { tuneStep = data.tune_step; labelTune(); }
    syncHeaderHeight();
    // POUNCer was updated while this page stayed open: load the new page once
    if (data.version && data.version !== PAGE_VERSION) {
      let done = '';
      try { done = sessionStorage.getItem('pouncer-reload') || ''; } catch (e) {}
      if (done !== data.version) {
        try { sessionStorage.setItem('pouncer-reload', data.version); } catch (e) {}
        location.reload();
        return;
      }
    }
    lastData = data;
    tapResult(data);
    // SO2R switch: shown when N1MM reports two radios (or B was chosen)
    const radioSw = document.getElementById('radiosw');
    const cur = data.radio === 2 ? 2 : 1;
    // Radio B remembered from an SO2R / SO2V session, but N1MM reports one
    // radio now: the A / B key is hidden, so forget B (else the band menu
    // saved the band for A and looked it up for B - "band doesn't change")
    if (myRadio && data.radio && myRadio !== data.radio && (data.radios || []).length <= 1) {
      if (views[String(myRadio)] && !views[String(data.radio)]) {
        views[String(data.radio)] = views[String(myRadio)];
      }
      delete views[String(myRadio)];
      saveViews();
      myRadio = null;
      try { localStorage.removeItem('pouncer.radio'); } catch (e) {}
    }
    radioSw.hidden = !((data.radios || []).length > 1 || cur === 2);
    document.querySelector('header').classList.toggle('so2r', !radioSw.hidden);
    radioSw.textContent = cur === 2 ? 'B' : 'A';
    radioSw.classList.toggle('b', cur === 2);
    // Radio frequency in kHz (falls back to the band, then "ALL band").
    const where = (typeof data.freq === 'number' && data.freq > 0)
      ? data.freq.toFixed(1) + '<span class="khz"> kHz</span>'
      : esc(data.band || 'ALL band');
    // SO2R: A's frequency with B's under it; the radio this phone works is bright
    const fr = data.freqs || {};
    const fA = fr['1'], fB = fr['2'];
    if (!radioSw.hidden && fA > 0 && fB > 0) {
      const line = (nr, f) => '<span class="fl r' + nr + (nr === cur ? ' cur' : '') + '"><i>' +
        (nr === 2 ? 'B' : 'A') + '</i>\u2009' + f.toFixed(1) + '</span>';
      bandEl.innerHTML = '<span class="two">' + line(1, fA) + line(2, fB) + '</span>';
      bandEl.classList.add('ab');
    } else {
      bandEl.classList.remove('ab');
      bandEl.innerHTML = '<span class="app">POUNCer&nbsp; </span>' + where +
        '<span class="modes"> ' + esc((data.modes || []).join('/')) + '</span>';
    }
    const lg = data.log || {};
    const logBad = lg.open === false && lg.state && lg.state !== 'unknown';
    logWarnEl.classList.toggle('show', logBad);
    if (logBad) logMsgEl.textContent = lg.message || 'No log open in N1MM - open a log';
    let meta = '<b>' + data.spots.length + '</b>' +
      (logBad ? ' spots' : ' needed spots') +
      (data.min_snr && data.snr_hidden ? ' <span class="weaktag" title="Weaker than ' +
        data.min_snr + ' dB, not listed (hold SNR)">(+' + data.snr_hidden + ' weak)</span>' : '');
    const q = data.qsos;
    if (lg.open && q) {
      const parts = (q.bands || []).map(x =>
        '<span' + (x.band === data.band ? ' class="cur"' : '') + '>' +
        esc(x.band) + ' ' + x.count + '</span>');
      meta += ' &middot; QSOs ' + (parts.length ? parts.join(' &middot; ') : '0') +
        ' &middot; total <b>' + q.total + '</b>';
    }
    if (lg.open && lg.name) {
      // "S55OO_26.s3db #8 CQWWCW" -> "S55OO_26 #8 CQWWCW"; "#8" hidden on phones
      const nm = esc(lg.name.replace(/\\.s3db\\b/i, ''))
        .replace(/(#\\d+)\\s*/, '<span class="cnr">$1 </span>');
      meta += ' &middot; ' + nm;
    }
    else if (!logBad) meta += ' &middot; ' + esc(data.worked || '');
    if (data.backup) meta = '<span class="warn" title="' + esc(data.backup) + '">\u26a0 backup skimmers</span> &middot; ' + meta;
    // team: M = this POUNCer is the team master (RBN), S = slave of it
    if (data.team && data.team.indexOf('master') >= 0) meta = '<b title="' + esc(data.team) + '">M</b> &middot; ' + meta;
    else if (data.team && data.team.indexOf('slave') >= 0) meta = '<b title="' + esc(data.team) + '">S</b> &middot; ' + meta;
    // team POUNCers on different versions
    const vd = (data.team || '').indexOf('VERSIONS DIFFER');
    if (vd >= 0) meta = '<span class="warn" title="' + esc(data.team) + '">\u26a0 ' + esc(data.team.slice(vd)) + '</span> &middot; ' + meta;
    {
      const v = views[String(cur)];
      if (v && data.radio_band && (v.from !== data.radio_band || v.band === data.radio_band)) {
        delete views[String(cur)];   // the radio changed band: follow it again
        saveViews();
      }
    }
    if (data.view) meta = '<span class="viewtag" id="viewtag" title="Tap: back to the band the radio is on">' +
      esc(data.band) + ' list &middot; ' + (radioSw.hidden ? 'radio' : (cur === 2 ? 'B' : 'A')) +
      ' on ' + esc(data.radio_band || '?') + ' \u2715</span> &middot; ' + meta;
    if (calm) meta = '<span class="calmtag" title="Calm list: tiles keep their place, re-sorted every 15 s">calm</span> &middot; ' + meta;
    const ck = data.checks;
    if (ck && ck.state !== 'ok' && (ck.items || []).length) {
      meta = '<span class="chktag ' + esc(ck.state) + '" id="chktag" title="Pre-contest check - tap for details">\u26a0 ' +
        ck.items.map(i => esc(i.name)).join(', ') + '</span> &middot; ' + meta;
    }
    metaEl.innerHTML = meta;
    const ct = document.getElementById('chktag');
    if (ct) ct.onclick = () => toast(ck.items.map(i => i.name + ': ' + i.text).join(' \u00b7 '), null, 8000);
    if (!snrSheet.classList.contains('show') && typeof data.min_snr === 'number') showMinSnr(data.min_snr);
    const vt = document.getElementById('viewtag');
    if (vt) vt.onclick = () => { delete views[String(cur)]; saveViews(); selKey = ''; refresh(); };
    const cols = columns();
    // SSB (radio on USB / LSB): no CW speed keys, no "SSB" on every tile
    const ssbOnly = (data.modes || []).length === 1 && data.modes[0] === 'SSB';
    document.querySelectorAll('.footbar .wpm').forEach(b => { b.hidden = ssbOnly; });
    // N1MM's CW speed of this radio; a short popup when it changes
    const wpm = (data.wpm || {})[String(cur)];
    const wv = document.getElementById('wpmval');
    wv.textContent = wpm ? String(wpm) : '';
    wv.hidden = ssbOnly || !wpm;
    if (wpm && lastWpm.r === cur && lastWpm.v && wpm !== lastWpm.v && !ssbOnly && data.wpm_popup !== false) {
      toast('CW ' + wpm + ' WPM ' + (wpm > lastWpm.v ? '\u25b2' : '\u25bc'), null, 1500);
    }
    lastWpm = {r: cur, v: wpm || 0};
    const spots = arrange(data);
    applyPin(spots);
    shown = spots;
    const rows = Math.max(1, Math.ceil(spots.length / cols));
    grid.style.gridTemplateColumns = 'repeat(' + cols + ', minmax(0, 1fr))';
    grid.style.gridTemplateRows = 'repeat(' + rows + ', minmax(var(--row), auto))';
    // "Tuned to": the spot nearest the radio frequency, within 0.5 kHz.
    let tuned = null;
    if (typeof data.freq === 'number' && data.freq > 0) {
      let best = 0.5 + 1e-9;
      spots.forEach(s => {
        const d = Math.abs(s.freq - data.freq);
        if (d <= best) { best = d; tuned = s; }
      });
    }
    // Tiles are kept between refreshes (by call + MHz): a tile is only
    // touched when what it shows changed, and only moved when its place in
    // the list changed - no full redraw every second, no flicker, and the
    // tile under your finger stays the same element.
    const seen = new Set();
    let pos = 0;
    // other column count / window width: measure "too wide" again
    const layout = cols + 'x' + window.innerWidth;
    if (layout !== tilesLayout) { tilesLayout = layout; tiles.forEach(t => { t.tight = null; }); }
    if (emptyEl && emptyEl.parentNode) emptyEl.remove();
    spots.forEach(s => {
      let key = calmKey(s), n = 1;
      while (seen.has(key)) key = calmKey(s) + '#' + (++n);
      seen.add(key);
      const cls = 'tile' + (snrVal(s) >= 20 ? ' strong' : '') +
        (s.mult >= 2 ? ' m2' : (s.mult === 1 ? ' m1' : '')) +
        (s === tuned ? ' onfreq' : '') +
        // not re-spotted for a while: probably gone - fade it
        (s.age != null && data.fade_s && s.age >= data.fade_s ? ' stale' : '') +
        (spotKey(s) === selKey ? ' sel' : '') +
        (calm && calmNew.has(calmKey(s)) ? ' fresh' : '');
      // DX (outside our continent): callsign in yellow; EU stays white
      const b = {title: ''};          // collects the tooltip text
      if (s.country) b.title = s.country + (s.cont ? ' (' + s.cont + ')' : '');
      // Run history (past logs): R = % of QSOs made running; ⚡ = rarely
      // runs, so a CQ from it won't last - work it now.
      const rare = isRare(s);
      if (s.cq_min != null) b.title = (b.title ? b.title + ' - ' : '') +
        'calling on this frequency for ' + cqText(s.cq_min) + (s.first ? ' (since ' + s.first + ')' : '') + (s.time ? ', last spot ' + s.time : '');
      if (s.run != null || s.hours != null) b.title = (b.title ? b.title + ' - ' : '') +
        (s.run != null ? 'runs ' + s.run + '% of QSOs' + (s.run_band ? ' on ' + s.run_band : '') : 'run % unknown (logs without frequencies)') +
        (s.hours != null ? ', on air ~' + s.hours + ' h' : '') +
        ' in its past logs' + (s.rec ? ' (reconstructed from other logs: a minimum)' : '') +
        (rare ? ' - rare CQ: work it now' : '');
      if (s.tried != null) b.title = (b.title ? b.title + ' - ' : '') +
        'already tapped ' + (s.tries > 1 ? s.tries + ' times, last ' : '') + s.tried + ' min ago';
      if (s.busted) b.title = (b.title ? b.title + ' - ' : '') +
        '? = not in the Super Check Partial list: maybe a skimmer error (busted call)';
      const len = (s.call || '').length;
      const size = len > 10 ? ' xlong' : (len > 8 ? ' long' : '');
      const html = '<span class="call' + size + (s.dx ? ' dx' : '') + '">' +
        esc(s.call) + (s.busted ? '<span class="busted">?</span>' : '') +
        (rare ? '<span class="rare">\u26a1</span>' : '') +
        // already tapped lately: minutes since the last tap (and how often)
        (s.tried != null ? '<span class="tried">\u21bb' + s.tried + 'm' +
          (s.tries > 1 ? '\u00d7' + s.tries : '') + '</span>' : '') + '</span>' +
        '<span class="detail"><span><b class="fq">' + s.freq.toFixed(1) + '</b>' +
        // mode only when it isn't CW (in a CW contest it always is)
        (s.mode && s.mode.toUpperCase() !== 'CW' && !ssbOnly ? ' ' + esc(s.mode) : '') +
        (s.run != null ? ' R' + s.run + '%' + (s.rec ? '*' : '') : '') +
        (s.hours != null ? ' ~' + Math.round(s.hours) + 'h' : '') +
        '</span> <span>' +
        // first spotted on this frequency (UTC), SNR, time calling there
        (s.first ? esc(s.first) + ' ' : (s.time ? esc(s.time) + ' ' : '')) +
        (s.snr == null ? '--' : s.snr + 'dB') +
        (s.cq_min != null ? ' cq' + cqText(s.cq_min) : '') +
        '</span></span>';
      let t = tiles.get(key);
      if (!t) {
        const el = document.createElement('button');
        t = {el: el, cls: null, title: null, html: null, tight: false};
        attachPress(el);
        tiles.set(key, t);
      }
      const el = t.el;
      el._spot = s;                   // tap / hold use the latest spot data
      if (t.html !== html) { el.innerHTML = html; t.html = html; t.tight = null; }
      if (t.title !== b.title) { el.title = b.title; t.title = b.title; }
      if (t.cls !== cls) {
        // keep a running hold / tap flash while the rest of the look changes
        const keepCls = ['holding', 'sel'].filter(c => el.classList.contains(c) && cls.indexOf(' ' + c) < 0);
        el.className = cls + (t.tight ? ' tight' : '') + (keepCls.length ? ' ' + keepCls.join(' ') : '');
        t.cls = cls;
      }
      if (grid.children[pos] !== el) grid.insertBefore(el, grid.children[pos] || null);
      pos++;
      // still too wide (long call + every mark)? slightly smaller text
      if (t.tight === null) {
        el.classList.remove('tight');
        t.tight = el.scrollWidth > el.clientWidth + 1;
        if (t.tight) el.classList.add('tight');
      }
    });
    // gone from the list: take those tiles out
    tiles.forEach((t, key) => {
      if (!seen.has(key)) { t.el.remove(); tiles.delete(key); }
    });
    checkAlerts(spots);
    if (!spots.length) {
      if (!emptyEl) {
        emptyEl = document.createElement('div');
        emptyEl.className = 'empty';
        emptyEl.textContent = 'No needed spots on this band right now.';
      }
      grid.appendChild(emptyEl);
    }
    markSort();
  }
  const tiles = new Map();          // call|MHz -> {el, cls, title, html, tight}
  let emptyEl = null, tilesLayout = '';
  // Tap = QSY. Hold 2 s = offer to remove the call from the list.
  const HOLD_MS = 2000;
  let pressing = false;   // a tile is being held: don't redraw the list
  let releaseTimer = null;
  function attachPress(b) {          // b._spot = the spot this tile shows now
    let timer = null, held = false;
    const cancel = () => {
      clearTimeout(timer); timer = null;
      // Keep the list still a moment after the finger lifts, so a re-sort
      // (e.g. run % just arrived) can't swap the tile before the tap lands.
      clearTimeout(releaseTimer);
      releaseTimer = setTimeout(() => { pressing = false; }, 400);
      b.classList.remove('holding');
    };
    b.addEventListener('pointerdown', () => {
      held = false;
      clearTimeout(releaseTimer);
      pressing = true;
      b.classList.add('holding');
      timer = setTimeout(() => { held = true; cancel(); askRemove(b._spot); }, HOLD_MS);
    });
    b.addEventListener('pointerup', cancel);
    b.addEventListener('pointerleave', cancel);
    b.addEventListener('pointercancel', cancel);
    b.addEventListener('contextmenu', e => e.preventDefault());
    b.onclick = () => { if (held) { held = false; return; } qsy(b._spot, b); };
  }
  // ---- Min SNR (shared with the POUNCer window, saved in config.ini)
  const snrSheet = document.getElementById('snrsheet');
  const snrBtn = document.getElementById('snrsort');
  const SNR_PRESETS = [0, 3, 6, 10, 13, 15, 20, 25];
  let minSnr = 0;
  function showMinSnr(v) {
    minSnr = v || 0;
    const html = 'SNR' + (minSnr ? '<span class="snrbadge">\u2265' + minSnr + '</span>' : '');
    if (snrBtn.innerHTML !== html) snrBtn.innerHTML = html;
    document.getElementById('snrval').textContent = minSnr ? minSnr + ' dB' : 'all';
    document.querySelectorAll('#snrpresets button').forEach(b => {
      b.classList.toggle('cur', Number(b.dataset.db) === minSnr);
    });
  }
  async function setMinSnr(v) {
    v = Math.max(0, Math.min(60, v));
    showMinSnr(v);
    try {
      const r = await fetch('/api/minsnr?' + new URLSearchParams({db: v}));
      const j = await r.json();
      if (!j.ok) toast('Failed: ' + (j.error || 'error'));
    } catch (e) { toast('Failed: ' + e); }
    setTimeout(refresh, 300);
  }
  SNR_PRESETS.forEach(db => {
    const b = document.createElement('button');
    b.dataset.db = db;
    b.textContent = db ? db : 'all';
    b.onclick = () => { setMinSnr(db); snrSheet.classList.remove('show'); };
    document.getElementById('snrpresets').appendChild(b);
  });
  // hold the SNR sort key 1.3 s: the sheet (a short tap still sorts by SNR)
  {
    let timer = null;
    const end = () => { clearTimeout(timer); snrBtn.classList.remove('holding'); };
    snrBtn.addEventListener('pointerdown', () => {
      snrBtn.dataset.held = '';
      snrBtn.classList.add('holding');
      clearTimeout(timer);
      timer = setTimeout(() => {
        snrBtn.dataset.held = '1';
        end();
        showMinSnr(minSnr);
        snrSheet.classList.add('show');
      }, 1300);
    });
    ['pointerup', 'pointerleave', 'pointercancel'].forEach(t => snrBtn.addEventListener(t, end));
    snrBtn.addEventListener('contextmenu', e => e.preventDefault());
  }
  document.getElementById('snrdown').onclick = () => setMinSnr(minSnr - 1);
  document.getElementById('snrup').onclick = () => setMinSnr(minSnr + 1);
  document.getElementById('snrclose').onclick = () => snrSheet.classList.remove('show');
  snrSheet.onclick = (e) => { if (e.target === snrSheet) snrSheet.classList.remove('show'); };
  const rmSheet = document.getElementById('rmsheet');
  let rmSpot = null;
  function askRemove(s) {
    rmSpot = s;
    document.getElementById('rmtitle').textContent =
      'Remove ' + s.call + ' (' + s.freq.toFixed(1) + ') from the list?';
    rmSheet.classList.add('show');
  }
  async function setHidden(s, hide) {
    try {
      const r = await fetch('/api/' + (hide ? 'hide' : 'unhide') + '?' +
        new URLSearchParams({call: s.call, freq: s.freq}));
      const j = await r.json();
      if (!j.ok) { toast('Failed: ' + (j.error || 'error')); return; }
      if (hide) toast('Removed ' + s.call, () => setHidden(s, false));
      else toast(s.call + ' is back');
    } catch (e) {
      toast('Failed: ' + e);
    }
    setTimeout(refresh, 400);
  }
  document.getElementById('rmyes').onclick = () => {
    rmSheet.classList.remove('show');
    if (rmSpot) setHidden(rmSpot, true);
  };
  document.getElementById('rmno').onclick = () => rmSheet.classList.remove('show');
  rmSheet.onclick = (e) => { if (e.target === rmSheet) rmSheet.classList.remove('show'); };
  // Selected (last tapped) station, kept across the 1 s redraws.
  let selKey = '';
  const spotKey = s => s.call + '|' + Number(s.freq).toFixed(1);
  // After a tap: what POUNCer / N1MM really did (radio moved? call typed?)
  // comes back in the next lists as data.tap - shown in place of "QSY ...".
  let pendingTap = null;
  function tapResult(data) {
    if (!pendingTap) return;
    const t = data.tap;
    if (t && t.n > pendingTap.since && String(t.call).toUpperCase() === pendingTap.call) {
      toast((t.ok ? '\u2713 ' : '\u26a0 ') + t.call + ': ' + t.text, null, t.ok ? 4000 : 8000);
      pendingTap = null;
    } else if (Date.now() - pendingTap.at > 8000) {
      toast('\u26a0 ' + pendingTap.call + ': no answer from POUNCer - is it still running?', null, 8000);
      pendingTap = null;
    }
  }
  // The station you tapped keeps its place in the list until you log it
  // (it leaves the list) or for PIN_MS - a re-sort (new SNR, run %) can't
  // move it away from under your finger while you work it.
  const PIN_MS = 60000;
  let pin = null;                     // {key: call|MHz, idx, at}
  function applyPin(list) {
    if (!pin) return;
    if (Date.now() - pin.at > PIN_MS) { pin = null; return; }
    const j = list.findIndex(x => calmKey(x) === pin.key);
    if (j < 0) { pin = null; return; }           // logged / gone: let go
    const to = Math.min(pin.idx, list.length - 1);
    if (j !== to) list.splice(to, 0, list.splice(j, 1)[0]);
  }
  async function qsy(s, btn) {
    const at = shown.findIndex(x => calmKey(x) === calmKey(s));
    pin = {key: calmKey(s), idx: at < 0 ? 0 : at, at: Date.now()};
    selKey = spotKey(s);
    document.querySelectorAll('button.tile.sel').forEach(t => t.classList.remove('sel'));
    btn.classList.add('sel');
    try {
      const r = await fetch('/api/qsy?' + rp(
        {call: s.call, freq: s.freq, mode: s.mode}));
      const j = await r.json();
      toast(j.ok ? ('QSY ' + s.call + ' ...') : ('Failed: ' + (j.error || 'error')));
      if (j.ok) pendingTap = {call: String(s.call).toUpperCase(), since: (lastData.tap || {}).n || 0,
                              at: Date.now()};
    } catch (e) {
      toast('Failed: ' + e);
    }
  }
  async function reconnect() {
    toast('Reconnecting to the N1MM log...');
    try {
      const r = await fetch('/api/reconnect', {cache: 'no-store'});
      const j = await r.json();
      if (!j.ok) toast('Failed: ' + (j.error || 'error'));
    } catch (e) {
      toast('Failed: ' + e);
    }
    setTimeout(refresh, 700);
  }
  document.getElementById('reconnect').onclick = reconnect;
  document.getElementById('logbtn').onclick = reconnect;
  const bandSheet = document.getElementById('bandsheet');
  const bandGrid = document.getElementById('bandgrid');
  // One band menu (160-10 m) for the radio chosen with the A / B key in the
  // header: it shows that band's spots for that radio (no QSY - tap a
  // station there and that radio goes to it).
  function openBands() {
    const bands = lastData.contest_bands || ['160m','80m','40m','20m','15m','10m'];
    const cur = lastData.radio === 2 ? 2 : 1;
    const so2r = (lastData.radios || []).length > 1 || cur === 2;
    const real = (lastData.rbands || {})[String(cur)] || lastData.radio_band || lastData.band;
    const shown = (views[String(cur)] && views[String(cur)].band) || real;
    document.getElementById('bandfor').innerHTML = so2r
      ? ' for <i class="r' + cur + '">radio ' + (cur === 2 ? 'B' : 'A') + '</i>' : '';
    bandGrid.textContent = '';
    bands.forEach(b => {
      const btn = document.createElement('button');
      btn.textContent = b;
      // band open? (skimmers on our continent, 10 min): dot + DX stations heard
      const bo = (lastData.bandopen || {})[b];
      if (bo) {
        const o = document.createElement('span');
        o.className = 'bopen ' + bo.state;
        o.textContent = bo.state === 'open' ? String(bo.dx) : '';
        // orange (only our continent) -> green (all DX), by the DX share
        if (bo.state !== 'closed' && bo.bg) {   // same colour as the window's chip
          o.style.background = bo.bg;
          o.style.color = bo.fg || '#fff';
        }
        o.title = bo.state === 'open' ? bo.dx + ' DX stations (' + (bo.pct || 0) + '% of all heard) by skimmers on your continent (10 min)'
          : (bo.state === 'local' ? 'only your continent heard (' + bo.calls + ' stations)' : 'nothing heard');
        btn.appendChild(o);
        if (bo.mults) {           // new multipliers spotted on that band now
          const m = document.createElement('span');
          m.className = 'bmult';
          m.textContent = bo.mults + 'M';
          m.title = bo.mults + ' new multiplier' + (bo.mults > 1 ? 's' : '') + ' spotted' +
            (bo.mm ? ' (' + bo.mm + ' double)' : '');
          btn.appendChild(m);
        }
      }
      if (b === shown) btn.classList.add('cur');
      if (b === real) btn.classList.add('here');   // the radio is on this band
      btn.onclick = () => chooseBand(b);
      bandGrid.appendChild(btn);
    });
    bandSheet.classList.add('show');
  }
  function closeBands() { bandSheet.classList.remove('show'); }
  // Band menu: show that band's spots for the radio - the radio stays where
  // it is until you tap a station (that QSY works for A and B alike).
  function chooseBand(b) {
    closeBands();
    const r = lastData.radio === 2 ? 2 : 1;      // the radio of the A / B key
    const real = (lastData.rbands || {})[String(r)] || lastData.radio_band || '';
    const tag = (lastData.radios || []).length > 1 ? (r === 2 ? 'B' : 'A') + ': ' : '';
    if (b === real) {
      delete views[String(r)];
      toast(tag + b + ' (the radio is there)');
    } else {
      views[String(r)] = {band: b, from: real};
      toast(tag + b + ' spots - tap a station to QSY');
    }
    saveViews();
    selKey = '';
    refresh();
  }
  bandEl.onclick = openBands;
  document.getElementById('bandclose').onclick = closeBands;
  bandSheet.onclick = (e) => { if (e.target === bandSheet) closeBands(); };
  // Footer: -/+ VFO step (Hz from config, shown on the buttons) and WPM -/+.
  let tuneStep = 25;
  function labelTune() {
    document.querySelectorAll('.footbar [data-tune]').forEach(b => {
      b.innerHTML = (b.dataset.tune === '1' ? '+' : '&minus;') + tuneStep + ' Hz';
    });
  }
  function flash(btn) {
    btn.classList.add('sel');
    setTimeout(() => btn.classList.remove('sel'), 500);
  }
  document.querySelectorAll('.footbar [data-tune]').forEach(b => {
    b.onclick = async () => {
      flash(b);
      const hz = Number(b.dataset.tune) * tuneStep;
      try {
        const r = await fetch('/api/tune?' + rp({hz: hz}));
        const j = await r.json();
        if (!j.ok) toast('Failed: ' + (j.error || 'error'));
      } catch (e) {
        toast('Failed: ' + e);
      }
    };
  });
  document.querySelectorAll('.footbar [data-key]').forEach(b => {
    b.onclick = async () => {
      flash(b);
      try {
        const r = await fetch('/api/key?' + rp({key: b.dataset.key}));
        const j = await r.json();
        toast(j.ok ? (b.dataset.key === 'PgUp' ? 'CW faster' : 'CW slower')
                   : ('Failed: ' + (j.error || 'error')));
      } catch (e) {
        toast('Failed: ' + e);
      }
    };
  });
  // NEXT: the station after the last tapped one, in the order shown (sort
  // + DX / MLT / \u26a1 toggles); the first one if none tapped or at the end.
  let shown = [];
  document.getElementById('next').onclick = () => {
    const btn = document.getElementById('next');
    flash(btn);
    if (!shown.length) { toast('No stations in the list'); return; }
    const at = shown.findIndex(s => spotKey(s) === selKey);
    const i = at >= 0 && at + 1 < shown.length ? at + 1 : 0;
    const tile = document.getElementById('grid').children[i];
    qsy(shown[i], tile || btn);
  };
  function syncHeaderHeight() {
    const f = document.getElementById('footbar');
    document.documentElement.style.setProperty('--ftr', (f ? f.offsetHeight : 0) + 'px');
    const h = document.querySelector('header').offsetHeight;
    document.documentElement.style.setProperty('--hdr', h + 'px');
  }
  window.addEventListener('resize', syncHeaderHeight);
  document.getElementById('radiosw').onclick = () => {
    myRadio = (lastData.radio === 2 ? 2 : 1) === 1 ? 2 : 1;
    try { localStorage.setItem('pouncer.radio', String(myRadio)); } catch (e) {}
    selKey = '';
    toast('Radio ' + (myRadio === 2 ? 'B' : 'A') + ': taps and keys go to Entry Window ' +
          (myRadio === 2 ? 'B' : 'A'));
    refresh();
  };
  async function refresh() {
    try {
      const q = rp();
      const vr = String(myRadio || lastData.radio || 1);
      if (views[vr] && views[vr].band) q.set('band', views[vr].band);
      const r = await fetch('/api/spots?' + q, {cache: 'no-store'});
      const data = await r.json();
      if (data.pending) {            // POUNCer builds that band's list next tick
        metaEl.textContent = 'loading ' + data.pending + ' spots...';
        setTimeout(refresh, 300);
        return;
      }
      // Holding a tile / remove box open: keep the list still (the redraw
      // would cancel the hold); show the fresh data afterwards.
      if (pressing || document.getElementById('rmsheet').classList.contains('show') ||
          (calm && Date.now() - lastTouch < CALM_HOLD_MS)) {
        lastData = data;
        return;
      }
      render(data);
    } catch (e) {
      metaEl.textContent = 'connection lost - retrying...';
    }
  }
  document.querySelectorAll('.sorts button[data-sort]').forEach(b => {
    b.onclick = () => {
      if (b.dataset.held === '1') { b.dataset.held = ''; return; }   // SNR held: sheet, no sort
      if (b.dataset.sort === 'freq' && sortKey === 'freq') {   // Freq again: toggle
        freqFrom = !freqFrom;
        try { localStorage.setItem('pouncer.freqfrom', freqFrom ? '1' : '0'); } catch (e) {}
        toast(freqFrom ? 'Freq: from your radio frequency up' : 'Freq: whole band from the bottom');
      }
      sortKey = b.dataset.sort;
      localStorage.setItem('pouncer.sort', sortKey);
      markSort();
      render(lastData);
    };
  });
  // calm list: a touch or scroll holds the list still for CALM_HOLD_MS
  const touched = () => { lastTouch = Date.now(); };
  ['pointerdown', 'touchstart', 'wheel'].forEach(ev =>
    document.getElementById('grid').addEventListener(ev, touched, {passive: true}));
  window.addEventListener('scroll', touched, {passive: true});
  markSort();
  refresh();
  setInterval(refresh, 1000);
  window.addEventListener('resize', refresh);
</script>
</body>
</html>
"""


HELP_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>POUNCer __VERSION__ help</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; background: #101418; color: #e6e6e6;
    font: 15px/1.45 -apple-system, "Segoe UI", Roboto, sans-serif; }
  main { max-width: 720px; margin: 0 auto; padding: 12px 16px 40px; }
  a.back { display: inline-block; margin: 4px 0 12px; padding: 10px 14px;
    background: #2e7d32; color: #fff; border-radius: 10px; text-decoration: none; font-weight: 700; }
  h1 { font-size: 20px; margin: 6px 0 4px; }
  h2 { font-size: 16px; margin: 22px 0 8px; color: #fff; border-bottom: 1px solid #263238;
    padding-bottom: 4px; }
  p.lead { opacity: .7; margin: 0 0 6px; font-size: 13px; }
  dl { margin: 0; display: grid; grid-template-columns: max-content 1fr; gap: 8px 12px; }
  dt { font-weight: 700; white-space: nowrap; }
  dd { margin: 0; }
  .k { display: inline-block; min-width: 26px; padding: 3px 8px; border-radius: 7px;
    background: #263238; color: #fff; text-align: center; font-weight: 700; font-size: 13px; }
  .green { background: #2e7d32; } .purple { background: #6a1b9a; } .blue { background: #1565c0; } .teal { background: #00897b; }
  .amber { background: #ff8f00; color: #111; } .red { color: #ef9a9a; }
  .yel { color: #ffeb3b; font-weight: 700; } .mag { color: #f48fb1; font-weight: 700; }
  .sky { color: #4fc3f7; font-weight: 700; }
  .resetbtn { background: #c62828; color: #fff; border: 0; border-radius: 10px;
    padding: 12px 18px; font: inherit; font-weight: 700; }
  .muted { opacity: .7; }
  .note { background: #1c2329; border-radius: 10px; padding: 10px 12px; margin-top: 10px;
    font-size: 13px; }
  @media (max-width: 420px) { dl { grid-template-columns: 1fr; gap: 2px 0; } dd { margin-bottom: 8px; } }
  label.opt { display: block; margin: 10px 0; padding: 10px; background: #263238;
    border-radius: 8px; line-height: 1.4; }
  label.opt input { transform: scale(1.5); margin: 0 10px 0 4px; vertical-align: middle; }
</style>
</head>
<body><main>
<a class="back" href="/">&larr; Back to the spots</a>
<h1>POUNCer __VERSION__ - what every button does</h1>
<p class="lead">The page follows the band your radio is on in N1MM and shows the RBN
spots you still need there. Nothing here transmits except the F-keys.</p>

<h2>Top row</h2>
<dl>
  <dt><span class="k">7020.0 &#9662;</span></dt>
  <dd>The radio frequency (kHz). Tap it to pick a band (160-10 m): the list shows
    <b>that band's spots</b> - the radio is not moved. Tap a station there to QSY to it.
    <span class="sky">15m list &middot; on 20m &#10005;</span> in the small line while you
    look at another band; tap it to go back. When the radio changes band (e.g. after
    that tap) the list follows the radio again. No transmit.</dd>
  <dt><span class="k red">CLR</span></dt>
  <dd>Clear every spot (e.g. old spots from before the contest). New ones come in
    again from RBN. <b>Undo</b> in the pop-up for 5 s.</dd>
  <dt><span class="k">Time</span> <span class="k">Freq</span> <span class="k">SNR</span></dt>
  <dd>Sort the list: newest spot first / by frequency / strongest first. The green one
    is the active sort. Tap <b>Freq</b> again for <b>Freq&#8593;</b>: from your radio
    frequency up (see Test options below).</dd>
  <dt><span class="k yel">DX</span></dt>
  <dd>DX stations (outside your continent) on top, still in the chosen sort.</dd>
  <dt><span class="k mag">MLT</span></dt>
  <dd>New multipliers on top: double first, then single.</dd>
  <dt><span class="k">&#9889;</span></dt>
  <dd>Rare CQ on top: stations that hardly ever run, or are on only a few hours
    (see <i>Run history</i> below). Unknown ones go last.</dd>
  <dt><span class="k">&#8635;</span></dt>
  <dd>Re-read which log N1MM has open (after you open another log).</dd>
</dl>
<p><span style="color:#ffb74d;font-weight:700">&#9888; backup skimmers</span> at the start
of the small line: your skimmer (S53WW) has sent nothing for 5 minutes, so spots from
other skimmers on your continent are shown until it is back.</p>
<p>The small line under it starts with <b>M</b> or <b>S</b> when several POUNCers
work as a team on your network: M = this one is the master (keeps the RBN
connection), S = it gets its spots from the master.
<span style="color:#ffb74d;font-weight:700">&#9888; VERSIONS DIFFER</span> means the
POUNCers in the team run different versions (it says which PC runs which) - update
them all to the same version (Restart after auto-update, or download the newest). Then: how many needed spots, your QSOs per band and in total, and
the open log. An <b>orange bar</b> means N1MM has no log open (or isn't running) -
open one and tap <b>Reconnect</b>.</p>
<p>The QSO counts are always those of the <b>contest open in N1MM</b>, not of the whole
database. Tick <b>App Info</b> in N1MM (<i>Config &gt; Config Ports &gt; Broadcast Data</i>,
address <code>127.0.0.1:12060</code>, like Radio): N1MM then tells POUNCer at once which log
and contest you opened. Without it POUNCer reads N1MM's ini, which N1MM saves only now and
then - after switching contests the counts can belong to the old one until N1MM is restarted.</p>

<h2>Spot tiles</h2>
<dl>
  <dt>Tap</dt>
  <dd>The radio goes to that spot and the call is typed into N1MM's Entry Window
    (no transmit). The tile you tapped keeps a <span class="yel">yellow ring</span> and
    <b>stays in its place</b> until you log it (or 60 s) - the others re-sort around it.
    The message then says what really happened: <b>&#10003; OK1BB: radio on 7012.0 &middot;
    call in</b>, or <b>&#9888; ... N1MM didn't report 7012.0</b> when the radio didn't move
    (no answer at all = POUNCer not running?).</dd>
  <dt>Hold 2 s</dt>
  <dd>Remove that call from the list (on this band), with Undo.</dd>
  <dt><span class="yel">YELLOW</span> call</dt>
  <dd>DX - outside your continent. White = your own continent.</dd>
  <dt>Green tile</dt>
  <dd>Strong signal (20 dB or more).</dd>
  <dt><span class="mag">&#9612;</span> bar on the left</dt>
  <dd>New multiplier on this band: thin = single, thick = double (e.g. new zone and
    new country).</dd>
  <dt><span class="sky">Blue</span> outline</dt>
  <dd>Your radio is on that spot's frequency (within 0.5 kHz).</dd>
</dl>
<p>Right side of a tile, e.g. <b>7003.1 R6%* ~4h</b> / <b>1132Z 22dB cq3m</b>:</p>
<dl>
  <dt><b>7003.1</b></dt><dd>Spot frequency (kHz), in bold. The mode is shown only when it
    isn't CW (e.g. RTTY).</dd>
  <dt>R81%</dt>
  <dd><b>Run history</b>: in its logs of the last 6 years of this contest (from S53ZO's
    public log archive), 81% of its QSOs were made calling CQ - on this band when its logs
    have enough QSOs here, else over all bands (tap and hold the tile: the tooltip says
    which); on SSB from its SSB logs, on CW from its CW logs. Logs written without real
    frequencies give only the hours (no R%). For CQWW, ARRL DX and CQ WPX (CW and, as a separate list,
    SSB) the stations are known in advance (warm-up lists loaded at start); others
    appear about a second after they are spotted.</dd>
  <dt>~23h</dt><dd>About how many hours it is usually on the air in this contest
    (average of its logs).</dd>
  <dt>*</dt>
  <dd>From a reconstructed log (the station never sent one; built from other stations'
    logs - a minimum).</dd>
  <dt><span style="color:#ffb74d;font-weight:700">?</span></dt>
  <dd>The call is not in the <b>Super Check Partial</b> list (calls active in contests) -
    maybe a skimmer error, one letter wrong. The list is downloaded fresh every week.</dd>
  <dt>&#9889;</dt>
  <dd><b>Rare CQ</b>: runs under 30% or is on under 6 h. A CQ from it won't last -
    work it now.</dd>
  <dt>1132Z</dt><dd>When it was first spotted on this frequency (UTC).</dd>
  <dt>22dB</dt><dd>Signal-to-noise on the skimmer.</dd>
  <dt>cq3m</dt>
  <dd>Calling on this frequency for 3 minutes (learned live from the RBN spots).</dd>
</dl>
<p>A <b>faded</b> tile has not been re-spotted for 5 minutes - the station has probably
stopped calling there. Not shown: stations you already worked on this band and mode,
your own call, and beacons. One tile per station and band (the newest spot).</p>
<p><b>Alert</b>: when a rare CQ (&#9889;) that is also a new multiplier appears on your
band, the phone beeps (and vibrates where it can) and shows it in a pop-up. Turn it
on/off in the band menu (tap the frequency). Sound works after your first tap on the
page.</p>

<h2>Keys on the right</h2>
<dl>
  <dt><span class="k green">F2</span> &hellip; <span class="k green">F7</span></dt>
  <dd>Press that function key in N1MM - it <b>transmits</b> N1MM's message (F2 report,
    F3 TU, F4 your call, F5 his call, F7 ?). The label under the key says which.</dd>
  <dt><span class="k">SNR</span> held 1.3 s</dt>
  <dd>Minimum SNR: tap SNR = sort by SNR; <b>hold it 1.3 s</b> (it fills teal) to set the
  minimum: spots weaker than this are not listed - here and in the POUNCer window (same
  setting, saved). <b>all</b> / 3 / 6 / 10 / 13 / 15 / 20 / 25 dB, or &minus;1 / +1. When on,
  the SNR key shows it small (&ge;10) and the top line says how many weak spots are hidden. Default <b>all</b> (0 = off). New multipliers and spots without an SNR (SSB)
  are always listed.</dd>
  <dt>Band menu dots</dt>
  <dd>Band open? From what the skimmers on <b>your continent</b> hear (the whole RBN feed)
  in the last 10 minutes: <span style="color:#43a047">green + number</span> = open, that many
  different DX stations heard; <span style="color:#f9a825">amber</span> = only your own
  continent; grey = nothing. A <span style="color:#ec407a"><b>pink 6M</b></span> = 6 stations
  spotted on that band now would be a new multiplier (2 of them double: hold for the
  details). The POUNCer window shows the same as a row of band lights.</dd>
  <dt>&#8635;3m after a call</dt>
  <dd>Already tried: you tapped (called) this station on this band 3 minutes ago
  (&#8635;3m&times;2 = twice) - maybe skip the pile-up for now. Shown for 30 min
  (<code>[ui] tried_minutes</code>); off with <code>mark_tried = false</code> or in Settings.</dd>
  <dt><span style="color:#ffd54f">&#9888; cty, SCP</span> in the top line</dt>
  <dd>Pre-contest check: something POUNCer needs is not right (N1MM radio info, log,
  station call, RBN, country file, Super Check Partial, phone page, PC clock). Tap it for
  details. Nothing is shown while everything is fine; the POUNCer window has the full line.</dd>
  <dt><span class="k green">ENTER</span></dt>
  <dd>Enter in N1MM: logs the QSO (no transmit).<br>
  <b>Hold it 1.3 s</b> (it fills up yellow): the <b>CW keyboard</b> opens at the top. What you
  type goes on the fly into N1MM's CW window (Ctrl+K) and is <b>sent as CW</b> (transmits);
  Backspace removes letters not sent yet. <b>DONE</b> (or Enter on the phone keyboard) =
  N1MM sends the rest and closes its CW window; <b>STOP (Esc)</b> = stops sending at once.
  If the phone keyboard doesn't appear, tap the text box. Off: <code>[web] cw_keyboard = false</code>.</dd>
</dl>
<p>Keys turn red for a moment when pressed. Which keys appear is set in
<code>config.ini</code> (<code>[ui] fkeys</code>).</p>

<h2>Contest summary</h2>
<p><span class="k">&Sigma;</span> next to <span class="k">?</span> (also in the band menu, and the
<b>View &gt; Contest summary</b> in the main window) opens the contest summary of the log N1MM has
open: QSOs and how many came <b>from POUNCer</b> (you tapped that call on that band up to
15 minutes before logging it), multipliers and how many came from spots, rate on the
air, best 60 minutes, a per-band table, rate per hour and the list of multipliers you
worked from spots. POUNCer keeps the taps in <code>cache\\taps.jsonl</code> (14 days).</p>
<h2>CQ zones: who hears me</h2>
<p><b>CQ zones</b> in the band menu (or <b>View &gt; CQ zones</b> in the window): per band two
rows of CQ zones 1-40 - <b style="color:#64b5f6">hears me</b> = skimmers in that zone spotted your
call (you need to call CQ), <b style="color:#81c784">I hear</b> = skimmers on your continent heard
stations from that zone. Number = best SNR, brighter = stronger; tap a box for who and when.
Your zone is yellow. The last 15 minutes (or 30 / 60).</p>
<h2>Band opening timeline</h2>
<p><b>Band opening timeline</b> in the band menu (or <b>View &gt; Band opening timeline</b>): one
row per band, time left to right in 10-minute boxes (or 1 hour), now at the right. What the
skimmers on your continent heard: <span style="color:#e08a3c">orange</span> = only your
continent, <span style="color:#5bc35b">green</span> = DX, brighter = more spots. Tap a box for
the numbers. 6 / 12 / 24 / 48 h; kept 48 hours, also over a restart.</p>

<h2>SSB</h2>
<p>RBN has no SSB, so on SSB the spots come from a <b>DX cluster</b> (human spots, only from
spotters in Europe). The list follows the radio: radio on USB / LSB = SSB spots, on CW =
the RBN CW spots - per radio (A / B). On SSB the WPM keys are hidden. Settings: <b>SSB</b>
on the Settings page.</p>

<h2>Settings</h2>
<p><b>Settings</b> in the band menu (or <b>Settings...</b> in the main window's menu bar, a window with tabs): the
most-used settings of config.ini - wait times and how the call goes into N1MM, radio B,
cursor back, skimmer, backup server, auto-update - changed in the browser and written into
config.ini. Most work at once; the ones marked &#8635; after a restart (Restart button).
By default only from the browser on the PC itself (<code>[web] remote_settings</code>).</p>

<h2>Two radios (SO2R / SO2V)</h2>
<dl>
  <dt><span class="k blue">A</span> <span class="k" style="background:#ef6c00">B</span></dt>
  <dd>Next to the frequency, before CLR; only shown when N1MM runs two radios. Tap to switch which radio this phone works: taps, the
    F-keys, ENTER, &minus;/+ Hz and the band menu go to that radio (N1MM Entry Window A or
    B), and the list shows that radio's band. Each phone remembers its own choice, so
    one phone can work radio A and another radio B.</dd>
  <dt><span class="k">A 7020.0<br>B 14025.3</span></dt>
  <dd>With two radios the frequency shows both: A on top, B under it. The radio this
    phone works is big and bright. Tap it for the band menu (160-10 m): it is for the
    radio chosen with the <b>A / B</b> key - with A it shows that band's spots for radio
    A, with B for radio B (green = the band shown, blue ring = the band the radio is on).
    Tap a station there and that radio goes to it (radio B: POUNCer types the
    frequency + Enter into Entry Window B, then the call - like you would by hand).
    If your cursor was in the other Entry Window (e.g. typing the exchange in A), it
    goes back there afterwards - same box, same position.</dd>
</dl>

<h2>Bottom row</h2>
<dl>
  <dt><span class="k purple">WPM &minus;</span> <span class="k purple">WPM +</span></dt>
  <dd>CW speed down / up (N1MM's PgDn / PgUp). The number between them is the speed
  N1MM really has (read from its Entry Window); when it changes - from here, the
  keyboard or N1MM itself - a short <b>CW 30 WPM</b> popup shows the new speed.</dd>
  <dt><span class="k blue">&minus;25 Hz</span> <span class="k blue">+25 Hz</span></dt>
  <dd>Move the VFO down / up by 25 Hz. No transmit.</dd>
  <dt><span class="k teal">NEXT &#9654;</span></dt>
  <dd>Go to the next station of the list, in the order shown (your sort and the DX /
    MLT / &#9889; buttons): QSY and enter its call, exactly like tapping its tile. Starts
    after the last station you tapped; after the last one it starts again at the top.
    No transmit.</dd>
</dl>

<h2 id="test">Test options</h2>
<p>For busy contests - try them, switch off what you don't like. Saved on this
phone (each phone its own).</p>
<label class="opt"><input type="checkbox" id="opt-freqfrom">
  <b>Freq from my radio up</b> (Freq&#8593;) - the frequency sort starts at the radio
  frequency: the station you're on is first, then the next ones up the band; the ones
  below you come at the end. As you tune up, the list goes with you, and
  <b>NEXT</b> walks up the band. Also: tap <b>Freq</b> twice.</label>
<label class="opt"><input type="checkbox" id="opt-calm">
  <b>Calm list</b> - tiles keep their place instead of jumping around every second.
  New stations are added at the end with a <span style="color:#ffd54f">yellow line</span>
  on top; gone ones drop out; SNR / time update in place. The whole list is re-sorted
  every 15 s, but never within 3 s of you touching or scrolling it. Changing the sort,
  DX / MLT / &#9889;, band or radio re-sorts at once. "calm" at the start of the small
  line = on.</label>
<script>
  [['opt-freqfrom', 'pouncer.freqfrom'], ['opt-calm', 'pouncer.calm']].forEach(([id, key]) => {
    const box = document.getElementById(id);
    try { box.checked = localStorage.getItem(key) === '1'; } catch (e) {}
    box.onchange = () => { try { localStorage.setItem(key, box.checked ? '1' : '0'); } catch (e) {} };
  });
</script>

<div class="note">Messages (e.g. "Cleared 12 spots", "CW faster") pop up level with the
ENTER key for 5 s - tap to close them. The page refreshes every second; while you touch a tile the
list holds still.</div>

<h2 id="reset">Reset this page</h2>
<p>Something odd on this phone / browser only (another browser works)? The page
remembers a few choices <b>on this device</b>: radio A / B, sort, DX / MLT / &#9889;,
the band picked in the band menu, alerts and the test options. <b>Reset</b> forgets
them all and opens the spot list fresh - nothing in POUNCer or N1MM changes.</p>
<p><button id="resetpage" class="resetbtn">Reset this page</button>
  <span id="resetinfo" class="muted"></span></p>
<script>
  (function () {
    const keys = () => { const k = []; try { for (let i = 0; i < localStorage.length; i++) {
      const n = localStorage.key(i); if (n && n.indexOf('pouncer.') === 0) k.push(n); } } catch (e) {}
      return k; };
    const info = document.getElementById('resetinfo');
    const n = keys().length;
    info.textContent = n ? ' (' + n + ' saved choice' + (n > 1 ? 's' : '') + ')' : ' (nothing saved)';
    document.getElementById('resetpage').onclick = () => {
      keys().forEach(k => { try { localStorage.removeItem(k); } catch (e) {} });
      location.href = '/';
    };
  })();
</script>
<p><a class="back" href="/">&larr; Back to the spots</a></p>
</main></body>
</html>
"""


def local_ipv4s() -> list[str]:
    """Best-effort list of this machine's IPv4 addresses."""
    addrs: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addrs.add(info[4][0])
    except OSError:
        pass
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            addrs.add(probe.getsockname()[0])
        finally:
            probe.close()
    except OSError:
        pass
    return sorted(a for a in addrs if not a.startswith("127."))


_IPCONFIG_IPV4 = re.compile(r"IPv4[^:]*:\s*(\d+\.\d+\.\d+\.\d+)")


def parse_ipconfig(text: str) -> list[tuple[str, str]]:
    """Parse ``ipconfig`` output into ``(adapter header, ipv4)`` pairs.

    Adapter headers are the unindented lines ending in ``:`` (e.g.
    ``Ethernet adapter ZeroTier One [8056c2e21c000001]:``). The IPv4 line is
    found by the ``IPv4`` token, which stays in English in localised Windows
    (e.g. Slovenian ``Naslov IPv4``).
    """
    out: list[tuple[str, str]] = []
    adapter = ""
    for line in (text or "").splitlines():
        if line and not line[0].isspace() and line.rstrip().endswith(":"):
            adapter = line.strip().rstrip(":")
            continue
        m = _IPCONFIG_IPV4.search(line)
        if m and adapter:
            out.append((adapter, m.group(1)))
    return out


def adapter_ipv4s() -> list[tuple[str, str]]:
    """``(adapter, ipv4)`` for this PC (Windows ``ipconfig``); [] elsewhere."""
    if sys.platform != "win32":
        return []
    try:
        res = subprocess.run(
            ["ipconfig"], capture_output=True, timeout=5,
            creationflags=0x08000000,  # CREATE_NO_WINDOW: no console flash
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_ipconfig(res.stdout.decode("oem", errors="replace"))


def default_route_ipv4() -> str | None:
    """The IPv4 used for the default route (normally the LAN address)."""
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))  # UDP connect: nothing is sent
            return probe.getsockname()[0]
        finally:
            probe.close()
    except OSError:
        return None


def pick_web_address(
    bind_host: str = "0.0.0.0",
    adapters: list[tuple[str, str]] | None = None,
    lan_ip: str | None = None,
) -> tuple[str, str]:
    """Return ``(ip, network)`` to show for the phone view.

    A ZeroTier address wins (works from anywhere), then the LAN address,
    then ``localhost``. When ``[web] host`` is a specific address, that is
    what we listen on, so it is shown as-is.
    """
    if bind_host not in ("", "0.0.0.0"):
        return bind_host, "bound"
    if adapters is None:
        adapters = adapter_ipv4s()
    for name, ip in adapters:
        if "zerotier" in name.lower() and not ip.startswith("169.254."):
            return ip, "ZeroTier"
    if lan_ip is None:
        lan_ip = default_route_ipv4()
    if lan_ip and not lan_ip.startswith("127."):
        return lan_ip, "LAN"
    ips = local_ipv4s()
    if ips:
        return ips[0], "LAN"
    return "localhost", "local only"


ZONES_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>POUNCer __VERSION__ CQ zones</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; background: #101418; color: #e6e6e6;
    font: 15px/1.45 -apple-system, "Segoe UI", Roboto, sans-serif; }
  main { padding: 12px 16px 40px; max-width: 1200px; margin: 0 auto; }
  .bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  a.back, .bar button { display: inline-block; margin: 4px 0 8px; padding: 9px 13px; border: 0;
    background: #263238; color: #fff; border-radius: 10px; text-decoration: none;
    font: inherit; font-weight: 700; cursor: pointer; }
  a.back { background: #2e7d32; }
  .bar button.on { background: #1565c0; }
  h1 { font-size: 20px; margin: 6px 0 2px; }
  p.lead { opacity: .75; margin: 0 0 8px; font-size: 13px; }
  .wrap { overflow-x: auto; border-radius: 10px; background: #161c21; }
  table { border-collapse: separate; border-spacing: 2px; font-variant-numeric: tabular-nums; }
  th, td { width: 24px; min-width: 24px; height: 24px; text-align: center; font-size: 11px;
    padding: 0; border-radius: 4px; }
  th { font-weight: 600; opacity: .75; }
  th.mine { color: #ffeb3b; opacity: 1; }
  td.c { cursor: pointer; color: #fff; font-weight: 700; }
  td.e { background: #1f272d; }
  td.mine { box-shadow: inset 0 0 0 1px #ffeb3b; }
  .lab { position: sticky; left: 0; background: #161c21; text-align: left; white-space: nowrap;
    padding: 0 8px 0 4px; width: auto; min-width: 92px; font-size: 12px; z-index: 1; }
  .lab b { font-size: 13px; }
  .lab .me { color: #64b5f6; } .lab .hr { color: #81c784; }
  tr.first td, tr.first th { border-top: 6px solid transparent; }
  .n { opacity: .7; font-size: 11px; padding: 0 6px; width: auto; min-width: 44px; text-align: left; }
  .cnt { opacity: .6; font-size: 11px; }
  #info { margin-top: 10px; min-height: 44px; background: #1c2329; border-radius: 10px;
    padding: 10px 12px; font-size: 13px; }
  .legend { font-size: 12px; opacity: .85; margin: 8px 0; display: flex; gap: 14px; flex-wrap: wrap; }
  .legend i { display: inline-block; width: 12px; height: 12px; border-radius: 3px; margin-right: 4px;
    vertical-align: -2px; }
  .msg { background: #3e2723; border-radius: 10px; padding: 10px 12px; margin: 8px 0; font-size: 13px; }
</style>
</head>
<body><main>
<div class="bar">
  <a class="back" href="/">&larr; Spots</a>
  <span style="opacity:.7;font-size:13px">last</span>
  <button data-min="15">15 min</button><button data-min="30">30 min</button><button data-min="60">60 min</button>
</div>
<h1>CQ zones: who hears me, what I hear</h1>
<p class="lead" id="lead">Per band two rows: <b style="color:#64b5f6">hears me</b> = skimmers in that
CQ zone spotted <b>your call</b> (you need to call CQ); <b style="color:#81c784">I hear</b> =
skimmers on <b>your continent</b> heard stations from that zone. The number = best SNR (dB);
brighter = stronger; the small number after the label = how many zones. Tap a box for who and
when. Your zone is marked yellow.</p>
<div id="msg"></div>
<div class="wrap"><table id="t"></table></div>
<div class="legend">
  <span><i style="background:hsl(210,70%,22%)"></i>hears me, weak</span>
  <span><i style="background:hsl(210,80%,48%)"></i>hears me, strong</span>
  <span><i style="background:hsl(125,55%,20%)"></i>I hear, weak</span>
  <span><i style="background:hsl(125,65%,42%)"></i>I hear, strong</span>
  <span><i style="background:#1f272d"></i>nothing</span>
</div>
<div id="info">Tap a box.</div>
</main>
<script>
  let minutes = 15;     // the last 15 minutes unless you pick 30 / 60
  try { minutes = Number(localStorage.getItem('pouncer.zonemin')) || 15; } catch (e) {}
  let last = null;
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  function color(kind, snr) {
    const v = Math.max(0, Math.min(1, ((snr == null ? 8 : snr) - 3) / 27));   // 3 dB .. 30 dB
    return kind === 'me' ? 'hsl(210,' + (70 + 10 * v) + '%,' + (22 + 26 * v) + '%)'
                         : 'hsl(125,' + (55 + 10 * v) + '%,' + (20 + 22 * v) + '%)';
  }
  function markButtons() {
    document.querySelectorAll('.bar button').forEach(b => b.classList.toggle('on', Number(b.dataset.min) === minutes));
  }
  function render(d) {
    last = d;
    const t = document.getElementById('t');
    const mz = d.my_zone;
    let h = '<tr><th class="lab"></th>';
    for (let z = 1; z <= 40; z++) h += '<th' + (z === mz ? ' class="mine"' : '') + '>' + z + '</th>';
    h += '<th class="n">zones</th></tr>';
    (d.bands || []).forEach(band => {
      [['me', 'hears_me', 'hears me'], ['hr', 'i_hear', 'I hear']].forEach(([cls, key, label], i) => {
        const row = (d[key] || {})[band] || {};
        let n = 0, cells = '';
        for (let z = 1; z <= 40; z++) {
          const c = row[z] || row[String(z)];
          const mine = z === mz ? ' mine' : '';
          if (!c) { cells += '<td class="e' + mine + '"></td>'; continue; }
          n++;
          cells += '<td class="c' + mine + '" style="background:' + color(cls, c.snr) + '" data-b="' + esc(band) +
            '" data-k="' + key + '" data-z="' + z + '">' + (c.snr == null ? '&bull;' : esc(c.snr)) + '</td>';
        }
        // the zone count also next to the label: on a phone the end of the row is off screen
        h += '<tr' + (i === 0 ? ' class="first"' : '') + '><td class="lab">' +
          (i === 0 ? '<b>' + esc(band) + '</b> ' : '<span style="visibility:hidden"><b>' + esc(band) + '</b> </span>') +
          '<span class="' + cls + '">' + label + '</span> <span class="cnt">' + n + '</span></td>' +
          cells + '<td class="n">' + n + '</td></tr>';
      });
    });
    t.innerHTML = h;
    const msgs = [];
    if (!d.own) msgs.push('Your call isn\\'t known yet (N1MM station call or [rbn] callsign) - "hears me" stays empty.');
    if (d.team === 'slave') msgs.push('This POUNCer is a team <b>slave</b>: it has no RBN feed of its own. Open this page on the master POUNCer.');
    document.getElementById('msg').innerHTML = msgs.map(m => '<div class="msg">' + m + '</div>').join('');
  }
  document.getElementById('t').addEventListener('click', e => {
    const td = e.target.closest('td.c');
    if (!td || !last) return;
    const c = ((last[td.dataset.k] || {})[td.dataset.b] || {})[td.dataset.z];
    if (!c) return;
    const me = td.dataset.k === 'hears_me';
    document.getElementById('info').innerHTML = '<b>' + esc(td.dataset.b) + ', CQ zone ' + esc(td.dataset.z) + '</b>: ' +
      (me ? c.calls + ' skimmer' + (c.calls > 1 ? 's' : '') + ' heard you'
          : c.calls + ' station' + (c.calls > 1 ? 's' : '') + ' heard here') +
      (c.snr != null ? ', best ' + c.snr + ' dB' : '') + ', ' + c.n + ' spot' + (c.n > 1 ? 's' : '') +
      ', last ' + (c.age ? c.age + ' min ago' : 'just now') + '<br><span style="opacity:.8">' +
      esc((c.who || []).join(', ')) + (c.calls > (c.who || []).length ? ' ...' : '') + '</span>';
  });
  document.querySelectorAll('.bar button').forEach(b => b.onclick = () => {
    minutes = Number(b.dataset.min);
    try { localStorage.setItem('pouncer.zonemin', String(minutes)); } catch (e) {}
    markButtons(); load();
  });
  async function load() {
    try {
      const r = await fetch('/api/zones?min=' + minutes, {cache: 'no-store'});
      const d = await r.json();
      if (d.ok === false) { document.getElementById('msg').innerHTML = '<div class="msg">' + esc(d.error) + '</div>'; return; }
      render(d);
    } catch (e) {
      document.getElementById('msg').innerHTML = '<div class="msg">POUNCer not reachable: ' + esc(e) + '</div>';
    }
  }
  markButtons(); load(); setInterval(load, 15000);
</script>
</body>
</html>
"""


TIMELINE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>POUNCer __VERSION__ band timeline</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; background: #101418; color: #e6e6e6;
    font: 15px/1.45 -apple-system, "Segoe UI", Roboto, sans-serif; }
  main { padding: 12px 16px 40px; max-width: 1400px; margin: 0 auto; }
  .bar { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
  a.back, .bar button { display: inline-block; margin: 4px 0 8px; padding: 9px 12px; border: 0;
    background: #263238; color: #fff; border-radius: 10px; text-decoration: none;
    font: inherit; font-weight: 700; cursor: pointer; }
  a.back { background: #2e7d32; }
  .bar button.on { background: #1565c0; }
  .sep { width: 10px; }
  h1 { font-size: 20px; margin: 6px 0 2px; }
  p.lead { opacity: .75; margin: 0 0 10px; font-size: 13px; }
  .wrap { overflow-x: auto; background: #161c21; border-radius: 10px; padding: 8px 8px 6px; }
  .grid { display: grid; gap: 2px 0; align-items: center; }
  .lab { position: sticky; left: 0; background: #161c21; font-weight: 700; font-size: 13px;
    padding-right: 8px; z-index: 1; }
  .cell { height: 26px; border-radius: 2px; cursor: pointer; margin-right: 1px; }
  .cell.h { margin-left: 3px; }                       /* first box of an hour */
  .cell.now { outline: 1px solid #ffeb3b; outline-offset: -1px; }
  .cell.e { background: #1f272d; }
  .hours { font-size: 11px; opacity: .7; height: 16px; white-space: nowrap; overflow: visible; }
  .tot { font-size: 11px; opacity: .6; padding-left: 8px; white-space: nowrap; }
  #info { margin-top: 10px; min-height: 40px; background: #1c2329; border-radius: 10px;
    padding: 10px 12px; font-size: 13px; }
  .legend { font-size: 12px; opacity: .85; margin: 8px 0; display: flex; gap: 14px; flex-wrap: wrap;
    align-items: center; }
  .legend i { display: inline-block; width: 14px; height: 12px; border-radius: 2px; margin-right: 4px;
    vertical-align: -2px; }
  .msg { background: #3e2723; border-radius: 10px; padding: 10px 12px; margin: 8px 0; font-size: 13px; }
</style>
</head>
<body><main>
<div class="bar">
  <a class="back" href="/">&larr; Spots</a>
  <button data-h="6">6 h</button><button data-h="12">12 h</button><button data-h="24">24 h</button><button data-h="48">48 h</button>
  <span class="sep"></span>
  <button data-g="10">10 min</button><button data-g="60">1 hour</button>
</div>
<h1>Band opening timeline</h1>
<p class="lead">What the skimmers on <b>your continent</b> heard, per band, in boxes of 10 minutes
(or 1 hour), time in UTC from left to right, now at the right (yellow frame).
<b>Colour</b> = the DX share like the band chips: <span style="color:#e08a3c">orange</span> = only
your continent, <span style="color:#5bc35b">green</span> = DX; <b>brighter</b> = more spots.
Tap a box for the numbers. Kept for 48 hours, also over a restart.</p>
<div id="msg"></div>
<div class="wrap"><div class="grid" id="g"></div></div>
<div class="legend">
  <span><i style="background:hsl(28,75%,22%)"></i>few, own continent</span>
  <span><i style="background:hsl(28,75%,48%)"></i>many, own continent</span>
  <span><i style="background:hsl(125,75%,22%)"></i>few, DX</span>
  <span><i style="background:hsl(125,75%,45%)"></i>many, DX</span>
  <span><i style="background:#1f272d"></i>nothing</span>
</div>
<div id="info">Tap a box.</div>
</main>
<script>
  let hours = 12, group = 10, last = null;
  try { hours = Number(localStorage.getItem('pouncer.tlhours')) || 12;
        group = Number(localStorage.getItem('pouncer.tlgroup')) || 10; } catch (e) {}
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const hhmm = t => { const d = new Date(t * 1000); return String(d.getUTCHours()).padStart(2, '0') + ':' +
    String(d.getUTCMinutes()).padStart(2, '0'); };
  function color(spots, stations, dx, max) {
    if (!spots) return '#1f272d';
    const share = stations ? dx / stations : 0;
    const hue = 28 + (125 - 28) * share;
    const v = Math.log(1 + spots) / Math.log(1 + Math.max(max, 2));
    return 'hsl(' + hue.toFixed(0) + ',75%,' + (16 + 34 * v).toFixed(0) + '%)';
  }
  function boxes(d) {           // [{t, len, cells: {band: [spots, stations, dx]}}]
    const per = group === 60 ? 6 : 1, out = [];
    for (let i = 0; i < d.slots.length; i++) {
      const t = d.slots[i];
      if (per > 1 && i > 0 && new Date(t * 1000).getUTCMinutes() !== 0 && out.length) {
        const b = out[out.length - 1];
        d.bands.forEach(band => { const c = d.cells[band][i], s = b.cells[band];
          s[0] += c[0]; s[1] += c[1]; s[2] += c[2]; });
        b.len += d.slot_s;
        continue;
      }
      const cells = {};
      d.bands.forEach(band => { cells[band] = d.cells[band][i].slice(); });
      out.push({t: t, len: d.slot_s, cells: cells});
    }
    return out;
  }
  function render(d) {
    last = d;
    const bx = boxes(d);
    last.boxes = bx;
    let max = 0;
    bx.forEach(b => d.bands.forEach(band => { max = Math.max(max, b.cells[band][0]); }));
    const wrapW = document.querySelector('.wrap').clientWidth - 70 - 60;
    const w = Math.max(group === 60 ? 14 : 6, Math.floor(wrapW / bx.length) - 1);
    const g = document.getElementById('g');
    g.style.gridTemplateColumns = 'max-content repeat(' + bx.length + ', ' + w + 'px) max-content';
    let h = '<div class="lab"></div>';
    // hour labels: every hour if there is room, else every 2 / 3 / 6 h
    const pxPerHour = (w + 1) * (group === 60 ? 1 : 6);
    const every = pxPerHour >= 34 ? 1 : (pxPerHour >= 17 ? 2 : (pxPerHour >= 11 ? 3 : 6));
    bx.forEach(b => {
      const d0 = new Date(b.t * 1000), top = d0.getUTCMinutes() === 0;
      h += '<div class="hours">' + (top && d0.getUTCHours() % every === 0 ? String(d0.getUTCHours()).padStart(2, '0') : '') + '</div>';
    });
    h += '<div class="hours tot">spots</div>';
    const nowIdx = bx.length - 1;
    d.bands.forEach(band => {
      h += '<div class="lab">' + esc(band) + '</div>';
      let tot = 0;
      bx.forEach((b, i) => {
        const c = b.cells[band];
        tot += c[0];
        const top = new Date(b.t * 1000).getUTCMinutes() === 0 && i > 0;
        h += '<div class="cell' + (c[0] ? '' : ' e') + (top ? ' h' : '') + (i === nowIdx ? ' now' : '') +
          '" data-i="' + i + '" data-b="' + esc(band) + '" style="background:' + color(c[0], c[1], c[2], max) + '"></div>';
      });
      h += '<div class="tot">' + tot + '</div>';
    });
    const wrap = document.querySelector('.wrap');
    const atEnd = !wrap.dataset.seen || wrap.scrollLeft + wrap.clientWidth >= wrap.scrollWidth - 20;
    g.innerHTML = h;
    // narrow screen: show "now" (the right end) unless you scrolled back
    if (atEnd) wrap.scrollLeft = wrap.scrollWidth;
    wrap.dataset.seen = '1';
    const msgs = [];
    if (d.team === 'slave') msgs.push('This POUNCer is a team <b>slave</b>: it has no RBN feed of its own - open this page on the master.');
    document.getElementById('msg').innerHTML = msgs.map(m => '<div class="msg">' + m + '</div>').join('');
  }
  document.getElementById('g').addEventListener('click', e => {
    const el = e.target.closest('.cell');
    if (!el || !last) return;
    const b = last.boxes[Number(el.dataset.i)], c = b.cells[el.dataset.b];
    const pct = c[1] ? Math.round(100 * c[2] / c[1]) : 0;
    document.getElementById('info').innerHTML = '<b>' + esc(el.dataset.b) + ' ' + hhmm(b.t) + '-' + hhmm(b.t + b.len) +
      ' UTC</b>: ' + (c[0] ? c[0] + ' spots, ' + c[1] + ' stations, ' + c[2] + ' DX (' + pct + '%)' +
      (group === 60 ? ' <span style="opacity:.7">(stations added up over the six 10-min boxes)</span>' : '')
      : 'nothing heard');
  });
  function markButtons() {
    document.querySelectorAll('[data-h]').forEach(b => b.classList.toggle('on', Number(b.dataset.h) === hours));
    document.querySelectorAll('[data-g]').forEach(b => b.classList.toggle('on', Number(b.dataset.g) === group));
  }
  document.querySelectorAll('[data-h]').forEach(b => b.onclick = () => {
    hours = Number(b.dataset.h); try { localStorage.setItem('pouncer.tlhours', String(hours)); } catch (e) {}
    markButtons(); load();
  });
  document.querySelectorAll('[data-g]').forEach(b => b.onclick = () => {
    group = Number(b.dataset.g); try { localStorage.setItem('pouncer.tlgroup', String(group)); } catch (e) {}
    markButtons(); if (last) render(last);
  });
  window.addEventListener('resize', () => { if (last) render(last); });
  async function load() {
    try {
      const r = await fetch('/api/timeline?hours=' + hours, {cache: 'no-store'});
      const d = await r.json();
      if (d.ok === false) { document.getElementById('msg').innerHTML = '<div class="msg">' + esc(d.error) + '</div>'; return; }
      render(d);
    } catch (e) {
      document.getElementById('msg').innerHTML = '<div class="msg">POUNCer not reachable: ' + esc(e) + '</div>';
    }
  }
  markButtons(); load(); setInterval(load, 60000);
</script>
</body>
</html>
"""


SUMMARY_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>POUNCer __VERSION__ contest summary</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; background: #101418; color: #e6e6e6;
    font: 15px/1.45 -apple-system, "Segoe UI", Roboto, sans-serif; }
  main { max-width: 720px; margin: 0 auto; padding: 12px 16px 40px; }
  .bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  a.back, button.reload { display: inline-block; margin: 4px 0 12px; padding: 10px 14px; border: 0;
    background: #2e7d32; color: #fff; border-radius: 10px; text-decoration: none; font-weight: 700;
    font: inherit; font-weight: 700; cursor: pointer; }
  button.reload { background: #263238; }
  h1 { font-size: 20px; margin: 6px 0 2px; }
  h2 { font-size: 16px; margin: 22px 0 8px; color: #fff; border-bottom: 1px solid #263238;
    padding-bottom: 4px; }
  p.lead { opacity: .7; margin: 0 0 6px; font-size: 13px; }
  .stats { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 8px; }
  .stat { background: #1c2329; border-radius: 10px; padding: 10px 12px; }
  .stat b { display: block; font-size: 24px; color: #fff; font-variant-numeric: tabular-nums; }
  .stat span { font-size: 12px; opacity: .75; }
  .stat.p b { color: #81c784; }
  table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
  th, td { padding: 6px 8px; text-align: right; border-bottom: 1px solid #1f282e; }
  th:first-child, td:first-child { text-align: left; }
  th { font-size: 12px; font-weight: 600; opacity: .7; }
  tr.tot td { font-weight: 700; border-top: 1px solid #37474f; }
  .p { color: #81c784; }
  .hours { display: grid; grid-template-columns: max-content 1fr max-content; gap: 3px 8px;
    align-items: center; font-size: 13px; font-variant-numeric: tabular-nums; }
  .track { height: 14px; background: #1c2329; border-radius: 4px; overflow: hidden; display: flex; }
  .track i { display: block; height: 100%; }
  .track i.pp { background: #43a047; } .track i.oo { background: #546e7a; }
  .legend { font-size: 12px; opacity: .8; margin: 4px 0 8px; }
  .legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin: 0 4px 0 10px; }
  .msg { background: #3e2723; border-radius: 10px; padding: 12px; }
  .note { background: #1c2329; border-radius: 10px; padding: 10px 12px; margin-top: 14px; font-size: 13px; }
  .empty { opacity: .6; font-size: 13px; }
</style>
</head>
<body><main>
<div class="bar"><a class="back" href="/">&larr; Back to the spots</a>
  <button class="reload" id="reload">&#8635; Refresh</button></div>
<h1>Contest summary</h1>
<p class="lead" id="lead">Reading the N1MM log...</p>
<div id="out"></div>
<div class="note">A QSO counts as <span class="p">from POUNCer</span> when you tapped that
call on that band (tile, NEXT or the main window) up to 15 minutes before it was logged.
Multipliers: the first QSO with a new zone / country on a band (WPX prefix: once per
contest). Times UTC. Off time = gaps of 30 minutes or more. The page updates every minute.</div>
</main>
<script>
  const out = document.getElementById('out');
  const lead = document.getElementById('lead');
  function esc(s) {
    return String(s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;',
      '"': '&quot;', "'": '&#39;'}[c]));
  }
  function pct(a, b) { return b ? Math.round(100 * a / b) + '%' : '-'; }
  function stat(v, label, cls) {
    return '<div class="stat' + (cls ? ' ' + cls : '') + '"><b>' + v + '</b><span>' + label + '</span></div>';
  }
  function render(d) {
    if (!d.ok) {
      lead.textContent = '';
      out.innerHTML = '<div class="msg">' + esc(d.error || 'No log open in N1MM') + '</div>';
      return;
    }
    lead.innerHTML = esc([d.station, d.contest, d.log].filter(Boolean).join(' \\u00b7 ')) +
      (d.first ? '<br>' + esc(d.first) + ' \\u2013 ' + esc(d.last.slice(-5)) + ' UTC' : '');
    let h = '<div class="stats">' +
      stat(d.qsos, 'QSOs') +
      stat(d.pouncer + ' <small>(' + d.pouncer_pct + '%)</small>', 'QSOs from POUNCer taps', 'p') +
      stat(d.mults, 'multipliers') +
      stat(d.pouncer_mults, 'mults from spots', 'p') +
      stat(d.rate == null ? '-' : d.rate, 'QSOs / hour on the air (' + d.on_hours + ' h)') +
      stat(d.best60, 'best 60 min' + (d.best60_at ? ' (from ' + esc(d.best60_at) + ')' : '')) +
      stat(d.tapped_worked + ' / ' + d.tapped_stations, 'tapped stations worked (' +
           pct(d.tapped_worked, d.tapped_stations) + ')') +
      '</div>';
    h += '<h2>Per band</h2>';
    if (d.bands.length) {
      h += '<table><tr><th>Band</th><th>QSOs</th><th class="p">POUNCer</th><th>Mults</th><th class="p">from spots</th></tr>';
      d.bands.forEach(b => {
        h += '<tr><td>' + esc(b.band) + '</td><td>' + b.qsos + '</td><td class="p">' + b.pouncer +
          ' <small>' + pct(b.pouncer, b.qsos) + '</small></td><td>' + b.mults + '</td><td class="p">' +
          b.pouncer_mults + '</td></tr>';
      });
      h += '<tr class="tot"><td>Total</td><td>' + d.qsos + '</td><td class="p">' + d.pouncer +
        '</td><td>' + d.mults + '</td><td class="p">' + d.pouncer_mults + '</td></tr></table>';
    } else h += '<p class="empty">No QSOs in this log yet.</p>';
    h += '<h2>Rate per hour</h2>';
    if (d.hours.length) {
      const max = Math.max(...d.hours.map(x => x.qsos));
      h += '<div class="legend"><i style="background:#43a047"></i>from POUNCer' +
        '<i style="background:#546e7a"></i>other QSOs</div><div class="hours">';
      d.hours.forEach(x => {
        const wp = 100 * x.pouncer / max, wo = 100 * (x.qsos - x.pouncer) / max;
        h += '<span>' + esc(x.hour.slice(5)) + '</span><div class="track"><i class="pp" style="width:' +
          wp + '%"></i><i class="oo" style="width:' + wo + '%"></i></div><span>' + x.qsos +
          (x.pouncer ? ' <span class="p">(' + x.pouncer + ')</span>' : '') + '</span>';
      });
      h += '</div>';
    } else h += '<p class="empty">-</p>';
    h += '<h2>Multipliers from spots (' + d.mults_from_spots.length + ')</h2>';
    if (d.mults_from_spots.length) {
      h += '<table><tr><th>Time</th><th>Band</th><th>Call</th><th>New</th></tr>';
      d.mults_from_spots.slice().reverse().forEach(m => {
        h += '<tr><td>' + esc(m.time) + '</td><td>' + esc(m.band) + '</td><td>' + esc(m.call) +
          '</td><td>' + esc(m.kind) + ' ' + esc(m.value) + '</td></tr>';
      });
      h += '</table>';
    } else h += '<p class="empty">None yet.</p>';
    out.innerHTML = h;
  }
  async function load() {
    try {
      const r = await fetch('/api/summary', {cache: 'no-store'});
      render(await r.json());
    } catch (e) {
      lead.textContent = 'POUNCer not reachable - retrying...';
    }
  }
  document.getElementById('reload').onclick = load;
  load();
  setInterval(load, 60000);
</script>
</body>
</html>
"""


SETTINGS_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>POUNCer __VERSION__ settings</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; background: #101418; color: #e6e6e6;
    font: 15px/1.45 -apple-system, "Segoe UI", Roboto, sans-serif; }
  main { max-width: 720px; margin: 0 auto; padding: 12px 16px 60px; }
  .bar { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  .bar a, .bar button { display: inline-block; margin: 4px 0 10px; padding: 10px 14px; border: 0;
    background: #263238; color: #fff; border-radius: 10px; text-decoration: none;
    font: inherit; font-weight: 700; cursor: pointer; }
  .bar a.back { background: #2e7d32; }
  .bar button.restart { background: #ef6c00; }
  h1 { font-size: 20px; margin: 6px 0 2px; }
  h2 { font-size: 16px; margin: 22px 0 8px; border-bottom: 1px solid #263238; padding-bottom: 4px; }
  p.lead { opacity: .75; margin: 0 0 8px; font-size: 13px; }
  .row { display: grid; grid-template-columns: 1fr auto; gap: 4px 12px; align-items: center;
    padding: 10px 0; border-bottom: 1px solid #1c2329; }
  .row label { font-weight: 600; }
  .row .help { grid-column: 1 / -1; font-size: 12px; opacity: .65; }
  .row .state { grid-column: 1 / -1; font-size: 12px; min-height: 0; }
  .ok { color: #81c784; } .err { color: #ef9a9a; } .later { color: #ffb74d; }
  input[type=number], input[type=text], select { background: #0b0f12; color: #fff;
    border: 1px solid #37474f; border-radius: 8px; padding: 8px; font: inherit; }
  input[type=number] { width: 7em; } input[type=text] { width: 12em; }
  input[type=checkbox] { transform: scale(1.5); margin: 6px; }
  .ro input, .ro select { opacity: .6; }
  .warn { background: #3e2723; border-radius: 10px; padding: 10px 12px; font-size: 13px; }
  @media (max-width: 420px) { input[type=text] { width: 9em; } }
</style>
</head>
<body><main>
<div class="bar"><a class="back" href="/">&larr; Back to the spots</a>
  <button class="restart" id="restart" hidden>Restart POUNCer</button></div>
<h1>Settings</h1>
<p class="lead" id="lead">loading...</p>
<div id="out"></div>
<script>
  const out = document.getElementById('out');
  const lead = document.getElementById('lead');
  function esc(s) {
    return String(s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;',
      '"': '&quot;', "'": '&#39;'}[c]));
  }
  function field(it, ro) {
    const id = 'f_' + it.key.replace('.', '_');
    const dis = ro ? ' disabled' : '';
    if (it.kind === 'bool')
      return '<input type="checkbox" id="' + id + '"' + (it.value ? ' checked' : '') + dis + '>';
    if (it.kind === 'choice')
      return '<select id="' + id + '"' + dis + '>' + it.choices.map(c =>
        '<option' + (String(c) === String(it.value) ? ' selected' : '') + '>' + esc(c) + '</option>').join('') + '</select>';
    if (it.kind === 'int')
      return '<input type="number" id="' + id + '" value="' + esc(it.value) + '" min="' + it.min +
        '" max="' + it.max + '"' + dis + '>';
    return '<input type="text" id="' + id + '" value="' + esc(it.value == null ? '' : it.value) + '"' + dis + '>';
  }
  async function save(it, el, state) {
    const v = it.kind === 'bool' ? (el.checked ? 'true' : 'false') : el.value;
    state.className = 'state'; state.textContent = 'saving...';
    try {
      const r = await fetch('/api/settings/set?' + new URLSearchParams({key: it.key, value: v}));
      const j = await r.json();
      if (j.ok) {
        state.className = 'state ' + (j.live ? 'ok' : 'later');
        state.textContent = j.live ? 'Saved - in use now' : 'Saved - used after a restart';
        if (!j.live) document.getElementById('restart').hidden = !window.canRestart;
      } else {
        state.className = 'state err'; state.textContent = j.error || 'not saved';
      }
    } catch (e) {
      state.className = 'state err'; state.textContent = 'POUNCer not reachable';
    }
  }
  async function load() {
    const r = await fetch('/api/settings', {cache: 'no-store'});
    const d = await r.json();
    window.canRestart = d.can_restart;
    const ro = !d.can_edit;
    lead.innerHTML = 'Written into <code>' + esc(d.path || 'config.ini') + '</code> of this POUNCer ' +
      '(each PC has its own). Changes are saved at once.' + (ro ? '' : ' Orange = used after a restart.');
    let h = ro ? '<div class="warn">Read-only here: settings can be changed only in the browser on the PC ' +
      'that runs POUNCer (http://127.0.0.1:' + location.port + '/settings), or set ' +
      '<code>[web] remote_settings = true</code>.</div>' : '';
    let group = '';
    d.items.forEach(it => {
      if (it.group !== group) { group = it.group; h += '<h2>' + esc(group) + '</h2>'; }
      h += '<div class="row' + (ro ? ' ro' : '') + '"><label for="f_' + it.key.replace('.', '_') + '">' +
        esc(it.label) + (it.live ? '' : ' <span class="later" title="used after a restart">&#8635;</span>') +
        '</label>' + field(it, ro) +
        (it.help ? '<div class="help">' + esc(it.help) + ' <code>[' + esc(it.key.replace('.', '] ')) + '</code></div>' : '') +
        '<div class="state" id="s_' + it.key.replace('.', '_') + '"></div></div>';
    });
    out.innerHTML = h;
    if (ro) return;
    d.items.forEach(it => {
      const el = document.getElementById('f_' + it.key.replace('.', '_'));
      const state = document.getElementById('s_' + it.key.replace('.', '_'));
      el.addEventListener('change', () => save(it, el, state));
    });
  }
  document.getElementById('restart').onclick = async () => {
    try { await fetch('/api/restart'); } catch (e) {}
    lead.textContent = 'Restarting POUNCer... this page reloads in a few seconds.';
    setTimeout(() => location.reload(), 6000);
  };
  load().catch(() => { lead.textContent = 'POUNCer not reachable'; });
</script>
</main></body>
</html>
"""


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address,
        get_spots: SpotsProvider,
        on_qsy: QSYHandler,
        on_key: KeyHandler,
        on_reconnect: ReconnectHandler | None = None,
        on_band: BandHandler | None = None,
        on_hide: HideHandler | None = None,
        on_tune: TuneHandler | None = None,
        on_filter: FilterHandler | None = None,
        on_clear: ClearHandler | None = None,
    ):
        super().__init__(address, _Handler)
        self.on_clear = on_clear
        self.on_tune = on_tune
        self.on_filter = on_filter
        self.on_band = on_band
        self.on_hide = on_hide
        self.get_spots = get_spots
        self.on_qsy = on_qsy
        self.on_key = on_key
        self.on_cw = None  # phone CW keyboard (set_cw_handler)
        self.on_min_snr = None  # phone SNR button (set_min_snr_handler)
        self.on_reconnect = on_reconnect


class _Handler(BaseHTTPRequestHandler):
    server_version = "POUNCer"

    def log_message(self, *args) -> None:  # silence stderr access logs
        pass

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _local_client(self) -> bool:
        ip = (self.client_address[0] or "") if self.client_address else ""
        return ip in ("127.0.0.1", "::1", "::ffff:127.0.0.1") or ip.startswith("127.")

    def _settings_api(self, parsed) -> None:
        srv = self.server
        items = getattr(srv, "get_settings", None)
        if items is None:
            self._send_json({"ok": False, "error": "not available"})
            return
        can_edit = self._local_client() or getattr(srv, "remote_settings", False)
        if parsed.path == "/api/settings":
            data = items()
            data["can_edit"] = can_edit
            data["can_restart"] = bool(getattr(srv, "restart_app", None)) and can_edit
            self._send_json(data)
            return
        if not can_edit:
            self._send_json({"ok": False, "error": "settings can be changed only on this PC "
                                                   "([web] remote_settings = true for all)"}, 403)
            return
        if parsed.path == "/api/restart":
            fn = getattr(srv, "restart_app", None)
            if fn is None:
                self._send_json({"ok": False, "error": "restart works only in the exe"})
                return
            fn()
            self._send_json({"ok": True})
            return
        q = parse_qs(parsed.query)
        key = (q.get("key") or [""])[0]
        value = (q.get("value") or [""])[0]
        try:
            self._send_json(srv.save_setting(key, value))
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, 500)

    def _send_json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

    def do_HEAD(self) -> None:
        self.do_GET()

    def _radio(self, parsed) -> int | None:
        """SO2R: ?radio=1|2 (A|B) chosen on the phone; None = not given."""
        raw = (parse_qs(parsed.query).get("radio") or [""])[0].strip().upper()
        return {"1": 1, "A": 1, "2": 2, "B": 2}.get(raw)

    def _call(self, fn, parsed, *args):
        """Call a handler, adding radio= only when the phone sent one (so
        handlers without SO2R support keep working)."""
        radio = self._radio(parsed)
        return fn(*args, radio=radio) if radio is not None else fn(*args)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:   # load counters for the perf log (requests / min, phones open)
            srv = self.server
            srv.req_count = getattr(srv, "req_count", 0) + 1
            seen = getattr(srv, "clients", None)
            if seen is None:
                seen = srv.clients = {}
            seen[self.client_address[0]] = time.time()
        except Exception:  # noqa: BLE001
            pass
        if parsed.path in ("/help", "/help.html"):
            body = HELP_HTML.replace("__VERSION__", __version__).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path in ("/", "/index.html"):
            page = INDEX_HTML.replace("__VERSION__", __version__)
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/spots":
            band = (parse_qs(parsed.query).get("band") or [""])[0].strip()
            if band:   # band menu: the list of another band (no QSY)
                radio = self._radio(parsed)
                kw = {"band": band}
                if radio is not None:
                    kw["radio"] = radio
                data = self.server.get_spots(**kw)
            else:
                data = self._call(self.server.get_spots, parsed)
            # the page reloads itself when POUNCer was updated under it
            self._send_json({**data, "version": __version__} if isinstance(data, dict) else data)
            return
        if parsed.path == "/api/qsy":
            q = parse_qs(parsed.query)
            call = (q.get("call") or [""])[0].strip()
            mode = (q.get("mode") or [""])[0].strip()
            try:
                freq = float((q.get("freq") or ["0"])[0])
            except ValueError:
                freq = 0.0
            if not call:
                self._send_json({"ok": False, "error": "call required"}, 400)
                return
            try:
                self._call(self.server.on_qsy, parsed, call, freq, mode)
            except Exception as exc:  # never kill the server thread
                log.warning("web qsy failed: %s", exc)
                self._send_json({"ok": False, "error": str(exc)}, 500)
                return
            self._send_json({"ok": True, "call": call})
            return
        if parsed.path == "/api/minsnr":
            fn = getattr(self.server, "on_min_snr", None)
            try:
                db = int(float((parse_qs(parsed.query).get("db") or [""])[0]))
            except ValueError:
                db = -1
            if fn is None or not 0 <= db <= 60:
                self._send_json({"ok": False, "error": "db must be 0..60"}, 400 if fn else 404)
                return
            fn(db)
            self._send_json({"ok": True, "db": db})
            return
        if parsed.path == "/api/cw":
            fn = getattr(self.server, "on_cw", None)
            q = parse_qs(parsed.query)
            op = (q.get("op") or [""])[0].strip().lower()
            if fn is None:
                self._send_json({"ok": False, "error": "CW keyboard is off ([web] cw_keyboard)"}, 404)
                return
            if op not in CW_OPS:
                self._send_json({"ok": False, "error": "bad op"}, 400)
                return
            text = clean_cw_text((q.get("text") or [""])[0]) if op == "text" else ""
            try:
                n = max(0, min(50, int((q.get("n") or ["0"])[0])))
            except ValueError:
                n = 0
            try:
                self._call(fn, parsed, op, text, n)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
                return
            self._send_json({"ok": True, "op": op, "text": text})
            return
        if parsed.path == "/api/key":
            key = (parse_qs(parsed.query).get("key") or [""])[0].strip()
            if not is_send_key(key):
                self._send_json({"ok": False, "error": "bad key"}, 400)
                return
            norm = normalize_key(key)
            try:
                self._call(self.server.on_key, parsed, norm)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
                return
            self._send_json({"ok": True, "key": norm})
            return
        if parsed.path == "/api/tune":
            raw = (parse_qs(parsed.query).get("hz") or [""])[0].strip()
            try:
                hz = int(raw)
            except ValueError:
                hz = 0
            if not hz or abs(hz) > 10000:
                self._send_json({"ok": False, "error": "hz must be +/-1..10000"}, 400)
                return
            if self.server.on_tune is None:
                self._send_json({"ok": False, "error": "not supported"}, 404)
                return
            try:
                self._call(self.server.on_tune, parsed, hz)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
                return
            self._send_json({"ok": True, "hz": hz})
            return
        if parsed.path == "/api/filter":
            q = parse_qs(parsed.query)
            width = (q.get("w") or [""])[0].strip().lower()
            fix = (q.get("fix") or ["0"])[0].strip() in ("1", "true", "yes")
            if width not in ("narrow", "wide"):
                self._send_json({"ok": False, "error": "w must be narrow or wide"}, 400)
                return
            if self.server.on_filter is None:
                self._send_json({"ok": False, "error": "not supported"}, 404)
                return
            try:
                self.server.on_filter(width, fix)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
                return
            self._send_json({"ok": True, "w": width, "fix": fix})
            return
        if parsed.path in ("/api/hide", "/api/unhide"):
            q = parse_qs(parsed.query)
            call = (q.get("call") or [""])[0].strip().upper()
            try:
                freq = float((q.get("freq") or ["0"])[0])
            except ValueError:
                freq = 0.0
            if not call or freq <= 0:
                self._send_json({"ok": False, "error": "call and freq required"}, 400)
                return
            if self.server.on_hide is None:
                self._send_json({"ok": False, "error": "not supported"}, 404)
                return
            try:
                self.server.on_hide(call, freq, parsed.path == "/api/hide")
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
                return
            self._send_json({"ok": True, "call": call})
            return
        if parsed.path == "/api/band":
            band = (parse_qs(parsed.query).get("band") or [""])[0].strip().lower()
            if band not in CONTEST_BANDS:
                self._send_json({"ok": False, "error": "bad band"}, 400)
                return
            if self.server.on_band is None:
                self._send_json({"ok": False, "error": "not supported"}, 404)
                return
            try:
                self._call(self.server.on_band, parsed, band)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
                return
            self._send_json({"ok": True, "band": band})
            return
        if parsed.path in ("/settings", "/settings.html"):
            page = SETTINGS_HTML.replace("__VERSION__", __version__)
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path.startswith("/api/settings") or parsed.path == "/api/restart":
            self._settings_api(parsed)
            return
        if parsed.path in ("/timeline", "/timeline.html"):
            page = TIMELINE_HTML.replace("__VERSION__", __version__)
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/timeline":
            getter = getattr(self.server, "get_timeline", None)
            if getter is None:
                self._send_json({"ok": False, "error": "not available"})
                return
            try:
                hours = int((parse_qs(parsed.query).get("hours") or ["12"])[0])
            except ValueError:
                hours = 12
            try:
                self._send_json(getter(hours))
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return
        if parsed.path in ("/zones", "/zones.html"):
            page = ZONES_HTML.replace("__VERSION__", __version__)
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/zones":
            getter = getattr(self.server, "get_zones", None)
            if getter is None:
                self._send_json({"ok": False, "error": "not available"})
                return
            try:
                minutes = int((parse_qs(parsed.query).get("min") or ["15"])[0])
            except ValueError:
                minutes = 15
            try:
                self._send_json(getter(minutes))
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return
        if parsed.path in ("/summary", "/summary.html"):
            page = SUMMARY_HTML.replace("__VERSION__", __version__)
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/summary":
            getter = getattr(self.server, "get_summary", None)
            if getter is None:
                self._send_json({"ok": False, "error": "not available"})
                return
            try:
                self._send_json(getter())
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return
        if parsed.path == "/api/runhist":  # team: a joining POUNCer syncs
            getter = getattr(self.server, "get_runhist", None)
            self._send_json(getter() if getter else {})
            return
        if parsed.path == "/api/clear":
            undo = (parse_qs(parsed.query).get("undo") or ["0"])[0] in ("1", "true")
            if self.server.on_clear is None:
                self._send_json({"ok": False, "error": "not supported"}, 404)
                return
            try:
                self.server.on_clear(undo)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
                return
            self._send_json({"ok": True, "undo": undo})
            return
        if parsed.path == "/api/reconnect":
            if self.server.on_reconnect is None:
                self._send_json({"ok": False, "error": "not supported"}, 404)
                return
            try:
                self.server.on_reconnect()
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
                return
            self._send_json({"ok": True})
            return
        self._send_json({"ok": False, "error": "not found"}, 404)


class WebServer:
    """Serve the phone view in a background thread."""

    def __init__(
        self,
        host: str,
        port: int,
        get_spots: SpotsProvider,
        on_qsy: QSYHandler,
        on_key: KeyHandler,
        on_reconnect: ReconnectHandler | None = None,
        on_band: BandHandler | None = None,
        on_hide: HideHandler | None = None,
        on_tune: TuneHandler | None = None,
        on_filter: FilterHandler | None = None,
        on_clear: ClearHandler | None = None,
    ) -> None:
        self._httpd = _Server(
            (host, port), get_spots, on_qsy, on_key, on_reconnect, on_band, on_hide,
            on_tune, on_filter, on_clear,
        )
        self._thread: threading.Thread | None = None

    def set_settings_provider(self, get_items, save, restart=None, remote: bool = False) -> None:
        """Settings page: list, save one (key, value), restart POUNCer.
        ``remote`` = also from other devices (else only from this PC)."""
        self._httpd.get_settings = get_items
        self._httpd.save_setting = save
        self._httpd.restart_app = restart
        self._httpd.remote_settings = bool(remote)

    def set_min_snr_handler(self, fn) -> None:
        """Phone SNR button: fn(db) sets the minimum SNR (0 = all)."""
        self._httpd.on_min_snr = fn

    def set_cw_handler(self, fn) -> None:
        """Phone CW keyboard: fn(op, text, n, radio=...)."""
        self._httpd.on_cw = fn

    def load(self) -> tuple[int, int]:
        """(requests since the last call, phones / browsers seen in 2 min)."""
        srv = self._httpd
        n = getattr(srv, "req_count", 0)
        srv.req_count = 0
        now = time.time()
        clients = getattr(srv, "clients", {}) or {}
        for ip in [ip for ip, t in list(clients.items()) if now - t > 120]:
            clients.pop(ip, None)
        return n, len(clients)

    def set_timeline_provider(self, getter) -> None:
        """Spots per band and 10 minutes, for /api/timeline."""
        self._httpd.get_timeline = getter

    def set_zones_provider(self, getter) -> None:
        """Who hears me / what I hear per band and CQ zone, for /api/zones."""
        self._httpd.get_zones = getter

    def set_summary_provider(self, getter) -> None:
        """Contest summary for /api/summary (a dict)."""
        self._httpd.get_summary = getter

    def set_runhist_provider(self, getter) -> None:
        """Team: serve our run history at /api/runhist."""
        self._httpd.get_runhist = getter

    @property
    def port(self) -> int:
        return self._httpd.server_address[1]

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="pouncer-web", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        try:
            self._httpd.shutdown()
        except Exception:
            pass
        self._httpd.server_close()
