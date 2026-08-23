"""Prompt templates, versioned as files.

Prompts live in ``.md`` files rather than in Python string literals so that a
change to what the agent asks for shows up as a reviewable diff in its own
right, separate from graph wiring.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

_PROMPT_DIR = Path(__file__).parent


@functools.lru_cache(maxsize=32)
def load(name: str) -> str:
    """Read prompt ``name`` (without extension). Cached; prompts are immutable at runtime."""
    return (_PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


def render(name: str, **sections: Any) -> str:
    """Instructions, then one tagged JSON block per input section.

    Tagged blocks rather than interpolation: the inputs are model-generated prose
    that will contain braces, backticks and occasionally something that reads
    like an instruction, and a delimited block keeps that clearly data.
    """
    parts = [load(name)]
    for tag, value in sections.items():
        payload = value if isinstance(value, str) else json.dumps(value, indent=2, default=str)
        parts.append(f"<{tag}>\n{payload}\n</{tag}>")
    return "\n\n".join(parts)
