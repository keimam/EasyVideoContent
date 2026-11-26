"""Loguru terminal stream handler deduplication guard for Streamlit reruns (FA2.3)."""

from __future__ import annotations

import threading
from typing import Any

from loguru import logger

_DEDUPE_LOCK = threading.Lock()


def dedupe_terminal_stream_handlers() -> int:
    """Remove duplicate StreamSink handlers on loguru's root logger while keeping task/file sinks intact."""
    removed = 0
    with _DEDUPE_LOCK:
        handlers = dict(getattr(logger._core, "handlers", {}) or {})
        stream_ids: list[int] = []
        for hid, handler in handlers.items():
            sink: Any = getattr(handler, "_sink", None)
            if type(sink).__name__ == "StreamSink":
                stream_ids.append(int(hid))

        if len(stream_ids) <= 1:
            return 0

        stream_ids.sort()
        keep_id = stream_ids[-1]
        for old_id in stream_ids[:-1]:
            try:
                logger.remove(old_id)
                removed += 1
            except ValueError:
                pass

        try:
            from app.utils import logging_utils

            logging_utils._terminal_handler_id = keep_id
        except Exception:
            pass

    return removed
