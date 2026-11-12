"""Structured logging for every component.

Log lines are ``key=value`` structured so they are easy to grep and to parse::

    2026-10-15T13:37:00.123 INFO  agv.controller state_change from=IDLE to=NAVIGATING order=42

Use :func:`kv` to append structured fields to a message::

    log.info("state_change %s", kv(frm="IDLE", to="NAVIGATING"))
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Any

from common.config import REPO_ROOT, Config

_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-5s %(name)s %(message)s"
_DATEFMT = "%Y-%m-%dT%H:%M:%S"


def kv(**fields: Any) -> str:
    """Format keyword arguments as ``key=value`` pairs (values with spaces are quoted)."""
    parts = []
    for key, value in fields.items():
        text = f"{value:.3f}" if isinstance(value, float) else str(value)
        if " " in text or text == "":
            text = '"' + text.replace('"', "'") + '"'
        parts.append(f"{key.rstrip('_')}={text}")
    return " ".join(parts)


def setup_logging(component: str, cfg: Config | None = None, level: str | None = None) -> Path | None:
    """Configure root logging to console and ``<log_dir>/<component>.log``.

    Returns the log file path (or ``None`` if the log directory is not writable).
    """
    lvl_name = (level or (cfg.get("system.log_level", "INFO") if cfg else "INFO")).upper()
    root = logging.getLogger()
    root.setLevel(lvl_name)
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = logging.Formatter(_FORMAT, _DATEFMT)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    log_file: Path | None = None
    log_dir = Path(str(cfg.get("system.log_dir", "logs"))) if cfg else Path("logs")
    if not log_dir.is_absolute():
        log_dir = REPO_ROOT / log_dir
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"{component}.log"
        fh = logging.handlers.RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError as exc:  # read-only FS etc. - console logging still works
        logging.getLogger(__name__).warning("File logging disabled: %s", exc)
        log_file = None

    # Third-party libraries are noisy at INFO.
    for noisy in ("uvicorn.access", "pymodbus", "PIL", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return log_file
