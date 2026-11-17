"""Station test runs only after YES, YES, YES (v0.7.213)."""
from pouncer.app import STATION_TEST_ASKS, station_test_confirmed


def _asker(answers):
    asked = []
    def ask(title, text):
        asked.append(title)
        return answers[len(asked) - 1]
    return ask, asked


def test_three_yes_starts():
    ask, asked = _asker([True, True, True])
    assert station_test_confirmed(ask)
    assert len(asked) == 3 == len(STATION_TEST_ASKS)


def test_any_no_stops_at_once():
    for n in range(3):
        answers = [True] * n + [False] + [True] * (2 - n)
        ask, asked = _asker(answers)
        assert not station_test_confirmed(ask)
        assert len(asked) == n + 1


def test_dialog_error_means_no_test():
    def ask(title, text):
        raise RuntimeError("no display")
    assert not station_test_confirmed(ask)


def test_first_question_warns_about_the_radio():
    assert "MOVES THE RADIO" in STATION_TEST_ASKS[0][1]
    assert "contest" in STATION_TEST_ASKS[0][1]
