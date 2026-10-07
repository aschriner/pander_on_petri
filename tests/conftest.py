import pytest
from inspect_ai.model import GenerateConfig

import fakes  # noqa: F401  (registers fake model APIs)
from pander_score.core.prompt_judge import JudgeConfig


@pytest.fixture
def fake_judges(monkeypatch):
    import pander_petri.scorer as scorer

    monkeypatch.setattr(
        scorer,
        "CREDENCE_JUDGES",
        {"fakejudge/a": GenerateConfig(), "fakejudge/b": GenerateConfig()},
    )
    monkeypatch.setattr(
        scorer,
        "GENERAL_JUDGES",
        {"fakejudge/c": JudgeConfig(reasoning_effort=None), "fakejudge/d": JudgeConfig(reasoning_effort=None)},
    )
