"""桌面版的后端入口，由 Electron 外壳启动（打包后是 note-agent-backend.exe）。

外壳在命令行里给出知识库位置、设置文件位置、端口和界面文件的位置（见 SCHEMA §7）。
只监听 127.0.0.1。带 --exit-with-stdin 时，标准输入一关（外壳退出或崩溃）后端就正常退出，
不会留下没人管的进程。
"""

import argparse
import logging
import os
import sys
import threading
from pathlib import Path

import uvicorn


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="note-agent-backend")
    parser.add_argument("--vault", type=Path, required=True, help="知识库文件夹")
    parser.add_argument("--settings", type=Path, required=True, help="设置文件（含密钥）")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--static", type=Path, default=None, help="界面构建产物所在的文件夹")
    parser.add_argument("--exit-with-stdin", action="store_true", help="标准输入关闭时退出")
    return parser.parse_args(argv)


def _watch_stdin(server: uvicorn.Server) -> None:
    if sys.stdin is None:
        return
    # 直接读文件描述符：用 sys.stdin.buffer 的话，进程因别的原因退出时这个线程还占着缓冲区的锁，
    # 解释器收尾会报 Fatal Python error
    fd = sys.stdin.fileno()
    while os.read(fd, 4096):
        pass
    logging.getLogger(__name__).info("外壳已关闭，后端退出")
    server.should_exit = True


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    # 外壳把输出写进日志文件；Windows 下管道默认按本地编码，中文日志会出错
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # 设置模块每次都按这个环境变量找 settings.json，所以要在处理任何请求之前设好
    os.environ["NOTE_AGENT_SETTINGS"] = str(args.settings)
    os.environ["VAULT_PATH"] = str(args.vault)

    from app.main import create_app

    app = create_app(args.vault, static_dir=args.static)
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=args.port,
        # 显式指定实现：打包后 uvicorn 按名字动态导入的「auto」会找不到模块
        loop="asyncio",
        http="h11",
        ws="none",
        lifespan="on",
        log_config=None,
    )
    server = uvicorn.Server(config)
    if args.exit_with_stdin:
        threading.Thread(target=_watch_stdin, args=(server,), daemon=True).start()
    server.run()


if __name__ == "__main__":
    main()
