"""Readable console and file logging for auto_preview runs."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

_COLORS = {
    logging.DEBUG: "\x1b[90m",
    logging.INFO: "\x1b[36m",
    logging.WARNING: "\x1b[33m",
    logging.ERROR: "\x1b[31m",
    logging.CRITICAL: "\x1b[31;1m",
}


class _ConsoleFormatter(logging.Formatter):
    def __init__(self, *, color: bool) -> None:
        super().__init__("%(message)s")
        self._color = color

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        if not self._color:
            return message
        color = _COLORS.get(record.levelno, "")
        return f"{color}{message}\x1b[0m" if color else message


class _ProjectRootFilter(logging.Filter):
    """Render paths inside the repository relative to its root."""

    def __init__(self, project_root: Path) -> None:
        super().__init__()
        resolved = project_root.resolve()
        self._prefixes = tuple(
            dict.fromkeys(
                (
                    str(resolved) + os.sep,
                    resolved.as_posix() + "/",
                )
            )
        )

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for prefix in self._prefixes:
            message = message.replace(prefix, "")
        record.msg = message
        record.args = ()
        return True


def configure_logging(
    run_directory: Path,
    *,
    project_root: Path | None = None,
) -> logging.Logger:
    run_directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("auto_preview")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in tuple(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.filters.clear()
    if project_root is not None:
        logger.addFilter(_ProjectRootFilter(project_root))

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(
        _ConsoleFormatter(color=bool(getattr(sys.stderr, "isatty", lambda: False)()))
    )
    logger.addHandler(console)

    file_handler = logging.FileHandler(
        run_directory / "auto_preview.log", encoding="utf-8"
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(message)s")
    )
    logger.addHandler(file_handler)
    return logger
