"""Command line entry point for POUNCer."""

from __future__ import annotations

import argparse
import logging
import os
import sys

from .app import PouncerApp
from .config import load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pouncer",
        description="Follow the N1MM Logger+ frequency and show S53WW RBN spots for that band.",
    )
    if getattr(sys, "frozen", False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.getcwd()
    default_cfg = os.path.join(base_dir, "config.ini")
    parser.add_argument("--config", "-c", default=default_cfg, help="path to config.ini")
    parser.add_argument("--verbose", "-v", action="store_true", help="enable debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not os.path.exists(args.config):
        logging.warning("config file not found: %s (using defaults)", args.config)

    config = load_config(args.config)
    app = PouncerApp(config)
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
