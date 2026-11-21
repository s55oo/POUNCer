"""POUNCer's N1MM port (UDP in): 12060 - 12069 in Settings > N1MM & radios,
applied at once - the listener moves to the new port (v0.7.243)."""
import os
import queue
import socket
import tempfile
import types
import unittest

from pouncer import settings
from pouncer.config import load_config
from pouncer.n1mm import RadioListener, RadioState

RADIO = (b'<?xml version="1.0"?><RadioInfo><RadioNr>1</RadioNr><Freq>1402500</Freq>'
         b'<TXFreq>1402500</TXFreq><Mode>CW</Mode><IsRunning>False</IsRunning>'
         b'<Active>True</Active></RadioInfo>')


def free_port() -> int:
    for port in range(12060, 12070):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
        finally:
            s.close()
    raise unittest.SkipTest("12060 - 12069 all in use here")


class SettingTests(unittest.TestCase):
    def test_choice_12060_to_12069_saved_as_int(self):
        item = next(i for i in settings.SETTINGS if i["key"] == "n1mm.listen_port")
        self.assertEqual(item["choices"], [str(p) for p in range(12060, 12070)])
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.ini")
            open(p, "w").close()
            cfg = load_config(p)
            self.assertTrue(settings.save(cfg, p, "n1mm.listen_port", "12063")["ok"])
            self.assertEqual(cfg.n1mm_listen_port, 12063)
            self.assertEqual(load_config(p).n1mm_listen_port, 12063)
            self.assertFalse(settings.save(cfg, p, "n1mm.listen_port", "12070")["ok"])
            self.assertFalse(settings.save(cfg, p, "n1mm.listen_port", "13064")["ok"])
        self.assertEqual(settings.place("n1mm.listen_port")[0], "N1MM & radios")


class RestartTests(unittest.TestCase):
    def fake(self, port):
        from pouncer.app import PouncerApp
        f = types.SimpleNamespace(
            cfg=types.SimpleNamespace(n1mm_listen_ip="127.0.0.1", n1mm_listen_port=port),
            state=RadioState(), _on_n1mm_spot=lambda pkt: None, status_queue=queue.Queue(),
            _listener=None)
        f._start_listener = lambda: PouncerApp._start_listener(f)
        f._restart = lambda: PouncerApp._restart_listener(f)
        return f

    def send(self, port):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.sendto(RADIO, ("127.0.0.1", port))
        finally:
            s.close()

    def test_moves_to_the_new_port_at_once(self):
        first = free_port()
        f = self.fake(first)
        f._start_listener()
        try:
            f._listener.bound.wait(2)
            self.assertEqual(f._listener.bind_error, "")
            old = f._listener
            f.cfg.n1mm_listen_port = second = next(
                p for p in range(12060, 12070) if p != first and _free(p))
            f._restart()
            self.assertFalse(old.is_alive())                  # the old port is let go
            self.assertEqual(f._listener.port, second)
            self.assertIn(f"UDP {second}", f.status_queue.get_nowait())
            self.send(second)
            import time
            for _ in range(40):
                if (f.state.snapshot(1) or {}).get("freq_khz"):
                    break
                time.sleep(0.05)
            self.assertAlmostEqual(f.state.snapshot(1)["freq_khz"], 14025.0)   # heard on the new port
        finally:
            f._listener.stop()

    def test_port_in_use_is_reported(self):
        port = free_port()
        blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            blocker.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        blocker.bind(("127.0.0.1", port))
        try:
            f = self.fake(port)
            f._restart()
            if not f._listener.bind_error:
                self.skipTest("this OS lets a second program share the port")
            self.assertIn("could not be opened", f.status_queue.get_nowait())
        finally:
            blocker.close()
            if f._listener is not None:
                f._listener.stop()


def _free(port) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


if __name__ == "__main__":
    unittest.main()
