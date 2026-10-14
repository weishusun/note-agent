"""PyInstaller 的入口脚本：打包出的 note-agent-backend.exe 从这里进 app.desktop。"""

from app.desktop import main

if __name__ == "__main__":
    main()
