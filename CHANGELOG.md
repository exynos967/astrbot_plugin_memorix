# Changelog

## v2.0.0

### Features

- 全面对齐 `A_memorix` **v2.0.0 / SCHEMA 31**（上游 `5221c028e`）：支持旧版数据库（v8、schema 9–30 及字段指纹匹配的无版本库）在首次打开时自动在线备份、事务升级与提交前校验至 **SCHEMA 31**；新增知识包注册与条目表、多模态图片资产/出现记录/观察描述/向量清单表、`chat_scope_transfers` 会话迁移账本与 `person_facts_ledger` 统一证据视图。
- 新增 **在线会话记忆迁移与账本恢复**（`ChatMemoryMigration` + `AstrBotChatMigrationService`）：支持在作用域内将旧会话的记忆段落、结构化事实、事件摘要、图片出现/观察记录与可选聊天流水原子迁移至新会话（免重算向量，迁移期间通过 `ChatMigrationGate` 安全挂起该会话并发写入，目标会话既有数据归档至 `migration_archive:<job_id>` 并对常规检索自动隐藏，支持一键按账本无损回退）；提供管理契约 `memory_chat_migration`、WebUI API `/v1/chat-migrations` 以及「知识包与迁移 -> 在线会话记忆迁移」前端工作台。
- 同步 **模块化多语种 Prompt 体系与 `.meta.toml` 元信息**（`prompts/{zh-CN,en-US,ja-JP}/{main,instructions}/memory/`）：升级 `prompt_shim` 支持递归分类发现、重复模板名检测、`.meta.toml`/`.meta.json` 解析、`transform_template` 与按作用域 `custom_prompts` 原地热更新覆盖。
- 新增 **`provider.fast_chat_provider_id` 轻量模型路由**、**`embedding.enable_cache` 向量内存缓存**与 **`web.import.llm_retry.max_attempts` 导入重试上限**：反馈纠错分类、检索调优自然语言改写、聊天日志时间抽取与画像分类优先路由至轻量模型（未配置时平滑回退主模型）；运行时自检自动绕过 Embedding 缓存探测真实连通性。
- 新增 **`.amembundle` 知识包导入导出**（`BundleRuntime`）：支持跨实例打包导出与校验导入结构化记忆、事实声明、人物画像及关联图片资产；提供管理工具 `memory_bundle_admin` 与 Dashboard 接口 `POST /v1/bundles`。
- 新增 **多模态图片记忆读写、未配置嵌入自动暂停门控与自动召回闭环**（`ImageMemoryRuntime` + `embedding_shim`）：支持聊天与知识包中的静态图片（PNG/JPEG/WebP/静态 GIF，纯标准库安全解析，自动拦截动图与超限分辨率）去重存储、出现位置追踪、用户原话与视觉模型描述（`[图片：...]`）认知绑定；默认开启 `image_memory.require_embedding=true`（对齐上游 `407deaf90`），静态检测图片嵌入配置状态（`is_image_embedding_configured`），未配置多模态图片嵌入模型时在运行时自动暂停图片记忆（不篡改用户已保存配置，避免未配置图片记忆时触发识图/嵌入报错，且状态接口明确区分“未配置图片嵌入模型自动暂停”与“手动关闭”）；如需免向量同图精确哈希（`exact_hash`）模式可显式设置 `image_memory.require_embedding=false`；提供管理工具 `memory_image_admin` 与 Dashboard 接口 `POST /v1/images`、`GET /api/images/status`。
- 升级 **人物事实写回与 `FactDeduplicator` 作用域去重**：单次提取自动写入 `uncertain` 事实线索，跨消息二次独立确认后在单 SQLite 事务内原子晋升为 `stable` 事实、复用既有段落与声明并推进 `episode_rebuild_sources` 版本号，结合 LLM 语义判重与离线确定性回退避免重复建段、事务残留或误合并时态事件。
- 新增统一管理契约调度器 **`AdminContractDispatcher`** 与领域服务容器 **`DomainServiceContainer`**：支持 14 个标准管理契约 + `memory_correction_admin`（可通过 `integration.enable_v2_extended_tools=true` 向 LLM 暴露 `memory_bundle_admin` / `memory_correction_admin` / `memory_image_admin`），并新增通用路由 `POST /v1/admin/{component_name}` 与 `POST /v1/correction`。
- 全面重构与扩充 **内嵌 WebUI v2.0 记忆工作台**（4 大业务分组、13 个路由视图，已重新构建分发静态产物至 `pages/memorix/`）：
  - 新增 **图片记忆（`/images`）**、**知识包迁移（`/bundles`）**、**纠错与调优（`/correction`：模糊修正 / 对话反馈审计 / 检索自动调优）** 三大旗舰面板，并在记忆治理页新增 **结构化事实台账（`FactLedger`）** 与 **双池向量索引维护** 面板。
  - 升级全局壳层与暗色视觉系统：新增顶栏全局作用域切换器、跟随 AstrBot 宿主/手动三态主题切换、18px 线性 SVG 分组侧栏与移动端抽屉导航，全面对齐 WCAG AA 暗色对比度。

### Fixes & Architecture

- **消除双引擎脑裂与数据目录解析分歧**：统一 `MemorixPlugin`（动态 `base_data_dir` 属性）、`ScopeEngineManager.get_scope_dir` 与 `ScopeRuntimeManager` 的数据目录解析优先级（`storage.data_dir` > `StarTools.get_data_dir("astrbot_plugin_memorix")` > context fallback），移除对 `astrbot.core.utils.astrbot_path` 私有模块的依赖；重构 `ScopeEngineManager` / `MemoryEngineFacade` 直接委托 `ScopeRuntimeManager` 共享同一套 `AppContext`（`MetadataStore` / `VectorStore` / `GraphStore` / `SparseBM25Index`），并补齐 `AppContext.persist_vector_store` / `record_person_evidence_written` 与 `KernelRuntimeFacade` 的持久化契约，彻底消除同 scope 双实例覆盖 `.npz` 文件与总结/导入持久化异常的问题。
- **修复多作用域 `host_shims` 全链路隔离与共享导入队列**：新增 `embedding_shim` 杜绝 `HostImageEmbedder` 命中 `_RecursiveStub`；为 `config_shim`、`llm_shim` 与 `embedding_shim` 引入 `ContextVar` 协程级隔离（`scope_shims` / `activate_context_shims`），全覆盖领域服务、后台 `TaskManager` / `ImportTaskManager` / `FeedbackService` / `RetrievalTuningManager` 与 `PluginPageWebUIBridge`，并让 WebUI 与 `AdminService` 共享同一 `ImportTaskManager`。
- **修复双向量池持久化、`external_id` 幂等与启动加载闭环**：`bootstrap` 启动时自动按 `dual_ready.json` 加载 `vectors/paragraph` 与 `vectors/graph` 双池索引，`AppContext.save_all()`、总结任务、导入任务与人物事实写回统一连同模型指纹持久化双池向量；`IngestService.ingest_text` 补齐 `external_memory_refs` 幂等跳过与账本落盘。
- **修复聊天图片 Single-Flight 零冗余缓存采集与异常清理**：默认未开启 `ingest.image_caption.enabled` 时直接在内存中提取图片字节而不产生磁盘临时缓存文件；开启视觉转述时通过事件级 Single-Flight 任务防并发重复拷贝，并在后台任务取消或结束时兜底清理临时文件。
- **加固后台写入跨取消护航与有界优雅停机排空**：引入 `cancellation_safe_to_thread` 与 `cancel_and_wait_for_tasks`，覆盖 `AppContext.save_all()`、`TaskManager`、`ImportTaskManager`、`FeedbackService`、`PersonFactWritebackService` 及内核运行时服务；若后台同步写盘在停机超时窗口内尚未排空，保留 worker 引用、临时目录与 `ScopeRuntime` 句柄直至安全落盘，杜绝关闭底层连接导致的半写异常；同时为人物事实提取增加有界 LRU 缓存（跳过异常与截断 JSON）及总结严格 `facts` 校验。
- **修复事实撤回单调时间戳与向量索引后台回填**：确保同一毫秒内快速先增后撤不会因时间戳相同被误判恢复，补齐 `runtime_admin` 向量重建与缺失回填链路。
- **修复内嵌 WebUI 沙箱兼容与跨作用域串数据缺陷**：引入无查询串上传凭据（`POST /v1/upload-tickets` + `POST /{plugin}/webui/upload/<ticket_id>`）修复 AstrBot `normalizePluginEndpoint` 上传报错；新增知识包原生下载（`GET /{plugin}/webui/bundle-download`）与 `read_chunk` 分块回退、图片二进制 `data_url` 沙箱读取；修复来源列表/批量删除漏传 `_scope`、人物覆盖写入非规范 ID、粘贴 JSON 聊天记录被 Python `repr` 破坏、图谱暗色弹窗与节点切边失灵、检索 `aggregate`/`entity` 丢结果等问题。
- **自动化验证**：新增并通过全套离线 Mock 单元与集成测试（共 247 项后端测试全部通过，`vue-tsc --noEmit` 零错误通过并完成 `pages/memorix/` 生产包构建）。

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
