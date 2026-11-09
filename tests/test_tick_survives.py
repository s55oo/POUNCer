"""The refresh tick must go on after an error in one step (v0.7.172):
before, one exception ended the loop and the window froze."""

import types
import unittest

try:
    from pouncer.app import PouncerApp as App, REFRESH_MS
except Exception:  # noqa: BLE001 - no Tk here
    App = None


@unittest.skipIf(App is None, "tkinter not available")
class TickTests(unittest.TestCase):
    def test_error_in_tick_body_still_schedules_the_next_tick(self) -> None:
        after = []
        fake = types.SimpleNamespace(
            root=types.SimpleNamespace(after=lambda ms, fn: after.append(ms)),
            _perf={"ticks": 0, "tick_ms": 0.0, "tick_max": 0.0})
        fake._tick = lambda: None

        def boom():
            raise ValueError("bad spot")
        fake._tick_body = boom
        with self.assertLogs("pouncer.app", "ERROR"):
            App._tick(fake)
        self.assertEqual(after, [REFRESH_MS])
        self.assertEqual(fake._perf["ticks"], 1)

    def test_normal_tick_schedules_once(self) -> None:
        after = []
        fake = types.SimpleNamespace(
            root=types.SimpleNamespace(after=lambda ms, fn: after.append(ms)),
            _perf={"ticks": 0, "tick_ms": 0.0, "tick_max": 0.0})
        fake._tick = lambda: None

        def body():
            fake.root.after(REFRESH_MS, fake._tick)
            fake._tick_scheduled = True
        fake._tick_body = body
        App._tick(fake)
        self.assertEqual(after, [REFRESH_MS])


if __name__ == "__main__":
    unittest.main()
