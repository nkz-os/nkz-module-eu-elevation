"""Connection URLs must not leak credentials into logs."""
from app.common.redact_url import redact_url_credentials


def test_hides_password_only_userinfo():
    out = redact_url_credentials("redis://:secret@host:6379/0")
    assert "secret" not in out
    assert out == "redis://***@host:6379/0"


def test_hides_user_and_password():
    out = redact_url_credentials("redis://user:secret@host:6379/0")
    assert "secret" not in out
    assert "user" not in out
    assert out == "redis://***@host:6379/0"


def test_passwordless_url_unchanged():
    assert redact_url_credentials("redis://host:6379/0") == "redis://host:6379/0"


def test_password_with_at_sign_fully_hidden():
    out = redact_url_credentials("redis://:p@ss@host:6379/1")
    assert "p@ss" not in out and "ss@" not in out
    assert out == "redis://***@host:6379/1"
