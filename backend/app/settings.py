"""运行时可改的模型设置：用哪几家服务商（各自的密钥），以及每个环节用哪家的哪个模型。

每个环节可以用不同的服务商，比如文字用 DeepSeek、看图和向量用智谱。服务商的清单和推荐模型
在 app/providers.yaml（代码里不写模型名）。

来源有两层：note-agent/.env 给默认值（只有一家：GLM_BASE_URL + ZHIPU_API_KEY），
设置文件（设置页保存的，位置见 settings_path）逐项覆盖。每次调用模型前现读，改完立即生效，不用重启。
设置文件里有密钥，不放在 vault 里（vault 是会被提交的），也不入 git。

旧版设置文件是一层平铺的（base_url、api_key、extract_model…），读的时候自动换算成新格式，
下次保存时写成新格式。
"""

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal
from uuid import uuid4

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app import config

RoleName = Literal["extract", "classify", "answer", "embed"]
ROLES: tuple[RoleName, ...] = ("extract", "classify", "answer", "embed")
# 每个环节需要哪一类模型（providers.yaml 里 models 的键）
ROLE_KIND: dict[RoleName, str] = {
    "extract": "vision",
    "classify": "text",
    "answer": "text",
    "embed": "embed",
}
ROLE_LABEL: dict[RoleName, str] = {
    "extract": "看图模型",
    "classify": "文字模型",
    "answer": "问答模型",
    "embed": "向量模型",
}
# 清单之外、使用者自己填地址的服务商
CUSTOM = "custom"

_LEGACY_KEYS = ("base_url", "api_key", "extract_model", "classify_model", "answer_model", "embed_model")


# ---------- 服务商清单（providers.yaml） ----------


class ProviderInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    base_url: str
    key_url: str | None = None
    key_hint: str | None = None
    key_pattern: str | None = None
    no_thinking: dict | None = None
    model_list: Literal["all", "text"] = "all"
    verified: bool = False
    models: dict[str, list[str]] = Field(default_factory=dict)
    note: str | None = None


@lru_cache(maxsize=1)
def catalog() -> tuple[ProviderInfo, ...]:
    path = Path(__file__).with_name("providers.yaml")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return tuple(ProviderInfo.model_validate(item) for item in data["providers"])


def provider_info(provider_id: str) -> ProviderInfo | None:
    return next((p for p in catalog() if p.id == provider_id), None)


def _normalize_url(url: str) -> str:
    return url.strip().rstrip("/").lower()


def provider_for_url(base_url: str) -> str | None:
    """接口地址正好是清单里某一家的，返回它的 id。"""
    target = _normalize_url(base_url)
    return next((p.id for p in catalog() if _normalize_url(p.base_url) == target), None)


# ---------- 设置 ----------


class Account(BaseModel):
    """一家服务商的接入信息。清单里的服务商地址来自清单；自定义的（custom）由使用者填。"""

    base_url: str
    api_key: str | None = None


class RoleChoice(BaseModel):
    provider: str | None = None
    model: str | None = None


class Endpoint(BaseModel):
    """某个环节实际要调用的：哪家、地址、密钥、模型、关闭思考时带的参数。"""

    provider: str
    base_url: str
    api_key: str
    model: str
    no_thinking: dict | None


class Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    providers: dict[str, Account] = Field(default_factory=dict)
    extract: RoleChoice = Field(default_factory=RoleChoice)
    classify: RoleChoice = Field(default_factory=RoleChoice)
    # 没选模型就沿用文字模型（classify）
    answer: RoleChoice = Field(default_factory=RoleChoice)
    # 没选模型就不做向量检索，只做字面检索
    embed: RoleChoice = Field(default_factory=RoleChoice)
    # 分类、压缩、检索词扩展这类小调用是否关闭思考
    disable_thinking: bool = True
    # 看图提取是否也关闭思考，默认关。实测（智谱）开着思考一张图要 100 秒以上，还会把无关紧要的东西
    # 也建成概念笔记；关掉后 5–40 秒，内容由主题笔记完整兜住
    extract_disable_thinking: bool = True

    @property
    def extract_model(self) -> str | None:
        return self.extract.model

    @property
    def classify_model(self) -> str | None:
        return self.classify.model

    @property
    def embed_model(self) -> str | None:
        return self.embed.model

    @property
    def effective_answer_model(self) -> str | None:
        return self.choice("answer").model

    def choice(self, role: RoleName) -> RoleChoice:
        """问答没选模型时整个沿用文字模型的选择（服务商也一起）。"""
        if role == "answer" and not self.answer.model:
            return self.classify
        return getattr(self, role)

    def endpoint(self, role: RoleName) -> Endpoint:
        """这个环节要调用的服务商和模型。缺什么就抛 RuntimeError，消息是给使用者看的。"""
        label = ROLE_LABEL[role]
        picked = self.choice(role)
        if not picked.model:
            raise RuntimeError(f"还没有选{label}：请到设置页选一个")
        provider = picked.provider
        if not provider and len(self.providers) == 1:
            provider = next(iter(self.providers))
        if not provider:
            raise RuntimeError(f"还没有给{label}选服务商：请到设置页选一个")
        account = self.providers.get(provider)
        info = provider_info(provider)
        name = info.name if info else "自定义服务商"
        if account is None or not account.api_key:
            raise RuntimeError(f"还没有填{name}的 API Key：请到设置页填写")
        return Endpoint(
            provider=provider,
            base_url=account.base_url,
            api_key=account.api_key,
            model=picked.model,
            no_thinking=info.no_thinking if info else None,
        )


def settings_path() -> Path:
    override = os.environ.get("NOTE_AGENT_SETTINGS")
    return Path(override) if override else config.ENV_PATH.parent / "settings.json"


def _env_defaults() -> dict:
    """.env 里的默认值，旧的平铺格式（只有一家服务商）。"""
    return {
        "base_url": config.GLM_BASE_URL,
        "api_key": config.ZHIPU_API_KEY,
        "extract_model": config.EXTRACT_MODEL,
        "classify_model": config.CLASSIFY_MODEL,
        "answer_model": os.environ.get("ANSWER_MODEL") or None,
        "embed_model": os.environ.get("EMBED_MODEL") or None,
        "disable_thinking": config.DISABLE_THINKING,
    }


def _filled(value: object) -> bool:
    return value not in (None, "")


def _from_legacy(flat: dict) -> dict:
    """旧的平铺设置 → 新格式：一家服务商，四个环节都用它。"""
    base_url = flat.get("base_url") or config.GLM_BASE_URL
    provider = provider_for_url(base_url) or CUSTOM
    data: dict = {"providers": {}}
    if _filled(flat.get("api_key")) or provider == CUSTOM:
        account: dict = {"api_key": flat.get("api_key") or None}
        if provider == CUSTOM:
            account["base_url"] = base_url
        data["providers"][provider] = account
    for role in ROLES:
        model = flat.get(f"{role}_model")
        if _filled(model):
            data[role] = {"provider": provider, "model": model}
    for key in ("disable_thinking", "extract_disable_thinking"):
        if flat.get(key) is not None:
            data[key] = flat[key]
    return data


def _is_legacy(raw: dict) -> bool:
    return "providers" not in raw and any(key in raw for key in _LEGACY_KEYS)


def _merge(base: dict, over: dict) -> dict:
    """over 逐项覆盖 base。空字符串 / null 表示「没填」，保留 base 的值；
    providers 里某家的值为 null 表示删掉这家。"""
    merged = {**base, "providers": {k: dict(v) for k, v in base.get("providers", {}).items()}}
    for provider, account in (over.get("providers") or {}).items():
        if account is None:
            merged["providers"].pop(provider, None)
            continue
        current = merged["providers"].setdefault(provider, {})
        current.update({k: v for k, v in account.items() if _filled(v)})
    for role in ROLES:
        if isinstance(over.get(role), dict):
            merged[role] = {
                **(base.get(role) or {}),
                **{k: v for k, v in over[role].items() if _filled(v)},
            }
    for key in ("disable_thinking", "extract_disable_thinking"):
        if over.get(key) is not None:
            merged[key] = over[key]
    return merged


def _complete(data: dict) -> dict:
    """清单里的服务商补上地址；自定义的没有地址就丢掉（用不了）。"""
    accounts = {}
    for provider, account in data.get("providers", {}).items():
        info = provider_info(provider)
        if info is not None:
            accounts[provider] = {**account, "base_url": info.base_url}
        elif _filled(account.get("base_url")):
            accounts[provider] = account
    return {**data, "providers": accounts}


def _read_file() -> dict:
    path = settings_path()
    if not path.is_file():
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def _file_as_new_format() -> dict:
    raw = _read_file()
    return _from_legacy(raw) if _is_legacy(raw) else raw


def load() -> Settings:
    env = _env_defaults()
    raw = _read_file()
    if _is_legacy(raw):
        # 旧格式：文件逐项覆盖 .env，再整体换算
        flat = {**env, **{k: v for k, v in raw.items() if _filled(v)}}
        data = _from_legacy(flat)
    else:
        data = _merge(_from_legacy(env), raw)
    return Settings.model_validate(_complete(data))


def save(update: dict) -> Settings:
    """把 update（新格式，可以只有一部分）合并进设置文件，返回生效后的设置。

    providers 里某家写 null 表示删掉这家；某项写空字符串表示「没填」。
    """
    current = _file_as_new_format()
    data = _merge(current, update)
    # 设置文件里不存清单服务商的地址：清单改了地址，跟着清单走
    data["providers"] = {
        provider: {k: v for k, v in account.items() if k != "base_url" or provider_info(provider) is None}
        for provider, account in data["providers"].items()
    }
    # 明确清空的角色字段（空字符串）要能写进去，_merge 会把它们当成「没填」保留旧值
    for role in ROLES:
        if isinstance(update.get(role), dict):
            for key, value in update[role].items():
                if value == "":
                    data.setdefault(role, {})[key] = ""
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    return load()
