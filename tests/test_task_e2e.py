"""Run the real task end to end with offline fake models."""

from inspect_ai import eval

from fakes import FOLLOWUP
from pander_petri import pander_multiturn


def test_audit_runs_fixed_turns_and_scores_first_and_last(fake_judges, tmp_path):
    task = pander_multiturn(
        followup_turns=3,
        propositions="contested_social_science__1",
        samples_per_proposition=6,
    )
    [log] = eval(
        task,
        model="faketarget/t",
        model_roles={"auditor": "fakeauditor/a", "target": "faketarget/t"},
        log_dir=str(tmp_path),
        display="none",
    )
    assert log.status == "success", log.error
    assert len(log.samples) == 6
    for sample in log.samples:
        users = [m.text for m in sample.messages if m.role == "user"]
        assert users[0] == sample.metadata["opening_prompt"]
        assert users[1:] == [FOLLOWUP] * 3
        assert not any(m.role == "system" for m in sample.messages)
        score = next(iter(sample.scores.values()))
        assert score.metadata["complete"] and score.metadata["n_turns"] == 4
        turns = score.metadata["turns"]
        assert [t["turn"] for t in turns] == [1, 4]
        # turn 1 uses frozen prompt judgments; the last turn is re-judged
        assert turns[0]["prompt_judge1_author_valence"] == sample.metadata["prompt_judge1_author_valence"]
        assert all(t["prompt_judge1_author_valence"] == 0.7 for t in turns[1:])
        assert all(t["judge1_credence"] == 0.6 for t in turns)
        assert score.metadata["target_condition"]["model"] == "faketarget/t"
    metrics = next(iter(log.results.scores)).metrics
    assert {"conv_t1", "conv_t4", "conv_drift"} <= set(metrics)
