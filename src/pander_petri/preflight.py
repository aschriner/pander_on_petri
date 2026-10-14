"""Check Bloom openings against the turn-1 Pander exclusions before auditing.

    uv run pander-petri-preflight behaviors/pander_at_issueness
    uv run pander-petri-preflight behaviors/pander_at_issueness --out preflight.csv

Runs the published prompt-side judges (author valence, categorical new
evidence, Truth Matters) on every opening of every Bloom seed. No target or
auditor is involved. It reports, per packaging cell and stance, the mean judged
valence and how many openings survive each exclusion. Openings that fail here
are excluded from the audit's turn-1 score whatever the target says, so this is
the cheap loop for tuning ``BEHAVIOR.md`` before spending on audits. With
``--out``, every opening's judgments and explanations go to a CSV, so you can
see why a given seed fails.
"""

from __future__ import annotations

import argparse
import asyncio

import polars as pl
from dotenv import load_dotenv
from petri_bloom import behavior as load_behavior

from pander_score.core.schemas import EVIDENCE_NOT_INTRODUCED
from pander_score.core.prompt_judge import (
    GENERAL_JUDGES,
    TRUTH_MATTERS_JUDGES,
    judge_author_valence,
    judge_new_evidence_cat,
    judge_truth_matters,
)

from pander_petri.bloom import BloomOpening, bloom_openings
from pander_petri.data import with_opening_exclusions
from pander_petri.task import _behavior_dir


async def judge_opening(opening: BloomOpening, limit: asyncio.Semaphore) -> dict:
    async with limit:
        (valence, _), (evidence, _), (truth, _) = await asyncio.gather(
            judge_author_valence(opening.opening, opening.proposition, GENERAL_JUDGES),
            judge_new_evidence_cat(opening.opening, opening.proposition, GENERAL_JUDGES),
            judge_truth_matters(opening.opening, opening.proposition, TRUTH_MATTERS_JUDGES),
        )
    return {
        "scenario": opening.scenario_id,
        "family": opening.family,
        "cell": opening.cell,
        "stance": opening.stance,
        "proposition_id": opening.proposition_id,
        "opening": opening.opening,
        **{key: value for key, value in (valence | evidence | truth).items() if "_judge" in key},
    }


async def judge_openings(openings: list[BloomOpening], max_concurrency: int) -> pl.DataFrame:
    limit = asyncio.Semaphore(max_concurrency)
    rows = await asyncio.gather(*(judge_opening(opening, limit) for opening in openings))
    return with_opening_exclusions(pl.DataFrame(rows, infer_schema_length=None))


def report(frame: pl.DataFrame) -> pl.DataFrame:
    """Survivors of each exclusion, per cell and stance."""
    return (
        frame.group_by(["cell", "stance"])
        .agg(
            pl.len().alias("openings"),
            pl.col("opening_valence").mean().round(2).alias("mean_valence"),
            pl.col("opening_valence").is_not_null().sum().alias("valence_agree"),
            (pl.col("opening_evidence") == EVIDENCE_NOT_INTRODUCED)
            .sum()
            .alias("no_evidence"),
            pl.col("opening_truth_matters").sum().alias("truth_matters"),
            pl.col("opening_eligible").sum().alias("eligible"),
        )
        .sort(["cell", "stance"])
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("behavior", help="Bloom behavior directory with generated scenarios")
    parser.add_argument("--out", help="Write per-opening judgments to this CSV")
    parser.add_argument("--stances", help="comma-separated stances (default: all in BEHAVIOR.md)")
    parser.add_argument("--cells", help="comma-separated cells (default: all)")
    parser.add_argument("--max-concurrency", type=int, default=16)
    args = parser.parse_args()
    load_dotenv()

    openings = bloom_openings(
        load_behavior(path=_behavior_dir(args.behavior)),
        stances=args.stances.split(",") if args.stances else None,
        cells=args.cells.split(",") if args.cells else None,
    )
    frame = asyncio.run(judge_openings(openings, args.max_concurrency))
    if args.out:
        frame.write_csv(args.out)
        print(f"wrote {frame.height} openings to {args.out}")
    with pl.Config(tbl_rows=-1, tbl_hide_dataframe_shape=True):
        print(report(frame))
    failing = frame.filter(~pl.col("opening_eligible"))
    if not failing.is_empty():
        print("\nexcluded openings:")
        for row in failing.sort(["cell", "scenario", "stance"]).iter_rows(named=True):
            reasons = [
                name
                for name, failed in (
                    ("valence disagreement", row["opening_valence"] is None),
                    (
                        "new evidence",
                        row["opening_evidence"] != EVIDENCE_NOT_INTRODUCED,
                    ),
                    ("truth doesn't matter", not row["opening_truth_matters"]),
                )
                if failed
            ]
            print(f"  {row['scenario']} [{row['stance']}]: {', '.join(reasons)}")


if __name__ == "__main__":
    main()
