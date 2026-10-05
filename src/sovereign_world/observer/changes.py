"""What changed in the observer's day records from one day to another, and parties on the
road (O3).

`record_changes` turns one day's record (`run_export.day_record`) into another's with less:
the settlements whose rows changed, those gone, and the tiles whose owner changed; the
travellers and counts are small and sent whole. `apply_changes` rebuilds the later record,
equal to the one the export writes. The people file is not diffed: it is sent whole.

`routes` lists the parties on the road on a day: active journeys and expeditions, with their
routes and where their first traveller stands, for the observer to draw and inspect.
"""

from __future__ import annotations

from typing import Any

from sovereign_world.exploration import ExpeditionStatus
from sovereign_world.ids import EntityId
from sovereign_world.state import WorldState


def record_changes(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    old = {row["id"]: row for row in before["settlements"]}
    ids = [row["id"] for row in after["settlements"]]
    present = set(ids)
    owners_before = {(q, r): owner for q, r, owner in before["owners"]}
    owners_after = {(q, r): owner for q, r, owner in after["owners"]}
    return {
        "day": after["day"],
        "from": before["day"],
        "counts": after["counts"],
        "order": ids,
        "settlements": [row for row in after["settlements"] if old.get(row["id"]) != row],
        "removed": [settlement_id for settlement_id in old if settlement_id not in present],
        "travellers": after["travellers"],
        "owners": {
            "set": [
                [q, r, owner]
                for (q, r), owner in sorted(owners_after.items())
                if owners_before.get((q, r)) != owner
            ],
            "unset": [[q, r] for q, r in sorted(owners_before) if (q, r) not in owners_after],
        },
    }


def apply_changes(before: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    if before["day"] != changes["from"]:
        raise ValueError("these changes start from another day")
    rows = {row["id"]: row for row in before["settlements"]}
    for row in changes["settlements"]:
        rows[row["id"]] = row
    owners = {(q, r): owner for q, r, owner in before["owners"]}
    for q, r in changes["owners"]["unset"]:
        del owners[(q, r)]
    for q, r, owner in changes["owners"]["set"]:
        owners[(q, r)] = owner
    return {
        "day": changes["day"],
        "counts": changes["counts"],
        "settlements": [rows[settlement_id] for settlement_id in changes["order"]],
        "travellers": changes["travellers"],
        "owners": [[q, r, owner] for (q, r), owner in sorted(owners.items())],
    }


def routes(state: WorldState) -> dict[str, Any]:
    """The day's parties on the road, in id order, by civilization index as in the export."""
    civilizations = sorted(state.civilizations)
    index = {civilization_id: k for k, civilization_id in enumerate(civilizations)}

    def standing(person_ids: tuple[str, ...]) -> list[int] | None:
        for person_id in person_ids:
            for civilization in state.civilizations.values():
                person = civilization.population.people.get(EntityId(person_id))
                if person is not None and person.alive:
                    return [person.location.q, person.location.r]
        return None

    parties: list[dict[str, Any]] = []
    for journey in state.journeys:
        if not journey.active:
            continue
        people = tuple(str(person_id) for person_id in journey.traveller_ids)
        parties.append(
            {
                "id": str(journey.journey_id),
                "kind": str(journey.kind.value),
                "civilization": index.get(journey.sender_civilization_id),
                "people": list(people),
                "route": [[tile.q, tile.r] for tile in journey.route],
                "at": journey.route_index,
                "tile": standing(people),
            }
        )
    for civilization_id in civilizations:
        for expedition in state.civilizations[civilization_id].expeditions:
            if expedition.status is not ExpeditionStatus.ACTIVE:
                continue
            people = tuple(str(person_id) for person_id in expedition.explorer_ids)
            parties.append(
                {
                    "id": str(expedition.expedition_id),
                    "kind": "expedition",
                    "civilization": index[civilization_id],
                    "people": list(people),
                    "route": [[tile.q, tile.r] for tile in expedition.route],
                    "at": expedition.next_route_index,
                    "tile": standing(people),
                }
            )
    parties.sort(key=lambda party: party["id"])
    return {"day": state.day, "parties": parties}
