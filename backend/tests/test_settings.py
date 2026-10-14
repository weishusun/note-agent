"""设置：.env 默认值与设置文件的覆盖、旧格式自动换算、各环节分用不同服务商、
密钥只进不出、模型客户端按当前设置构建、列出服务商的模型。"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app import config, settings
from app.extract import client as model_client
from app.extract import extractor
from app.main import create_app

ENV_KEY = "env-key-12345678"
ZHIPU_URL = "https://open.bigmodel.cn/api/paas/v4/"
ZHIPU_NO_THINKING = {"thinking": {"type": "disabled"}}


@pytest.fixture(autouse=True)
def env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 .env 那一层固定成已知的假值：测试进程里 config 读到的是真实 .env。"""
    monkeypatch.setattr(config, "ZHIPU_API_KEY", ENV_KEY)
    monkeypatch.setattr(config, "GLM_BASE_URL", ZHIPU_URL)
    monkeypatch.setattr(config, "EXTRACT_MODEL", "env-vision")
    monkeypatch.setattr(config, "CLASSIFY_MODEL", "env-text")
    monkeypatch.setattr(config, "DISABLE_THINKING", True)
    monkeypatch.delenv("ANSWER_MODEL", raising=False)
    monkeypatch.delenv("EMBED_MODEL", raising=False)


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


# ---------- 服务商清单 ----------


def test_catalog_lists_the_providers() -> None:
    ids = [p.id for p in settings.catalog()]
    assert ids == ["zhipu", "deepseek", "dashscope", "siliconflow", "ark"]
    for p in settings.catalog():
        assert p.base_url.startswith("https://")
        assert set(p.models) == {"vision", "text", "embed"}
        assert p.models["text"], p.id  # 每家至少能当文字模型用
    assert settings.provider_info("zhipu").verified is True
    assert settings.provider_info("deepseek").models["embed"] == []
    assert settings.provider_for_url("https://open.bigmodel.cn/api/paas/v4") == "zhipu"
    assert settings.provider_for_url("https://example.com/v1") is None


def test_get_providers(client: TestClient) -> None:
    data = client.get("/api/providers").json()
    assert [p["id"] for p in data] == ["zhipu", "deepseek", "dashscope", "siliconflow", "ark"]
    # 关闭思考的参数是内部的，不给前端
    assert "no_thinking" not in data[0]
    assert data[0]["key_pattern"]


# ---------- 两层来源 ----------


def test_env_defaults_become_one_provider() -> None:
    s = settings.load()
    assert set(s.providers) == {"zhipu"}
    assert (s.providers["zhipu"].base_url, s.providers["zhipu"].api_key) == (ZHIPU_URL, ENV_KEY)
    assert (s.extract.provider, s.extract_model) == ("zhipu", "env-vision")
    assert (s.classify.provider, s.classify_model) == ("zhipu", "env-text")
    assert s.answer.model is None and s.effective_answer_model == "env-text"
    assert s.embed_model is None
    assert (s.disable_thinking, s.extract_disable_thinking) == (True, True)


def test_unknown_base_url_becomes_custom(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "GLM_BASE_URL", "https://env.example/v4/")
    s = settings.load()
    assert set(s.providers) == {"custom"}
    assert s.providers["custom"].base_url == "https://env.example/v4/"
    endpoint = s.endpoint("classify")
    # 自定义服务商不带关闭思考的参数：不认识的参数有的服务商会报错
    assert (endpoint.provider, endpoint.no_thinking) == ("custom", None)


def test_legacy_file_is_converted(isolated_settings: Path) -> None:
    """旧版设置页存的是平铺格式；读的时候照旧逐项覆盖 .env，再换算成新格式。"""
    isolated_settings.write_text(
        json.dumps(
            {
                "base_url": ZHIPU_URL,
                "api_key": "file-key-00001111",
                "classify_model": "file-text",
                "extract_model": "",
                "embed_model": "file-embed",
                "disable_thinking": False,
            }
        ),
        encoding="utf-8",
    )
    s = settings.load()
    assert s.providers["zhipu"].api_key == "file-key-00001111"
    assert (s.classify_model, s.extract_model, s.embed_model) == ("file-text", "env-vision", "file-embed")
    assert s.embed.provider == "zhipu"
    assert s.disable_thinking is False

    # 下次保存时写成新格式，原来的值都在
    settings.save({"answer": {"model": "file-answer"}})
    data = json.loads(isolated_settings.read_text(encoding="utf-8"))
    assert "base_url" not in data and data["providers"]["zhipu"]["api_key"] == "file-key-00001111"
    s = settings.load()
    assert (s.classify_model, s.effective_answer_model, s.embed_model) == (
        "file-text",
        "file-answer",
        "file-embed",
    )


def test_saved_values_override_env_and_blank_falls_back(isolated_settings: Path) -> None:
    settings.save({"classify": {"model": "file-text"}, "extract": {"model": ""}})

    s = settings.load()
    assert s.classify_model == "file-text"
    assert s.effective_answer_model == "file-text"  # 问答跟着文字模型走
    assert s.extract_model == "env-vision"  # 空字符串 = 没填

    # 后一次保存与前一次合并，不互相覆盖
    settings.save({"answer": {"model": "file-answer"}})
    s = settings.load()
    assert (s.classify_model, s.effective_answer_model) == ("file-text", "file-answer")
    # 清单服务商的地址不写进文件，跟着清单走
    data = json.loads(isolated_settings.read_text(encoding="utf-8"))
    assert data["classify"]["model"] == "file-text"


def test_roles_can_use_different_providers(built: list[dict]) -> None:
    settings.save(
        {
            "providers": {"deepseek": {"api_key": "ds-key-55556666"}},
            "classify": {"provider": "deepseek", "model": "ds-text"},
        }
    )
    model_client.get_extract_model()
    model_client.get_classify_model()
    model_client.get_answer_model()
    extract, classify, answer = built

    assert (extract["base_url"], extract["api_key"], extract["model"]) == (ZHIPU_URL, ENV_KEY, "env-vision")
    deepseek = settings.provider_info("deepseek")
    assert (classify["base_url"], classify["api_key"], classify["model"]) == (
        deepseek.base_url,
        "ds-key-55556666",
        "ds-text",
    )
    assert classify["extra_body"] == deepseek.no_thinking
    # 问答没单独选：连服务商一起沿用文字模型
    assert (answer["base_url"], answer["model"]) == (deepseek.base_url, "ds-text")


def test_removing_a_provider(client: TestClient) -> None:
    client.put("/api/settings", json={"providers": {"deepseek": {"api_key": "ds-key-55556666"}}})
    assert set(settings.load().providers) == {"zhipu", "deepseek"}
    client.put("/api/settings", json={"providers": {"deepseek": None}})
    assert set(settings.load().providers) == {"zhipu"}


# ---------- 接口：密钥只进不出 ----------


def test_get_settings_never_returns_the_key(client: TestClient) -> None:
    data = client.get("/api/settings").json()
    assert set(data) == {
        "providers",
        "extract",
        "classify",
        "answer",
        "embed",
        "disable_thinking",
        "extract_disable_thinking",
    }
    assert data["providers"] == {
        "zhipu": {"base_url": ZHIPU_URL, "api_key_set": True, "api_key_hint": "5678"}
    }
    assert ENV_KEY not in json.dumps(data)
    assert data["classify"] == {"provider": "zhipu", "model": "env-text"}
    assert data["answer"] == {"provider": "", "model": ""}


def test_put_settings_saves_and_masks(client: TestClient, isolated_settings: Path) -> None:
    resp = client.put(
        "/api/settings",
        json={
            "providers": {
                "custom": {"base_url": "  https://other.example/v1  ", "api_key": "new-key-abcdWXYZ"}
            },
            "extract": {"provider": "custom", "model": "vision-x"},
            "classify": {"provider": "custom", "model": "text-x"},
            "answer": {"model": "answer-x"},
            "disable_thinking": False,
            "extract_disable_thinking": True,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["providers"]["custom"] == {
        "base_url": "https://other.example/v1",
        "api_key_set": True,
        "api_key_hint": "WXYZ",
    }
    assert "new-key-abcdWXYZ" not in resp.text
    assert data["extract"] == {"provider": "custom", "model": "vision-x"}
    assert data["classify"] == {"provider": "custom", "model": "text-x"}
    assert data["disable_thinking"] is False

    assert settings.load().providers["custom"].api_key == "new-key-abcdWXYZ"
    saved = json.loads(isolated_settings.read_text(encoding="utf-8"))
    assert saved["providers"]["custom"]["api_key"] == "new-key-abcdWXYZ"


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_blank_api_key_keeps_the_saved_one(client: TestClient, blank: str | None) -> None:
    client.put("/api/settings", json={"providers": {"zhipu": {"api_key": "saved-key-00001111"}}})
    data = client.put(
        "/api/settings",
        json={"providers": {"zhipu": {"api_key": blank}}, "classify": {"model": "text-y"}},
    ).json()

    assert data["providers"]["zhipu"]["api_key_hint"] == "1111"
    assert data["classify"]["model"] == "text-y"
    assert settings.load().providers["zhipu"].api_key == "saved-key-00001111"


def test_short_key_gets_no_hint(client: TestClient) -> None:
    data = client.put("/api/settings", json={"providers": {"zhipu": {"api_key": "abc"}}}).json()
    assert data["providers"]["zhipu"] == {"base_url": ZHIPU_URL, "api_key_set": True, "api_key_hint": None}


def test_catalog_provider_url_cannot_be_changed(client: TestClient) -> None:
    data = client.put(
        "/api/settings",
        json={"providers": {"zhipu": {"base_url": "https://evil.example/v1", "api_key": "k-123456789"}}},
    ).json()
    assert data["providers"]["zhipu"]["base_url"] == ZHIPU_URL


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"providers": {"custom": {"base_url": "ftp://example.com"}}}, 422),
        ({"providers": {"custom": {"base_url": "open.bigmodel.cn/api"}}}, 422),
        ({"providers": {"custom": {"api_key": "k"}}}, 400),  # 自定义的要有地址
        ({"providers": {"nobody": {"api_key": "k"}}}, 400),
        ({"classify": {"provider": "nobody", "model": "m"}}, 400),
        ({"base_url": "https://x.example"}, 422),  # 旧格式的字段不再接受
        ({"unknown_field": 1}, 422),
    ],
)
def test_put_settings_rejects_invalid(
    client: TestClient, isolated_settings: Path, body: dict, status: int
) -> None:
    assert client.put("/api/settings", json=body).status_code == status
    assert not isolated_settings.exists()


def test_connection_test_reports_the_models(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(model_client, "probe_text", lambda: {"ok": True, "detail": "正常", "seconds": 0.8})
    monkeypatch.setattr(
        model_client, "probe_vision", lambda: {"ok": False, "detail": "模型不支持图片", "seconds": 1.2}
    )
    monkeypatch.setattr(model_client, "probe_answer", lambda: {"ok": True, "detail": "正常", "seconds": 2.0})
    assert client.post("/api/settings/test").json() == {
        "text": {"ok": True, "detail": "正常", "seconds": 0.8},
        "vision": {"ok": False, "detail": "模型不支持图片", "seconds": 1.2},
    }
    # 问答单独选了才测它
    client.put("/api/settings", json={"answer": {"model": "answer-x"}})
    assert client.post("/api/settings/test").json()["answer"]["seconds"] == 2.0


# ---------- 列出服务商的模型 ----------


def _fake_http(monkeypatch: pytest.MonkeyPatch, status: int, body: object) -> list[dict]:
    calls: list[dict] = []

    def fake_get(url: str, headers: dict, timeout: float) -> httpx.Response:
        calls.append({"url": url, "auth": headers["Authorization"]})
        return httpx.Response(status, json=body)

    monkeypatch.setattr(httpx, "get", fake_get)
    return calls


def test_list_models_uses_the_given_key_and_only_that_provider(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_http(monkeypatch, 200, {"object": "list", "data": [{"id": "b-model"}, {"id": "a-model"}]})
    data = client.post("/api/providers/deepseek/models", json={"api_key": "sk-typed-key"}).json()
    assert data == {"status": "ok", "models": ["a-model", "b-model"], "detail": "2 个模型"}
    assert calls == [
        {"url": settings.provider_info("deepseek").base_url.rstrip("/") + "/models", "auth": "Bearer sk-typed-key"}
    ]
    # 不带 Key 就用已保存的
    client.post("/api/providers/zhipu/models", json={})
    assert calls[-1]["auth"] == f"Bearer {ENV_KEY}"
    # 只是查一下，不保存
    assert "deepseek" not in settings.load().providers


@pytest.mark.parametrize(
    ("status", "expected"), [(401, "bad_key"), (403, "bad_key"), (404, "unsupported"), (500, "error")]
)
def test_list_models_reports_problems(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, status: int, expected: str
) -> None:
    _fake_http(monkeypatch, status, {"error": "x"})
    data = client.post("/api/providers/deepseek/models", json={"api_key": "sk-x"}).json()
    assert data["status"] == expected and data["models"] == []


def test_list_models_needs_a_key(client: TestClient) -> None:
    assert client.post("/api/providers/deepseek/models", json={}).status_code == 400
    assert client.post("/api/providers/nobody/models", json={"api_key": "k"}).status_code == 400


# ---------- 模型客户端按当前设置构建 ----------


def test_models_are_built_from_current_settings(built: list[dict]) -> None:
    settings.save({"providers": {"zhipu": {"api_key": "file-key-99990000"}}})

    model_client.get_extract_model()
    model_client.get_classify_model()
    model_client.get_expand_model()
    model_client.get_answer_model()
    extract, classify, expand, answer = built

    assert {c["base_url"] for c in built} == {ZHIPU_URL}
    assert {c["api_key"] for c in built} == {"file-key-99990000"}
    assert [c["model"] for c in built] == ["env-vision", "env-text", "env-text", "env-text"]
    # 提取和各种小调用默认关闭思考（参数来自 providers.yaml）；问答保持开启
    assert [c["extra_body"] for c in (extract, classify, expand)] == [ZHIPU_NO_THINKING] * 3
    assert answer["extra_body"] is None
    # 扩展超时短且不重试；其余都有超时
    assert (expand["timeout"], expand["max_retries"]) == (10.0, 0)
    assert all(c["timeout"] for c in built)


def test_settings_change_takes_effect_without_restart(built: list[dict]) -> None:
    model_client.get_classify_model()
    settings.save({"classify": {"model": "text-z"}, "disable_thinking": False})
    model_client.get_classify_model()

    assert [c["model"] for c in built] == ["env-text", "text-z"]
    assert built[1]["extra_body"] is None


def test_missing_key_or_model_gives_readable_error(
    built: list[dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "ZHIPU_API_KEY", None)
    with pytest.raises(RuntimeError, match="还没有给文字模型选服务商|还没有填"):
        model_client.get_classify_model()

    monkeypatch.setattr(config, "ZHIPU_API_KEY", ENV_KEY)
    monkeypatch.setattr(config, "EXTRACT_MODEL", None)
    with pytest.raises(RuntimeError, match="还没有选看图模型"):
        model_client.get_extract_model()

    settings.save({"classify": {"provider": "deepseek"}})
    with pytest.raises(RuntimeError, match="还没有填DeepSeek的 API Key"):
        model_client.get_classify_model()
    assert built == []


def test_extract_thinking_can_be_turned_back_on(built: list[dict]) -> None:
    """提取默认关闭思考；打开只影响思考参数，输出方式始终是 json_mode。"""
    model_client.get_extract_model()
    settings.save({"extract_disable_thinking": False})
    model_client.get_extract_model()
    assert [c["extra_body"] for c in built] == [ZHIPU_NO_THINKING, None]

    bound: dict = {}
    fake = SimpleNamespace(with_structured_output=lambda schema, **kw: bound.update(kw))
    model_client.bind_json(fake, extractor._DraftBatch)
    assert bound == {"method": "json_mode", "include_raw": True}
    assert '"concepts": [' in extractor._extract_messages(None, "一段文字")[0].content


def test_probe_png_is_a_valid_image() -> None:
    png = model_client._png()
    assert png.startswith(b"\x89PNG\r\n\x1a\n") and png.endswith(b"IEND\xaeB`\x82")
    assert extractor._image_mime(png) == "image/png"
