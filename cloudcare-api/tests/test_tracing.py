"""Respan tracing is optional and, once enabled, records the consultation.

The Respan SDK is exercised for real here: the spans are collected with the
SDK's in-memory exporter, so no key and no network are involved.
"""
from types import SimpleNamespace

from opentelemetry import trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from respan_tracing.testing import InMemorySpanExporter

from app import tracing


def _settings_with_key(value):
    return SimpleNamespace(respan_api_key=value)


def _reset_tracing(monkeypatch):
    monkeypatch.setattr(tracing, "_initialized", False)
    monkeypatch.setattr(tracing, "_active", False)
    monkeypatch.setattr(tracing, "_telemetry", None)


class _StubAgent:
    """Stands in for the optional bundled agent; no trained models needed."""

    def __init__(self):
        self.calls = []

    def consult(self, patient, query, chat_history=None):
        self.calls.append({"patient": patient, "query": query, "chat_history": chat_history})
        return {"answer": f"Advice for: {query}", "sources": ["ACC/AHA"]}


def test_tracing_stays_off_without_an_api_key(monkeypatch):
    monkeypatch.setattr(tracing, "get_settings", lambda: _settings_with_key(None))
    _reset_tracing(monkeypatch)

    assert tracing.tracing_enabled() is False
    assert tracing.init_tracing() is False
    assert tracing.current_trace_id() is None


def test_consultation_workflow_emits_one_trace(monkeypatch):
    from app.routers import health_risk

    monkeypatch.setattr(tracing, "get_settings", lambda: _settings_with_key("test-key"))
    _reset_tracing(monkeypatch)
    # Spans are inspected in memory; this keeps the SDK's own exporter local.
    monkeypatch.setenv("RESPAN_BASE_URL", "http://127.0.0.1:9/api")
    assert tracing.init_tracing() is True

    exporter = InMemorySpanExporter()
    trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(exporter))

    agent = _StubAgent()
    monkeypatch.setattr(health_risk, "get_agent", lambda: agent)

    record = {"age": 58, "bmi": 31.2, "systolic_bp": 148, "diastolic_bp": 92}
    result = health_risk._run_consultation(record, "How is my heart risk?", [])

    assert result["answer"] == "Advice for: How is my heart risk?"
    assert agent.calls == [
        {"patient": record, "query": "How is my heart risk?", "chat_history": []}
    ]

    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert set(spans) == {
        "clinical_consultation.workflow",
        "load_health_risk_agent.task",
        "consultation_answer.task",
    }, sorted(spans)

    workflow_span = spans["clinical_consultation.workflow"]
    assert workflow_span.parent is None
    for name in ("load_health_risk_agent.task", "consultation_answer.task"):
        assert spans[name].parent.span_id == workflow_span.context.span_id
        assert spans[name].context.trace_id == workflow_span.context.trace_id
