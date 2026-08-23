"""The post-mortem draft (ADR-0010).

A *draft*: Triage writes what it can defend from the incident record, and the
person who was on call corrects it. Four fields rather than free prose, for the
same reason the ticket has nine — a section that is missing is visible, and a
section that says nothing has to say why.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from triage.schemas.common import Filled


class Postmortem(BaseModel):
    timeline: Filled = Field(
        description="What happened, in order, with timestamps taken from the collected "
        "events. Only moments that were actually observed."
    )
    what_happened: Filled = Field(
        description="The incident in a paragraph, for someone who missed it."
    )
    why_it_happened: Filled = Field(
        description="The mechanism from the diagnosis, with its confidence stated."
    )
    what_would_have_helped: Filled = Field(
        description="What was missing during the incident: a signal not collected, an alert "
        "that fired late, a dashboard nobody had. Not a fix for the cause."
    )

    def to_markdown(self) -> str:
        return (
            f"## Post-mortem draft\n\n"
            f"*Written by Triage from the incident record. Corrections welcome — "
            f"the timeline is only as complete as what was collected.*\n\n"
            f"### Timeline\n\n{self.timeline}\n\n"
            f"### What happened\n\n{self.what_happened}\n\n"
            f"### Why it happened\n\n{self.why_it_happened}\n\n"
            f"### What would have helped\n\n{self.what_would_have_helped}\n"
        )
