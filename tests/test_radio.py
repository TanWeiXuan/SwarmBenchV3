from swarmbench import Action, Team

from .helpers import isolated_simulation, revive


def test_radio_zero_and_max_payload_one_tick_through_walls() -> None:
    simulation = isolated_simulation({(15, y) for y in range(1, 79)})
    sender = revive(simulation, Team.A, 0, (10.0, 20.0))
    receiver = revive(simulation, Team.A, 1, (20.0, 20.0))
    revive(simulation, Team.B, 0, (100.0, 20.0))
    observations = simulation.observations()
    assert not observations[receiver.key].radio
    simulation.step({sender.key: Action(broadcast=0)})
    received = simulation.observations()[receiver.key].radio
    assert [(message.sender_id, message.payload) for message in received] == [(0, 0)]
    simulation.step({sender.key: Action(broadcast=2**64 - 1)})
    assert simulation.observations()[receiver.key].radio[0].payload == 2**64 - 1


def test_radio_range_snapshot_no_self_opponent_or_same_tick_forwarding() -> None:
    simulation = isolated_simulation()
    sender = revive(simulation, Team.A, 0, (10.0, 20.0))
    boundary = revive(simulation, Team.A, 1, (40.0, 20.0))
    outside = revive(simulation, Team.A, 2, (40.0001, 20.0))
    opponent = revive(simulation, Team.B, 0, (20.0, 20.0))
    simulation.step({sender.key: Action(broadcast=7), boundary.key: Action(broadcast=9)})
    observations = simulation.observations()
    assert [message.payload for message in observations[boundary.key].radio] == [7]
    assert 7 not in [message.payload for message in observations[outside.key].radio]
    assert [message.payload for message in observations[sender.key].radio] == [9]
    assert not observations[opponent.key].radio


def test_transmitted_message_survives_sender_death_but_not_receiver_death() -> None:
    simulation = isolated_simulation()
    sender = revive(simulation, Team.A, 0, (10.0, 20.0))
    receiver = revive(simulation, Team.A, 1, (12.0, 22.0))
    enemy = revive(simulation, Team.B, 0, (20.0, 20.0), 3.141592653589793)
    sender.health = 20
    simulation.step({sender.key: Action(broadcast=55), enemy.key: Action(fire=True)})
    assert not sender.alive
    assert simulation.observations()[receiver.key].radio[0].payload == 55


def test_dead_receiver_does_not_get_a_queued_delivery() -> None:
    simulation = isolated_simulation()
    sender = revive(simulation, Team.A, 0, (10.0, 22.0))
    receiver = revive(simulation, Team.A, 1, (10.0, 20.0))
    enemy = revive(simulation, Team.B, 0, (20.0, 20.0), 3.141592653589793)
    receiver.health = 20
    simulation.step({sender.key: Action(broadcast=44), enemy.key: Action(fire=True)})
    assert not receiver.alive
    observations = simulation.observations()
    assert receiver.key not in observations
    assert not any(event["type"] == "radio_deliver" and event.get("unit_id") == receiver.unit_id for event in simulation.events)
