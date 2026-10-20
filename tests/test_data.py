from pander_petri.data import build_samples, eligible_openings, select_openings


def test_selection_is_balanced_and_deterministic():
    frame = eligible_openings()
    first = select_openings(frame, samples_per_proposition=6, seed=3)
    again = select_openings(frame, samples_per_proposition=6, seed=3)
    assert first["sample_id"].to_list() == again["sample_id"].to_list()
    assert not first["is_artifact"].any()
    counts = first.group_by("proposition_id").len()["len"]
    assert counts.max() <= 6
    # a proposition with plenty of openings gets two of each prior
    rich = first.filter(first["proposition_id"] == "contested_social_science__1")
    assert sorted(rich["prior"].value_counts()["count"].to_list()) == [2, 2, 2]


def test_samples_carry_verbatim_opening_and_frozen_judgments():
    frame = select_openings(eligible_openings(), propositions=["historical_facts__3"])
    dataset = build_samples(frame, followup_turns=2, score_turns=[1, 3])
    sample = dataset[0]
    row = frame.row(0, named=True)
    assert sample.metadata["opening_prompt"] == row["prompt_text"]
    assert row["prompt_text"] in sample.input
    assert sample.metadata["tm_judge1_certainty"] >= 0.9
    assert "exactly 2 follow-up" in sample.input
