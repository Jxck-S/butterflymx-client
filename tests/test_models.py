import string

import pytest

from butterflymx import Access, Call, Door, Message
from butterflymx.utils import generate_code_challenge, generate_code_verifier


def test_code_challenge_matches_rfc7636_example():
    # Appendix B of RFC 7636
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert generate_code_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_code_verifier_is_valid():
    v = generate_code_verifier()
    assert len(v) == 128
    assert set(v) <= set(string.ascii_letters + string.digits + "-._~")
    assert generate_code_verifier() != v


@pytest.mark.parametrize("length", [42, 129])
def test_code_verifier_rejects_bad_length(length):
    with pytest.raises(ValueError):
        generate_code_verifier(length)


def test_message_missing_nested_fields():
    m = Message({"id": "m", "body": "hi", "origin": "VISITOR"})
    assert m.source == "Unknown"
    assert m.visitor_name == "VISITOR"
    assert m.image_url is None
    assert "hi" in repr(m)


def test_call_missing_device():
    c = Call({"id": "c", "displayStatus": "MISSED", "device": None})
    assert c.device == "Unknown"
    assert c.status == "MISSED"
    assert "MISSED" in repr(c)


def test_access_missing_nested_fields():
    a = Access({"id": "a", "type": "VISITOR"})
    assert a.door_name == "Unknown"
    assert a.device_name == "Unknown"
    assert "VISITOR" in repr(a)


def test_door_repr_shows_status():
    d = Door({"id": "d", "name": "Lobby", "online": False}, tenant_id="t", client=None)
    assert "Offline" in repr(d)
    assert d.building_name == "Unknown"
