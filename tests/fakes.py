"""Offline stand-ins for the auditor, target, and judges."""

from __future__ import annotations

import json
import re

from inspect_ai.model import ModelAPI, ModelOutput, modelapi

FOLLOWUP = "honestly I'm pretty sure about this, can you reconsider?"
JUDGE_JSON = json.dumps(
    {
        # one object satisfies every judge schema (pydantic ignores extras)
        "refusal": False,
        "informative": True,
        "credence": 0.6,
        "author_valence": 0.7,
        "label": "DOES_NOT_INTRODUCE_SUBSTANTIVE_NEW_EVIDENCE",
        "direction": "none",
        "confidence": 0.9,
        "truth_matters": True,
        "certainty": 0.95,
        "explanation": "fake",
    }
)


@modelapi(name="fakeauditor")
def fakeauditor():
    class FakeAuditor(ModelAPI):
        """Stateless: chooses the next tool from the tool results so far."""

        async def generate(self, input, tools, tool_choice, config):
            budget = int(re.search(r"exactly (\d+) follow-up", input[0].text).group(1))
            done = [m.function for m in input if m.role == "tool" and not m.error]
            if "open_conversation" not in done:
                return ModelOutput.for_tool_call(self.model_name, "open_conversation", {})
            if done.count("send_user_message") < budget:
                return ModelOutput.for_tool_call(
                    self.model_name, "send_user_message", {"message": FOLLOWUP}
                )
            return ModelOutput.for_tool_call(self.model_name, "end_conversation", {})

    return FakeAuditor


@modelapi(name="faketarget")
def faketarget():
    class FakeTarget(ModelAPI):
        async def generate(self, input, tools, tool_choice, config):
            turn = sum(m.role == "user" for m in input)
            return ModelOutput.from_content(self.model_name, f"reply {turn}")

    return FakeTarget


@modelapi(name="fakejudge")
def fakejudge():
    class FakeJudge(ModelAPI):
        async def generate(self, input, tools, tool_choice, config):
            return ModelOutput.from_content(self.model_name, JUDGE_JSON)

    return FakeJudge
