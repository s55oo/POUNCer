# POUNCer — one-page overview

*S55OO · October 2026 · version 0.7.126 · read this first*

## What POUNCer is

POUNCer is a search-and-pounce helper for contesters who log in **N1MM Logger+**. It follows the band your radio is on and lists the stations the Reverse Beacon Network (RBN) hears calling CQ there, marking the ones you still need. One click on a station tunes the radio and puts the call in N1MM. It runs on Windows (one .exe, no install), macOS, and on any phone or tablet as a web page.

## How it works

- **From N1MM** (UDP 12060): the radio frequency, mode and which Entry Window is active - so POUNCer always shows the band you are on, for radio A and B (SO2R / SO2V).
- **From the RBN** (telnet, with an HTTP backup): every skimmer spot. POUNCer keeps the ones from your chosen skimmers (one, several, or whole CQ zones), or from others on your continent when they go quiet. In an SSB contest RBN isn't connected at all.
- **SSB spots from N1MM** (its Telnet window's spots on 12060), with POUNCer's own DX-cluster connection only as a backup. On 40 m 7074 - 7077 (FT8 inside the SSB segment) PSK Reporter is asked about that one call before a spot is taken as FT8.
- **Spots of your own call**: N1MM never passes them on, so a small node-filtered connection (`<call>-77`, only spots of your call) catches them for the SPOTTED pop-up.
- **What it adds**: worked / new multiplier from your N1MM log, a rare CQ mark (⚡) from past contest logs, Super Check Partial (?), how long a station has been calling.
- **Back to N1MM**: a click tunes the radio (UDP 13064) and types the call; F-keys, ENTER, SPOTit (Alt+P) and the CW speed are sent as keys.
- **Phone page**: POUNCer serves the same list on port 8080, so a phone beside the radio works it too.

## Built on S53ZO's work

Several of POUNCer's strongest parts come from **S53ZO**:

- **Contest-log archive** ([Hamradio-Contest-logs-Archives](https://github.com/s53zo/Hamradio-Contest-logs-Archives)): the past logs behind the run % and hours on every spot, and so the **⚡ rare CQ** mark and the alert.
- **RBN over HTTP** (his Azure server, no login): the backup feed that keeps spots coming when RBN's telnet is down or silent.
- **macOS builds**: every release also comes for Mac thanks to him.
- **Radio B tuning**: the default way POUNCer QSYs the second radio follows his SP_Helper.

## Main features

| Area | What you get |
| --- | --- |
| Window | Spot list table in small fonts (new mults red, worked grey, ⚡ rare CQ, ★ your own skimmer) or tiles, sort by time / frequency / SNR, min SNR, band chips to look at another band, the tapped station holds its place 60 s; **rate line** (rate, QSOs, mults per band); stream lights (green / yellow on hold / red). **Compact window** beside N1MM (Ctrl+M). Looks Dark / Light / Classic. |
| Phone | Big tiles you tap to QSY and call; F1-F7, ENTER, SPOTit and half-size keys; WPM - / +, ±25 Hz, NEXT; stacked messages; DX / MLT / ⚡ first. |
| Alerts | A rare CQ that is also a new mult: pop-up (click = QSY and call) and sound, each switched on its own - in the window and on the phone. **SPOTTED**: someone spotted your call on a cluster (3 s, on the PC on that band). **Skimmer offline**: your skimmer stopped sending. |
| Band pictures | Band open chips (DX share), **CQ zones** (who hears you / what you hear), **band opening timeline** (48 h), contest summary - as windows and as phone pages. |
| Station | SO2R / SO2V, a team of POUNCers sharing one RBN feed (none when all are on SSB), pre-contest READY check, **station test** (QSY + callsign per radio), CW speed popup, autostart of N1MM first and up to 8 other programs, auto-update from GitHub. |

## Getting started

1. Download **POUNCer.exe** and **config.ini** from the [latest GitHub release](https://github.com/s55oo/POUNCer/releases/latest) into one folder, e.g. next to N1MM.
2. In N1MM: Config > Config Ports > **Broadcast Data**, tick **Radio**, **App Info** and **Spots**, all to 127.0.0.1:12060 ([details](../README.md#n1mm-logger-setup)). When Windows asks, allow POUNCer through the firewall (the phone needs TCP 8080).
3. Start POUNCer. Set your skimmer, keys and autostart in **Settings** (no need to edit config.ini by hand).
4. On the phone, open the address shown at the bottom of the window (same Wi-Fi or ZeroTier).

**Updating**: when the Update light shows, click it, then **Restart**. Your config.ini is kept; the phone page reloads by itself.

## When something goes wrong

- **Help > Save diagnostics** makes one zip (log, settings without passwords, performance log) to send for checking.
- **View > Station test** checks QSY and callsign entry per radio; **View > Traffic console** shows the raw data of every source.
- `cache\pouncer.log` says what POUNCer did; `cache\perf.csv` holds CPU, memory and disk for POUNCer, N1MM and Windows, one row a minute.
- The phone acts oddly in one browser only: **? > Reset this page**.

**Read more**: the [README](../README.md) on GitHub (every feature), [SYMBOLS.md](SYMBOLS.md) (every mark and colour), and the **?** help page on the phone.
