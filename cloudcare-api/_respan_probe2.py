import os

os.environ["RESPAN_API_KEY"] = "test-key-for-local-check"
os.environ["RESPAN_BASE_URL"] = "http://127.0.0.1:9/api"

from opentelemetry import trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from respan import Respan, task, workflow
from respan_tracing.testing import InMemorySpanExporter

respan = Respan(app_name="cloudcare-probe")

provider = trace.get_tracer_provider()
print("provider:", type(provider))
exporter = InMemorySpanExporter()
provider.add_span_processor(SimpleSpanProcessor(exporter))


@task(name="risk_model")
def score(record):
    return 42


@workflow(name="consultation")
def run(record, query):
    return {"score": score(record), "answer": "ok"}


print(run({"age": 58}, "heart risk?"))
respan.flush()
for span in exporter.get_finished_spans():
    print(span.name, dict(span.attributes).get("traceloop.span.kind"), span.status.status_code)
