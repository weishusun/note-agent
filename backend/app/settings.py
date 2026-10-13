"""运行时可改的模型设置：接口地址、密钥、各环节用的模型。

来源有两层：note-agent/.env 给默认值，note-agent/settings.json（设置页保存的）逐项覆盖。
每次调用模型前现读，所以在界面上改完立即生效，不用重启。
settings.json 里有密钥，已在 .gitignore 中忽略；它不放在 vault 里，因为 vault 是会被提交的。
"""

import json
import os
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from app import config


class Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    base_url: str
    api_key: str | None
    extract_model: str | None
    classify_model: str | None
    # 留空则沿用 classify_model
    answer_model: str | None
    # 向量模型：留空则不做向量检索，只做字面检索
    embed_model: str | None
    # 分类、压缩、检索词扩展这类小调用是否关闭思考（智谱专有参数）
    disable_thinking: bool
    # 看图提取是否也关闭思考，默认关。实测开着思考一张图要 100 秒以上，还会把无关紧要的东西
    # 也建成概念笔记；关掉后 5–40 秒，内容由主题笔记完整兜住
    extract_disable_thinking: bool

    @property
    def effective_answer_model(self) -> str | None:
        return self.answer_model or self.classify_model


def settings_path() -> Path:
    override = os.environ.get("NOTE_AGENT_SETTINGS")
    return Path(override) if override else config.ENV_PATH.parent / "settings.json"


def _env_defaults() -> dict:
    return {
        "base_url": config.GLM_BASE_URL,
        "api_key": config.ZHIPU_API_KEY,
        "extract_model": config.EXTRACT_MODEL,
        "classify_model": config.CLASSIFY_MODEL,
        "answer_model": os.environ.get("ANSWER_MODEL") or None,
        "embed_model": os.environ.get("EMBED_MODEL") or None,
        "disable_thinking": config.DISABLE_THINKING,
        "extract_disable_thinking": True,
    }


def _read_file() -> dict:
    path = settings_path()
    if not path.is_file():
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def load() -> Settings:
    merged = _env_defaults()
    for key, value in _read_file().items():
        # 文件里的空字符串 / null 表示「没填」，退回 .env 的默认值
        if key in merged and value not in (None, ""):
            merged[key] = value
    return Settings.model_validate(merged)


def save(update: dict) -> Settings:
    """把 update 里的项写进 settings.json（与已有内容合并），返回生效后的设置。"""
    data = {**_read_file(), **update}
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    return load()
