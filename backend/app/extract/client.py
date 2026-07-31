"""GLM(智谱开放平台,OpenAI 兼容接口)模型客户端工厂。

服务商固定为智谱,但**模型名一律从环境变量读,代码里绝不出现硬编码模型名**:
  ZHIPU_API_KEY   密钥
  GLM_BASE_URL    OpenAI 兼容 base_url
  EXTRACT_MODEL   视觉/提取模型
  CLASSIFY_MODEL  文本/分类模型

langchain_openai 的 import 放在函数内(惰性),这样单元测试 mock 掉本工厂时
无需真正安装/导入 GLM SDK,也不会碰网络。
"""

import os

# 温度设 0:提取要忠实、分类要在冷启动时稳定复用已有领域名,都不需要发散
_TEMPERATURE = 0.0


def _require_env(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise RuntimeError(f"环境变量 {key} 未设置")
    return value


def _resolve_model(override: str | None, env_key: str) -> str:
    """override 优先,否则读环境变量。两者皆空则报错——不允许有默认模型名。"""
    name = override or os.environ.get(env_key)
    if not name:
        raise RuntimeError(f"未指定模型:请传 override 或设置环境变量 {env_key}")
    return name


def _build(model_name: str):
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model_name,
        api_key=_require_env("ZHIPU_API_KEY"),
        base_url=_require_env("GLM_BASE_URL"),
        temperature=_TEMPERATURE,
    )


def get_extract_model(override: str | None = None):
    """视觉/提取模型(EXTRACT_MODEL)。"""
    return _build(_resolve_model(override, "EXTRACT_MODEL"))


def get_classify_model(override: str | None = None):
    """文本/分类模型(CLASSIFY_MODEL)。"""
    return _build(_resolve_model(override, "CLASSIFY_MODEL"))
