from unittest.mock import patch

import pytest
from fastapi import HTTPException

from open_webui.routers import connectors


class FakeUser:
    def __init__(self, email):
        self.email = email


@pytest.mark.parametrize(
    'email,expected',
    [
        ('someone@keepersolutions.com', True),
        ('SOMEONE@KeeperSolutions.COM', True),  # domain match is case-insensitive
        ('someone@gmail.com', False),
        ('someone@notkeepersolutions.com', False),  # not a suffix match, a different domain entirely
        ('', False),
        (None, False),
    ],
)
def test_is_internal_email(email, expected):
    with patch.object(connectors, 'INTERNAL_EMAIL_DOMAINS', ['keepersolutions.com']):
        assert connectors.is_internal_email(email) is expected


def test_is_internal_email_with_no_domains_configured():
    # Mirrors a deployment that never set INTERNAL_EMAIL_DOMAINS - nobody should pass
    with patch.object(connectors, 'INTERNAL_EMAIL_DOMAINS', []):
        assert connectors.is_internal_email('someone@keepersolutions.com') is False


def test_get_internal_drive_user_allows_internal_accounts():
    with patch.object(connectors, 'INTERNAL_EMAIL_DOMAINS', ['keepersolutions.com']):
        user = FakeUser('someone@keepersolutions.com')
        assert connectors.get_internal_drive_user(user=user) is user


def test_get_internal_drive_user_rejects_external_accounts_with_403():
    with patch.object(connectors, 'INTERNAL_EMAIL_DOMAINS', ['keepersolutions.com']):
        with pytest.raises(HTTPException) as exc_info:
            connectors.get_internal_drive_user(user=FakeUser('someone@gmail.com'))

    assert exc_info.value.status_code == 403
