"""Multi-turn Pander samples from Petri Bloom scenarios.

Bloom (``bloom scenarios <dir>``) turns a behavior definition into scenario
seeds: base scenarios plus one variation per axis in ``BEHAVIOR.md``. For the
Pander use case each seed is a tagged block (see
``behaviors/pander_at_issueness/BEHAVIOR.md``) holding a proposition, one
verbatim opening per user stance, and a description of how the user's view is
packaged. Each (seed, stance) pair becomes one audit with the same fixed shape
as ``pander_multiturn``: the opening is sent verbatim, the auditor adds a fixed
number of evidence-free follow-ups, and the first and last replies are judged.

A seed's *cell* is its Bloom variation axis, or ``pander.base_cell`` from the
behavior frontmatter for base scenarios. Metrics are reported per cell.
"""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from inspect_ai.dataset import MemoryDataset, Sample
from petri_bloom import Behavior, Scenario

from pander_petri.data import Pressure, ensure_data

log = logging.getLogger(__name__)

DEFAULT_BASE_CELL = "base"
DEFAULT_STANCES = ("believer", "skeptic")

_TAG = re.compile(r"<(?P<tag>[a-z_]+)(?P<attrs>[^>]*)>(?P<body>.*?)</(?P=tag)>", re.DOTALL)
_ATTR = re.compile(r'(\w+)\s*=\s*"([^"]*)"')
# seed tag -> label the user simulator sees
_PACKAGING_TAGS = {
    "qud": "users_question",
    "carrier": "how_the_view_is_carried",
    "followups": "plan_for_followups",
}


@dataclass(frozen=True)
class BloomOpening:
    """One (scenario, stance) pair: a single audit."""

    scenario_id: str
    family: str
    cell: str
    stance: str
    proposition_id: str
    proposition: str
    domain: str
    opening: str
    is_artifact: bool
    packaging: dict[str, str]


class SeedFormatError(ValueError):
    """A Bloom seed is missing the tagged blocks a Pander audit needs."""


def parse_seed(seed: str) -> dict:
    """Tagged blocks of a Pander seed: ``{tag: text}`` plus ``openings`` by stance."""
    parsed: dict = {"openings": {}}
    for match in _TAG.finditer(seed):
        tag, body = match["tag"], match["body"].strip()
        attrs = dict(_ATTR.findall(match["attrs"]))
        if tag == "opening":
            if "stance" not in attrs:
                raise SeedFormatError("<opening> without a stance attribute")
            parsed["openings"][attrs["stance"]] = body
        elif tag == "proposition":
            parsed["proposition"] = body
            parsed["proposition_id"] = attrs.get("id")
        else:
            parsed[tag] = body
    missing = [tag for tag in ("proposition",) if tag not in parsed]
    if not parsed["openings"]:
        missing.append("opening")
    if missing:
        raise SeedFormatError(f"missing <{'>, <'.join(missing)}>")
    return parsed


def proposition_bank(data_dir: Path | None = None) -> dict[str, tuple[str, str]]:
    """Pander v1 proposition id -> (text, domain)."""
    path = ensure_data(data_dir) / "propositions.csv"
    with path.open(newline="", encoding="utf-8") as f:
        return {row["id"]: (row["proposition"], row["domain"]) for row in csv.DictReader(f)}


@dataclass(frozen=True)
class _ParsedScenario:
    scenario_id: str
    scenario: Scenario
    seed: dict

    @property
    def proposition_id(self) -> str:
        return self.seed.get("proposition_id") or self.seed["proposition"]


def scenario_families(parsed: list[_ParsedScenario]) -> dict[str, str]:
    """Base scenario id for every scenario id.

    Bloom's variation step never sees the base scenario's name, so variations
    are named freely and the link to their base is lost. The variator is told to
    keep the base's ``<setting>``, so within each (proposition, variation) group
    the variations are assigned one-to-one to the base scenarios of that
    proposition, most similar setting first. A variation with no base left
    becomes its own family.
    """
    bases = [item for item in parsed if not item.scenario.variation]
    families = {item.scenario_id: item.scenario_id for item in bases}
    groups: dict[tuple[str, str], list[_ParsedScenario]] = {}
    for item in parsed:
        if item.scenario.variation:
            key = (item.proposition_id, item.scenario.variation)
            groups.setdefault(key, []).append(item)
    for (proposition_id, _), variations in groups.items():
        candidates = [base for base in bases if base.proposition_id == proposition_id]
        pairs = sorted(
            (
                (
                    SequenceMatcher(
                        None, base.seed.get("setting", ""), item.seed.get("setting", "")
                    ).ratio(),
                    item.scenario_id,
                    base.scenario_id,
                )
                for item in variations
                for base in candidates
            ),
            reverse=True,
        )
        taken: set[str] = set()
        for _, variation_id, base_id in pairs:
            if variation_id not in families and base_id not in taken:
                families[variation_id] = base_id
                taken.add(base_id)
        for item in variations:
            families.setdefault(item.scenario_id, item.scenario_id)
    return families


def pander_config(behavior: Behavior) -> dict:
    """The ``pander:`` block of the behavior frontmatter (Bloom keeps it as metadata)."""
    return dict(behavior.metadata.get("pander") or {})


def bloom_openings(
    behavior: Behavior,
    *,
    stances: list[str] | None = None,
    cells: list[str] | None = None,
    propositions: list[str] | None = None,
    bank: dict[str, tuple[str, str]] | None = None,
) -> list[BloomOpening]:
    """Expand a behavior's scenarios into one opening per (scenario, stance).

    Propositions whose id is in the Pander v1 bank use the canonical text (so a
    paraphrase by the scenario model can't split a proposition in two); any
    other id is taken from the seed as written.
    """
    from petri_bloom._behavior.behavior import unique_scenario_id

    if not behavior.scenarios:
        raise ValueError(
            f"Behavior {behavior.name!r} has no scenarios. Run "
            f"`uv run bloom scenarios <dir> --model-role scenarios=<model>` first."
        )
    config = pander_config(behavior)
    base_cell = config.get("base_cell", DEFAULT_BASE_CELL)
    stances = stances or list(config.get("stances", DEFAULT_STANCES))
    cell_rules: dict[str, str] = config.get("cells", {})
    bank = proposition_bank() if bank is None else bank

    used_ids: set[str] = set()
    skipped: list[str] = []
    parsed: list[_ParsedScenario] = []
    for scenario in behavior.scenarios:
        scenario_id = unique_scenario_id(scenario, used_ids)
        try:
            parsed.append(_ParsedScenario(scenario_id, scenario, parse_seed(scenario.seed)))
        except SeedFormatError as ex:
            skipped.append(f"{scenario_id} ({ex})")
    families = scenario_families(parsed)

    openings: list[BloomOpening] = []
    for item in parsed:
        seed, cell = item.seed, item.scenario.variation or base_cell
        if cells and cell not in cells:
            continue
        if propositions and item.proposition_id not in propositions:
            continue
        text, domain = bank.get(
            item.proposition_id, (seed["proposition"], item.proposition_id.split("__")[0])
        )
        packaging = {"cell": cell} | {
            label: seed[tag] for tag, label in _PACKAGING_TAGS.items() if seed.get(tag)
        }
        if cell_rules.get(cell):
            packaging["rule_for_every_followup"] = cell_rules[cell]
        for stance in stances:
            if stance not in seed["openings"]:
                skipped.append(f"{item.scenario_id} (no {stance} opening)")
                continue
            openings.append(
                BloomOpening(
                    scenario_id=item.scenario_id,
                    family=families[item.scenario_id],
                    cell=cell,
                    stance=stance,
                    proposition_id=item.proposition_id,
                    proposition=text,
                    domain=domain,
                    opening=seed["openings"][stance],
                    is_artifact=seed.get("deliverable", "").lower().startswith("yes"),
                    packaging=packaging,
                )
            )
    if skipped:
        log.warning("Skipped %d malformed Bloom seed(s): %s", len(skipped), "; ".join(skipped))
    if not openings:
        raise ValueError(f"No usable Pander openings in behavior {behavior.name!r}.")
    return openings


def build_bloom_samples(
    openings: list[BloomOpening],
    *,
    behavior_name: str,
    followup_turns: int,
    pressure: Pressure,
) -> MemoryDataset:
    from pander_petri.prompts import seed_instructions

    samples = []
    for opening in openings:
        opening_id = f"{opening.scenario_id}__{opening.stance}"
        samples.append(
            Sample(
                id=f"{opening_id}__{pressure}{followup_turns}",
                input=seed_instructions(
                    opening_prompt=opening.opening,
                    proposition=opening.proposition,
                    prior=opening.stance,
                    opening_valence=None,
                    is_artifact=opening.is_artifact,
                    followup_turns=followup_turns,
                    pressure=pressure,
                    packaging=opening.packaging,
                ),
                target=opening.proposition,
                metadata={
                    "opening_sample_id": opening_id,
                    "opening_prompt": opening.opening,
                    "opening_generator": f"bloom/{behavior_name}",
                    "proposition": opening.proposition,
                    "proposition_id": opening.proposition_id,
                    "domain": opening.domain,
                    "prior": opening.stance,
                    "is_artifact": opening.is_artifact,
                    "followup_turns": followup_turns,
                    "pressure": pressure,
                    "cell": opening.cell,
                    "scenario": opening.scenario_id,
                    "scenario_family": opening.family,
                    "packaging": opening.packaging,
                },
            )
        )
    return MemoryDataset(samples, name=f"pander_bloom_{behavior_name}")
