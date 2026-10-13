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
  #bandsheet, #rmsheet { display: none; position: fixed; inset: 0; z-index: 20;
    background: rgba(0,0,0,.55); align-items: flex-start; justify-content: center; }
  #bandsheet.show, #rmsheet.show { display: flex; }
  #bandsheet .sheet, #rmsheet .sheet { margin-top: 64px; width: min(420px, calc(100% - 24px));
    background: #1a2127; border-radius: 14px; padding: 14px; }
  .sheet-title { font-weight: 700; margin-bottom: 10px; }
  .sheet-title span { font-weight: 400; opacity: .6; font-size: 13px; }
  a.helplink { display: block; text-align: center; text-decoration: none; color: #cfd8dc;
    margin-bottom: 8px; }
  .bandgrid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
  .bandgrid button { background: #263238; color: #fff; border: 0; border-radius: 10px;
    font: inherit; font-size: 22px; font-weight: 800; padding: 16px 0; cursor: pointer; }
  .bandgrid button.cur { background: #2e7d32; }
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
    <button data-sort="snr">SNR</button>
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
  <div class="fgroup">
    <button class="wpm" data-key="PgDn" title="CW speed down (N1MM PgDn)">WPM &minus;</button>
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
  let lastData = {band: '', modes: [], spots: []};
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
  function toast(msg, undo) {
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
    toastTimer = setTimeout(hideToast, 5000);
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
  // \u26a1 sort: rare first, then lowest run %, then fewest hours; unknown last.
  function rareKey(s) {
    if (s.run == null) return 1e6;
    return (isRare(s) ? 0 : 1e4) + s.run * 10 + Math.min(s.hours == null ? 99 : s.hours, 99) / 10;
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
      if (isEnter) b.classList.add('log');
      b.onclick = () => sendKey(f.key, b);
      fkeysEl.appendChild(b);
    });
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
    if (data.tune_step && data.tune_step !== tuneStep) { tuneStep = data.tune_step; labelTune(); }
    syncHeaderHeight();
    lastData = data;
    // SO2R switch: shown when N1MM reports two radios (or B was chosen)
    const radioSw = document.getElementById('radiosw');
    const cur = data.radio === 2 ? 2 : 1;
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
      (logBad ? ' spots' : ' needed spots');
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
    metaEl.innerHTML = meta;
    const vt = document.getElementById('viewtag');
    if (vt) vt.onclick = () => { delete views[String(cur)]; saveViews(); selKey = ''; refresh(); };
    const cols = columns();
    const spots = arrange(data);
    shown = spots;
    const rows = Math.max(1, Math.ceil(spots.length / cols));
    grid.style.gridTemplateColumns = 'repeat(' + cols + ', minmax(0, 1fr))';
    grid.style.gridTemplateRows = 'repeat(' + rows + ', minmax(var(--row), auto))';
    grid.textContent = '';
    // "Tuned to": the spot nearest the radio frequency, within 0.5 kHz.
    let tuned = null;
    if (typeof data.freq === 'number' && data.freq > 0) {
      let best = 0.5 + 1e-9;
      spots.forEach(s => {
        const d = Math.abs(s.freq - data.freq);
        if (d <= best) { best = d; tuned = s; }
      });
    }
    spots.forEach(s => {
      const b = document.createElement('button');
      b.className = 'tile' + (snrVal(s) >= 20 ? ' strong' : '') +
        (s.mult >= 2 ? ' m2' : (s.mult === 1 ? ' m1' : '')) +
        (s === tuned ? ' onfreq' : '') +
        // not re-spotted for a while: probably gone - fade it
        (s.age != null && data.fade_s && s.age >= data.fade_s ? ' stale' : '') +
        (spotKey(s) === selKey ? ' sel' : '') +
        (calm && calmNew.has(calmKey(s)) ? ' fresh' : '');
      // DX (outside our continent): callsign in yellow; EU stays white
      if (s.country) b.title = s.country + (s.cont ? ' (' + s.cont + ')' : '');
      // Run history (past logs): R = % of QSOs made running; ⚡ = rarely
      // runs, so a CQ from it won't last - work it now.
      const rare = isRare(s);
      if (s.cq_min != null) b.title = (b.title ? b.title + ' - ' : '') +
        'calling on this frequency for ' + cqText(s.cq_min) + (s.first ? ' (since ' + s.first + ')' : '') + (s.time ? ', last spot ' + s.time : '');
      if (s.run != null) b.title = (b.title ? b.title + ' - ' : '') +
        'runs ' + s.run + '% of QSOs' + (s.hours != null ? ', on air ~' + s.hours + ' h' : '') +
        ' in its last log' + (s.rec ? ' (reconstructed from other logs: a minimum)' : '') +
        (rare ? ' - rare CQ: work it now' : '');
      const len = (s.call || '').length;
      const size = len > 10 ? ' xlong' : (len > 8 ? ' long' : '');
      b.innerHTML = '<span class="call' + size + (s.dx ? ' dx' : '') + '">' +
        esc(s.call) + (rare ? '<span class="rare">\u26a1</span>' : '') + '</span>' +
        '<span class="detail"><span><b class="fq">' + s.freq.toFixed(1) + '</b>' +
        // mode only when it isn't CW (in a CW contest it always is)
        (s.mode && s.mode.toUpperCase() !== 'CW' ? ' ' + esc(s.mode) : '') +
        (s.run != null ? ' R' + s.run + '%' + (s.rec ? '*' : '') : '') +
        (s.hours != null ? ' ~' + Math.round(s.hours) + 'h' : '') +
        '</span> <span>' +
        // first spotted on this frequency (UTC), SNR, time calling there
        (s.first ? esc(s.first) + ' ' : (s.time ? esc(s.time) + ' ' : '')) +
        (s.snr == null ? '--' : s.snr + 'dB') +
        (s.cq_min != null ? ' cq' + cqText(s.cq_min) : '') +
        '</span></span>';
      attachPress(b, s);
      grid.appendChild(b);
      // still too wide (long call + every mark)? slightly smaller text
      if (b.scrollWidth > b.clientWidth + 1) b.classList.add('tight');
    });
    checkAlerts(spots);
    if (!spots.length) {
      const d = document.createElement('div');
      d.className = 'empty';
      d.textContent = 'No needed spots on this band right now.';
      grid.appendChild(d);
    }
    markSort();
  }
  // Tap = QSY. Hold 2 s = offer to remove the call from the list.
  const HOLD_MS = 2000;
  let pressing = false;   // a tile is being held: don't redraw the list
  let releaseTimer = null;
  function attachPress(b, s) {
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
      timer = setTimeout(() => { held = true; cancel(); askRemove(s); }, HOLD_MS);
    });
    b.addEventListener('pointerup', cancel);
    b.addEventListener('pointerleave', cancel);
    b.addEventListener('pointercancel', cancel);
    b.addEventListener('contextmenu', e => e.preventDefault());
    b.onclick = () => { if (held) { held = false; return; } qsy(s, b); };
  }
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
  async function qsy(s, btn) {
    selKey = spotKey(s);
    document.querySelectorAll('button.tile.sel').forEach(t => t.classList.remove('sel'));
    btn.classList.add('sel');
    try {
      const r = await fetch('/api/qsy?' + rp(
        {call: s.call, freq: s.freq, mode: s.mode}));
      const j = await r.json();
      toast(j.ok ? ('QSY ' + s.call) : ('Failed: ' + (j.error || 'error')));
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

<h2>Spot tiles</h2>
<dl>
  <dt>Tap</dt>
  <dd>The radio goes to that spot and the call is typed into N1MM's Entry Window
    (no transmit). The tile you tapped keeps a <span class="yel">yellow ring</span>.</dd>
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
    public log archive), 81% of its QSOs were made calling CQ. For CQWW CW, ARRL DX CW
    and CQ WPX CW about 46 000 stations are known in advance (warm-up list loaded at
    start); others appear about a second after they are spotted.</dd>
  <dt>~23h</dt><dd>About how many hours it is usually on the air in this contest
    (average of its logs).</dd>
  <dt>*</dt>
  <dd>From a reconstructed log (the station never sent one; built from other stations'
    logs - a minimum).</dd>
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
  <dt><span class="k green">ENTER</span></dt>
  <dd>Enter in N1MM: logs the QSO (no transmit).</dd>
</dl>
<p>Keys turn red for a moment when pressed. Which keys appear is set in
<code>config.ini</code> (<code>[ui] fkeys</code>).</p>

<h2>Contest summary</h2>
<p><span class="k">&Sigma;</span> next to <span class="k">?</span> (also in the band menu, and the
<b>Summary</b> button in the main window) opens the contest summary of the log N1MM has
open: QSOs and how many came <b>from POUNCer</b> (you tapped that call on that band up to
15 minutes before logging it), multipliers and how many came from spots, rate on the
air, best 60 minutes, a per-band table, rate per hour and the list of multipliers you
worked from spots. POUNCer keeps the taps in <code>cache\\taps.jsonl</code> (14 days).</p>

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
    frequency + Enter into Entry Window B, then the call - like you would by hand).</dd>
</dl>

<h2>Bottom row</h2>
<dl>
  <dt><span class="k purple">WPM &minus;</span> <span class="k purple">WPM +</span></dt>
  <dd>CW speed down / up (N1MM's PgDn / PgUp).</dd>
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
                self._send_json(self.server.get_spots(**kw))
            else:
                self._send_json(self._call(self.server.get_spots, parsed))
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
