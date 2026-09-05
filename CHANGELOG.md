# Changelog

## v1.3.0

- 数据库首次打开自动识别旧库，支持 v8 和匹配的无版本库；SQLite 在线备份包含 WAL，迁移全程事务与提交前校验，失败回滚。旧字段逐列补齐，离线脚本复用同一入口，拒绝未知结构和自动降级。

- 转为 AstrBot 自有 schema 23：线程隔离 SQLite 连接、嵌套事务、可恢复删除操作与持久化索引同步队列。工具、WebUI、来源批量删除和自动剪枝共用删除服务。
- Episode 后台、手动重建与反馈修正共用版本/租约协议，旧生成结果不覆盖新请求；保留 AstrBot 原生总结和消息记录。
- 双池检索新增段落证据落地评分与查询局部图权重校准。保留显式离线双池迁移和原生生命周期策略。
- Vue 源码增加人物事实、别名和删除操作管理；接通 Plugin Pages 文件上传及聊天记录选项，修复作用域切换竞态和来源传递，增加上传上限预检与 24 小时临时文件保留期。
- Web handler 使用 `astrbot.api.web`，最低 AstrBot 版本为 4.26。
- 已做静态核验；未运行测试或前端构建，新增页面的分发静态资源尚未生成。详见 [原生设计与验证边界](docs/architecture/astrbot-native-memory.md)。

## v1.2.0

### Features

- 对齐本地新版 A_memorix schema 22，新增事实账本管理和人工人物别名覆盖；已版本化数据库自动迁移，保留 AstrBot 本地表与队列。
- 新增管理员工具 `memory_fact_admin`，支持查询、创建、修正、撤回、恢复及审计；画像别名通过 `memory_profile_admin` 管理。
- 支持按消息边界导入聊天记录，JSON 导入保留、合并 `person_ids` 并刷新画像；连续失败重试保留原始分块索引。

### Fixes

- 自动提取的事实进入待确认账本；显式撤回不会被重复写回覆盖。旧 `memory_points` 保留在登记数据中，不再直接注入画像。
- 段落删除、恢复、自动复活同步处理事实证据和画像缓存；Episode 查询排除证据不完整或已删除的摘要。
- 图谱工具和 WebUI 共用写入服务；重命名同步关系哈希、段落关联、实体与关系向量，保存审计和待更新记录，合并节点保留提及次数。
- 双池时序检索扩展候选数量并遵守 `max_scan`；图谱权重拒绝无效值，保留合法的零值。
- Provider 选择使用 AstrBot 异步公开接口；初始化失败回收全部服务，scope 启动失败回收尚未入缓存的资源。

### Notes

- 新接口：Dashboard API `POST /v1/facts`、`POST /v1/person/aliases`；原有页面布局未新增对应表单。
- v8 旧库仍使用原离线迁移脚本，脚本目标版本为 22。
- 升级范围、静态 review 结论和验证限制见 [review 记录](docs/reviews/amemorix-v22.md)。

## v1.1.1

### Fixes

- 成员/管理员 LLM 工具不再接受客户端传入的 `scope_key` / `respect_filter`，避免跨会话读写记忆。
- `group_global` 私聊改走 `platform:user:{sender}`，不再和同号群聊共用一个库。
- `maintain_memory` 去掉未鉴权的 `recycle_bin`；回收站只走 `memory_v5_admin`。
- cron 定时任务不再做自动记忆注入；命令消息的助手回复不再入库。
- `runtime_admin get_config` 与 WebUI 一样脱敏密钥/路径；聊天过滤失败改为 fail-closed。
- WebUI 默认 scope 改读磁盘 `list_scope_keys()`；配置/自检/保存请求带上当前 `_scope`。
- `AstrBotLLMClient.complete()` 透传 `unified_msg_origin`；畸形 timestamp 不再让整次注入静默失败。

### Notes

- 已有私聊记忆如果落在 `platform:group:{用户ID}` 下，不会自动搬到 `platform:user:{用户ID}`。需要的话请手工迁移该 scope 目录。
- 未知 `scope.mode` 不再退化成整平台共用一个库，而是回退 `group_global`。

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
