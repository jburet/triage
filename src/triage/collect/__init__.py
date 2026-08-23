"""F1's collection layer: recipes, reduction, the sweep and its budget (ADR-0016).

Pure functions over Datadog responses. Nothing here knows about LangGraph, and
the nodes that call it hold no Datadog knowledge of their own — which is what
lets the whole collection be exercised against captured fixtures, offline, with
no graph running.
"""
