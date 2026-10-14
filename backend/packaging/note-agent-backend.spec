# PyInstaller 配置：把后端打成一个文件夹（dist/note-agent-backend/），由桌面外壳启动。
# 在 backend/ 下运行：.venv/Scripts/python.exe -m PyInstaller --noconfirm packaging/note-agent-backend.spec
# 一般不用手动跑，desktop/scripts/build.mjs 会调用它。

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

backend = Path(SPECPATH).parent

# 提示词等非 Python 文件，按原来的相对位置放进去（代码用 Path(__file__).parent 找它们）
datas = [
    (str(path), str(path.parent.relative_to(backend)))
    for path in (backend / "app").rglob("*")
    if path.is_file()
    and path.suffix in (".md", ".yaml", ".json", ".txt")
    and not {"__pycache__", ".mypy_cache"} & set(path.parts)
]

a = Analysis(
    [str(backend / "packaging" / "entry.py")],
    pathex=[str(backend)],
    datas=datas,
    # app 里各模块和 uvicorn 的实现有些是按名字动态导入的，静态分析看不到
    hiddenimports=collect_submodules("app") + collect_submodules("uvicorn"),
    excludes=["tkinter", "pytest", "IPython", "matplotlib", "numpy.tests"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="note-agent-backend",
    console=True,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="note-agent-backend", upx=False)
