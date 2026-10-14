"""Let Petri Bloom generate scenarios with models that reject forced tool choice.

Every Bloom generation stage forces its submit tool
(``tool_choice=ToolFunction("submit_...")``). Some models reject that outright,
e.g. Claude Opus 5.5::

    400 tool_choice: type "tool" and "any" are not supported for this model.

``install()`` wraps the model Bloom resolves for scenario generation. It still
forces the tool first; if the provider rejects forced tool choice, that model
falls back to ``tool_choice="auto"`` for the rest of the process. Bloom's own
retry loop already handles a reply without a tool call (it appends "use the
`submit_...` tool" and retries up to 3 times), and every Bloom prompt already
asks for the tool, so ``auto`` works in practice. Models that accept forced tool
choice behave exactly as before.

Use the ``pander-bloom`` command in place of ``bloom`` (same arguments)::

    uv run pander-bloom scenarios behaviors/pander_at_issueness \\
      --model-role scenarios=anthropic/claude-opus-5-5
"""

from __future__ import annotations

import logging
from typing import Any

from inspect_ai.model import Model
from inspect_ai.tool import ToolFunction

log = logging.getLogger(__name__)

_WRAPPED = "_pander_tool_choice_fallback"


def rejects_forced_tool_choice(ex: BaseException) -> bool:
    message = str(ex)
    return "tool_choice" in message and "not supported" in message


def with_tool_choice_fallback(model: Model) -> Model:
    """Make ``model.generate`` retry a rejected forced tool choice as ``auto``."""
    if getattr(model, _WRAPPED, False):
        return model
    generate = model.generate
    state = {"auto": False}

    async def generate_with_fallback(*args: Any, **kwargs: Any):
        if state["auto"] and isinstance(kwargs.get("tool_choice"), ToolFunction):
            kwargs["tool_choice"] = "auto"
            return await generate(*args, **kwargs)
        try:
            return await generate(*args, **kwargs)
        except Exception as ex:
            if not (
                isinstance(kwargs.get("tool_choice"), ToolFunction)
                and rejects_forced_tool_choice(ex)
            ):
                raise
            log.warning("%s rejects forced tool choice; using tool_choice='auto'.", model)
            state["auto"] = True
            kwargs["tool_choice"] = "auto"
            return await generate(*args, **kwargs)

    model.generate = generate_with_fallback  # type: ignore[method-assign]
    setattr(model, _WRAPPED, True)
    return model


def install() -> None:
    """Patch Bloom's scenario-model resolution (both modules import it by name)."""
    import petri_bloom._ideation.ideation as ideation
    import petri_bloom._understanding.understanding as understanding

    if getattr(understanding._resolve_model, _WRAPPED, False):
        return
    resolve = understanding._resolve_model

    def resolve_with_fallback(scenarios_model):
        return with_tool_choice_fallback(resolve(scenarios_model))

    setattr(resolve_with_fallback, _WRAPPED, True)
    understanding._resolve_model = resolve_with_fallback
    ideation._resolve_model = resolve_with_fallback


def main() -> None:
    """``bloom`` CLI with the tool-choice fallback installed."""
    from petri_bloom._cli.main import main as bloom_main

    install()
    bloom_main()
