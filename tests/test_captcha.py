"""注册防机器人：阿里云验证码开关校验 + IP 限频。"""
from app import captcha as captcha_mod
from app import ratelimit


def test_extract_verify_result_real_shapes():
    """实测阿里云返回 {'Result': {'VerifyResult': True}}；文档 Tea 模型为 verification_result。"""
    f = captcha_mod._extract_verify_result
    assert f({"Result": {"CertifyId": "x", "VerifyCode": "T001", "VerifyResult": True}}) is True
    assert f({"Result": {"VerifyCode": "F001", "VerifyResult": False}}) is False
    assert f({"verification_result": {"verify_result": True}}) is True
    assert f({"VerificationResult": {"VerifyResult": False}}) is False
    assert f(None) is False
    assert f({}) is False
    assert f("garbage") is False

    class _Node:
        pass

    node = _Node()
    node.verify_result = True
    body = _Node()
    body.verification_result = node
    assert f(body) is True


def _enable_captcha(monkeypatch, verify_result=True):
    """打开验证码配置并把 SDK 调用替换为可控桩。"""
    monkeypatch.setenv("ALIYUN_CAPTCHA_SCENE_ID", "scene-test")
    monkeypatch.setenv("ALIYUN_CAPTCHA_PREFIX", "prefix-test")
    monkeypatch.setenv("ALIYUN_ACCESS_KEY_ID", "ak-test")
    monkeypatch.setenv("ALIYUN_ACCESS_KEY_SECRET", "sk-test")
    monkeypatch.setattr(captcha_mod, "_verify_with_sdk", lambda p: verify_result)


def test_register_ok_when_captcha_disabled(api_client):
    r = api_client.post("/api/register", json={"username": "u1", "password": "pass1234"})
    assert r.status_code == 200


def test_register_rejected_without_ticket_when_enabled(api_client, monkeypatch):
    _enable_captcha(monkeypatch)
    r = api_client.post("/api/register", json={"username": "u1", "password": "pass1234"})
    assert r.status_code == 403


def test_register_rejected_with_bad_ticket(api_client, monkeypatch):
    _enable_captcha(monkeypatch, verify_result=False)
    r = api_client.post("/api/register", json={
        "username": "u1", "password": "pass1234", "captcha_verify_param": "fake-ticket"})
    assert r.status_code == 403


def test_register_ok_with_valid_ticket(api_client, monkeypatch):
    _enable_captcha(monkeypatch, verify_result=True)
    r = api_client.post("/api/register", json={
        "username": "u1", "password": "pass1234", "captcha_verify_param": "real-ticket"})
    assert r.status_code == 200


def test_verify_captcha_fail_closed_on_empty_param():
    assert captcha_mod.verify_captcha("") is False


def test_public_config_reports_captcha_state(api_client, monkeypatch):
    r = api_client.get("/api/public-config")
    assert r.status_code == 200
    assert r.json() == {"captcha_enabled": False, "captcha_scene_id": "", "captcha_prefix": ""}

    _enable_captcha(monkeypatch)
    r = api_client.get("/api/public-config")
    assert r.json() == {"captcha_enabled": True, "captcha_scene_id": "scene-test", "captcha_prefix": "prefix-test"}


def test_register_rate_limited_per_ip(api_client):
    for i in range(ratelimit.REGISTER_LIMIT):
        r = api_client.post("/api/register", json={"username": f"u{i}", "password": "pass1234"})
        assert r.status_code == 200
    r = api_client.post("/api/register", json={"username": "over", "password": "pass1234"})
    assert r.status_code == 429
    assert "频繁" in r.json()["detail"]


def test_login_rate_limited_per_ip(api_client):
    api_client.post("/api/register", json={"username": "lu", "password": "pass1234"})
    for _ in range(ratelimit.LOGIN_LIMIT):
        r = api_client.post("/api/login", json={"username": "lu", "password": "wrong"})
        assert r.status_code == 401
    r = api_client.post("/api/login", json={"username": "lu", "password": "pass1234"})
    assert r.status_code == 429
    assert "频繁" in r.json()["detail"]


def test_rate_limit_window_expires(monkeypatch):
    ratelimit.reset()
    monkeypatch.setattr(ratelimit, "REGISTER_LIMIT", 1)
    assert ratelimit.hit("ip:x", 1, 3600) is True
    assert ratelimit.hit("ip:x", 1, 3600) is False
    # 模拟窗口滑过：手动把记录时间拨回窗口之外
    with ratelimit._lock:
        ratelimit._store["ip:x"][0] -= 3601
    assert ratelimit.hit("ip:x", 1, 3600) is True
