"""桌面版：后端连界面一起提供、命令行参数（SCHEMA §9）。"""

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.desktop import parse_args
from app.main import create_app
# conftest 会把 git.commit 换成记录器，这里先拿到真的
from app.vault.git import commit as real_git_commit


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    root = tmp_path / "frontend"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>知识库</title>", encoding="utf-8")
    (root / "assets" / "index-abc.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("不该被读到", encoding="utf-8")
    return root


@pytest.fixture
def client(vault_root: Path, static_dir: Path) -> TestClient:
    return TestClient(create_app(vault_root, static_dir=static_dir))


def test_serves_index_and_built_files(client: TestClient) -> None:
    root = client.get("/")
    assert root.status_code == 200
    assert "知识库" in root.text
    assert root.headers["cache-control"] == "no-cache"
    script = client.get("/assets/index-abc.js")
    assert script.status_code == 200
    assert script.text == "console.log(1)"


def test_unknown_paths_fall_back_to_index(client: TestClient) -> None:
    resp = client.get("/notes/something")
    assert resp.status_code == 200
    assert "知识库" in resp.text


def test_api_routes_win_and_unknown_api_is_404(client: TestClient) -> None:
    assert client.get("/api/taxonomy").json()["version"] == 2
    assert client.get("/health").json() == {"status": "ok"}
    missing = client.get("/api/nope")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "接口不存在"


def test_cannot_escape_the_static_folder(client: TestClient) -> None:
    for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/assets/..%2f..%2fsecret.txt"):
        resp = client.get(path)
        assert "不该被读到" not in resp.text


def test_no_frontend_without_static_dir(vault_root: Path) -> None:
    client = TestClient(create_app(vault_root))
    assert client.get("/").status_code == 404


def test_missing_index_html_fails_fast(vault_root: Path, tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        create_app(vault_root, static_dir=tmp_path / "empty")


def test_locations(client: TestClient, vault_root: Path, isolated_settings: Path) -> None:
    assert client.get("/api/locations").json() == {
        "vault": str(vault_root.resolve()),
        "settings": str(isolated_settings),
    }


def test_parse_args() -> None:
    args = parse_args(
        ["--vault", "D:/kb", "--settings", "C:/data/settings.json", "--port", "51234", "--exit-with-stdin"]
    )
    assert args.vault == Path("D:/kb")
    assert args.settings == Path("C:/data/settings.json")
    assert args.port == 51234
    assert args.static is None
    assert args.exit_with_stdin is True
    with pytest.raises(SystemExit):
        parse_args(["--vault", "D:/kb"])


def test_git_runs_without_inherited_stdin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # 桌面版后端的标准输入是连着外壳的管道，git 继承它会在 Windows 上卡住
    calls: list[dict] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert real_git_commit(tmp_path, "测试") is False
    assert calls
    assert all(c["stdin"] is subprocess.DEVNULL and c["timeout"] for c in calls)
