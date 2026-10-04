"""節日資料(docs/HOLIDAYS_SD.md):產生器分類、資料集版本、讀端點 known_version 契約。"""
from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.services.holidays import dataset
from src.services.holidays.generator import generate_country_year


def _by(entries, d: str):
    return [e for e in entries if e.date.isoformat() == d]


def test_tw_2026_mid_autumn_and_national_day():
    tw = generate_country_year("TW", 2026)
    mid = _by(tw, "2026-09-25")
    assert [(e.key, e.kind) for e in mid] == [("mid_autumn", "public")]
    assert mid[0].name_zh_tw == "中秋節"
    assert mid[0].emoji == "🥮"
    assert ("tw_national_day", "public") in [(e.key, e.kind) for e in _by(tw, "2026-10-10")]


def test_tw_observed_becomes_day_off_with_base_name():
    tw = generate_country_year("TW", 2026)
    observed = _by(tw, "2026-10-09")
    assert [(e.key, e.kind, e.name_zh_tw) for e in observed] == [("day_off", "day_off", "國慶日補假")]


def test_exact_festival_extra_lib_day_is_downgraded():
    """套件把 2026-02-15 也標成除夕;正日是 02-16,02-15 要降級成連假,不能出現兩個除夕。"""
    tw = generate_country_year("TW", 2026)
    eves = [e for e in tw if e.key == "lunar_new_year_eve"]
    assert [e.date.isoformat() for e in eves] == ["2026-02-16"]
    feb15 = _by(tw, "2026-02-15")
    assert [(e.key, e.name_zh_tw) for e in feb15] == [("day_off", "除夕連假")]


def test_observances_from_catalog():
    tw = generate_country_year("TW", 2026)
    keys = {(e.date.isoformat(), e.key): e.kind for e in tw}
    assert keys[("2026-08-19", "qixi")] == "observance"  # 農曆 7/7
    assert keys[("2026-05-10", "mothers_day")] == "observance"  # 5 月第 2 個週日
    assert keys[("2026-12-25", "christmas")] == "observance"  # 台灣聖誕節不放假
    us = generate_country_year("US", 2026)
    us_keys = {(e.date.isoformat(), e.key): e.kind for e in us}
    assert us_keys[("2026-11-26", "thanksgiving")] == "public"
    assert us_keys[("2026-11-27", "black_friday")] == "observance"
    assert us_keys[("2026-12-25", "christmas")] == "public"


def test_kr_chuseok_shares_mid_autumn_key():
    kr = generate_country_year("KR", 2026)
    assert [(e.key, e.kind) for e in _by(kr, "2026-09-25")] == [("mid_autumn", "public")]


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def test_target_years_include_next_year_from_november():
    assert dataset.target_years(date(2026, 10, 31)) == [2025, 2026]
    assert dataset.target_years(date(2026, 11, 1)) == [2025, 2026, 2027]


def test_refresh_is_idempotent_and_bumps_version_on_new_year():
    TS = _session()
    with TS() as db:
        first = dataset.refresh_dataset(db, today=date(2026, 10, 5))
        assert first["version"] == 1
        assert first["changed_years"] == "2025,2026"
        again = dataset.refresh_dataset(db, today=date(2026, 10, 6))
        assert again["version"] == 1
        assert again["changed_years"] == "-"
        nov = dataset.refresh_dataset(db, today=date(2026, 11, 1))
        assert nov["version"] == 2
        assert nov["changed_years"] == "2027"
        assert {r.year for r in dataset.list_entries(db)} == {2025, 2026, 2027}


def _client():
    TS = _session()

    def override():
        db = TS()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    return TestClient(app)


def _login_web(client, email):
    client.post("/api/v1/auth/register", json={"email": email, "password": "Pa$$word1!"})
    r = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "Pa$$word1!",
            "device_id": "d-web",
            "client_type": "web",
            "device_name": "pytest-web",
            "platform": "test",
        },
    )
    return r.json()["access_token"]


def test_read_holidays_known_version_returns_unchanged():
    client = _client()
    try:
        hdr = {"Authorization": f"Bearer {_login_web(client, 'holiday1@t.com')}"}
        r = client.get("/api/v1/read/holidays", headers=hdr, params={"countries": "TW,jp"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["unchanged"] is False
        assert body["version"] >= 1
        assert {e["country"] for e in body["entries"]} == {"TW", "JP"}
        assert body["countries"] == ["TW", "CN", "HK", "JP", "KR", "US"]

        again = client.get(
            "/api/v1/read/holidays", headers=hdr, params={"known_version": body["version"]}
        )
        assert again.status_code == 200
        assert again.json()["unchanged"] is True
        assert again.json()["entries"] == []
    finally:
        app.dependency_overrides.clear()


def test_read_holidays_rejects_unknown_country():
    client = _client()
    try:
        hdr = {"Authorization": f"Bearer {_login_web(client, 'holiday2@t.com')}"}
        r = client.get("/api/v1/read/holidays", headers=hdr, params={"countries": "XX"})
        assert r.status_code == 422
    finally:
        app.dependency_overrides.clear()
