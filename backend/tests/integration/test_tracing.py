from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from fluxion.observability.tracing import configure_tracer, get_tracer


def test_configure_tracer_and_get_tracer_return_usable_tracer() -> None:
    configure_tracer(service_name="fluxion-test", environment="test")
    tracer = get_tracer("fluxion-test")

    with tracer.start_as_current_span("test.span") as span:
        current = trace.get_current_span()

    assert span.get_span_context().span_id != 0
    assert current.get_span_context().span_id == span.get_span_context().span_id


def test_span_records_business_trace_id_attribute() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    tracer = provider.get_tracer("fluxion.runtime")
    with tracer.start_as_current_span(
        "runtime.execute",
        attributes={"fluxion.trace_id": "trace_abc", "fluxion.tenant_id": "tenant-a"},
    ):
        pass

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].attributes["fluxion.trace_id"] == "trace_abc"
    assert spans[0].attributes["fluxion.tenant_id"] == "tenant-a"
