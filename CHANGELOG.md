# Changelog

## v1.1.0

### Features

- 元数据 schema 对齐上游 A_memorix **21**：生命周期列、事实账本表、Episode rebuild revision、storage cleanup jobs。
- 插件主线 `AppContext` 真正接入双向量池：`build_context` 把 paragraph/graph store 传给 `DualPathRetriever`。
- 双池检索支持查询内分数校准（`retrieval.vector_pools.score_calibration_method`）。
- 删除会同步清理双池向量；`memory_delete_admin` 补齐 `list_operations` / `get_operation` / `purge`。
- `memory_episode_admin` 兼容上游 `process_sources` action，同时保留插件本地 `process_pending` 队列。

### Notes

- 运行时自动迁移覆盖 `schema_version >= 9 && < 21`。v8 及更旧库仍需先跑离线脚本。
- 上游 schema 19 会删除 `episode_pending_paragraphs`；插件按开闭原则保留该兼容队列，不跟随 DROP。
- 版本号统一为 `v1.1.0`（`metadata.yaml` / `@register` / 适配层）。`astrbot_version` 声明为 `>=4.16,<5`。

## v1.0.0

### Breaking Changes

- 从 `main` 旧版升级到 feat2.0 / SCHEMA 15 是破坏性更新：`main` 使用 `SCHEMA_VERSION=8`，新版本运行时不会直接打开 v8 旧库。
- 文本生成不再回退到环境变量驱动的 OpenAI-compatible `LLMClient`：插件 LLM 任务只走 AstrBot provider bridge。请在 AstrBot 中配置可用聊天 Provider，必要时设置 `provider.chat_provider_id`。
- 远程 Embedding 不再从 `OPENAPI_*` / `OPENAI_*` 环境变量兜底读取端点、密钥或模型；启用 `embedding.enabled=true` 时必须在插件配置中显式填写 `embedding.openapi.base_url` 和 `embedding.openapi.model`。
- 升级前请停止插件并备份 `data/plugin_data/astrbot_plugin_memorix/`。
- 升级代码后、首次启动新版本前，必须执行一次离线迁移脚本；脚本会自动扫描并迁移所有 scope 的 `metadata.db`：

  ```bash
  uv run --no-project python scripts/migrate_schema_v8_to_v13.py
  ```

- 如 AstrBot 数据目录不在默认位置，可使用 `--plugin-data-dir /path/to/data/plugin_data/astrbot_plugin_memorix`；高级用户仍可使用 `--db /path/to/metadata.db` 只迁移单个数据库。
- 脚本文件名保留历史兼容，实际会迁移到当前代码的 `SCHEMA_VERSION`（本版为 15）。脚本会自动备份原库，迁移后旧段落/关系数据保留。
- `schema_version >= 9 && < 15` 的已版本化数据库可由运行时自动迁移到 15；全新安装会直接创建 SCHEMA 15。

### Notes

- dual-pool 向量池配置默认改为 `dual`，与上游 A_memorix 对齐；未生成 `dual_ready.json` 时运行时会自动降级为 `single`。要真正启用双池检索，请运行 `scripts/migrate_vectors_to_dual_pools.py`。
- `integration.fuzzy_modify.enabled` 默认改为 `true`，与上游 A_memorix 对齐；`auto_execute_enabled` 仍默认 `false`，执行修改仍需要显式确认。
