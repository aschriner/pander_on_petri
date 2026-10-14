"""Bloom's forced tool choice falls back to auto for models that reject it."""

import asyncio

import pytest

from inspect_ai.model import (
    ChatMessageUser,
    GenerateConfig,
    ModelAPI,
    ModelOutput,
    get_model,
    modelapi,
)
from inspect_ai.tool import ToolFunction

import pander_petri.bloom_compat as compat

ARGS = {"behavior_understanding": "u", "scientific_motivation": "m"}
calls: list[str] = []


@modelapi(name="noforcedtool")
def noforcedtool():
    class NoForcedTool(ModelAPI):
        """Rejects forced tool choice like Claude Opus 5.5; calls the tool under auto."""

        async def generate(self, input, tools, tool_choice, config):
            calls.append("forced" if isinstance(tool_choice, ToolFunction) else "auto")
            if isinstance(tool_choice, ToolFunction):
                raise RuntimeError(
                    'tool_choice: type "tool" and "any" are not supported for this model.'
                )
            return ModelOutput.for_tool_call(self.model_name, tools[0].name, ARGS)

    return NoForcedTool


def test_rejected_forced_tool_choice_falls_back_to_auto_once():
    from petri_bloom._understanding.prompts import submit_understanding
    from petri_bloom._understanding.understanding import _generate_with_tool

    compat.install()
    import petri_bloom._ideation.ideation as ideation
    import petri_bloom._understanding.understanding as understanding

    assert ideation._resolve_model is understanding._resolve_model
    model = understanding._resolve_model("noforcedtool/x")
    calls.clear()

    async def run_twice():
        results = []
        for _ in range(2):
            results.append(
                await _generate_with_tool(
                    model=model,
                    messages=[ChatMessageUser(content="go")],
                    tool=submit_understanding(),
                    tool_name="submit_understanding",
                    config=GenerateConfig(),
                )
            )
        return results

    assert asyncio.run(run_twice()) == [ARGS, ARGS]
    # forced once, rejected; auto from then on with no further forced attempts
    assert calls == ["forced", "auto", "auto"]


def test_other_errors_are_not_swallowed():
    model = compat.with_tool_choice_fallback(get_model("noforcedtool/y"))

    async def plain_failure():
        return await model.generate(
            input="go", tools=[], tool_choice="auto", config=GenerateConfig()
        )

    # an auto call that fails for another reason (no tools here) still raises
    try:
        asyncio.run(plain_failure())
    except Exception as ex:
        assert not compat.rejects_forced_tool_choice(ex)
    else:
        raise AssertionError("expected the underlying error")


def test_max_output_tokens_override_sets_cap_and_batch_size(monkeypatch):
    compat.install()
    import petri_bloom._ideation.ideation as ideation
    import petri_bloom._understanding.understanding as understanding
    from petri_bloom._util.model import calculate_batch_size

    model = get_model("noforcedtool/z")  # unknown to Inspect's model-info table
    monkeypatch.delenv(compat.MAX_OUTPUT_TOKENS_ENV, raising=False)
    assert ideation.resolve_max_tokens(model) == 8192
    assert calculate_batch_size(16, 8192, "conversation") == (8, 2)

    monkeypatch.setenv(compat.MAX_OUTPUT_TOKENS_ENV, "64000")
    assert ideation.resolve_max_tokens(model) == 64000
    assert understanding._resolve_max_tokens(model) == 64000
    assert calculate_batch_size(16, 64000, "conversation") == (16, 1)

    monkeypatch.setenv(compat.MAX_OUTPUT_TOKENS_ENV, "lots")
    with pytest.raises(ValueError):
        ideation.resolve_max_tokens(model)
