"""Общие фикстуры: изолированная SQLite-БД на каждый тест."""
import pytest


@pytest.fixture()
def db(tmp_path):
    from app import db as dbm

    dbm.configure_engine(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    dbm.init_db()
    yield dbm
    dbm.dispose_engine()
