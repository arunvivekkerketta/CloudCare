"""Respan tracing for CloudCare's clinical consultation workflow (optional).

The health-risk consultation is the AI workflow of this API: a caregiver sends
a patient record and a question, the bundled agent scores the four
cardiometabolic risks and composes the answer. With ``RESPAN_API_KEY`` set that
workflow is reported to Respan as one trace — a workflow span plus a span per
step — so a slow or failed consultation can be inspected end to end.

Tracing is deliberately optional and provider-neutral:
- nothing is initialized (and no traffic leaves the process) without the key;
- the decorated functions run unchanged, so the endpoints answer exactly as
  before on a machine without the key or without a Respan account;
- model calls keep their local routing; Respan only receives spans.

``RESPAN_TRACE_CONTENT=false`` keeps span names, timings and errors while
withholding the patient payloads the decorators would otherwise record.
"""
from __future__ import annotations

import logging

from respan import Respan, get_client, task, workflow

from app.config import get_settings

logger = logging.getLogger("cloudcare.tracing")

# Empty OTEL trace id, returned by the SDK when no span is in progress.
_NO_TRACE_ID = "0" * 32

_telemetry: Respan | None = None
_active = False
_initialized = False


def tracing_enabled() -> bool:
    """True when a Respan API key is configured."""
    return bool((get_settings().respan_api_key or "").strip())


def init_tracing() -> bool:
    """Initialize the Respan SDK once at startup.

    Returns True when spans are being recorded. Any failure (missing key, bad
    key, unreachable endpoint, broken install) leaves tracing off instead of
    stopping the API.
    """
    global _telemetry, _active, _initialized
    if _initialized:
        return _active
    _initialized = True

    if not tracing_enabled():
        logger.info("Respan tracing is off: RESPAN_API_KEY is not set.")
        return False

    try:
        _telemetry = Respan(
            api_key=get_settings().respan_api_key,
            app_name="cloudcare-api",
        )
    except Exception:  # noqa: BLE001 — telemetry must never block the API
        logger.warning("Respan tracing could not start.", exc_info=True)
        return False

    _active = True
    logger.info("Respan tracing is on for the health-risk consultation workflow.")
    return True


def flush_tracing() -> None:
    """Send buffered spans now (shutdown, and short-lived processes)."""
    if _telemetry is None:
        return
    try:
        _telemetry.flush()
    except Exception:  # noqa: BLE001 — a flush failure must not raise
        logger.warning("Respan tracing could not flush its spans.", exc_info=True)


def current_trace_id() -> str | None:
    """Trace id of the span in progress, or None outside a trace."""
    if not _active:
        return None
    try:
        trace_id = get_client().get_current_trace_id()
    except Exception:  # noqa: BLE001 — tracing must never break a request
        return None
    if not trace_id or trace_id == _NO_TRACE_ID:
        return None
    return trace_id


__all__ = [
    "current_trace_id",
    "flush_tracing",
    "init_tracing",
    "task",
    "tracing_enabled",
    "workflow",
]
