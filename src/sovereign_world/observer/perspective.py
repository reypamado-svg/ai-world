"""One civilization's view of the world, as its council knows it (O5).

`perspective_record` turns a `CouncilReport`, and nothing else, into the plain data the
observer draws in a civilization's perspective: the tiles it knows and when it last saw each,
its own settlements in the run export's row shape, its people as the council counts them
(with the notable people named), the foreign settlements, ruins, sites, roads, bridges and
tolls it knows of, its own parties out, and its council's own dated news.

The report is the only input, so the perspective can hold nothing the council could not know.
The observer server builds the report from a saved day (`build_council_report`, the same call
the engine makes for a council) in one place, and its answers are checked against hidden
facts changed at random (`tests/observer/test_perspective.py`).
"""

from __future__ import annotations

from typing import Any

from sovereign_world.commands import CouncilReport, PersonView
from sovereign_world.housing import GRADE_ORDER, HOUSEHOLD
from sovereign_world.ids import EntityId
from sovereign_world.logistics import Journey
from sovereign_world.observer.projection import (
    _LABEL_DUTY,
    DUTY,
    ELDER_YEARS,
    GROWN_YEARS,
    _defence_of,
    _plan_of,
    _walls_of,
)
from sovereign_world.people_store import Sex
from sovereign_world.ranks import SettlementRank

PERSPECTIVE_VERSION = 1
NEWS_ITEMS = 60
"""The most council news items a perspective lists, newest first."""


def _tile(tile: Any) -> list[int]:
    return [tile.q, tile.r]


def _duty(person: PersonView) -> int:
    kind = (person.duty or "").partition(":")[0]
    name = _LABEL_DUTY.get(kind)
    if name is None and kind == "work":
        name = "farmer"
    if name is None and kind == "staff":
        name = "scholar"
    if name is None:
        age = person.age_years
        name = "child" if age < GROWN_YEARS else "elder" if age >= ELDER_YEARS else "farmer"
    return DUTY[name]


def _person(person: PersonView) -> dict[str, Any]:
    return {
        "id": str(person.person_id),
        "sex": 1 if person.sex == Sex.FEMALE else 0,
        "age": person.age_years,
        "health": person.health_bp // 100,
        "hungry": person.hungry,
        "settlement": None if person.settlement_id is None else str(person.settlement_id),
        "duty": _duty(person),
        "duty_label": person.duty,
        "skills": dict(sorted(person.skills.items())),
    }


def _settlements(report: CouncilReport) -> list[dict[str, Any]]:
    population = report.population
    residents = {} if population is None else population.residents
    idle = {} if population is None else population.idle_workers
    rows = []
    for item in report.settlements:
        key = item.settlement_id
        housing = report.housing.get(key)
        houses = (
            {grade.value: housing.houses[grade] for grade in GRADE_ORDER if grade in housing.houses}
            if housing is not None
            else {}
        )
        row: dict[str, Any] = {
            "id": str(key),
            "civilization": str(item.civilization_id),
            "q": item.tile.q,
            "r": item.tile.r,
            "capital": item.capital,
            "founded_day": item.founded_day,
            "rank": report.ranks.get(key, SettlementRank.VILLAGE).value,
            "houses": houses,
            "slots": sum(houses.values()) * HOUSEHOLD,
            "residents": residents.get(key, 0),
            "idle_workers": idle.get(key, 0),
            "house_jobs": [
                {
                    "grade": job.grade.value,
                    "count": job.count,
                    "built": job.built(),
                    "workers": len(job.worker_ids),
                }
                for job in report.house_jobs
                if job.settlement_id == key
            ],
            "institutions": [
                {"kind": institution.kind.value, "staff": len(institution.staff_ids)}
                for institution in report.institutions
                if institution.settlement_id == key
            ],
        }
        plan = _plan_of(report.town_plans.get(key))
        walls = _walls_of(report.wall_rings.get(key), report.citadels.get(key))
        defence = _defence_of(report.defence_orders.get(key))
        if plan is not None:
            row["plan"] = plan
        if walls is not None:
            row["walls"] = walls
        if defence is not None:
            row["defence"] = defence
        rows.append(row)
    return rows


def _journey(journey: Journey) -> dict[str, Any]:
    at = min(journey.route_index, len(journey.route) - 1) if journey.route else 0
    return {
        "id": str(journey.journey_id),
        "kind": journey.kind.value,
        "civilization": str(journey.sender_civilization_id),
        "to": str(journey.recipient_civilization_id),
        "people": len(journey.traveller_ids),
        "route": [_tile(tile) for tile in journey.route],
        "at": at,
        "tile": _tile(journey.route[at]) if journey.route else None,
        "departed_day": journey.departed_day,
    }


def _parties(report: CouncilReport) -> list[dict[str, Any]]:
    parties = [_journey(j) for j in (*report.spy_missions, *report.extractions)]
    for petition in report.petitions:
        row = _journey(petition)
        # Petitioners wait at the end of their road, at this civilization's gates.
        if petition.route:
            row["at"] = len(petition.route) - 1
            row["tile"] = _tile(petition.route[-1])
        parties.append(row)
    return sorted(parties, key=lambda party: party["id"])


def _news(report: CouncilReport) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for battle in report.war_reports:
        items.append(
            {
                "day": battle.day,
                "kind": "battle",
                "tile": _tile(battle.tile),
                "enemy": str(battle.enemy_id),
                "won": battle.won,
                "own_fighters": battle.own_fighters,
                "own_dead": len(battle.own_dead),
                "own_wounded": len(battle.own_wounded),
                "own_captured": len(battle.own_captured),
                "enemy_fighters_estimate": battle.enemy_fighters_estimate,
                "enemy_losses_estimate": battle.enemy_losses_estimate,
            }
        )
    for notice in report.logistics_notices:
        items.append(
            {
                "day": notice.day,
                "kind": "notice",
                "notice": notice.kind.value,
                "counterpart": str(notice.counterpart_civilization_id),
                "cargo": {k.value: v for k, v in sorted(notice.cargo.items())},
                "people": len(notice.person_ids),
                "outcome": None
                if notice.reported_outcome is None
                else notice.reported_outcome.value,
            }
        )
    for caught in report.caught_spies:
        items.append(
            {
                "day": caught.day,
                "kind": "caught_spy",
                "sender": str(caught.sender_civilization_id),
                "settlement": str(caught.settlement_id),
            }
        )
    for spy in report.spy_reports:
        estimate = spy.estimate
        items.append(
            {
                "day": spy.delivered_day,
                "kind": "spy_report",
                "by_courier": spy.by_courier,
                "civilization": str(estimate.civilization_id),
                "tile": _tile(estimate.tile),
                "seen_day": estimate.day,
                "residents": estimate.residents,
                "fighters": estimate.fighters,
                "store_units": estimate.store_units,
                "wall_grade": None if estimate.wall_grade is None else estimate.wall_grade.value,
                "towers": estimate.towers,
            }
        )
    for message in report.received_messages:
        if message.delivered_day is None:
            continue
        items.append(
            {
                "day": message.delivered_day,
                "kind": "message",
                "sender": str(message.sender_civilization_id),
                "text": message.delivered_text or "",
            }
        )
    for treaty in report.treaties:
        counterparty = treaty.counterparty(report.civilization_id)
        items.append(
            {
                "day": treaty.activated_day,
                "kind": "treaty",
                "treaty": treaty.kind.value,
                "counterparty": str(counterparty),
            }
        )
        if treaty.ended_day is not None:
            items.append(
                {
                    "day": treaty.ended_day,
                    "kind": "treaty_ended",
                    "treaty": treaty.kind.value,
                    "counterparty": str(counterparty),
                    "end": None if treaty.end_kind is None else treaty.end_kind.value,
                }
            )
    for text in report.crisis:
        items.append({"day": report.day, "kind": "crisis", "text": text})
    items.sort(key=lambda item: (-item["day"], item["kind"], _order_key(item)))
    return items[:NEWS_ITEMS]


def _order_key(item: dict[str, Any]) -> str:
    """A stable order among news items of the same day and kind."""
    return repr(sorted((key, repr(value)) for key, value in item.items()))


def _owners(report: CouncilReport) -> list[list[Any]]:
    owners: dict[tuple[int, int], str] = {}
    for view in report.observed_control:
        if view.owner is not None:
            owners[(view.tile.q, view.tile.r)] = str(view.owner)
    for tile in report.controlled_tiles:
        owners[(tile.q, tile.r)] = str(report.civilization_id)
    return [[q, r, owner] for (q, r), owner in sorted(owners.items())]


def _travellers(parties: list[dict[str, Any]]) -> list[list[Any]]:
    groups: dict[tuple[int, int, str], int] = {}
    for party in parties:
        if party["tile"] is None or party["people"] == 0:
            continue
        q, r = party["tile"]
        key = (q, r, party["civilization"])
        groups[key] = groups.get(key, 0) + party["people"]
    return [[q, r, civ, n] for (q, r, civ), n in sorted(groups.items())]


def perspective_record(report: CouncilReport) -> dict[str, Any]:
    """What `report`'s council knows, as the observer draws it; built from the report alone."""
    own = EntityId(report.civilization_id)
    population = report.population
    settlements = _settlements(report)
    parties = _parties(report)
    at_home = sum(row["residents"] for row in settlements)
    away = sum(party["people"] for party in parties if party["civilization"] == str(own))
    return {
        "perspective_version": PERSPECTIVE_VERSION,
        "day": report.day,
        "civilization": str(own),
        "report_id": report.report_id,
        "rules_version": report.rules_version,
        "realm_rank": None if report.realm_rank is None else report.realm_rank.value,
        "start_center": _tile(report.start_center),
        "known_tiles": sorted(_tile(tile) for tile in report.known_tiles),
        "known_terrain": sorted([t.q, t.r, terrain.value] for t, terrain in report.known_terrain),
        "tile_dates": sorted(
            [
                view.tile.q,
                view.tile.r,
                view.as_of_day,
                None if view.owner is None else str(view.owner),
            ]
            for view in report.observed_control
        ),
        "settlements": settlements,
        "people": {
            "population": None if population is None else population.model_dump(mode="json"),
            "notable": [_person(person) for person in report.notable_people],
        },
        "foreign_settlements": sorted(
            (
                {
                    "civilization": str(contact.civilization_id),
                    "q": contact.settlement.q,
                    "r": contact.settlement.r,
                    "first_contact_day": contact.first_contact_day,
                    "last_seen_day": contact.last_seen_day,
                }
                for contact in report.contacts
            ),
            key=lambda row: (row["q"], row["r"], row["civilization"]),
        ),
        "ruins": sorted(
            (
                {
                    "q": view.ruin.tile.q,
                    "r": view.ruin.tile.r,
                    "former_civilization": str(view.ruin.former_civilization_id),
                    "since_day": view.ruin.since_day,
                    "as_of_day": view.as_of_day,
                }
                for view in report.ruins
            ),
            key=lambda row: (row["q"], row["r"]),
        ),
        "sites": sorted(
            (
                {
                    "id": str(site.site_id),
                    "q": site.tile.q,
                    "r": site.tile.r,
                    "kind": site.kind.value,
                    "richness": site.richness,
                    "remaining": site.remaining,
                    "as_of_day": site.as_of_day,
                }
                for site in report.known_sites
            ),
            key=lambda row: row["id"],
        ),
        "roads": sorted(
            (
                {
                    "q": road.tile.q,
                    "r": road.tile.r,
                    "grade": road.grade.value,
                    "as_of_day": road.as_of_day,
                }
                for road in report.known_roads
            ),
            key=lambda row: (row["q"], row["r"]),
        ),
        "bridges": sorted([b.a.q, b.a.r, b.b.q, b.b.r] for b in report.known_bridges),
        "tolls": sorted(
            (
                {
                    "q": toll.tile.q,
                    "r": toll.tile.r,
                    "owner": str(toll.owner),
                    "cargo_rate_bp": toll.cargo_rate_bp,
                    "food_per_head": toll.food_per_head,
                    "as_of_day": toll.as_of_day,
                }
                for toll in report.known_tolls
            ),
            key=lambda row: (row["q"], row["r"]),
        ),
        "garrisons": sorted(
            (
                {
                    "id": str(garrison.garrison_id),
                    "q": garrison.tile.q,
                    "r": garrison.tile.r,
                    "members": len(garrison.member_ids),
                }
                for garrison in report.garrisons
            ),
            key=lambda row: row["id"],
        ),
        "owners": _owners(report),
        "parties": parties,
        "sieges": sorted(
            (
                {
                    "id": str(siege.siege_id),
                    "besieger": str(siege.besieger_id),
                    "defender": str(siege.defender_id),
                    "settlement_tile": _tile(siege.settlement_tile),
                    "camp": _tile(siege.camp),
                    "started_day": siege.started_day,
                    "ended_day": siege.ended_day,
                }
                for siege in report.sieges
            ),
            key=lambda row: row["id"],
        ),
        "wars": sorted(
            (
                {
                    "id": str(war.war_id),
                    "aggressor": str(war.aggressor_id),
                    "defender": str(war.defender_id),
                    "started_day": war.started_day,
                    "ended_day": war.ended_day,
                }
                for war in report.wars
            ),
            key=lambda row: row["id"],
        ),
        "travellers": _travellers(parties),
        "news": _news(report),
        "counts": {"living": at_home + away, "at_home": at_home, "away": away},
    }
