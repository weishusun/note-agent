# CLAUDE.md

## 铁律

- 所有文件读写必须显式 `encoding="utf-8"`，路径统一用 `pathlib.Path`
- vault 的读写必须经过 `backend/app/vault/`，禁止在别处 `open()`
- 分类只能取自 `taxonomy.yaml`，禁止在代码里硬编码分类名
