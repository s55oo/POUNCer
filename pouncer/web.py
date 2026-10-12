"""A tiny local web server that mirrors the touch view for a phone.

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
  /* footer: CW speed -/+, VFO -/+ step, RX filter NAR/WIDE (no TX) */
  .footbar { position: fixed; left: 0; right: 0; bottom: 0; z-index: 3;
    /* thirds: CW speed left, VFO step in the middle, RX filter right */
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
  /* RX filter: the width POUNCer last set is lit amber */
  .footbar .filt { background: #37474f; color: #cfd8dc; }
  .footbar .filt.on { background: #ff8f00; color: #111; }
  .footbar button:active, .footbar button.sel { background: #c62828; color: #fff; }
  @media (max-width: 619px) { .footbar button { font-size: 14px; } }
  body { padding-bottom: var(--ftr, 0px); }
  /* pop-up message: at the top, just under the sticky header (far from the
     footer buttons); faded out when hidden; 5 s, tap to close */
  #toast { position: fixed; left: 50%;
    top: calc(var(--hdr, 0px) + 12px);
    transform: translateX(-50%) translateY(-12px); opacity: 0; visibility: hidden;
    pointer-events: none; max-width: calc(100vw - 32px);
    background: #2e7d32; color: #fff; padding: 12px 18px; border-radius: 12px;
    font-size: 16px; z-index: 10; border: 1px solid #81c784;
    box-shadow: 0 6px 24px rgba(0, 0, 0, .7);
    transition: transform .15s ease, opacity .15s ease, visibility 0s linear .15s; }
  .call .rare { font-size: .8em; margin-left: 4px; }
  #toast[hidden] { display: none !important; }
  #toast.show { transform: translateX(-50%) translateY(0); opacity: 1;
    visibility: visible; pointer-events: auto;
    transition: transform .15s ease, opacity .15s ease, visibility 0s; }
  #toast button { margin-left: 12px; background: #fff; color: #1b5e20; border: 0;
    border-radius: 8px; padding: 6px 12px; font: inherit; font-weight: 700; }
  /* long-press on a tile: no iOS text selection / callout menu */
  button.tile, .footbar .filt { -webkit-touch-callout: none; -webkit-user-select: none; user-select: none; }
  button.tile.holding { filter: brightness(.7); transition: filter 2s linear; }
  #rmsheet .rm { background: #c62828; }
  .top { display: flex; align-items: center; gap: 10px; }
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
  .bandgrid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
  .bandgrid button { background: #263238; color: #fff; border: 0; border-radius: 10px;
    font: inherit; font-size: 22px; font-weight: 800; padding: 16px 0; cursor: pointer; }
  .bandgrid button.cur { background: #2e7d32; }
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
  /* DX-on-top toggle: yellow like the DX callsigns when on */
  .sorts button.dxfirst { color: #ffeb3b; font-weight: 700; }
  .sorts button.dxfirst.on { background: #f9a825; color: #1a1a1a; }
  /* MLT toggle and multiplier marks: magenta */
  .sorts button.multfirst { color: #f48fb1; font-weight: 700; }
  .sorts button.multfirst.on { background: #d81b60; color: #fff; }
  .sorts button.rarefirst { color: #ffd54f; font-weight: 700; }
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
    <button data-sort="time">Time</button>
    <button data-sort="freq">Freq</button>
    <button data-sort="snr">SNR</button>
    <button class="dxfirst" id="dxfirst" title="DX (outside your continent) on top">DX</button>
    <button class="multfirst" id="multfirst" title="New multipliers on top: double, then single">MLT</button>
    <button class="rarefirst" id="rarefirst" title="Rarely-running stations on top (lowest run % from past logs first)">\u26a1</button>
    <button class="reconnect" id="reconnect" title="Re-read which log N1MM has open">&#x21bb;<span class="lbl"> Log</span></button>
  </div>
  </div>
  <div class="meta" id="meta">connecting...</div>
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
    <button class="filt" data-filter="narrow" title="Narrow RX filter (N1MM Alt+'). Hold 1 s: toggle the radio only, to resync">NAR</button>
    <button class="filt" data-filter="wide" title="Wide RX filter (N1MM Alt+'). Hold 1 s: toggle the radio only, to resync">WIDE</button>
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
  <div class="sheet-title">Change band <span>(QSY the radio)</span></div>
  <div class="bandgrid" id="bandgrid"></div>
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
  let rareFirst = false;
  try { rareFirst = localStorage.getItem('pouncer.rarefirst') === '1'; } catch (e) {}

  function esc(s) {
    return String(s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
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
  function sorted(spots) {
    const a = spots.slice();
    if (sortKey === 'freq') a.sort((x, y) => x.freq - y.freq);
    else if (sortKey === 'time') a.sort((x, y) => timeVal(y) - timeVal(x));
    else a.sort((x, y) => snrVal(y) - snrVal(x));
    // \u26a1 button: lowest run % first (rarely runs = won't stay on that
    // frequency), unknown run % last. DX / MLT still group on top of it.
    if (rareFirst) a.sort((x, y) => (x.run == null ? 999 : x.run) - (y.run == null ? 999 : y.run));
    // DX button: DX spots first, each group keeping the chosen sort order
    // (Array.sort is stable, so this only moves DX ahead of EU).
    if (dxFirst) a.sort((x, y) => (y.dx ? 1 : 0) - (x.dx ? 1 : 0));
    // MLT button: double mults, then single, then the rest (stable, so the
    // chosen order - and DX-first - is kept inside each group).
    if (multFirst) a.sort((x, y) => (y.mult || 0) - (x.mult || 0));
    return a;
  }
  function markSort() {
    document.querySelectorAll('.sorts button[data-sort]').forEach(b =>
      b.classList.toggle('active', b.dataset.sort === sortKey));
    document.getElementById('dxfirst').classList.toggle('on', dxFirst);
    document.getElementById('multfirst').classList.toggle('on', multFirst);
    document.getElementById('rarefirst').classList.toggle('on', rareFirst);
  }
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
      const r = await fetch('/api/key?' + new URLSearchParams({key: key}));
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
    if ('filter' in data && data.filter !== filterWidth) showFilter(data.filter);
    if (data.filter_mode) filterMode = data.filter_mode;
    syncHeaderHeight();
    lastData = data;
    // Radio frequency in kHz (falls back to the band, then "ALL band").
    const where = (typeof data.freq === 'number' && data.freq > 0)
      ? data.freq.toFixed(1) + '<span class="khz"> kHz</span>'
      : esc(data.band || 'ALL band');
    bandEl.innerHTML = '<span class="app">POUNCer&nbsp; </span>' + where +
      '<span class="modes"> ' + esc((data.modes || []).join('/')) + '</span>';
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
    metaEl.innerHTML = meta;
    const cols = columns();
    const spots = sorted(data.spots);
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
        (spotKey(s) === selKey ? ' sel' : '');
      // DX (outside our continent): callsign in yellow; EU stays white
      if (s.country) b.title = s.country + (s.cont ? ' (' + s.cont + ')' : '');
      // Run history (past logs): R = % of QSOs made running; ⚡ = rarely
      // runs, so a CQ from it won't last - work it now.
      const rare = s.run != null && s.run < (lastData && lastData.rare_run || 30);
      if (s.run != null) b.title = (b.title ? b.title + ' - ' : '') +
        'runs ' + s.run + '% of QSOs in past logs' + (rare ? ' (rarely runs: work it now)' : '');
      const len = (s.call || '').length;
      const size = len > 10 ? ' xlong' : (len > 8 ? ' long' : '');
      b.innerHTML = '<span class="call' + size + (s.dx ? ' dx' : '') + '">' +
        esc(s.call) + (rare ? '<span class="rare">\u26a1</span>' : '') + '</span>' +
        '<span class="detail"><span>' + s.freq.toFixed(1) + ' ' + esc(s.mode) +
        (s.run != null ? ' R' + s.run + '%' : '') +
        '</span> <span>' + (s.snr == null ? '--' : s.snr + ' dB') +
        (s.time ? ' ' + esc(s.time) : '') + '</span></span>';
      attachPress(b, s);
      grid.appendChild(b);
    });
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
  function attachPress(b, s) {
    let timer = null, held = false;
    const cancel = () => {
      clearTimeout(timer); timer = null; pressing = false;
      b.classList.remove('holding');
    };
    b.addEventListener('pointerdown', () => {
      held = false;
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
      const r = await fetch('/api/qsy?' + new URLSearchParams(
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
  function openBands() {
    const bands = lastData.contest_bands || ['160m','80m','40m','20m','15m','10m'];
    bandGrid.textContent = '';
    bands.forEach(b => {
      const btn = document.createElement('button');
      btn.textContent = b;
      if (b === lastData.band) btn.classList.add('cur');
      btn.onclick = () => chooseBand(b);
      bandGrid.appendChild(btn);
    });
    bandSheet.classList.add('show');
  }
  function closeBands() { bandSheet.classList.remove('show'); }
  async function chooseBand(b) {
    closeBands();
    try {
      const r = await fetch('/api/band?' + new URLSearchParams({band: b}));
      const j = await r.json();
      toast(j.ok ? ('QSY ' + b) : ('Failed: ' + (j.error || 'error')));
    } catch (e) {
      toast('Failed: ' + e);
    }
    setTimeout(refresh, 600);
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
        const r = await fetch('/api/tune?' + new URLSearchParams({hz: hz}));
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
        const r = await fetch('/api/key?' + new URLSearchParams({key: b.dataset.key}));
        const j = await r.json();
        toast(j.ok ? (b.dataset.key === 'PgUp' ? 'CW faster' : 'CW slower')
                   : ('Failed: ' + (j.error || 'error')));
      } catch (e) {
        toast('Failed: ' + e);
      }
    };
  });
  // RX filter: N1MM only has a wide/narrow toggle (Alt+'), so POUNCer
  // remembers the width it set and lights that button. Tap = switch to that
  // width (nothing is sent if it is already lit). Hold 1 s = toggle the
  // radio but keep the label - use it when the radio and the label differ.
  let filterWidth = null;
  let filterMode = 'toggle';  // 'cat': NAR/WIDE set the filter, always sent
  function showFilter(w) {
    filterWidth = w || null;
    document.querySelectorAll('.footbar [data-filter]').forEach(b => {
      b.classList.toggle('on', b.dataset.filter === filterWidth);
    });
  }
  async function sendFilter(b, fix) {
    flash(b);
    try {
      const q = {w: b.dataset.filter};
      if (fix) q.fix = 1;
      const r = await fetch('/api/filter?' + new URLSearchParams(q));
      const j = await r.json();
      if (!j.ok) { toast('Failed: ' + (j.error || 'error')); return; }
      if (fix) { toast('Filter toggled, label kept'); return; }
      showFilter(j.w);
      toast(j.w === 'narrow' ? 'Narrow filter' : 'Wide filter');
    } catch (e) {
      toast('Failed: ' + e);
    }
  }
  document.querySelectorAll('.footbar [data-filter]').forEach(b => {
    let timer = null, held = false;
    const start = () => {
      held = false;
      clearTimeout(timer);
      timer = setTimeout(() => { held = true; sendFilter(b, filterMode !== 'cat'); }, 1000);
    };
    const stop = () => { clearTimeout(timer); timer = null; };
    b.addEventListener('pointerdown', start);
    b.addEventListener('pointerup', stop);
    b.addEventListener('pointerleave', stop);
    b.addEventListener('pointercancel', stop);
    b.addEventListener('contextmenu', e => e.preventDefault());
    b.onclick = () => {
      if (held) { held = false; return; }
      if (filterMode !== 'cat' && b.dataset.filter === filterWidth) {
        flash(b);
        toast(b.dataset.filter === 'narrow' ? 'Already narrow' : 'Already wide');
        return;
      }
      sendFilter(b, false);
    };
  });
  function syncHeaderHeight() {
    const f = document.getElementById('footbar');
    document.documentElement.style.setProperty('--ftr', (f ? f.offsetHeight : 0) + 'px');
    const h = document.querySelector('header').offsetHeight;
    document.documentElement.style.setProperty('--hdr', h + 'px');
  }
  window.addEventListener('resize', syncHeaderHeight);
  async function refresh() {
    try {
      const r = await fetch('/api/spots', {cache: 'no-store'});
      const data = await r.json();
      // Holding a tile / remove box open: keep the list still (the redraw
      // would cancel the hold); show the fresh data afterwards.
      if (pressing || document.getElementById('rmsheet').classList.contains('show')) {
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
      sortKey = b.dataset.sort;
      localStorage.setItem('pouncer.sort', sortKey);
      render(lastData);
    };
  });
  markSort();
  refresh();
  setInterval(refresh, 1000);
  window.addEventListener('resize', refresh);
</script>
</body>
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
    ):
        super().__init__(address, _Handler)
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

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            page = INDEX_HTML.replace("__VERSION__", __version__)
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/spots":
            self._send_json(self.server.get_spots())
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
                self.server.on_qsy(call, freq, mode)
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
                self.server.on_key(norm)
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
                self.server.on_tune(hz)
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
                self.server.on_band(band)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
                return
            self._send_json({"ok": True, "band": band})
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
    ) -> None:
        self._httpd = _Server(
            (host, port), get_spots, on_qsy, on_key, on_reconnect, on_band, on_hide,
            on_tune, on_filter,
        )
        self._thread: threading.Thread | None = None

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
