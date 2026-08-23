"""Logging to a rotating file under ~/.clicodelog/.

The app previously reported everything through print(), which goes to the stdout
of a process the user launched hours ago and minimised. That is why a source
could stop returning sessions entirely and nobody noticed. Anything worth
knowing after the fact belongs in a file.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler

from .config import APP_DATA_DIR

LOG_FILE = APP_DATA_DIR / "clicodelog.log"
_configured = False


def setup_logging(debug: bool = False) -> None:
    global _configured
    if _configured:
        return

    root = logging.getLogger("clicodelog")
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    root.propagate = False

    try:
        APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3)
        fh.setLevel(logging.DEBUG if debug else logging.INFO)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"))
        root.addHandler(fh)
    except OSError:
        pass                      # a read-only or full disk must not stop the app

    # Console stays quiet unless something actually went wrong.
    ch = logging.StreamHandler(sys.stderr)
    ch.setLevel(logging.DEBUG if debug else logging.WARNING)
    ch.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    root.addHandler(ch)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name if name.startswith("clicodelog") else f"clicodelog.{name}")
