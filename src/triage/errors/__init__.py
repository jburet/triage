"""F2's rules over Datadog Error Tracking (ADR-0025, ADR-0026).

Pure functions over Error Tracking responses, as ``triage.collect`` is for F1's
telemetry. Nothing here knows about LangGraph, and the node that ticks the
hourly pass holds no Error Tracking knowledge of its own — which is what lets
the whole of F2's input be exercised against one captured hour, offline.
"""
