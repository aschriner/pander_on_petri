"""Multi-turn Pander Score as an Inspect Petri audit."""

from __future__ import annotations

from inspect_ai import Task, task
from inspect_ai.agent import Agent, AgentState, agent
from inspect_ai.model import GenerateConfig, get_model
from inspect_ai.util import store

from inspect_petri import audit_solver, auditor_agent, target_agent
from inspect_petri.target import TargetContext

from pander_score.core.config import DEFAULT_MAX_TOKENS, resolve_model_config

from pander_petri.data import (
    Pressure,
    PromptType,
    build_samples,
    eligible_openings,
    select_openings,
)
from pander_petri.prompts import AUDITOR_SYSTEM_MESSAGE, AUDITOR_USER_MESSAGE
from pander_petri.scorer import TARGET_CONDITION_KEY, pander_multiturn_scorer
from pander_petri.tools import pander_auditor_tools


@agent(name="target")
def pander_target(
    *,
    reasoning_effort: str | None = None,
    temperature: float | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> Agent:
    """Petri's target driver, with the published Pander generation condition.

    The 18 paper models get their recorded reasoning effort and temperature
    (exactly as pander-score's runner does); other models get provider defaults
    plus any explicit overrides. No system prompt, matching the single-turn
    benchmark.
    """

    async def execute(state: AgentState, context: TargetContext) -> AgentState:
        base = get_model(role="target", required=True)
        resolved = resolve_model_config(
            str(base),
            reasoning_effort=reasoning_effort,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        store().set(TARGET_CONDITION_KEY, {"model": str(base), **resolved.to_dict()})
        config = base.config.merge(
            GenerateConfig(
                reasoning_effort=resolved.reasoning_effort,  # type: ignore[arg-type]
                temperature=resolved.temperature,
                max_tokens=resolved.max_tokens,
            )
        )
        model = get_model(
            str(base),
            config=config,
            base_url=base.explicit_base_url,
            **base.model_args,
        )
        return await target_agent(model=model, system_required=False)(state, context)

    return execute


@task
def pander_multiturn(
    followup_turns: int = 9,
    pressure: Pressure = "persistent",
    prompt_type: PromptType = "conversational",
    samples_per_proposition: int = 6,
    propositions: str | list[str] | None = None,
    selection_seed: int = 0,
    target_reasoning_effort: str | None = None,
    target_temperature: float | None = None,
    target_max_tokens: int = DEFAULT_MAX_TOKENS,
) -> Task:
    """Multi-turn Pander Score.

    Each sample opens with a frozen Pander Score v1 prompt (sent verbatim), then
    the Petri auditor plays the same user for ``followup_turns`` more turns,
    holding the same attitude and adding no new evidence. Expressed credence is
    judged after the first and last assistant turns with the published
    two-judge protocol.

    Args:
        followup_turns: User messages after the opening (total turns = this + 1).
        pressure: "persistent" (push back when the assistant disagrees with the
            user's lean) or "natural" (continue in character without arguing).
        prompt_type: "conversational", "instructional" (artifact requests), or "both".
        samples_per_proposition: Openings per proposition (per prompt type),
            balanced across skeptic/neutral/believer priors. The estimator needs
            at least 4 surviving prompts per proposition.
        propositions: Proposition ids to include (comma-separated or list). Default: all 349.
        selection_seed: Seed for choosing openings.
        target_reasoning_effort: Override the published/default reasoning effort.
        target_temperature: Override the published/default temperature.
        target_max_tokens: Target max tokens (published condition: 16000).

    Model roles:
        target: The model being evaluated.
        auditor: The model simulating the user.
    """
    if followup_turns < 1:
        raise ValueError("followup_turns must be at least 1")
    if isinstance(propositions, str):
        propositions = [p.strip() for p in propositions.split(",") if p.strip()]
    openings = select_openings(
        eligible_openings(),
        prompt_type=prompt_type,
        samples_per_proposition=samples_per_proposition,
        propositions=propositions,
        seed=selection_seed,
    )
    return Task(
        dataset=build_samples(openings, followup_turns=followup_turns, pressure=pressure),
        solver=audit_solver(
            auditor=auditor_agent(
                # opening + follow-ups + end, with slack for a stray tool error
                max_turns=followup_turns + 6,
                tools=pander_auditor_tools(),
                system_message=AUDITOR_SYSTEM_MESSAGE,
                user_message=AUDITOR_USER_MESSAGE,
                compaction=False,
            ),
            target=pander_target(
                reasoning_effort=target_reasoning_effort,
                temperature=target_temperature,
                max_tokens=target_max_tokens,
            ),
        ),
        scorer=pander_multiturn_scorer(),
    )
