"""Wars, fighting strength, and battles fought in rounds until one side breaks."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sovereign_world.armoury import CATAPULT_HITS_BP, CREW_STRENGTH_BP, LEVY, Kit
from sovereign_world.hexmap import HexCoord, Terrain
from sovereign_world.ids import EntityId
from sovereign_world.people import Person
from sovereign_world.rng import StableRng
from sovereign_world.walls import TOWER_HITS_BP

ARMS = "arms"
"""The fighting skill: drill and battle both raise it."""

DAYS_PER_YEAR = 365
BASIS = 10_000
BASE_STRENGTH = 100

DRILL_DAYS_PER_POINT = 5
DRILL_CAP = 20
BATTLE_SURVIVED_POINTS = 3
BATTLE_WON_POINTS = 5
BATTLE_CAP = 60
VETERAN = 30
MAX_ARMS_BONUS_BP = 6_000

ROUND_LOSS_BP = 500
"""Share of a side hit in one round of an even fight."""
MAX_ROUND_LOSS_BP = 3_000
MAX_ROUNDS = 20
WAR_PARTY_MORALE_BP = 2_000
HOME_DEFENCE_MORALE_BP = 3_000
VETERAN_MORALE_BP_PER_TENTH = 200
PURSUIT_BP = 1_000
WOUND_MIN = 3_000
WOUND_MAX = 12_000
ROUT_WOUND_MIN = 6_000
ROUT_WOUND_MAX = 15_000
VETERAN_WOUND_BP = 9_000

TERRAIN_DEFENCE_BP: dict[Terrain, int] = {
    Terrain.FOREST: 12_500,
    Terrain.MOUNTAIN: 15_000,
    Terrain.HILLS: 12_500,
    Terrain.SNOW: 15_000,
}
SETTLEMENT_DEFENCE_BP = 12_500


class WarObjective(StrEnum):
    RAID = "raid"
    """Beat the defenders of the target tile, carry off goods, and march home."""
    ATTACK = "attack"
    """Beat the defenders of the target tile, then march home."""
    BESIEGE = "besiege"
    """Camp beside the target settlement and cut it off until ordered home or broken."""
    OCCUPY = "occupy"
    """Beat the defenders of the target settlement, then stay and hold it."""


class War(BaseModel):
    """A war between two civilizations, declared or begun by an undeclared attack."""

    model_config = ConfigDict(frozen=True)

    war_id: EntityId
    aggressor_id: EntityId
    defender_id: EntityId
    started_day: int = Field(ge=0)
    declared: bool
    defender_learned_day: int | None = Field(default=None, ge=0)
    ended_day: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def distinct_sides(self) -> War:
        if self.aggressor_id == self.defender_id:
            raise ValueError("a war has two sides")
        return self

    @property
    def active(self) -> bool:
        return self.ended_day is None

    def involves(self, first: EntityId, second: EntityId) -> bool:
        return {first, second} == {self.aggressor_id, self.defender_id}

    def known_to(self, civilization_id: EntityId) -> bool:
        return civilization_id == self.aggressor_id or self.defender_learned_day is not None


MIN_BESIEGERS = 4
"""A camp with fewer living fighters cannot hold a blockade and goes home."""


class SiegeEnd(StrEnum):
    STARVED = "starved"
    TOO_FEW = "too_few"
    RECALLED = "recalled"
    BROKEN = "broken"
    STORMED = "stormed"
    PEACE = "peace"
    GONE = "gone"
    """The besieged civilization is no more."""


class Siege(BaseModel):
    """A war party camped beside an enemy settlement, cutting it off."""

    model_config = ConfigDict(frozen=True)

    siege_id: EntityId
    journey_id: EntityId
    besieger_id: EntityId
    defender_id: EntityId
    settlement_id: EntityId
    settlement_tile: HexCoord
    camp: HexCoord
    started_day: int = Field(ge=0)
    ended_day: int | None = Field(default=None, ge=0)
    end: SiegeEnd | None = None
    defender_learned_day: int | None = Field(default=None, ge=0)
    """The day the defender saw the camp; until then it does not know it is besieged."""

    @model_validator(mode="after")
    def consistent(self) -> Siege:
        if self.besieger_id == self.defender_id:
            raise ValueError("a siege has two sides")
        if self.camp.distance(self.settlement_tile) != 1:
            raise ValueError("a camp stands next to the settlement it besieges")
        if (self.ended_day is None) != (self.end is None):
            raise ValueError("an ended siege records why it ended")
        return self

    @property
    def active(self) -> bool:
        return self.ended_day is None


CAPTURE_BP = 4_000
"""The chance that a pursuit blow takes a fleeing fighter prisoner instead."""
CAPTIVES_PER_WINNER = 2
"""Winners cannot hold more captives than twice their standing number."""
ESCAPE_BP = 1_000
"""Each captive's chance to escape at every council."""
RISING_RATIO = 3
"""Residents rise when their able people outnumber the occupiers this many times over."""


class OccupationEnd(StrEnum):
    RECALLED = "recalled"
    STARVED = "starved"
    TOO_FEW = "too_few"
    BEATEN = "beaten"
    ROSE = "rose"
    PEACE = "peace"
    GONE = "gone"
    """The occupied civilization is no more."""


class Occupation(BaseModel):
    """A war party holding an enemy settlement. The owner keeps it; its people stay loyal."""

    model_config = ConfigDict(frozen=True)

    occupation_id: EntityId
    journey_id: EntityId
    occupier_id: EntityId
    owner_id: EntityId
    settlement_id: EntityId
    tile: HexCoord
    started_day: int = Field(ge=0)
    ended_day: int | None = Field(default=None, ge=0)
    end: OccupationEnd | None = None
    owner_learned_day: int | None = Field(default=None, ge=0)
    """The day the owner saw its settlement held; until then it does not know."""

    @model_validator(mode="after")
    def consistent(self) -> Occupation:
        if self.occupier_id == self.owner_id:
            raise ValueError("a civilization cannot occupy its own settlement")
        if (self.ended_day is None) != (self.end is None):
            raise ValueError("an ended occupation records why it ended")
        return self

    @property
    def active(self) -> bool:
        return self.ended_day is None


class Drill(BaseModel):
    """People training at home: no other work, and a slow rise in arms."""

    model_config = ConfigDict(frozen=True)

    drill_id: EntityId
    person_ids: tuple[EntityId, ...]
    started_day: int = Field(ge=0)
    days: int = Field(ge=1, le=180)
    days_done: int = Field(default=0, ge=0)

    @property
    def active(self) -> bool:
        return self.days_done < self.days


class Casualty(BaseModel):
    model_config = ConfigDict(frozen=True)

    person_id: EntityId
    civilization_id: EntityId
    died: bool
    damage: int = Field(ge=0)


class Battle(BaseModel):
    """What really happened: the engine's record, never shown whole to a civilization."""

    model_config = ConfigDict(frozen=True)

    battle_id: EntityId
    day: int = Field(ge=0)
    tile: HexCoord
    attacker_id: EntityId
    defender_id: EntityId
    attackers: tuple[EntityId, ...]
    defenders: tuple[EntityId, ...]
    rounds: int = Field(ge=0)
    winner_id: EntityId
    casualties: tuple[Casualty, ...] = ()
    captured: tuple[EntityId, ...] = ()
    """Fleeing fighters the winners took prisoner instead of cutting down."""


class BattleReport(BaseModel):
    """One side's account of a battle: its own losses exactly, the enemy's estimated."""

    model_config = ConfigDict(frozen=True)

    battle_id: EntityId
    day: int = Field(ge=0)
    tile: HexCoord
    enemy_id: EntityId
    won: bool
    own_fighters: int = Field(ge=0)
    own_dead: tuple[EntityId, ...] = ()
    own_wounded: tuple[EntityId, ...] = ()
    enemy_fighters_estimate: int = Field(ge=0)
    enemy_dead_seen: int = Field(ge=0)
    enemy_losses_estimate: int = Field(ge=0)
    own_captured: tuple[EntityId, ...] = ()
    """Its own fighters seen taken prisoner."""


def able_to_fight(person: Person) -> bool:
    years = person.age_days // DAYS_PER_YEAR
    return person.alive and 13 <= years <= 60 and person.health_bp > 0


def fighting_strength(
    person: Person,
    kit: Kit | None = None,
    *,
    attacking: bool = True,
    crewing: bool = False,
) -> int:
    """A fighter's strength, from health, age, hunger, arms skill, and kit.

    A fighter working a siege engine fights at half strength.
    """
    if not able_to_fight(person):
        return 0
    years = person.age_days // DAYS_PER_YEAR
    strength_bp = BASIS * person.health_bp // BASIS
    if not 16 <= years <= 50:
        strength_bp = strength_bp * 6_000 // BASIS
    hunger_bp = max(BASIS - person.nutrition_debt * 100, BASIS // 2)
    strength_bp = strength_bp * hunger_bp // BASIS
    arms_bp = min(person.skills.get(ARMS, 0) * 100, MAX_ARMS_BONUS_BP)
    strength_bp = strength_bp * (BASIS + arms_bp) // BASIS
    kit = kit or LEVY
    strength_bp = strength_bp * kit.strength_bp // BASIS
    strength_bp = strength_bp * (kit.attack_bp if attacking else kit.defence_bp) // BASIS
    if crewing:
        strength_bp = strength_bp * CREW_STRENGTH_BP // BASIS
    return max(BASE_STRENGTH * strength_bp // BASIS, 1)


def morale_bp(fighters: list[Fighter], *, at_home: bool, supplied: bool) -> int:
    """The share of a side that may fall before it breaks."""
    base = HOME_DEFENCE_MORALE_BP if at_home else WAR_PARTY_MORALE_BP
    if fighters:
        veterans = sum(fighter.veteran for fighter in fighters)
        base += VETERAN_MORALE_BP_PER_TENTH * (veterans * 10 // len(fighters))
        # Heavy infantry steadies a line in proportion to its share of it.
        base += sum(fighter.morale_bp for fighter in fighters) // len(fighters)
        hungry = sum(fighter.hungry for fighter in fighters)
        # Hunger and a severed supply line can halve a side's resolve.
        penalty_bp = BASIS // 2 * hungry // len(fighters)
        if not supplied:
            penalty_bp = max(penalty_bp, BASIS // 4)
        base = base * (BASIS - penalty_bp) // BASIS
    return base


@dataclass(frozen=True, slots=True)
class Fighter:
    person_id: EntityId
    civilization_id: EntityId
    strength: int
    health_bp: int
    veteran: bool
    hungry: bool
    volley_bp: int = 0
    morale_bp: int = 0
    unit: str = LEVY.unit.value


def fighter(
    person: Person,
    kit: Kit | None = None,
    *,
    attacking: bool = True,
    crewing: bool = False,
) -> Fighter:
    kit = kit or LEVY
    return Fighter(
        person_id=person.person_id,
        civilization_id=person.civilization_id,
        strength=fighting_strength(person, kit, attacking=attacking, crewing=crewing),
        health_bp=person.health_bp,
        veteran=person.skills.get(ARMS, 0) >= VETERAN,
        hungry=person.nutrition_debt > 0,
        volley_bp=0 if crewing else kit.volley_bp,
        morale_bp=kit.morale_bp,
        unit=kit.unit.value,
    )


@dataclass(frozen=True, slots=True)
class BattleOutcome:
    rounds: int
    attackers_won: bool
    casualties: tuple[Casualty, ...]
    captured: tuple[EntityId, ...] = ()
    reserve_round: int = 0
    """The round the defenders' reserve joined the fight; 0 if it never did, or there was
    none."""


def resolve_battle(
    attackers: list[Fighter],
    defenders: list[Fighter],
    *,
    defence_bp: int,
    attacker_morale_bp: int,
    defender_morale_bp: int,
    rng: StableRng,
    stream: str,
    catapults: int = 0,
    towers: int = 0,
    tower_hits_bp: tuple[int, ...] = (),
    defender_reserve: tuple[Fighter, ...] = (),
    reserve_joins_round: int = 0,
    defender_refuge: bool = False,
    defender_catapults: int = 0,
    attacker_cover_bp: int = 0,
    attacker_pursuit_bp: int = PURSUIT_BP,
    defender_pursuit_bp: int = PURSUIT_BP,
) -> BattleOutcome:
    """Fight rounds until a side's losses pass its morale; the rout costs it more.

    Slingers and archers on both sides loose one volley before the lines meet, and the
    attackers' catapults bombard the defenders before every round. Manned towers on the
    defenders' walls shoot in the opening volley and before every round. Then each round,
    each side loses a share of its standing fighters that grows with the other side's
    strength. A hit wounds; a wound deeper than a fighter's health kills.

    `tower_hits_bp`, when given, is each manned tower's chance of a hit instead of
    `towers` at the usual chance. `defender_reserve` waits out of the fight until the start
    of round `reserve_joins_round`, or joins at once when the defenders' line would break;
    on joining it swells the line, so the share fallen falls. With `defender_refuge` (a
    citadel), beaten defenders fall back into it: no pursuit, so no rout blows and no
    captives. `defender_catapults` (a town's own, crewed) hit the attackers before every
    round. `attacker_cover_bp` is fire from walls behind the attackers (a sally's towers):
    added to their opening volley and striking the defenders before every round. The
    pursuit rates set how far each side is chased when it breaks. Without them the draws are
    the same as before.
    """
    roll = rng.stream(stream)
    towers_bp = sum(tower_hits_bp) if tower_hits_bp else towers * TOWER_HITS_BP
    waiting = sorted(defender_reserve, key=lambda item: item.person_id)
    reserve_round = 0
    standing = {
        "attackers": sorted(attackers, key=lambda item: item.person_id),
        "defenders": sorted(defenders, key=lambda item: item.person_id),
    }
    starting = {side: len(members) for side, members in standing.items()}
    health = {item.person_id: item.health_bp for item in (*attackers, *defenders)}
    by_id = {item.person_id: item for item in (*attackers, *defenders, *waiting)}
    damage: dict[EntityId, int] = {}
    fallen = {"attackers": 0, "defenders": 0}
    thresholds = {"attackers": attacker_morale_bp, "defenders": defender_morale_bp}

    def strike(side: str, hits: int, low: int, high: int) -> None:
        for _ in range(hits):
            members = standing[side]
            if not members:
                return
            target = members[int(roll.integers(0, len(members)))]
            wound = int(roll.integers(low, high + 1))
            if target.veteran:
                wound = wound * VETERAN_WOUND_BP // BASIS
            damage[target.person_id] = damage.get(target.person_id, 0) + wound
            members.remove(target)
            fallen[side] += 1

    def hits_for(side: str, loss_bp: int) -> int:
        exact = len(standing[side]) * loss_bp
        whole, part = divmod(exact, BASIS)
        return whole + (1 if int(roll.integers(0, BASIS)) < part else 0)

    def chance_hits(total_bp: int) -> int:
        whole, part = divmod(total_bp, BASIS)
        return whole + (1 if int(roll.integers(0, BASIS)) < part else 0)

    def join_reserve() -> None:
        nonlocal waiting, reserve_round
        standing["defenders"] = sorted(
            (*standing["defenders"], *waiting), key=lambda item: item.person_id
        )
        starting["defenders"] += len(waiting)
        reserve_round = rounds
        waiting = []

    def breaking_side() -> str | None:
        shares = {
            side: fallen[side] * BASIS // max(starting[side], 1)
            for side in ("attackers", "defenders")
        }
        breaking = [side for side in shares if shares[side] >= thresholds[side]]
        if len(breaking) == 2:
            # Both lines waver: the side that has suffered more gives way; attackers on a tie.
            return "defenders" if shares["defenders"] > shares["attackers"] else "attackers"
        return breaking[0] if breaking else None

    broken: str | None = None
    rounds = 0
    if not standing["defenders"] and waiting:
        join_reserve()
    if not standing["defenders"]:
        broken = "defenders"
    else:
        # The opening volley: every slinger and archer looses once, on both sides at once.
        volleys = {
            side: chance_hits(
                sum(item.volley_bp for item in standing[side])
                + (towers_bp if side == "defenders" else attacker_cover_bp)
            )
            for side in ("attackers", "defenders")
        }
        strike("defenders", volleys["attackers"], WOUND_MIN, WOUND_MAX)
        strike("attackers", volleys["defenders"], WOUND_MIN, WOUND_MAX)
        broken = breaking_side()
        if broken == "defenders" and waiting:
            join_reserve()
            broken = breaking_side()
    while broken is None and rounds < MAX_ROUNDS:
        rounds += 1
        if waiting and rounds >= reserve_joins_round:
            join_reserve()
        if catapults:
            strike("defenders", chance_hits(catapults * CATAPULT_HITS_BP), WOUND_MIN, WOUND_MAX)
        if attacker_cover_bp:
            strike("defenders", chance_hits(attacker_cover_bp), WOUND_MIN, WOUND_MAX)
        if defender_catapults:
            strike(
                "attackers",
                chance_hits(defender_catapults * CATAPULT_HITS_BP),
                WOUND_MIN,
                WOUND_MAX,
            )
        if towers_bp:
            strike("attackers", chance_hits(towers_bp), WOUND_MIN, WOUND_MAX)
        if not standing["defenders"] and waiting:
            join_reserve()
        attack = sum(item.strength for item in standing["attackers"])
        defence = sum(item.strength for item in standing["defenders"]) * defence_bp // BASIS
        if not attack or not defence:
            broken = "attackers" if not attack else "defenders"
            break
        to_defenders = min(ROUND_LOSS_BP * attack // defence, MAX_ROUND_LOSS_BP)
        to_attackers = min(ROUND_LOSS_BP * defence // attack, MAX_ROUND_LOSS_BP)
        defender_hits = hits_for("defenders", to_defenders)
        attacker_hits = hits_for("attackers", to_attackers)
        strike("defenders", defender_hits, WOUND_MIN, WOUND_MAX)
        strike("attackers", attacker_hits, WOUND_MIN, WOUND_MAX)
        broken = breaking_side()
        if broken == "defenders" and waiting:
            # The line wavers: the reserve comes up and steadies it.
            join_reserve()
            broken = breaking_side()
    if broken is None:
        # A long stalemate: the attackers, far from home, give up the field.
        broken = "attackers"
    rate = attacker_pursuit_bp if broken == "attackers" else defender_pursuit_bp
    pursuit = -(-starting[broken] * rate // BASIS)
    if broken == "defenders" and defender_refuge:
        pursuit = 0
    winners = "defenders" if broken == "attackers" else "attackers"
    limit = CAPTIVES_PER_WINNER * len(standing[winners])
    captured: list[EntityId] = []
    for _ in range(pursuit):
        members = standing[broken]
        if not members:
            break
        # A fleeing fighter the winners catch may be taken alive instead of cut down.
        if len(captured) < limit and int(roll.integers(0, BASIS)) < CAPTURE_BP:
            taken = members.pop(int(roll.integers(0, len(members))))
            captured.append(taken.person_id)
            continue
        strike(broken, 1, ROUT_WOUND_MIN, ROUT_WOUND_MAX)
    casualties = tuple(
        Casualty(
            person_id=person_id,
            civilization_id=by_id[person_id].civilization_id,
            died=wound >= health[person_id],
            damage=wound,
        )
        for person_id, wound in sorted(damage.items())
    )
    return BattleOutcome(
        rounds=rounds,
        attackers_won=broken == "defenders",
        casualties=casualties,
        captured=tuple(sorted(captured)),
        reserve_round=reserve_round,
    )


def defence_bonus_bp(terrain: Terrain, *, settlement: bool) -> int:
    bonus = TERRAIN_DEFENCE_BP.get(terrain, BASIS)
    if settlement:
        bonus = bonus * SETTLEMENT_DEFENCE_BP // BASIS
    return bonus


def estimate(count: int) -> int:
    """What a side can tell of the enemy's numbers: to the nearest five."""
    return (count + 2) // 5 * 5
