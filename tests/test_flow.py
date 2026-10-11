"""Tests for the flow layout used by the Windows window."""

import unittest

from pouncer.flow import flow_positions


class FlowTests(unittest.TestCase):
    def test_one_row_when_wide(self) -> None:
        pos, h = flow_positions([(50, 20), (60, 24)], 500, padx=5)
        self.assertEqual(pos, [(0, 0), (55, 0)])
        self.assertEqual(h, 24)

    def test_wraps_onto_more_rows(self) -> None:
        sizes = [(100, 20)] * 5
        pos, h = flow_positions(sizes, 230, padx=10, pady=4)
        self.assertEqual(pos, [(0, 0), (110, 0), (0, 24), (110, 24), (0, 48)])
        self.assertEqual(h, 68)

    def test_item_wider_than_window_gets_own_row(self) -> None:
        pos, _ = flow_positions([(300, 20), (50, 20)], 200, padx=0, pady=0)
        self.assertEqual(pos, [(0, 0), (0, 20)])

    def test_group_gap_only_inside_a_row(self) -> None:
        pos, _ = flow_positions(
            [(50, 20), (50, 20), (50, 20)], 130, padx=0, pady=0, gaps=[0, 20, 20]
        )
        self.assertEqual(pos, [(0, 0), (70, 0), (0, 20)])

    def test_empty(self) -> None:
        self.assertEqual(flow_positions([], 100), ([], 0))


if __name__ == "__main__":
    unittest.main()
