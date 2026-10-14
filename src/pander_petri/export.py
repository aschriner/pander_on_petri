"""Export multi-turn Pander conversations to CSV: one row per sample.

    uv run pander-petri-messages logs/ --out messages.csv
    uv run pander-petri-messages logs/x.eval --complete-only

Each turn k gets four sequential columns: ``tk_user``, ``tk_user_model``,
``tk_assistant``, ``tk_assistant_model``. The model attributed to each message:

- turn-1 user message: the model that generated the frozen v1 opening prompt
  (``generator_llm_id`` in ``prompts.jsonl``);
- later user messages: the auditor model role;
- assistant messages: the model recorded on the message (the target).

Model names are reported without the provider prefix (e.g. ``claude-sonnet-4-6``).
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from inspect_ai.log import list_eval_logs, read_eval_log

from pander_petri.data import default_data_dir, ensure_data

META_COLUMNS = (
    "log",
    "sample_id",
    "epoch",
    "proposition_id",
    "proposition",
    "domain",
    "prior",
    "is_artifact",
    "pressure",
    "target_model",
    "auditor_model",
    "n_messages",
    "complete",
    "error",
)


def short_model(name: str | None) -> str:
    return name.split("/", 1)[-1] if name else ""


def opening_generators(data_dir: Path | None = None) -> dict[str, str]:
    """Frozen v1 prompt id -> model that generated it."""
    path = ensure_data(data_dir or default_data_dir()) / "prompts.jsonl"
    generators: dict[str, str] = {}
    with path.open() as f:
        for line in f:
            record = json.loads(line)
            generators[record["id"]] = record["metadata"].get("generator_llm_id", "")
    return generators


def sample_rows(paths: list[str], *, complete_only: bool) -> tuple[list[dict], int]:
    """Rows for every pander_multiturn sample, and the max turn count seen."""
    generators = opening_generators()
    rows: list[dict] = []
    max_turns = 0
    for path in paths:
        logs = (
            [info.name for info in list_eval_logs(path)] if Path(path).is_dir() else [path]
        )
        for name in logs:
            log = read_eval_log(name)
            if log.eval.task.split("/")[-1] != "pander_multiturn":
                continue
            roles = log.eval.model_roles or {}
            target = short_model(roles["target"].model if "target" in roles else log.eval.model)
            auditor = short_model(roles["auditor"].model if "auditor" in roles else None)
            for sample in log.samples or []:
                metadata = sample.metadata or {}
                messages = [m for m in sample.messages if m.role in ("user", "assistant")]
                expected = 2 * (int(metadata.get("followup_turns", 0)) + 1)
                complete = len(messages) == expected
                if complete_only and not complete:
                    continue
                row: dict = {
                    "log": Path(name).name,
                    "sample_id": sample.id,
                    "epoch": sample.epoch,
                    "proposition_id": metadata.get("proposition_id"),
                    "proposition": metadata.get("proposition"),
                    "domain": metadata.get("domain"),
                    "prior": metadata.get("prior"),
                    "is_artifact": metadata.get("is_artifact"),
                    "pressure": metadata.get("pressure"),
                    "target_model": target,
                    "auditor_model": auditor,
                    "n_messages": len(messages),
                    "complete": complete,
                    "error": sample.error.message.splitlines()[0] if sample.error else "",
                }
                turn = 0
                for message in messages:
                    if message.role == "user":
                        turn += 1
                        row[f"t{turn}_user"] = message.text
                        row[f"t{turn}_user_model"] = (
                            short_model(generators.get(metadata.get("opening_sample_id", "")))
                            if turn == 1
                            else auditor
                        )
                    else:
                        row[f"t{turn}_assistant"] = message.text
                        row[f"t{turn}_assistant_model"] = short_model(message.model) or target
                max_turns = max(max_turns, turn)
                rows.append(row)
    return rows, max_turns


def message_columns(turns: int) -> list[str]:
    columns: list[str] = []
    for k in range(1, turns + 1):
        for role in ("user", "assistant"):
            columns += [f"t{k}_{role}", f"t{k}_{role}_model"]
    return columns


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", help=".eval logs or directories of logs")
    parser.add_argument("--out", default="messages.csv", help="output CSV path")
    parser.add_argument(
        "--complete-only",
        action="store_true",
        help="skip samples with fewer than the expected messages (scoring errors are kept)",
    )
    args = parser.parse_args()

    rows, max_turns = sample_rows(args.paths, complete_only=args.complete_only)
    if not rows:
        raise SystemExit("No pander_multiturn samples found.")
    columns = [*META_COLUMNS, *message_columns(max_turns)]
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    complete = sum(row["complete"] for row in rows)
    print(f"Wrote {len(rows)} samples ({complete} complete, {max_turns} turns) to {args.out}")


if __name__ == "__main__":
    main()
