from swarmbench.controllers.baselines.radio_rush import pack_contact, unpack_contact


def test_contact_packet_is_exactly_eight_bytes_and_stale_reports_are_ignored() -> None:
    payload = pack_contact(7, 119.99, 79.99, 1200)
    assert len(payload.to_bytes(8, "little")) == 8
    assert unpack_contact(payload, 1200) == (7, 119.99, 79.99, 1200)
    assert unpack_contact(payload, 1231) is None
    assert unpack_contact(0, 1) is None
