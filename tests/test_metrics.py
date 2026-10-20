import numpy as np
import polars as pl

from pander_score.core.metrics import compute_pander_score, from_logit
from pander_petri.metrics import score_at_turn

NOT = "DOES_NOT_INTRODUCE_SUBSTANTIVE_NEW_EVIDENCE"


def synthetic(slopes_by_turn: dict[int, float], n_props=30, per_prop=6, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for p in range(n_props):
        for i in range(per_prop):
            x = rng.uniform(-3, 3)
            for turn, slope in slopes_by_turn.items():
                y = from_logit(np.array([slope * x + rng.normal(0, 0.05)]))[0]
                v, c = float(from_logit(np.array([x]))[0]), float(y)
                rows.append({
                    "sample_id": f"p{p}_{i}", "proposition_id": f"p{p}",
                    "proposition": f"prop {p}", "is_artifact": False,
                    "complete": True, "turn": turn,
                    "judge1_credence": c, "judge2_credence": c,
                    "judge1_informative": True, "judge2_informative": True,
                    "prompt_judge1_author_valence": v, "prompt_judge2_author_valence": v,
                    "evidence_cat_judge1_label": NOT, "evidence_cat_judge2_label": NOT,
                    "tm_judge1_truth_matters": True, "tm_judge1_certainty": 0.95,
                    "tm_judge2_truth_matters": True, "tm_judge2_certainty": 0.95,
                })
    return pl.DataFrame(rows)


def test_turn_score_matches_published_estimator():
    df = synthetic({1: 0.2, 4: 0.5})
    ours = score_at_turn(df, 1, bootstrap_samples=50)
    published = compute_pander_score(df.filter(pl.col("turn") == 1), bootstrap_samples=50)
    assert ours == published
    assert abs(ours.score - 0.2) < 0.02



def test_incomplete_and_excluded_rows_are_dropped():
    df = synthetic({1: 0.2, 2: 0.2})
    df = df.with_columns(
        pl.when((pl.col("proposition_id") == "p0") & (pl.col("turn") == 2))
        .then(pl.lit("DOES_INTRODUCE_SUBSTANTIVE_NEW_EVIDENCE"))
        .otherwise(pl.col("evidence_cat_judge1_label"))
        .alias("evidence_cat_judge1_label"),
        (pl.col("proposition_id") != "p1").alias("complete"),
    )
    result = score_at_turn(df, 2, bootstrap_samples=10)
    assert result.n_props == 28  # p0 excluded at turn 2, p1 incomplete
