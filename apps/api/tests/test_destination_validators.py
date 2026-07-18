from __future__ import annotations

import pytest

from app.services.destination_validators import validate_e164_phone, validate_sip_uri


@pytest.mark.parametrize(
    'value',
    [
        'sip:agent@example.com',
        'sips:agent@example.com',
        'sip:agent@example.com:5060',
        'sip:agent@127.0.0.1:5060',
        'sip:agent@[2001:db8::1]:5060',
        'sip:agent@example.com;transport=tcp',
    ],
)
def test_validate_sip_uri_accepts_valid(value: str):
    assert validate_sip_uri(value) == value


@pytest.mark.parametrize(
    'value',
    [
        '',
        'agent@example.com',
        'http://example.com',
        '+12125550123',
        'sip:@example.com',
        'sip:agent@',
        'sip:agent@exa mple.com',
    ],
)
def test_validate_sip_uri_rejects_invalid(value: str):
    with pytest.raises(ValueError):
        validate_sip_uri(value)


@pytest.mark.parametrize('value', ['+12125550123', '+442071838750', '+33123456789'])
def test_validate_e164_accepts_valid(value: str):
    assert validate_e164_phone(value) == value


@pytest.mark.parametrize(
    'value',
    [
        '+1212555',
        '+1',
        'sip:agent@example.com',
        '+1 (212) 555-0123',
        '12125550123',
        '',
        '+0123',
    ],
)
def test_validate_e164_rejects_invalid(value: str):
    with pytest.raises(ValueError):
        validate_e164_phone(value)
