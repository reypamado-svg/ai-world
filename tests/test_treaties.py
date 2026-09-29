from logistics_helpers import move_home

from sovereign_world.commands import (
    CommandEnvelope,
    DirectOrder,
    DirectOrderKind,
    validate_envelope,
)
from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.diplomacy import Contact, DiplomaticMessage, TreatyKind, TreatyOffer
from sovereign_world.engine import advance_day
from sovereign_world.ids import EntityId
from sovereign_world.rng import StableRng
from sovereign_world.state import build_initial_state


def _state():
    return build_initial_state(
        RunManifest.new(config=WorldConfig(seed=21, width=48, height=48), engine_version="0.1.0")
    )


def test_acceptance_cannot_activate_a_treaty_until_it_reaches_the_proposer() -> None:
    state = _state()
    proposer, acceptor = sorted(state.civilizations)[:2]
    ambassador = state.civilizations[acceptor].population.living_ids[0]
    origin = state.civilizations[acceptor].population.people[ambassador].location
    offer = TreatyOffer(
        offer_id=EntityId("treaty:peace"),
        proposer_civilization_id=proposer,
        recipient_civilization_id=acceptor,
        kind=TreatyKind.PEACE,
        proposed_day=0,
    )
    state.treaty_offers = (offer,)
    state.diplomatic_missions = (
        DiplomaticMessage(
            message_id=EntityId("message:acceptance"),
            sender_civilization_id=acceptor,
            recipient_civilization_id=proposer,
            ambassador_id=ambassador,
            route=(origin,),
            source_text="We accept peace.",
            departed_day=0,
            acceptance_of=offer.offer_id,
        ),
    )

    assert state.active_treaties == ()
    result = advance_day(state, StableRng(state.config.seed))
    for _ in range(10):
        if result.state.active_treaties:
            break
        result = advance_day(result.state, StableRng(result.state.config.seed))

    treaty = result.state.active_treaties[0]
    assert treaty.treaty_id == offer.offer_id
    assert treaty.kind is TreatyKind.PEACE
    assert "treaty_activated" in {event.kind for event in result.events.events}


def test_acceptance_requires_a_delivered_offer() -> None:
    state = _state()
    acceptor, proposer = sorted(state.civilizations)[:2]
    ambassador = state.civilizations[acceptor].population.living_ids[0]
    origin = state.civilizations[acceptor].population.people[ambassador].location
    destination = state.world_map.neighbors(origin)[0]
    state.civilizations[acceptor].contacts = (
        Contact(
            civilization_id=proposer,
            settlement=destination,
            first_contact_day=0,
            last_seen_day=0,
        ),
    )
    move_home(state.civilizations[proposer], destination)
    envelope = CommandEnvelope(
        schema_version=1,
        civilization_id=acceptor,
        council_day=0,
        correlation_id="report:0",
        commands=(
            DirectOrder(
                command_id="accept:unknown",
                kind=DirectOrderKind.ACCEPT_TREATY,
                message_id=EntityId("message:accept"),
                treaty_id=EntityId("treaty:unknown"),
                ambassador_id=ambassador,
                recipient_civilization_id=proposer,
                message_text="We accept.",
                route=(origin, destination),
            ),
        ),
    )

    result = validate_envelope(envelope, state)

    assert result.accepted == ()
    assert result.errors[0].code == "unknown_treaty"
