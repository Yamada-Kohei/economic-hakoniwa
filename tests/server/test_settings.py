from server.settings import BASE_DIR, database_from_url


def test_sqlite_url():
    db = database_from_url("sqlite:///db.sqlite3")
    assert db["ENGINE"].endswith("sqlite3")
    assert db["NAME"] == BASE_DIR / "db.sqlite3"


def test_postgres_url():
    db = database_from_url("postgres://user:p%40ss@db.example:5433/hakoniwa")
    assert db == {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "hakoniwa",
        "USER": "user",
        "PASSWORD": "p@ss",
        "HOST": "db.example",
        "PORT": "5433",
    }
