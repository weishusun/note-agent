"""设置页的接口。密钥只进不出：读取时只回「是否已设置」和末四位。"""

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, field_validator

from app import settings
from app.extract import client

router = APIRouter()

_HINT_MIN_KEY_LENGTH = 8


class SettingsView(BaseModel):
    base_url: str
    api_key_set: bool
    # 末四位，帮用户认出存的是哪一把；密钥太短时不给
    api_key_hint: str | None
    extract_model: str
    classify_model: str
    answer_model: str
    disable_thinking: bool
    extract_disable_thinking: bool


class SettingsUpdate(BaseModel):
    """只改请求里带了的项。api_key 为空表示保持不变。"""

    model_config = ConfigDict(extra="forbid")

    base_url: str | None = None
    api_key: str | None = None
    extract_model: str | None = None
    classify_model: str | None = None
    answer_model: str | None = None
    disable_thinking: bool | None = None
    extract_disable_thinking: bool | None = None

    @field_validator("base_url", "api_key", "extract_model", "classify_model", "answer_model")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        return v.strip() if v is not None else None

    @field_validator("base_url")
    @classmethod
    def _http_url(cls, v: str | None) -> str | None:
        if v and not v.startswith(("http://", "https://")):
            raise ValueError("接口地址必须以 http:// 或 https:// 开头")
        return v


def _view(current: settings.Settings) -> SettingsView:
    key = current.api_key or ""
    return SettingsView(
        base_url=current.base_url,
        api_key_set=bool(key),
        api_key_hint=key[-4:] if len(key) >= _HINT_MIN_KEY_LENGTH else None,
        extract_model=current.extract_model or "",
        classify_model=current.classify_model or "",
        answer_model=current.answer_model or "",
        disable_thinking=current.disable_thinking,
        extract_disable_thinking=current.extract_disable_thinking,
    )


@router.get("/api/settings")
def get_settings() -> SettingsView:
    return _view(settings.load())


@router.put("/api/settings")
def put_settings(req: SettingsUpdate) -> SettingsView:
    update = {k: v for k, v in req.model_dump(exclude_unset=True).items() if v is not None}
    if not update.get("api_key"):
        update.pop("api_key", None)
    return _view(settings.save(update))


@router.post("/api/settings/test")
def test_settings() -> dict[str, dict]:
    """用当前已保存的设置各发一次真实请求：文本模型一句话，提取模型一张小图。"""
    return {"text": client.probe_text(), "vision": client.probe_vision()}
