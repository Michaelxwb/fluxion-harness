import pytest
from muad_common import SharedSettings


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
