"""阿里云验证码 2.0 服务端二次校验（VerifyIntelligentCaptcha）。

配置驱动：.env 同时具备 ALIYUN_CAPTCHA_SCENE_ID / ALIYUN_ACCESS_KEY_ID /
ALIYUN_ACCESS_KEY_SECRET 时启用；未配置则注册流程不要求验证码（本地开发/测试）。
"""
import logging

from .config import get_env

logger = logging.getLogger(__name__)


def captcha_enabled() -> bool:
    return bool(
        get_env("ALIYUN_CAPTCHA_SCENE_ID")
        and get_env("ALIYUN_ACCESS_KEY_ID")
        and get_env("ALIYUN_ACCESS_KEY_SECRET")
    )


def _extract_verify_result(body) -> bool:
    """统一取出校验结果。实测响应体为 {'Result': {'VerifyResult': True}} 嵌套 dict，
    SDK 文档的 Tea 模型则是 .verification_result.verify_result，两种形态都兼容。"""
    if body is None:
        return False
    if isinstance(body, dict):
        for outer in ("Result", "result", "VerificationResult", "verification_result"):
            node = body.get(outer)
            if isinstance(node, dict):
                for inner in ("VerifyResult", "verify_result"):
                    v = node.get(inner)
                    if v is not None:
                        return bool(v)
        return False
    for attr in ("verification_result", "result"):
        node = getattr(body, attr, None)
        if node is not None:
            v = getattr(node, "verify_result", None)
            if v is None and isinstance(node, dict):
                v = node.get("VerifyResult")
            if v is not None:
                return bool(v)
    return False


def _verify_with_sdk(captcha_verify_param: str) -> bool:
    from alibabacloud_captcha20230305.client import Client
    from alibabacloud_captcha20230305 import models as captcha_models
    from alibabacloud_tea_openapi import models as open_api_models

    config = open_api_models.Config(
        access_key_id=get_env("ALIYUN_ACCESS_KEY_ID"),
        access_key_secret=get_env("ALIYUN_ACCESS_KEY_SECRET"),
        endpoint=f"captcha.{get_env('ALIYUN_CAPTCHA_REGION', 'cn-shanghai')}.aliyuncs.com",
    )
    client = Client(config)
    request = captcha_models.VerifyIntelligentCaptchaRequest(
        scene_id=get_env("ALIYUN_CAPTCHA_SCENE_ID"),
        captcha_verify_param=captcha_verify_param,
    )
    resp = client.verify_intelligent_captcha(request)
    ok = _extract_verify_result(resp.body)
    if not ok:
        logger.warning("验证码校验未通过，响应体: %s", resp.body)
    return ok


def verify_captcha(captcha_verify_param: str) -> bool:
    """校验前端提交的 captchaVerifyParam。任何异常一律判失败（fail-closed）。"""
    if not captcha_verify_param:
        return False
    try:
        return _verify_with_sdk(captcha_verify_param)
    except ImportError:
        logger.error("aliyun captcha SDK 未安装，验证码校验不可用（按未通过处理）")
        return False
    except Exception as e:
        logger.warning("验证码校验失败: %s", e)
        return False
