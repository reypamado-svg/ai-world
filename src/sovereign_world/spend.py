"""Spending on model calls, and the run's hard cap (sealed trial).

What has been spent is counted from the councils' recorded usage (``CouncilRecord.usage``), so
a resumed run counts what it spent before it stopped. Before each day the runner asks
``stop_reason``: the day goes ahead only while what remains of every cap covers
``reserve_councils`` worst-case council rounds, because a crisis can call a council on any day.

A worst-case council round is every model-played civilization asking its model twice (the turn
and a repair), each time with the largest prompt its budgets allow and the most output it may
write, priced at the table's dearest rate: whatever answers is charged at most that, and a
fallback or a dated model name may answer in place of the configured model. A program that
takes no output limit (Codex) is reserved at ``OUTPUT_CEILING_TOKENS`` a call.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from sovereign_world.config import (
    BudgetConfig,
    Price,
    RunManifest,
    SovereignConfig,
    SpendConfig,
)
from sovereign_world.gateway.records import CouncilRecord
from sovereign_world.state import WorldState

CHARS_PER_TOKEN = 3
"""A cautious rate: model tokenizers give about four characters a token of English."""
CHARTER_ALLOWANCE_CHARS = 32_000
"""The charter (system prompt) is about 25,000 characters under rules 3."""
SLACK_CHARS = 2_000
"""Headings and the repair note."""
CALLS_PER_COUNCIL = 2
OUTPUT_CEILING_TOKENS = {"codex": 32_000}
"""Output reserved for one call by a program that cannot be given an output limit (a call above
it can pass a cap by its excess for that day; the runbook says so)."""


@dataclass
class Tally:
    """Tokens and cost, in all and by civilization and by answering model."""

    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    by_civilization: dict[str, list[float]] = field(default_factory=dict)
    """``[input tokens, output tokens, cost]`` by civilization id."""
    by_model: dict[str, list[float]] = field(default_factory=dict)
    unpriced: set[str] = field(default_factory=set)
    """Answering models the price table does not list."""
    councils: int = 0

    def add(
        self, civilization: str, model: str, tokens_in: int, tokens_out: int, cost: float
    ) -> None:
        self.input_tokens += tokens_in
        self.output_tokens += tokens_out
        self.cost_usd += cost
        for table, key in ((self.by_civilization, civilization), (self.by_model, model)):
            row = table.setdefault(key, [0, 0, 0.0])
            row[0] += tokens_in
            row[1] += tokens_out
            row[2] += cost

    def merge(self, other: Tally) -> None:
        """Add another tally to this one."""
        self.councils += other.councils
        self.unpriced |= other.unpriced
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cost_usd += other.cost_usd
        for mine, theirs in (
            (self.by_civilization, other.by_civilization),
            (self.by_model, other.by_model),
        ):
            for key, row in theirs.items():
                total = mine.setdefault(key, [0, 0, 0.0])
                for index in range(3):
                    total[index] += row[index]


def dearest(spend: SpendConfig) -> Price:
    """The table's highest rates (each side on its own), or zero for an empty table."""
    if not spend.prices:
        return Price(input_per_million_usd=0, output_per_million_usd=0)
    return Price(
        input_per_million_usd=max(price.input_per_million_usd for price in spend.prices.values()),
        output_per_million_usd=max(price.output_per_million_usd for price in spend.prices.values()),
    )


def cost_of(model: str, tokens_in: int, tokens_out: int, spend: SpendConfig) -> tuple[float, bool]:
    """What the tokens cost, and whether the model was priced."""
    price = spend.prices.get(model)
    priced = price is not None
    price = price or dearest(spend)
    cost = (
        tokens_in * price.input_per_million_usd + tokens_out * price.output_per_million_usd
    ) / 1_000_000
    return cost, priced


def tally(records: Iterable[CouncilRecord], spend: SpendConfig | None) -> Tally:
    """What the councils recorded so far have spent."""
    table = spend or SpendConfig()
    total = Tally()
    for record in records:
        if not record.usage:
            continue
        total.councils += 1
        for usage in record.usage:
            cost, priced = cost_of(usage.model, usage.input_tokens, usage.output_tokens, table)
            if not priced:
                total.unpriced.add(usage.model)
            total.add(
                str(record.civilization_id),
                usage.model,
                usage.input_tokens,
                usage.output_tokens,
                cost,
            )
    return total


def call_input_tokens(budgets: BudgetConfig) -> int:
    """The most input one call can carry under the run's budgets."""
    chars = (
        CHARTER_ALLOWANCE_CHARS
        + budgets.state_chars
        + budgets.memory_chars
        + budgets.transcript_chars
        + SLACK_CHARS
    )
    return -(-chars // CHARS_PER_TOKEN)


def output_bound(sovereign: SovereignConfig, budgets: BudgetConfig) -> int:
    """The most output one call may write: the budget, or a ceiling for a program that cannot
    be held to one."""
    return max(budgets.max_output_tokens, OUTPUT_CEILING_TOKENS.get(sovereign.provider, 0))


def worst_case_round(manifest: RunManifest) -> Tally:
    """Every model-played civilization's council at its largest, at the table's dearest rate."""
    spend = manifest.spend or SpendConfig()
    price = dearest(spend)
    round_ = Tally()
    tokens_in = CALLS_PER_COUNCIL * call_input_tokens(manifest.budgets)
    for civilization_id, sovereign in sorted(manifest.sovereigns.items()):
        if sovereign.provider == "baseline":
            continue
        tokens_out = CALLS_PER_COUNCIL * output_bound(sovereign, manifest.budgets)
        cost = (
            tokens_in * price.input_per_million_usd + tokens_out * price.output_per_million_usd
        ) / 1_000_000
        round_.add(civilization_id, sovereign.model, tokens_in, tokens_out, cost)
        round_.councils += 1
    return round_


def stop_reason(manifest: RunManifest, spent: Tally) -> str | None:
    """Why the next day must not start, or None while the caps allow it."""
    spend = manifest.spend
    if spend is None:
        return None
    if spend.unknown_model == "refuse" and spent.unpriced:
        return f"a reply came from {', '.join(sorted(spent.unpriced))}, which has no price"
    worst = worst_case_round(manifest)
    reserve = spend.reserve_councils
    for name, used, cap, need in (
        ("cost", spent.cost_usd, spend.max_cost_usd, worst.cost_usd * reserve),
        ("input tokens", spent.input_tokens, spend.max_input_tokens, worst.input_tokens * reserve),
        (
            "output tokens",
            spent.output_tokens,
            spend.max_output_tokens,
            worst.output_tokens * reserve,
        ),
    ):
        if cap is not None and used + need > cap:
            shown = f"${used:,.2f} of ${cap:,.2f}" if name == "cost" else f"{used:,} of {cap:,}"
            held = f"${need:,.2f}" if name == "cost" else f"{need:,}"
            return (
                f"the spending cap: {name} {shown} used, and a worst-case council round needs"
                f" {held} more"
            )
    return None


UNLIMITED_ROUNDS = 10**6
"""Rounds a cap covers when a worst-case round costs nothing against it."""


def caps_of(spend: SpendConfig | None) -> list[tuple[str, float]]:
    """The caps a run sets, by name: ``cost`` (US dollars), ``input tokens``, ``output tokens``."""
    if spend is None:
        return []
    named = (
        ("cost", spend.max_cost_usd),
        ("input tokens", spend.max_input_tokens),
        ("output tokens", spend.max_output_tokens),
    )
    return [(name, float(cap)) for name, cap in named if cap is not None]


def rounds_covered(manifest: RunManifest, spent: Tally) -> dict[str, int]:
    """For each cap the run sets, how many more worst-case council rounds it covers."""
    worst = worst_case_round(manifest)
    used = {"cost": spent.cost_usd, "input tokens": spent.input_tokens}
    used["output tokens"] = spent.output_tokens
    per = {"cost": worst.cost_usd, "input tokens": worst.input_tokens}
    per["output tokens"] = worst.output_tokens
    return {
        name: int(max(0.0, cap - used[name]) // per[name]) if per[name] > 0 else UNLIMITED_ROUNDS
        for name, cap in caps_of(manifest.spend)
    }


def shown_cap(name: str, cap: float) -> str:
    """A cap as people read it: ``$50.00`` or ``6,000,000 input tokens``."""
    return f"${cap:,.2f}" if name == "cost" else f"{int(cap):,} {name}"


def prompt_round(manifest: RunManifest, state: WorldState) -> Tally:
    """A council round on `state` sized from the real prompts, with no model asked: each
    model-played civilization's turn at its prompt's length and the most output it may write.
    The repair call is left out; ``worst_case_round`` is the cautious bound."""
    from sovereign_world.commands import build_council_report
    from sovereign_world.gateway.factory import budgets_of
    from sovereign_world.gateway.prompt import build_prompt

    spend = manifest.spend or SpendConfig()
    budgets = budgets_of(manifest.budgets)
    round_ = Tally()
    for civilization_id in sorted(state.civilizations):
        sovereign = manifest.sovereigns.get(str(civilization_id))
        if sovereign is None or sovereign.provider == "baseline":
            continue
        system, user = build_prompt(build_council_report(state, civilization_id), (), budgets)
        tokens_in = -(-(len(system) + len(user)) // CHARS_PER_TOKEN)
        tokens_out = output_bound(sovereign, manifest.budgets)
        cost, _ = cost_of(sovereign.model, tokens_in, tokens_out, spend)
        round_.add(str(civilization_id), sovereign.model, tokens_in, tokens_out, cost)
        round_.councils += 1
    return round_


@dataclass
class CouncilSummary:
    """One civilization's councils as recorded: who held them, how they went, what they cost."""

    civilization: str
    who: str
    """The vendor label, or the provider kind (``baseline`` for the scripted sovereign)."""
    model: str
    """The configured model."""
    councils: int = 0
    asked: int = 0
    """Councils that called a model (and so used tokens)."""
    outcomes: dict[str, int] = field(default_factory=dict)
    """Councils by outcome: accepted, repaired, timeout, late, malformed, refused, unavailable."""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    answering: set[str] = field(default_factory=set)
    """Models that answered in place of the configured one (a fallback, or a dated name)."""
    unpriced: set[str] = field(default_factory=set)


def outcome_word(record: CouncilRecord) -> str:
    """A council's outcome in one word: ``accepted``, ``timeout`` and so on."""
    return record.outcome.value.removeprefix("no_commands:")


def council_summary(
    records: Iterable[CouncilRecord], manifest: RunManifest
) -> list[CouncilSummary]:
    """Every civilization's councils, in civilization order (those that held none included)."""
    spend = manifest.spend or SpendConfig()
    rows: dict[str, CouncilSummary] = {}

    def row(civilization: str, record: CouncilRecord | None) -> CouncilSummary:
        if civilization not in rows:
            config = manifest.sovereigns.get(civilization)
            if config is not None and config.provider != "baseline":
                who, model = config.label or config.provider, config.model
            else:
                who = "baseline"
                model = record.model if record is not None else "BaselineSovereign"
            rows[civilization] = CouncilSummary(civilization, who, model)
        return rows[civilization]

    for civilization in sorted(manifest.sovereigns):
        row(civilization, None)
    for record in records:
        summary = row(str(record.civilization_id), record)
        summary.councils += 1
        word = outcome_word(record)
        summary.outcomes[word] = summary.outcomes.get(word, 0) + 1
        summary.asked += bool(record.usage)
        for usage in record.usage:
            cost, priced = cost_of(usage.model, usage.input_tokens, usage.output_tokens, spend)
            summary.input_tokens += usage.input_tokens
            summary.output_tokens += usage.output_tokens
            summary.cost_usd += cost
            if usage.model != summary.model:
                summary.answering.add(usage.model)
            if not priced:
                summary.unpriced.add(usage.model)
    return [rows[key] for key in sorted(rows)]
