"""POUNCer team: several POUNCers on one network work together.

Each POUNCer announces itself (UDP, port 12079) every 2 s: a broadcast out
of **every** network adapter (LAN, ZeroTier, ...), plus directly to every
POUNCer it already knows and to any ``[team] peers`` IPs (for networks that
drop broadcasts). The one running longest - and with a working RBN connection - is the
**master**: it alone keeps the RBN connection and passes its (already
S53WW-filtered) spots to the others, the **slaves**. Run-history results
learned by any of them are sent to all, and a POUNCer that joins later
fetches what is already known from the master (HTTP ``/api/runhist``).
If the master stops (no announce for 6 s) or its RBN is down for over a
minute, the next one takes over.

Stdlib only; plain JSON datagrams; a team is everyone on the same network
and port.
"""

from __future__ import annotations

from . import traffic

import json
import logging
import socket
import threading
import time
import urllib.request
import uuid

log = logging.getLogger(__name__)

TEAM_PORT = 12079
ANNOUNCE_S = 2.0      # how often each POUNCer says "I'm here"
PEER_TIMEOUT_S = 6.0  # not heard for this long = gone
RBN_SICK_S = 60.0     # master whose RBN is down this long steps aside
MAX_DATAGRAM = 60000


def _ver(text: str) -> tuple[int, ...]:
    """'v0.7.11' / '0.7.11' -> (0, 7, 11); unparsable -> ()."""
    from .update import parse_version  # local: keep team import light
    return parse_version(text)


def local_ipv4s_all() -> list[str]:
    """Every IPv4 of this PC: all adapters on Windows (ipconfig), else what
    the hostname / default route give."""
    from .web import adapter_ipv4s, local_ipv4s  # local: avoid import cycle

    ips = [ip for _, ip in adapter_ipv4s()]
    return sorted(set(ips) | set(local_ipv4s()))


def elect(nodes: dict[str, dict], now: float) -> str | None:
    """Master id: earliest started among the alive, healthy nodes (id breaks
    ties); if none is healthy, among all alive ones."""
    alive = {i: n for i, n in nodes.items() if now - n["seen"] <= PEER_TIMEOUT_S}
    pool = {i: n for i, n in alive.items() if n.get("healthy", True)} or alive
    if not pool:
        return None
    return min(pool, key=lambda i: (pool[i]["started"], i))



# ---- team password (optional): signed messages --------------------------------
#
# With a password set on every PC, each team message ends in
# "\n#PCS1 <unix time> <HMAC-SHA256>" over the time and the message. Without a
# valid signature, or more than AUTH_WINDOW s old (a recorded message played
# back later), it is dropped. Without a password nothing is added - exactly as
# before. Same scheme as N1MMPingPong's network password.

AUTH_MARK = b"\n#PCS1 "
AUTH_WINDOW = 300.0      # s: the PC clocks must agree within 5 minutes


def derive_key(password: str) -> bytes:
    """The key from the password - only this is stored, never the password."""
    import hashlib
    if not password:
        return b""
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), b"POUNCer-team-12079", 20000)


def _mac(key: bytes, ts: bytes, payload: bytes) -> bytes:
    import hashlib
    import hmac
    return hmac.new(key, ts + b" " + payload, hashlib.sha256).hexdigest()[:32].encode("ascii")


def seal(payload: bytes, now: float | None = None, key: bytes = b"") -> bytes:
    """Add the signature (only with a password)."""
    if not key:
        return payload
    ts = str(int(now if now is not None else time.time())).encode("ascii")
    return payload + AUTH_MARK + ts + b" " + _mac(key, ts, payload)


def unseal(data: bytes, now: float | None = None, key: bytes = b"") -> tuple[bytes, str]:
    """(payload, status): "ok" valid signature, "none" unsigned, "bad" wrong
    password / changed, "old" outside AUTH_WINDOW, "foreign" signed but this
    PC has no password."""
    now = time.time() if now is None else now
    i = data.rfind(AUTH_MARK)
    if i < 0:
        return data, "none"
    payload = data[:i]
    parts = data[i + len(AUTH_MARK):].strip().split(b" ")
    if not key:
        return payload, "foreign"
    if len(parts) != 2:
        return payload, "bad"
    import hmac
    ts, mac = parts
    if not hmac.compare_digest(mac, _mac(key, ts, payload)):
        return payload, "bad"
    try:
        age = abs(now - int(ts))
    except ValueError:
        return payload, "old"
    return payload, ("ok" if age <= AUTH_WINDOW else "old")


def accepted(status: str, key: bytes) -> bool:
    """With a password only valid signed messages; without one only unsigned."""
    return status == "ok" if key else status == "none"



def _sources(text: str) -> str:
    """The master's sources ('rbn,cluster'), only known names."""
    from .config import parse_sources
    return parse_sources(text)

class Team:
    """Discovery, master election and data sharing between POUNCers."""

    def __init__(
        self,
        web_port: int,
        on_spot=None,         # slave: called with a spot dict from the master
        on_runhist=None,      # called with {key: value} learned by a peer
        get_runhist=None,     # -> dict of everything we know (for joiners)
        rbn_ok=None,          # -> bool: our RBN connection is up
        port: int = TEAM_PORT,
        send_to: list[tuple[str, int]] | None = None,
        version: str = "",
        key: bytes = b"",
        peers: list[str] | None = None,
        get_bands=None,       # -> band-open counts (master sends them to the slaves)
        needs_rbn=None,       # -> bool: this POUNCer needs RBN (a radio on CW, not an SSB contest)
        rbn_paused=None,      # -> bool: (master) RBN closed - nobody needs it
        on_phone=None,        # -> bool: a radio here is on USB/LSB (wants SSB spots)
        on_self=None,         # slave: called with {call, spotter, khz, comment} - my call spotted
        on_update=None,       # called when the master says: update now
        get_src=None,         # -> "rbn" / "telnet": (master) where the CW spots come from
        on_src=None,          # (master) a slave's button: called with "rbn" / "telnet"
    ) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.started = time.time()
        self.web_port = web_port
        self.port = port
        self.version = version
        self.key = key or b""         # team password key (b"" = none): signed messages
        self.auth_seen: dict[str, tuple[float, str]] = {}   # status -> (when, ip)
        self._on_spot = on_spot
        self._on_runhist = on_runhist
        self._get_runhist = get_runhist
        self._rbn_ok = rbn_ok or (lambda: True)
        self._get_bands = get_bands
        self._needs_rbn = needs_rbn
        self._rbn_paused = rbn_paused
        self._on_phone = on_phone
        self._on_self = on_self
        self._on_update = on_update
        self._get_src = get_src
        self._on_src = on_src
        self._fixed_targets = send_to  # tests: only these
        self._manual_peers = [p.strip() for p in (peers or []) if p.strip()]
        self._if_socks: dict[str, socket.socket] = {}  # local IP -> socket bound to it
        self._if_checked = 0.0
        self._lock = threading.Lock()
        self.peers: dict[str, dict] = {}   # id -> {ip, web, started, healthy, seen, ver}
        self._master_since: float | None = None
        self._rbn_down_since: float | None = None
        self._synced = False
        self._stop = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._sock.bind(("", port))
        self._sock.settimeout(0.5)

    # --- lifecycle -------------------------------------------------------
    def start(self) -> None:
        threading.Thread(target=self._listen, name="team-rx", daemon=True).start()
        threading.Thread(target=self._announce_loop, name="team-tx", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        for sk in [self._sock, *self._if_socks.values()]:
            try:
                sk.close()
            except OSError:
                pass

    # --- state -----------------------------------------------------------
    def _healthy(self, now: float) -> bool:
        """A master whose RBN has been down over RBN_SICK_S isn't healthy."""
        if not self.is_master():
            return True
        if self._rbn_ok():
            self._rbn_down_since = None
            return True
        if self._rbn_down_since is None:
            self._rbn_down_since = now
        return now - self._rbn_down_since < RBN_SICK_S

    def _nodes(self, now: float) -> dict[str, dict]:
        with self._lock:
            nodes = {i: dict(p) for i, p in self.peers.items()}
        nodes[self.id] = {"started": self.started, "seen": now,
                          "healthy": getattr(self, "_last_healthy", True)}
        return nodes

    def master_id(self) -> str | None:
        return elect(self._nodes(time.time()), time.time())

    def is_master(self) -> bool:
        return self.master_id() == self.id

    def alive_peers(self) -> list[dict]:
        now = time.time()
        with self._lock:
            return [dict(p, id=i) for i, p in self.peers.items()
                    if now - p["seen"] <= PEER_TIMEOUT_S]

    def version_mismatch(self) -> list[tuple[str, str]]:
        """(ip, version) of team POUNCers running another version than this one."""
        mine = _ver(self.version)
        if not mine:
            return []
        return sorted((p["ip"], p.get("ver") or "?") for p in self.alive_peers()
                      if _ver(p.get("ver") or "") != mine)

    def auth_problem(self, within: float = 60.0) -> str:
        """The team password in one sentence when something doesn't fit, else ''."""
        now = time.time()
        recent = {st: ip for st, (t, ip) in self.auth_seen.items() if now - t <= within}
        if self.key:
            for st, text in (("bad", "wrong team password"), ("old", "wrong time (check the PC "
                             "clock)"), ("none", "no team password")):
                if st in recent:
                    return f"{recent[st]}: {text} - ignored"
            return ""
        if "foreign" in recent:
            return ("the other POUNCers use a team password - set it: File > Team password\u2026 "
                    f"({recent['foreign']})")
        return ""

    def role(self) -> str:
        """'solo' (no other POUNCer), 'master' or 'slave'."""
        if not self.alive_peers():
            return "solo"
        return "master" if self.is_master() else "slave"

    def master_ip(self) -> str:
        mid = self.master_id()
        with self._lock:
            p = self.peers.get(mid or "")
        return p["ip"] if p else ""

    # --- sending ---------------------------------------------------------
    def _interface_sockets(self) -> dict[str, socket.socket]:
        """One broadcast socket bound to each local IPv4 (refreshed every
        60 s, e.g. ZeroTier coming up later): Windows sends a broadcast only
        out of the adapter the socket is bound to."""
        now = time.time()
        if now - self._if_checked < 60:
            return self._if_socks
        self._if_checked = now
        ips = set(local_ipv4s_all())
        for ip in list(self._if_socks):
            if ip not in ips:
                self._if_socks.pop(ip).close()
        for ip in ips:
            if ip in self._if_socks or ip.startswith(("127.", "169.254.")):
                continue
            try:
                sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sk.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sk.bind((ip, 0))
                self._if_socks[ip] = sk
            except OSError as exc:
                log.debug("team: no broadcast on %s: %s", ip, exc)
        return self._if_socks

    def _send(self, msg: dict, broadcast: bool = True) -> None:
        msg["id"] = self.id
        data = seal(json.dumps(msg, separators=(",", ":")).encode(), key=self.key)
        if traffic._sink is not None:
            traffic.emit("TEAM >", traffic.squeeze(data.decode(), 300))
        if len(data) > MAX_DATAGRAM:
            return

        def send(sock, addr) -> None:
            try:
                sock.sendto(data, addr)
            except OSError as exc:
                log.debug("team send to %s: %s", addr, exc)

        if self._fixed_targets is not None:
            for addr in self._fixed_targets:
                send(self._sock, addr)
            return
        # directly to every POUNCer we know (+ manual ones): works even where
        # broadcasts are dropped
        with self._lock:
            known = {p["ip"] for p in self.peers.values()}
        for ip in known | set(self._manual_peers):
            send(self._sock, (ip, self.port))
        if broadcast:
            send(self._sock, ("255.255.255.255", self.port))
            for sk in self._interface_sockets().values():
                send(sk, ("255.255.255.255", self.port))

    def _announce_loop(self) -> None:
        while not self._stop.is_set():
            now = time.time()
            self._last_healthy = self._healthy(now)
            hi = {"t": "hi", "started": self.started, "web": self.web_port,
                  "healthy": self._last_healthy, "ver": self.version}
            for key, fn in (("cw", self._needs_rbn), ("paused", self._rbn_paused),
                            ("ssb", getattr(self, "_on_phone", None))):
                if fn is not None:
                    try:
                        hi[key] = bool(fn())
                    except Exception:  # noqa: BLE001
                        pass
            if self._get_bands is not None and self.is_master():
                try:
                    hi["bands"] = self._get_bands()
                except Exception:  # noqa: BLE001
                    pass
            if self._get_src is not None and self.is_master():
                try:
                    hi["src"] = str(self._get_src())
                except Exception:  # noqa: BLE001
                    pass
            self._send(hi)
            self._maybe_sync()
            self._stop.wait(ANNOUNCE_S)

    def peers_need_rbn(self) -> bool:
        """Any other POUNCer of the team needs RBN (a radio on CW, ...)."""
        return any(p.get("cw", True) for p in self.alive_peers())

    def peers_on_phone(self) -> bool:
        """Any other POUNCer of the team has a radio on USB/LSB."""
        return any(p.get("ssb", False) for p in self.alive_peers())

    def master_paused(self) -> bool:
        """Slave: the master has closed RBN (no one in the team needs it)."""
        mid = self.master_id()
        with self._lock:
            p = self.peers.get(mid or "")
            return bool(p and p.get("paused"))

    def master_bands(self) -> dict | None:
        """Slave: the band-open counts the master sent last."""
        mid = self.master_id()
        with self._lock:
            p = self.peers.get(mid or "")
            return p.get("bands") if p else None

    def send_spot(self, spot: dict) -> None:
        """Master: pass one RBN spot on to the slaves."""
        if self.alive_peers():
            self._send({"t": "spot", "s": spot}, broadcast=False)

    def master_src(self) -> str | None:
        """Slave: where the master takes the CW spots from ('rbn' / 'telnet')."""
        mid = self.master_id()
        with self._lock:
            p = self.peers.get(mid or "")
            return p.get("src") if p else None

    def send_src(self, src: str) -> None:
        """Slave's button: ask the master to take the CW spots from 'rbn' /
        'telnet' (only the master acts on it)."""
        if src in ("rbn", "telnet", "http", "cluster") and self.alive_peers():
            self._send({"t": "src", "v": src}, broadcast=False)

    def send_update(self) -> None:
        """Master: every POUNCer of the team - update to the newest version."""
        if self.alive_peers():
            self._send({"t": "update"}, broadcast=False)

    def send_self(self, d: dict) -> None:
        """Master: my call was spotted - the slaves show the pop-up too."""
        if self.alive_peers():
            self._send({"t": "self", "d": d}, broadcast=False)

    def send_runhist(self, key: str, value) -> None:
        """Tell everyone what we just learned about a call."""
        if self.alive_peers():
            self._send({"t": "rh", "d": {key: value}}, broadcast=False)

    # --- receiving -------------------------------------------------------
    def _listen(self) -> None:
        while not self._stop.is_set():
            try:
                data, (ip, _) = self._sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    return
                continue
            payload, status = unseal(data, key=self.key)
            try:
                msg = json.loads(payload.decode())
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(msg, dict) or msg.get("id") in (None, self.id):
                continue
            self.auth_seen[status] = (time.time(), ip)
            if not accepted(status, self.key):
                continue
            if traffic._sink is not None:
                traffic.emit(f"TEAM {ip}", traffic.squeeze(data.decode("utf-8", "replace"), 300))
            self.handle(msg, ip)

    def handle(self, msg: dict, ip: str) -> None:
        kind = msg.get("t")
        pid = str(msg["id"])
        if kind == "hi":
            with self._lock:
                new = pid not in self.peers
                self.peers[pid] = {
                    "ip": ip, "web": int(msg.get("web") or 0),
                    "started": float(msg.get("started") or time.time()),
                    "healthy": bool(msg.get("healthy", True)),
                    "seen": time.time(), "ver": str(msg.get("ver") or ""),
                    "bands": msg.get("bands") if isinstance(msg.get("bands"), dict) else None,
                    # needs RBN? (older POUNCers don't say: yes, to be safe)
                    "cw": bool(msg.get("cw", True)),
                    "ssb": bool(msg.get("ssb", not msg.get("cw", True))), "paused": bool(msg.get("paused", False)),
                    "src": (_sources(msg.get("src")) if isinstance(msg.get("src"), str)
                            else None),
                }
            if new:
                log.info("team: POUNCer %s at %s joined", pid, ip)
        elif kind == "spot" and self._on_spot and not self.is_master():
            if pid == self.master_id() and isinstance(msg.get("s"), dict):
                self._on_spot(msg["s"])
        elif kind == "update" and self._on_update and pid == self.master_id():
            self._on_update()
        elif kind == "src" and self._on_src and self.is_master() \
                and msg.get("v") in ("rbn", "telnet", "http", "cluster"):
            self._on_src(msg["v"])
        elif kind == "self" and self._on_self and isinstance(msg.get("d"), dict) \
                and pid == self.master_id():
            self._on_self(msg["d"])
        elif kind == "rh" and self._on_runhist and isinstance(msg.get("d"), dict):
            self._on_runhist(msg["d"])

    def _maybe_sync(self) -> None:
        """Once, on joining: fetch what the master already knows."""
        if self._synced or self.is_master() or self._on_runhist is None:
            return
        mid = self.master_id()
        with self._lock:
            p = self.peers.get(mid or "")
        if not p or not p.get("web"):
            return
        url = f"http://{p['ip']}:{p['web']}/api/runhist"
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read().decode())
        except (OSError, ValueError) as exc:
            log.info("team: sync from %s failed: %s", url, exc)
            return
        if isinstance(data, dict):
            self._on_runhist(data)
            log.info("team: %d run-history entries from the master", len(data))
        self._synced = True
