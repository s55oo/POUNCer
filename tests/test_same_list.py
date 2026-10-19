"""Phone refresh: 'same' when nothing changed (ages don't count), paused when hidden."""

import json
import unittest
import urllib.request

from pouncer.web import INDEX_HTML, WebServer, payload_tag


def _payload(age=5, snr=20):
    return {"band": "40m", "spots": [{"call": "DL5ARM", "freq": 7008.6, "snr": snr, "age": age}]}


class TagTests(unittest.TestCase):
    def test_ages_do_not_change_the_tag(self) -> None:
        self.assertEqual(payload_tag(_payload(age=5)), payload_tag(_payload(age=65)))
        self.assertNotEqual(payload_tag(_payload(snr=20)), payload_tag(_payload(snr=21)))

    def test_server_says_same(self) -> None:
        box = {"p": _payload()}
        web = WebServer("127.0.0.1", 0, lambda **k: box["p"], lambda *a, **k: None,
                        lambda *a, **k: None, lambda: None, lambda *a, **k: None, lambda *a: None,
                        lambda *a, **k: None, lambda *a: None, lambda *a: None)
        web.start()
        try:
            get = lambda q="": json.loads(urllib.request.urlopen(
                f"http://127.0.0.1:{web.port}/api/spots{q}", timeout=5).read())
            first = get()
            self.assertIn("spots", first)
            tag = first["tag"]
            box["p"] = _payload(age=40)                     # only older: nothing new
            same = get("?since=" + tag)
            self.assertEqual(same, {"same": True, "tag": tag, "version": first["version"]})
            box["p"] = _payload(snr=25)                     # changed: the whole list again
            full = get("?since=" + tag)
            self.assertEqual(full["spots"][0]["snr"], 25)
            self.assertNotEqual(full["tag"], tag)
        finally:
            web.stop()

    def test_page_pauses_and_sends_since(self) -> None:
        self.assertIn("visibilitychange", INDEX_HTML)
        self.assertIn("document.hidden || inFlight", INDEX_HTML)
        self.assertIn("q.set('since', lastTag)", INDEX_HTML)


if __name__ == "__main__":
    unittest.main()
