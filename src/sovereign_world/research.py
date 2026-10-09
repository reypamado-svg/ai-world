"""Research: scholars at home work toward new knowledge, point by point.

Every world knows the military topics; rules version 2 adds the civil ones."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from sovereign_world.capabilities import CapabilityId, CapabilityRecord
from sovereign_world.ids import EntityId

POINTS_PER_SCHOLAR = 1
WRITING_BONUS = 5
"""Extra points a day from each scholar who practises writing."""
DISCOVERED_SKILL = 100
"""The skill a scholar holds in what they discovered."""
MAX_RESEARCH_DAYS = 180
LOGISTICS_CARRY = 60
"""What a fighter can bear once military logistics is known."""
DOCTRINE_DRILL_CAP = 30
"""Drill doctrine raises what drill alone can teach."""


@dataclass(frozen=True, slots=True)
class Topic:
    capability: CapabilityId
    cost: int
    requires: tuple[CapabilityId, ...] = ()


MILITARY_TOPICS: dict[CapabilityId, Topic] = {
    topic.capability: topic
    for topic in (
        Topic(CapabilityId.ARCHERY, 300, (CapabilityId.TIMBERCRAFT,)),
        Topic(CapabilityId.SPEAR_FORMATIONS, 200),
        Topic(CapabilityId.DRILL_DOCTRINE, 250),
        Topic(CapabilityId.BRONZE_WORKING, 400, (CapabilityId.METALLURGY_AWARENESS,)),
        Topic(
            CapabilityId.SIEGECRAFT,
            400,
            (CapabilityId.TIMBERCRAFT, CapabilityId.STONEWORKING),
        ),
        Topic(CapabilityId.MILITARY_LOGISTICS, 300),
        Topic(CapabilityId.FORTIFICATION, 400, (CapabilityId.STONEWORKING,)),
    )
}
"""What can be researched, what it costs, and what must already be known."""
CIVIL_TOPICS: dict[CapabilityId, Topic] = {
    topic.capability: topic
    for topic in (
        Topic(CapabilityId.WRITING, 250),
        Topic(CapabilityId.IRRIGATION, 300, (CapabilityId.CULTIVATION,)),
        Topic(CapabilityId.HERBAL_CARE, 200),
        Topic(CapabilityId.FISHING, 150),
        Topic(CapabilityId.SURVEYING, 300, (CapabilityId.WRITING,)),
        Topic(
            CapabilityId.ORGANIZED_LOGISTICS,
            400,
            (CapabilityId.WRITING, CapabilityId.SURVEYING),
        ),
    )
}
"""Rules version 2: the arts of a settled people."""
TOPICS: dict[CapabilityId, Topic] = {**MILITARY_TOPICS, **CIVIL_TOPICS}


class ResearchAssignment(BaseModel):
    """Scholars working on one topic for a number of days; they do no other work."""

    model_config = ConfigDict(frozen=True)

    assignment_id: EntityId
    topic: CapabilityId
    scholar_ids: tuple[EntityId, ...]
    started_day: int = Field(ge=0)
    days: int = Field(ge=1, le=MAX_RESEARCH_DAYS)
    days_done: int = Field(default=0, ge=0)

    @property
    def active(self) -> bool:
        return self.days_done < self.days


def knows(records: tuple[CapabilityRecord, ...], capability: CapabilityId) -> bool:
    return any(record.capability is capability for record in records)


def research_error(
    topic: CapabilityId, records: tuple[CapabilityRecord, ...], *, civil: bool = False
) -> str | None:
    """Why this civilization cannot research the topic now, if it cannot."""
    if topic not in MILITARY_TOPICS and not (civil and topic in CIVIL_TOPICS):
        return "not a research topic"
    if knows(records, topic):
        return "already known"
    missing = [need for need in TOPICS[topic].requires if not knows(records, need)]
    if missing:
        return "needs " + ", ".join(sorted(need.value for need in missing))
    return None
