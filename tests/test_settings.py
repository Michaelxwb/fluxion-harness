import pytest
from muad_common import SharedSettings
from pydantic import ValidationError


def test_explicit_database_url_is_used(monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    settings = SharedSettings(_env_file=None, database_url='postgresql+asyncpg://explicit/db')
    assert settings.database_url == 'postgresql+asyncpg://explicit/db'
    assert settings.require_database_url() == 'postgresql+asyncpg://explicit/db'


def test_require_database_url_raises_when_unset(monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    settings = SharedSettings(_env_file=None)
    assert settings.database_url is None
    with pytest.raises(RuntimeError):
        settings.require_database_url()


def test_env_var_takes_precedence_over_default(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'postgresql+asyncpg://env/db')
    settings = SharedSettings(_env_file=None)
    assert settings.require_database_url() == 'postgresql+asyncpg://env/db'


def test_wecom_media_max_tls_version_is_unlimited_by_default(monkeypatch):
    monkeypatch.delenv("WECOM_MEDIA_MAX_TLS_VERSION", raising=False)
    assert SharedSettings(_env_file=None).wecom_media_max_tls_version is None


def test_wecom_media_max_tls_version_empty_means_unlimited(monkeypatch):
    monkeypatch.setenv("WECOM_MEDIA_MAX_TLS_VERSION", "")
    assert SharedSettings(_env_file=None).wecom_media_max_tls_version is None


def test_wecom_media_max_tls_version_accepts_the_whitelist(monkeypatch):
    monkeypatch.setenv("WECOM_MEDIA_MAX_TLS_VERSION", "TLSv1_2")
    assert SharedSettings(_env_file=None).wecom_media_max_tls_version == "TLSv1_2"


def test_wecom_media_max_tls_version_rejects_unknown_values(monkeypatch):
    monkeypatch.setenv("WECOM_MEDIA_MAX_TLS_VERSION", "TLSv1")
    with pytest.raises(ValidationError):
        SharedSettings(_env_file=None)
