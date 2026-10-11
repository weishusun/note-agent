"""GLM(智谱开放平台,OpenAI 兼容接口)客户端工厂 + 结构化输出封装。

模型名一律从 config 读(config 已从 note-agent/.env 加载),**代码里绝不出现硬编码模型名**。
langchain_openai 的 import 放在函数内(惰性):测试 mock 掉本工厂时无需安装/导入
GLM SDK,也不碰网络。

结构化输出的方式由 config.STRUCTURED_OUTPUT_METHOD 控制(function_calling / json_mode):
GLM 的 OpenAI 兼容接口对二者支持不一,第一次撞墙时改 .env 一个变量就能换,不用改代码。
"""

from app import config

# 温度设 0:提取要忠实、分类要在冷启动时稳定复用已有领域名,都不需要发散
_TEMPERATURE = 0.0


def _resolve_model(override: str | None, configured: str | None, env_key: str) -> str:
    """override 优先,否则用 config 里的值。两者皆空则报错——不允许默认模型名。"""
    name = override or configured
    if not name:
        raise RuntimeError(f"未指定模型:请传 override 或在 note-agent/.env 设置 {env_key}")
    return name


def _build(model_name: str):
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model_name,
        api_key=config.require_api_key(),
        base_url=config.GLM_BASE_URL,
        temperature=_TEMPERATURE,
    )


def get_extract_model(override: str | None = None):
    """视觉/提取模型(EXTRACT_MODEL)。"""
    return _build(_resolve_model(override, config.EXTRACT_MODEL, "EXTRACT_MODEL"))


def get_classify_model(override: str | None = None):
    """文本/分类模型(CLASSIFY_MODEL)。"""
    return _build(_resolve_model(override, config.CLASSIFY_MODEL, "CLASSIFY_MODEL"))


def get_answer_model(override: str | None = None):
    """问答模型(ANSWER_MODEL,未配置时沿用 CLASSIFY_MODEL)。"""
    return _build(_resolve_model(override, config.ANSWER_MODEL, "ANSWER_MODEL"))


def bind_structured(model, schema):
    """统一挂结构化输出:method 取自 config.STRUCTURED_OUTPUT_METHOD,始终 include_raw
    以便读 usage_metadata 和在失败时看清模型实际返回。"""
    return model.with_structured_output(
        schema, method=config.STRUCTURED_OUTPUT_METHOD, include_raw=True
    )
