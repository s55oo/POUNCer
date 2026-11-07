"""Windows Firewall rules for POUNCer (File > Allow POUNCer in Windows Firewall...).

Inbound allow rules for the phone page (TCP) and the team (UDP), private and
domain networks only, added with netsh in one elevated cmd (Windows asks
for administrator rights - UAC). Same approach as N1MMPingPong's.
"""

from __future__ import annotations

import subprocess
import sys

CREATE_NO_WINDOW = 0x08000000


def firewall_rule_exists(name: str) -> bool | None:
    """Is there a Windows Firewall rule with this name? None = can't tell
    (not Windows, netsh failed)."""
    if sys.platform != "win32":
        return None
    try:
        done = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}"],
                              capture_output=True, timeout=15, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.returncode == 0


def rules_command(rules: list[tuple[str, str, int]]) -> str:
    """One cmd line: for each (name, protocol, port) delete an older rule of
    that name, then add it (inbound allow, private + domain)."""
    parts = []
    for name, proto, port in rules:
        safe = name.replace('"', "")
        parts.append(f'netsh advfirewall firewall delete rule name="{safe}" >nul 2>&1')
        parts.append(f'netsh advfirewall firewall add rule name="{safe}" dir=in action=allow '
                     f'protocol={proto.upper()} localport={int(port)} profile=private,domain')
    return " & ".join(parts)


def add_firewall_rules(rules: list[tuple[str, str, int]]) -> tuple[bool, str]:
    """Run the rules elevated (UAC prompt). (True, "") when Windows started it."""
    if sys.platform != "win32":
        return False, "not Windows"
    try:
        import ctypes
        result = ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe",
                                                     "/c " + rules_command(rules), None, 0)
    except Exception as exc:  # noqa: BLE001
        return False, repr(exc)
    if result > 32:
        return True, ""
    return False, "administrator rights refused" if result == 5 else f"error {result}"
