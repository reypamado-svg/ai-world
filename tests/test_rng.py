from sovereign_world.ids import IdAllocator
from sovereign_world.rng import StableRng


def test_named_stream_repeats_exact_sequence() -> None:
    rng = StableRng(912)

    first = rng.stream("weather").integers(0, 1_000_000, size=8).tolist()
    second = rng.stream("weather").integers(0, 1_000_000, size=8).tolist()

    assert first == second


def test_streams_are_independent_of_request_order() -> None:
    first_rng = StableRng(912)
    weather_first = first_rng.stream("weather").integers(0, 1_000_000, size=8).tolist()
    people_second = first_rng.stream("people").integers(0, 1_000_000, size=8).tolist()

    second_rng = StableRng(912)
    people_first = second_rng.stream("people").integers(0, 1_000_000, size=8).tolist()
    weather_second = second_rng.stream("weather").integers(0, 1_000_000, size=8).tolist()

    assert weather_first == weather_second
    assert people_first == people_second
    assert weather_first != people_first


def test_id_allocator_uses_namespace_and_monotonic_sequence() -> None:
    people = IdAllocator("person")
    projects = IdAllocator("project")

    assert str(people.allocate()) == "person:0000000001"
    assert str(people.allocate()) == "person:0000000002"
    assert str(projects.allocate()) == "project:0000000001"


def test_id_allocator_can_resume_from_persisted_sequence() -> None:
    allocator = IdAllocator("person", next_sequence=18)

    assert str(allocator.allocate()) == "person:0000000018"
    assert allocator.next_sequence == 19

