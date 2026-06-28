# Changelog

## v0.9.5

### Breaking Changes

- 从 `main` 旧版升级到 feat2.0 / SCHEMA 15 是破坏性更新：`main` 使用 `SCHEMA_VERSION=8`，新版本运行时不会直接打开 v8 旧库。
- 文本生成不再回退到环境变量驱动的 OpenAI-compatible `LLMClient`：插件 LLM 任务只走 AstrBot provider bridge。请在 AstrBot 中配置可用聊天 Provider，必要时设置 `provider.chat_provider_id`。
- 远程 Embedding 不再从 `OPENAPI_*` / `OPENAI_*` 环境变量兜底读取端点、密钥或模型；启用 `embedding.enabled=true` 时必须在插件配置中显式填写 `embedding.openapi.base_url` 和 `embedding.openapi.model`。
- 升级前请停止插件并备份 `data/plugin_data/astrbot_plugin_memorix/`。
- 升级代码后、首次启动新版本前，必须对每个 scope 的 `metadata.db` 手动执行离线迁移：

  ```bash
  uv run python scripts/migrate_schema_v8_to_v13.py \
    --db data/plugin_data/astrbot_plugin_memorix/scopes/<scope_key>/metadata/metadata.db
  ```

- 脚本文件名保留历史兼容，实际会迁移到当前代码的 `SCHEMA_VERSION`（本版为 15）。脚本会自动备份原库，迁移后旧段落/关系数据保留。
- `schema_version >= 9 && < 15` 的已版本化数据库可由运行时自动迁移到 15；全新安装会直接创建 SCHEMA 15。

### Notes

- dual-pool 向量池配置默认改为 `dual`，与上游 A_memorix 对齐；未生成 `dual_ready.json` 时运行时会自动降级为 `single`。要真正启用双池检索，请运行 `scripts/migrate_vectors_to_dual_pools.py`。
- `integration.fuzzy_modify.enabled` 默认改为 `true`，与上游 A_memorix 对齐；`auto_execute_enabled` 仍默认 `false`，执行修改仍需要显式确认。
