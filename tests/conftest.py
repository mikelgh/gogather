"""pytest 全局夹具：临时数据库隔离，避免污染生产数据。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """将 database 指向临时 DB 并初始化 schema。"""
    from app import database

    db_path = tmp_path / "test_plans.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    database.init_db()
    return database


@pytest.fixture(autouse=True)
def _reset_rate_limiter(monkeypatch):
    """每个测试前清空 IP 限频记录，并隔离服务器 .env 里的真实验证码配置。"""
    from app import ratelimit

    for key in (
        "ALIYUN_CAPTCHA_SCENE_ID",
        "ALIYUN_CAPTCHA_PREFIX",
        "ALIYUN_ACCESS_KEY_ID",
        "ALIYUN_ACCESS_KEY_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)

    ratelimit.reset()
    yield
    ratelimit.reset()


@pytest.fixture()
def api_client(temp_db):
    """FastAPI TestClient（依赖 temp_db 先行）。"""
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


@pytest.fixture()
def auth_headers(api_client):
    """注册一个测试用户并返回 Bearer 头。"""
    resp = api_client.post(
        "/api/register",
        json={"username": "testuser", "password": "test1234"},
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["token"]
    return {"Authorization": f"Bearer {token}"}
