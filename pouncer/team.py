"""POUNCer team: several POUNCers on one network work together.

Each POUNCer announces itself on the LAN (UDP broadcast, port 12079) every
2 s. The one running longest - and with a working RBN connection - is the
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


def elect(nodes: dict[str, dict], now: float) -> str | None:
    """Master id: earliest started among the alive, healthy nodes (id breaks
    ties); if none is healthy, among all alive ones."""
    alive = {i: n for i, n in nodes.items() if now - n["seen"] <= PEER_TIMEOUT_S}
    pool = {i: n for i, n in alive.items() if n.get("healthy", True)} or alive
    if not pool:
        return None
    return min(pool, key=lambda i: (pool[i]["started"], i))


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
    ) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.started = time.time()
        self.web_port = web_port
        self.port = port
        self.version = version
        self._on_spot = on_spot
        self._on_runhist = on_runhist
        self._get_runhist = get_runhist
        self._rbn_ok = rbn_ok or (lambda: True)
        self._send_to = send_to or [("255.255.255.255", port)]
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
        try:
            self._sock.close()
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
    def _send(self, msg: dict) -> None:
        msg["id"] = self.id
        data = json.dumps(msg, separators=(",", ":")).encode()
        if len(data) > MAX_DATAGRAM:
            return
        for addr in self._send_to:
            try:
                self._sock.sendto(data, addr)
            except OSError as exc:
                log.debug("team send to %s: %s", addr, exc)

    def _announce_loop(self) -> None:
        while not self._stop.is_set():
            now = time.time()
            self._last_healthy = self._healthy(now)
            self._send({"t": "hi", "started": self.started, "web": self.web_port,
                        "healthy": self._last_healthy, "ver": self.version})
            self._maybe_sync()
            self._stop.wait(ANNOUNCE_S)

    def send_spot(self, spot: dict) -> None:
        """Master: pass one RBN spot on to the slaves."""
        if self.alive_peers():
            self._send({"t": "spot", "s": spot})

    def send_runhist(self, key: str, value) -> None:
        """Tell everyone what we just learned about a call."""
        if self.alive_peers():
            self._send({"t": "rh", "d": {key: value}})

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
            try:
                msg = json.loads(data.decode())
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(msg, dict) or msg.get("id") in (None, self.id):
                continue
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
                }
            if new:
                log.info("team: POUNCer %s at %s joined", pid, ip)
        elif kind == "spot" and self._on_spot and not self.is_master():
            if pid == self.master_id() and isinstance(msg.get("s"), dict):
                self._on_spot(msg["s"])
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
