"""Alipay OAuth helper: RSA2 signing and API calls."""
import json
import base64
from urllib.parse import urlencode
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.backends import default_backend


def _load_private_key(pem_str: str):
    """Load RSA private key from PEM string (with or without headers)."""
    pem = pem_str.strip()
    if not pem.startswith("-----"):
        pem = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            + "\n".join(pem[i:i+64] for i in range(0, len(pem), 64))
            + "\n-----END RSA PRIVATE KEY-----"
        )
    return serialization.load_pem_private_key(pem.encode(), password=None, backend=default_backend())


def _rsa2_sign(content: str, private_key_pem: str) -> str:
    """Sign content with RSA2 (SHA256WithRSA)."""
    key = _load_private_key(private_key_pem)
    signature = key.sign(
        content.encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("utf-8")


def build_auth_url(app_id: str, redirect_uri: str, state: str) -> str:
    """Build Alipay authorization URL."""
    params = {
        "app_id": app_id,
        "scope": "auth_user",
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return f"https://openauth.alipay.com/oauth2/publicAppAuthorize.htm?{urlencode(params)}"


def build_token_params(app_id: str, auth_code: str, private_key_pem: str) -> dict:
    """Build parameters for alipay.system.oauth.token API call."""
    params = {
        "app_id": app_id,
        "method": "alipay.system.oauth.token",
        "charset": "utf-8",
        "sign_type": "RSA2",
        "timestamp": _now_str(),
        "version": "1.0",
        "grant_type": "authorization_code",
        "code": auth_code,
    }
    sign_content = "&".join(f"{k}={params[k]}" for k in sorted(params))
    params["sign"] = _rsa2_sign(sign_content, private_key_pem)
    return params


def build_userinfo_params(app_id: str, access_token: str, private_key_pem: str) -> dict:
    """Build parameters for alipay.user.info.share API call."""
    params = {
        "app_id": app_id,
        "method": "alipay.user.info.share",
        "charset": "utf-8",
        "sign_type": "RSA2",
        "timestamp": _now_str(),
        "version": "1.0",
        "auth_token": access_token,
    }
    sign_content = "&".join(f"{k}={params[k]}" for k in sorted(params))
    params["sign"] = _rsa2_sign(sign_content, private_key_pem)
    return params


def _now_str() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
