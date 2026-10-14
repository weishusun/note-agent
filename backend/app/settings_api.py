"""设置页的接口。密钥只进不出：读取时只回「是否已设置」和末四位。"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, field_validator

from app import settings
from app.extract import client

router = APIRouter()

_HINT_MIN_KEY_LENGTH = 8


def _http_url(v: str | None) -> str | None:
    if v and not v.startswith(("http://", "https://")):
        raise ValueError("接口地址必须以 http:// 或 https:// 开头")
    return v


def _known_provider(provider: str) -> None:
    if provider != settings.CUSTOM and settings.provider_info(provider) is None:
        raise HTTPException(status_code=400, detail=f"不认识的服务商：{provider}")


# ---------- 服务商清单 ----------


class ProviderView(BaseModel):
    id: str
    name: str
    base_url: str
    key_url: str | None
    key_hint: str | None
    key_pattern: str | None
    model_list: str
    verified: bool
    models: dict[str, list[str]]
    note: str | None


@router.get("/api/providers")
def get_providers() -> list[ProviderView]:
    return [ProviderView.model_validate(p.model_dump()) for p in settings.catalog()]


class ModelsRequest(BaseModel):
    """不带 api_key 就用已保存的；自定义服务商要带 base_url（或已保存过）。"""

    model_config = ConfigDict(extra="forbid")

    api_key: str | None = None
    base_url: str | None = None

    @field_validator("api_key", "base_url")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        return v.strip() if v is not None else None

    @field_validator("base_url")
    @classmethod
    def _check_url(cls, v: str | None) -> str | None:
        return _http_url(v)


@router.post("/api/providers/{provider}/models")
def list_provider_models(provider: str, req: ModelsRequest) -> dict:
    """用这家的 Key 去问它有哪些模型。Key 只发给这一家。不保存任何东西。"""
    _known_provider(provider)
    saved = settings.load().providers.get(provider)
    info = settings.provider_info(provider)
    base_url = info.base_url if info else (req.base_url or (saved.base_url if saved else None))
    api_key = req.api_key or (saved.api_key if saved else None)
    if not base_url:
        raise HTTPException(status_code=400, detail="自定义服务商要先填接口地址")
    if not api_key:
        raise HTTPException(status_code=400, detail="还没有填 API Key")
    return client.list_models(base_url, api_key)


# ---------- 设置 ----------


class AccountView(BaseModel):
    base_url: str
    api_key_set: bool
    # 末四位，帮用户认出存的是哪一把；密钥太短时不给
    api_key_hint: str | None


class ChoiceView(BaseModel):
    provider: str
    model: str


class SettingsView(BaseModel):
    providers: dict[str, AccountView]
    extract: ChoiceView
    classify: ChoiceView
    answer: ChoiceView
    embed: ChoiceView
    disable_thinking: bool
    extract_disable_thinking: bool


class AccountUpdate(BaseModel):
    """api_key 为空表示保持不变；base_url 只对自定义服务商有意义。"""

    model_config = ConfigDict(extra="forbid")

    api_key: str | None = None
    base_url: str | None = None

    @field_validator("api_key", "base_url")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        return v.strip() if v is not None else None

    @field_validator("base_url")
    @classmethod
    def _check_url(cls, v: str | None) -> str | None:
        return _http_url(v)


class ChoiceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str | None = None
    model: str | None = None

    @field_validator("provider", "model")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        return v.strip() if v is not None else None


class SettingsUpdate(BaseModel):
    """只改请求里带了的项。providers 里某家给 null 表示删掉这家（连同密钥）。"""

    model_config = ConfigDict(extra="forbid")

    providers: dict[str, AccountUpdate | None] | None = None
    extract: ChoiceUpdate | None = None
    classify: ChoiceUpdate | None = None
    answer: ChoiceUpdate | None = None
    embed: ChoiceUpdate | None = None
    disable_thinking: bool | None = None
    extract_disable_thinking: bool | None = None


def _view(current: settings.Settings) -> SettingsView:
    accounts = {}
    for provider, account in current.providers.items():
        key = account.api_key or ""
        accounts[provider] = AccountView(
            base_url=account.base_url,
            api_key_set=bool(key),
            api_key_hint=key[-4:] if len(key) >= _HINT_MIN_KEY_LENGTH else None,
        )

    def choice(role: settings.RoleName) -> ChoiceView:
        picked = getattr(current, role)
        provider = picked.provider or ""
        # 只配了一家时，没写服务商的环节就是用它
        if not provider and picked.model and len(current.providers) == 1:
            provider = next(iter(current.providers))
        return ChoiceView(provider=provider, model=picked.model or "")

    return SettingsView(
        providers=accounts,
        extract=choice("extract"),
        classify=choice("classify"),
        answer=choice("answer"),
        embed=choice("embed"),
        disable_thinking=current.disable_thinking,
        extract_disable_thinking=current.extract_disable_thinking,
    )


@router.get("/api/settings")
def get_settings() -> SettingsView:
    return _view(settings.load())


@router.put("/api/settings")
def put_settings(req: SettingsUpdate) -> SettingsView:
    update = req.model_dump(exclude_unset=True)
    current = settings.load()
    for provider, account in (update.get("providers") or {}).items():
        _known_provider(provider)
        if account is None:
            continue
        if provider == settings.CUSTOM and not account.get("base_url"):
            if settings.CUSTOM not in current.providers:
                raise HTTPException(status_code=400, detail="自定义服务商要填接口地址")
        if provider != settings.CUSTOM:
            account.pop("base_url", None)
        # 密钥留空 = 不改
        if not account.get("api_key"):
            account.pop("api_key", None)
    for role in settings.ROLES:
        picked = update.get(role)
        if picked and picked.get("provider"):
            _known_provider(picked["provider"])
    return _view(settings.save(update))


@router.post("/api/settings/test")
def test_settings() -> dict[str, dict]:
    """用当前已保存的设置各发一次真实请求：文字模型一句话，看图模型一张小图，向量模型一句话。

    问答模型单独选了才测它；向量模型没选不测。没测的项不出现在结果里。
    """
    current = settings.load()
    result = {"text": client.probe_text(), "vision": client.probe_vision()}
    if current.answer.model:
        result["answer"] = client.probe_answer()
    if current.embed_model:
        result["embedding"] = client.probe_embedding()
    return result
