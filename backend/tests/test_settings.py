"""设置：.env 默认值与 settings.json 的覆盖、密钥只进不出、模型客户端按当前设置构建。"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import config, settings
from app.extract import client as model_client
from app.extract import extractor
from app.main import create_app

ENV_KEY = "env-key-12345678"


@pytest.fixture(autouse=True)
def env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 .env 那一层固定成已知的假值：测试进程里 config 读到的是真实 .env。"""
    monkeypatch.setattr(config, "ZHIPU_API_KEY", ENV_KEY)
    monkeypatch.setattr(config, "GLM_BASE_URL", "https://env.example/v4/")
    monkeypatch.setattr(config, "EXTRACT_MODEL", "env-vision")
    monkeypatch.setattr(config, "CLASSIFY_MODEL", "env-text")
    monkeypatch.setattr(config, "DISABLE_THINKING", True)
    monkeypatch.delenv("ANSWER_MODEL", raising=False)


@pytest.fixture
def client(vault_root: Path) -> TestClient:
    return TestClient(create_app(vault_root))


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """把 ChatOpenAI 换成记录构造参数的假类，看客户端到底拿什么去建模型。"""
    calls: list[dict] = []

    class FakeChatOpenAI:
        def __init__(self, **kwargs) -> None:
            calls.append(kwargs)

    monkeypatch.setitem(sys.modules, "langchain_openai", SimpleNamespace(ChatOpenAI=FakeChatOpenAI))
    return calls


# ---------- 两层来源 ----------


def test_defaults_come_from_env() -> None:
    s = settings.load()
    assert (s.base_url, s.api_key, s.extract_model, s.classify_model) == (
        "https://env.example/v4/",
        ENV_KEY,
        "env-vision",
        "env-text",
    )
    assert s.answer_model is None and s.effective_answer_model == "env-text"
    assert (s.disable_thinking, s.extract_disable_thinking) == (True, True)


def test_saved_values_override_env_and_blank_falls_back(isolated_settings: Path) -> None:
    settings.save({"classify_model": "file-text", "extract_model": "", "base_url": None})

    s = settings.load()
    assert s.classify_model == "file-text"
    assert s.effective_answer_model == "file-text"  # 问答跟着文件里的分类模型走
    assert s.extract_model == "env-vision"  # 空字符串 = 没填
    assert s.base_url == "https://env.example/v4/"

    # 后一次保存与前一次合并，不互相覆盖
    settings.save({"answer_model": "file-answer"})
    s = settings.load()
    assert (s.classify_model, s.effective_answer_model) == ("file-text", "file-answer")
    assert json.loads(isolated_settings.read_text(encoding="utf-8"))["classify_model"] == "file-text"


# ---------- 接口：密钥只进不出 ----------


def test_get_settings_never_returns_the_key(client: TestClient) -> None:
    data = client.get("/api/settings").json()
    assert set(data) == {
        "base_url",
        "api_key_set",
        "api_key_hint",
        "extract_model",
        "classify_model",
        "answer_model",
        "disable_thinking",
        "extract_disable_thinking",
    }
    assert data["api_key_set"] is True
    assert data["api_key_hint"] == "5678"
    assert ENV_KEY not in json.dumps(data)
    assert data["answer_model"] == ""


def test_put_settings_saves_and_masks(client: TestClient, isolated_settings: Path) -> None:
    resp = client.put(
        "/api/settings",
        json={
            "base_url": "  https://other.example/v1  ",
            "api_key": "new-key-abcdWXYZ",
            "extract_model": "vision-x",
            "classify_model": "text-x",
            "answer_model": "answer-x",
            "disable_thinking": False,
            "extract_disable_thinking": True,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["base_url"] == "https://other.example/v1"
    assert (data["api_key_set"], data["api_key_hint"]) == (True, "WXYZ")
    assert "new-key-abcdWXYZ" not in resp.text
    assert (data["extract_model"], data["classify_model"], data["answer_model"]) == (
        "vision-x",
        "text-x",
        "answer-x",
    )
    assert data["disable_thinking"] is False
    assert data["extract_disable_thinking"] is True

    assert settings.load().api_key == "new-key-abcdWXYZ"
    assert json.loads(isolated_settings.read_text(encoding="utf-8"))["api_key"] == "new-key-abcdWXYZ"


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_blank_api_key_keeps_the_saved_one(client: TestClient, blank: str | None) -> None:
    client.put("/api/settings", json={"api_key": "saved-key-00001111"})
    data = client.put("/api/settings", json={"api_key": blank, "classify_model": "text-y"}).json()

    assert data["api_key_hint"] == "1111"
    assert data["classify_model"] == "text-y"
    assert settings.load().api_key == "saved-key-00001111"


def test_short_key_gets_no_hint(client: TestClient) -> None:
    data = client.put("/api/settings", json={"api_key": "abc"}).json()
    assert (data["api_key_set"], data["api_key_hint"]) == (True, None)


@pytest.mark.parametrize(
    "body",
    [
        {"base_url": "ftp://example.com"},
        {"base_url": "open.bigmodel.cn/api"},
        {"structured_output_method": "json_mode"},  # 这个设置已经删除
        {"unknown_field": 1},
    ],
)
def test_put_settings_rejects_invalid(client: TestClient, isolated_settings: Path, body: dict) -> None:
    assert client.put("/api/settings", json=body).status_code == 422
    assert not isolated_settings.exists()


def test_connection_test_reports_both_models(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(model_client, "probe_text", lambda: {"ok": True, "detail": "正常", "seconds": 0.8})
    monkeypatch.setattr(
        model_client, "probe_vision", lambda: {"ok": False, "detail": "模型不支持图片", "seconds": 1.2}
    )
    assert client.post("/api/settings/test").json() == {
        "text": {"ok": True, "detail": "正常", "seconds": 0.8},
        "vision": {"ok": False, "detail": "模型不支持图片", "seconds": 1.2},
    }


# ---------- 模型客户端按当前设置构建 ----------


def test_models_are_built_from_current_settings(built: list[dict]) -> None:
    settings.save({"base_url": "https://other.example/v1", "api_key": "file-key-99990000"})

    model_client.get_extract_model()
    model_client.get_classify_model()
    model_client.get_expand_model()
    model_client.get_answer_model()
    extract, classify, expand, answer = built

    assert {c["base_url"] for c in built} == {"https://other.example/v1"}
    assert {c["api_key"] for c in built} == {"file-key-99990000"}
    assert [c["model"] for c in built] == ["env-vision", "env-text", "env-text", "env-text"]
    # 提取和各种小调用默认关闭思考；问答保持开启（回答质量是使用者直接看到的）
    no_thinking = {"thinking": {"type": "disabled"}}
    assert [c["extra_body"] for c in (extract, classify, expand)] == [no_thinking] * 3
    assert answer["extra_body"] is None
    # 扩展超时短且不重试；其余都有超时
    assert (expand["timeout"], expand["max_retries"]) == (10.0, 0)
    assert all(c["timeout"] for c in built)


def test_settings_change_takes_effect_without_restart(built: list[dict]) -> None:
    model_client.get_classify_model()
    settings.save({"classify_model": "text-z", "disable_thinking": False})
    model_client.get_classify_model()

    assert [c["model"] for c in built] == ["env-text", "text-z"]
    assert built[1]["extra_body"] is None


def test_missing_key_or_model_gives_readable_error(
    built: list[dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "ZHIPU_API_KEY", None)
    with pytest.raises(RuntimeError, match="还没有配置 API Key"):
        model_client.get_classify_model()

    monkeypatch.setattr(config, "ZHIPU_API_KEY", ENV_KEY)
    monkeypatch.setattr(config, "EXTRACT_MODEL", None)
    with pytest.raises(RuntimeError, match="还没有配置提取模型"):
        model_client.get_extract_model()
    assert built == []


def test_extract_thinking_can_be_turned_back_on(built: list[dict]) -> None:
    """提取默认关闭思考；打开只影响思考参数，输出方式始终是 json_mode。"""
    model_client.get_extract_model()
    settings.save({"extract_disable_thinking": False})
    model_client.get_extract_model()
    assert [c["extra_body"] for c in built] == [{"thinking": {"type": "disabled"}}, None]

    bound: dict = {}
    fake = SimpleNamespace(with_structured_output=lambda schema, **kw: bound.update(kw))
    model_client.bind_json(fake, extractor._DraftBatch)
    assert bound == {"method": "json_mode", "include_raw": True}
    assert '"concepts": [' in extractor._extract_messages(None, "一段文字")[0].content


def test_probe_png_is_a_valid_image() -> None:
    png = model_client._png()
    assert png.startswith(b"\x89PNG\r\n\x1a\n") and png.endswith(b"IEND\xaeB`\x82")
    assert extractor._image_mime(png) == "image/png"
