"""Score multi-turn Pander logs: per-turn scores with CIs.

    uv run pander-petri-score logs/            # every .eval log in a directory
    uv run pander-petri-score logs/x.eval --out turns.parquet
"""

from __future__ import annotations

import argparse
from pathlib import Path

import polars as pl
from inspect_ai.log import list_eval_logs, read_eval_log

from pander_score.core.metrics import with_pander_score_exclusions

from pander_petri.metrics import score_at_turn, subsets, turn_frame

TASKS = {"pander_multiturn", "pander_bloom"}


def load_logs(paths: list[str]) -> pl.DataFrame:
    frames = []
    for path in paths:
        logs = (
            [info.name for info in list_eval_logs(path)] if Path(path).is_dir() else [path]
        )
        for name in logs:
            log = read_eval_log(name)
            if log.eval.task.split("/")[-1] not in TASKS:
                continue
            records = []
            for sample in log.samples or []:
                score = next(iter((sample.scores or {}).values()), None)
                if score is None:
                    continue
                records.append((str(sample.id), sample.metadata, score.metadata or {}))
            frame = turn_frame(records)
            if not frame.is_empty():
                target = log.eval.model_roles.get("target") if log.eval.model_roles else None
                frames.append(
                    frame.with_columns(
                        pl.lit(target.model if target else log.eval.model).alias("target_model"),
                        pl.lit(name).alias("log"),
                    )
                )
    if not frames:
        raise SystemExit("No scored pander_multiturn or pander_bloom samples found.")
    return pl.concat(frames, how="diagonal_relaxed")


def _fmt(value: float, low: float, high: float) -> str:
    return f"{value * 100:+6.1f} [{low * 100:+6.1f}, {high * 100:+6.1f}]"


def report(df: pl.DataFrame, *, bootstrap_samples: int, seed: int) -> None:
    for (model,), group in df.group_by(["target_model"], maintain_order=True):
        complete = group.filter(pl.col("complete"))
        n_samples = group["sample_id"].n_unique()
        print(f"\n== {model}")
        print(
            f"   samples: {n_samples}, complete: {complete['sample_id'].n_unique()}"
        )
        for name, subset in subsets(group).items():
            print(f"\n   {name}")
            print("   turn   score  [95% CI]           props  prompts  survive")
            for turn in sorted(subset["turn"].unique().to_list()):
                rows = subset.filter((pl.col("turn") == turn) & pl.col("complete"))
                survive = with_pander_score_exclusions(rows).height if rows.height else 0
                fit = score_at_turn(subset, turn, bootstrap_samples=bootstrap_samples, seed=seed)
                line = _fmt(fit.score, fit.ci_low, fit.ci_high) if fit else "   n/a" + " " * 21
                n_props = fit.n_props if fit else 0
                n_prompts = fit.n_samples if fit else 0
                print(
                    f"   {turn:>4}  {line}  {n_props:>5}  {n_prompts:>7}  "
                    f"{survive}/{rows.height}"
                )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("logs", nargs="+", help="Eval log files or directories")
    parser.add_argument("--out", help="Write the per-turn table to this Parquet file")
    parser.add_argument("--bootstrap-samples", type=int, default=1_000)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    df = load_logs(args.logs)
    if args.out:
        df.write_parquet(args.out)
        print(f"wrote {df.height} rows to {args.out}")
    report(df, bootstrap_samples=args.bootstrap_samples, seed=args.seed)


if __name__ == "__main__":
    main()
