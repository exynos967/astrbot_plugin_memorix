<div align="center">

![:name](https://count.getloli.com/@:astrbot_plugin_memorix?name=%3Aastrbot_plugin_memorix&theme=miku&padding=7&offset=0&align=top&scale=1&pixelated=1&darkmode=auto)

# Memorix

**为 AstrBot 打造的完整记忆系统插件**

图谱 + 向量混合检索 · 记忆生命周期管理 · 人物画像 · 总结导入 · 内嵌 WebUI

[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.16-blue)](https://github.com/Soulter/AstrBot)
[![Version](https://img.shields.io/badge/version-v0.9.5-green)]()
[![Platforms](https://img.shields.io/badge/platforms-QQ%20%7C%20Telegram%20%7C%20Discord-orange)]()

</div>

---

## 为什么选择 Memorix？

| | 传统方案 | Memorix |
|---|---|---|
| **存储** | 单一向量库或纯文本缓存 | 段落向量 + 实体图谱 + 时间线三维存储 |
| **检索** | 仅语义相似度 | 向量 + BM25 稀疏召回 + 图谱 PageRank 重排 |
| **生命周期** | 记忆只增不减 | 自动衰减 → 冻结 → 剪枝，支持保护/强化/恢复 |
| **可观测性** | 黑盒 | 内嵌 WebUI，图谱可视化、来源追溯、回收站 |
| **部署要求** | 依赖外部模型/服务 | 零外部依赖可启动，embedding 按需开启 |

## 核心特性

### 混合检索引擎

采用双路检索架构，向量路径（FAISS / Numpy 余弦）与稀疏路径（BM25 + Jieba 中文分词）并行召回，通过加权 RRF 融合排序。可选启用 Personalized PageRank 利用图谱拓扑进行二次重排，无需额外 reranker 模型。

### 智能查询路由

插件可根据用户提问内容自动选择最佳检索模式：识别到「昨天」「上周」「最近一个月」等时间表达时自动切换为时序检索或混合检索；普通语义问题则走标准向量+稀疏路径。也支持 Episode 事件摘要检索和聚合检索，多通道结果通过可配置权重的 RRF 融合排序。

### 记忆生命周期

记忆不是静态数据，而是有"生命"的：

- **衰减**：关系权重按半衰期（默认 24h）指数衰减
- **冻结**：低于活跃阈值进入冻结态，保留 24h 等待唤醒
- **剪枝**：权重降至阈值以下则移入回收站
- **保护** / **强化** / **恢复**：主动干预记忆命运

### 人物画像系统

自动从消息中提取发送者信息，后台定期刷新画像（默认 6 小时 TTL）。LLM 请求时自动注入发送者画像作为上下文参考。支持手动覆盖与清除。

### 人物事实写回

机器人回复后，自动从用户发言中提取稳定的个人事实（如「我是学生」「我喜欢打篮球」），写入长期记忆和用户画像。支持队列化异步提取、每用户特征上限管理、独立 Provider 和温度参数配置。

### 总结导入

会话总结支持三种数据源：插件 transcript、AstrBot 原生会话历史、以及 hybrid 回退策略。可手动触发，也可按新增消息数+冷却时间自动触发。通过 LLM 对对话生成结构化摘要并回写至记忆系统，支持叙事、事实、结构化等多种知识类型。

### 事件摘要

自动将聊天内容按话题分段，后台异步生成可检索的事件摘要。例如问「上周发生了什么」时，机器人能给出完整的事件概述，而不是零散的句子。支持配置生成间隔、批次大小和重试策略。

### 内嵌 WebUI

基于 AstrBot Plugin Pages 的 Dashboard 内嵌管理界面，提供：图谱浏览与关系编辑、记忆管理与来源追踪、回收站恢复、人物画像管理。接口经 AstrBot Dashboard 鉴权后转发到插件内 runtime。

### 聊天过滤

支持白名单/黑名单两种模式，精确控制哪些群聊或私聊可以使用记忆功能。过滤粒度支持 `group:群号`、`user:用户号`、`stream:频道标识` 等格式。

### 导入中心

Dashboard 内嵌导入视图默认启用。页面可进行如下三种导入：
- 上传文件导入（`.txt/.md/.json`）
- 粘贴文本导入
- 原始目录扫描导入（`raw` / `plugin_data` 别名）

导入中心支持手动选择 `knowledge_type`（`auto/factual/narrative/structured/mixed`），并提供任务级/文件级/分块级状态观察、任务取消与失败重试。

### 记忆模糊修正

支持基于自然语言请求对既有记忆进行模糊修正：LLM 根据用户的修改诉求生成结构化修改计划，先 **preview** 预览待改写项，由用户/管理员 **确认** 后再 **execute** 落库，并保留 **rollback** 回滚能力。

- **默认启用**：`integration.fuzzy_modify.enabled=True`，与上游 A_memorix 对齐；如需关闭可显式设为 `False`。
- **安全默认**：`auto_execute_enabled=False`（必须经过显式 confirmed 才会执行）、`confirm_threshold=0.85`、`allow_global_scope=False`（禁止跨作用域全局修改）。
- **管理入口**：管理工具 `memory_fuzzy_modify_admin`（actions：`preview` / `execute` / `get` / `list` / `rollback` / `reconcile`），仅 AstrBot 管理员事件可调用；WebUI 路由 `POST /v1/fuzzy_modify`。

#### 本插件基于 A_Dawn 的 A_Memorix 设计理念开发，并针对 AstrBot 做了完整适配。

## 工作流

```
消息到达
  │
  ▼
① 作用域路由 ── 按 scope.mode 确定记忆归属
  │
  ▼
② 聊天过滤 ── 按白名单/黑名单决定是否处理该会话
  │
  ▼
③ 消息清洗与内容路由 ── 默认采用 transcript_only：先生成 processed_plain_text 并进入 transcript，再由总结任务提炼长期记忆；可切到 direct/both/auto 获得直写或事实候选直写
  │
  ▼
④ 写回提炼 ── 自动/手动总结带状态游标；人物事实写回会在机器人回复后提取用户稳定事实
  │
  ▼
⑤ 检索注入 ── LLM 请求时按 scope/source 混合检索记忆，支持自动查询路由（语义/时序/混合/聚合）
  │
  ▼
⑥ 后台维护 ── 衰减 / 冻结 / 剪枝 / 画像刷新 / Episode 生成 / 向量持久化
```

## 快速开始

### 安装

在 AstrBot 插件管理中搜索 `Memorix` 安装，或通过仓库地址安装：

```
https://github.com/exynos967/astrbot_plugin_memorix
```

### 推荐配置（启用独立 Embedding）

聊天模型复用 AstrBot 已定义 Provider；Embedding 没有 AstrBot bridge 时在插件内独立配置 OpenAI-compatible 端点：

| 配置项 | 值 | 说明 |
|---|---|---|
| `provider.chat_provider_id` | AstrBot 中的聊天 Provider ID（可选） | 指定后使用该模型做总结/画像；留空使用当前会话默认 Provider |
| `embedding.enabled` | `true` | 启用远程向量化 |
| `embedding.openapi.base_url` | 你的 Embedding API 地址 | 必填；支持不带 `/v1`，插件会自动补全 |
| `embedding.openapi.api_key` | 你的 API Key | 远程鉴权 |
| `embedding.openapi.model` | 你的 Embedding 模型名 | 必填，如 `text-embedding-3-large` |

## 使用方式

- **日常记忆写入/召回**：请求前会自动注入当前聊天相关的长期记忆和人物画像；LLM 也可继续通过 `search_memory`、`get_person_profile` 等工具按需补查，通过 `ingest_summary`、`ingest_text`、`maintain_memory`、`memory_stats` 写入或维护记忆。
- **管理员记忆维护**：已注册的管理工具 `memory_graph_admin`、`memory_source_admin`、`memory_episode_admin`、`memory_profile_admin`、`memory_runtime_admin`、`memory_import_admin`、`memory_tuning_admin`、`memory_v5_admin`、`memory_delete_admin`；这些工具仅 AstrBot 管理员事件可调用。
- **图谱、检索、导入、总结、回收站、画像覆盖等管理操作**：可在 AstrBot Dashboard 的插件详情页打开 `Memorix 控制台`，也可由管理员通过上述管理工具让 LLM 执行。
- **作用域、检索、生命周期、人物画像、自动总结等策略**：在 AstrBot 插件配置页修改配置项。

## 作用域模式

`scope.mode` 决定哪些会话共享同一份记忆：

| 模式 | 行为 | 适用场景 |
|---|---|---|
| `platform_global` | 同平台所有会话共享 | 希望机器人跨群/跨会话保持记忆连续性 |
| `user_global` | 同平台按用户隔离 | 需要用户级隐私隔离 |
| `group_global` | 同平台按群隔离，私聊退化为用户隔离 | 以群为单位沉淀独立记忆，降低串群污染 |
| `umo` | 按 `unified_msg_origin` 最细粒度隔离 | 最严格的隔离需求 |

## 存储架构

```
data/plugin_data/astrbot_plugin_memorix/scopes/<scope_key>/
├── vectors/      # FAISS / Numpy 向量索引
├── graph/        # SciPy 稀疏矩阵图谱
└── metadata/     # SQLite 元数据（段落/实体/关系/对话/画像/任务）
```

| 存储层 | 实现 | 职责 |
|---|---|---|
| 向量存储 | FAISS（降级 Numpy 余弦） | 段落和关系的语义向量索引 |
| 图谱存储 | SciPy 稀疏矩阵 | 实体节点 + 关系边权重图 |
| 元数据存储 | SQLite | 结构化数据与全文检索（FTS5） |

### 数据库 Schema 版本

> **破坏性更新提醒**
>
> 从 `main` 旧版升级到 feat2.0 / SCHEMA 15 时，旧版 `metadata.db` 为 `SCHEMA_VERSION=8`，新版本运行时不会直接打开 v8 库。请先停止插件，备份 `data/plugin_data/astrbot_plugin_memorix/`，并按下方步骤执行一次离线迁移脚本，自动扫描所有 scope 后再启动新版本。

当前元数据 Schema 版本为 **SCHEMA 15**。相对前一版本，本次升级包含两项结构变更：

- **新增 `fuzzy_modify_plans` 表**：存放模糊修正功能的修改计划与执行状态（preview / confirmed / executed / rolled_back），与模糊修正功能配套。
- **`stale_relation` 表新增三列**：`source_type`、`source_id`、`source_operation_id`，用于精确标记关系条目的来源类型与具体来源操作 ID，便于追溯与回收。

升级路径：

- **从 `main` 旧版升级**：`main` 使用 `SCHEMA_VERSION=8`，feat2.0 运行时不会直接打开 v8 库。升级代码后、启动插件前，执行一次离线迁移脚本即可自动扫描所有 scope：

  ```bash
  uv run --no-project python scripts/migrate_schema_v8_to_v13.py
  ```

  脚本默认查找 `data/plugin_data/astrbot_plugin_memorix/scopes/*/metadata/metadata.db`；如果数据目录不在默认位置，可加 `--plugin-data-dir /path/to/data/plugin_data/astrbot_plugin_memorix`。高级用户仍可用 `--db /path/to/metadata.db` 只迁移单个数据库。脚本文件名保留历史兼容，实际目标版本取当前代码的 `SCHEMA_VERSION`（本版为 15）。脚本会先备份原库，迁移后旧段落/关系数据保留。
- **从已版本化的新库升级**：`schema_version >= 9 && < 15` 时，插件启动会自动迁移到 15。
- **全新安装**：直接创建 SCHEMA 15 库。

### 双向量池架构（dual-pool）

SCHEMA 15 起支持双向量池模式，将 **段落向量** 与 **图谱向量** 分离到独立索引，便于独立扩容、独立检索调参。

- **默认 `mode=dual`，与上游 A_memorix 对齐**：如果未生成 `dual_ready.json`，运行时会自动降级为 single，不会因配置错配而无法检索。
- **正式启用双池步骤**：
  1. 运行离线迁移脚本预览分流结果（先 dry-run 再正式迁移）：
     ```
     uv run --no-project python scripts/migrate_vectors_to_dual_pools.py --data-dir <scope目录> [--scope <scope_key>] [--dry-run]
     ```
  2. 迁移成功后，脚本会在 `vectors/` 下写入 `dual_ready.json` manifest，运行时据此判定 dual 是否就绪；
  3. 若 manifest 缺失，运行时自动降级为 single。
- 迁移脚本幂等，不删除单池原数据，dual 模式可安全回退至 single。

## 完整配置参考

<details>
<summary>点击展开全部配置项</summary>

### 作用域（scope）

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `scope.mode` | string | `group_global` | 作用域模式 |

### 写入（ingest）

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `ingest.record_all_events` | bool | `true` | 是否记录所有消息事件 |
| `ingest.skip_command_messages` | bool | `true` | 忽略命令消息（按 `command_prefixes` 判断） |
| `ingest.memory_write_mode` | string | `transcript_only` | 写入模式：`transcript_only` 先保留聊天流水并由自动总结提炼长期记忆；`direct`/`both` 会额外直接写入长期记忆；`auto` 只让事实候选消息直写 |
| `ingest.direct_write_assistant` | bool | `true` | 是否将机器人回复也直接写入长期记忆（仅 direct/both 模式生效） |
| `ingest.content_router.enabled` | bool | `true` | 启用内容路由器，决定 transcript/直写策略 |
| `ingest.content_router.drop_ephemeral_transcript` | bool | `false` | 是否直接丢弃寒暄/纯占位消息的 transcript |
| `ingest.content_router.auto_direct_min_chars` | int | `12` | `auto` 模式下事实候选消息直写所需的最小字符数 |
| `ingest.command_prefixes` | list | `["/"]` | 命令前缀列表（支持自定义前缀，如 `["/", "!", "."]`） |
| `ingest.skip_placeholder_only` | bool | `true` | 忽略只有 `[图片]`、`[表情]`、`[转发消息]` 等占位符的消息 |
| `ingest.max_message_chars` | int | `2000` | 单条消息平面化后写入 transcript/记忆的最大字符数 |
| `ingest.max_forward_fetch` | int | `8` | OneBot 合并转发解析时最多拉取的转发消息层数/批次 |
| `ingest.image_caption.enabled` | bool | `false` | 是否调用视觉模型对图片做转述；默认关闭以避免额外 LLM 成本 |
| `ingest.image_caption.provider_id` | string | `""` | 图片转述专用 Provider ID；留空使用当前会话 Provider |
| `ingest.image_caption.max_count` | int | `1` | 单条消息最多转述的图片数量 |
| `ingest.image_caption.prompt` | string | `"请简洁描述这张图片。"` | 图片转述发给视觉模型的提示词 |

### 人物事实写回（person_fact_writeback）

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `person_fact_writeback.enabled` | bool | `true` | 启用后，机器人回复后从用户原始发言中提取稳定人物事实 |
| `person_fact_writeback.queue_maxsize` | int | `256` | 待处理队列上限 |
| `person_fact_writeback.min_user_text_chars` | int | `4` | 用户消息最少字符数才尝试提取 |
| `person_fact_writeback.max_facts_per_turn` | int | `5` | 单轮最多写入事实数 |
| `person_fact_writeback.max_registry_facts` | int | `30` | 每用户画像最多保留的特征条目数 |
| `person_fact_writeback.max_evidence_chars` | int | `800` | 发给 LLM 分析的用户消息最大字符数 |
| `person_fact_writeback.update_registry_memory_points` | bool | `true` | 同步把事实追加到人物画像 memory_points |
| `person_fact_writeback.chat_provider_id` | string | `""` | 事实提取专用 Provider ID，留空使用当前/默认 Provider |
| `person_fact_writeback.temperature` | float | `0.1` | 事实提取 LLM 温度参数 |
| `person_fact_writeback.max_tokens` | int | `800` | 事实提取最大输出 token 数 |

### 提供商（provider）

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `provider.chat_provider_id` | string | `""` | 可选指定 AstrBot 聊天 Provider；为空时回退当前会话 provider |

### Embedding

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `embedding.enabled` | bool | `false` | 启用插件内 OpenAI-compatible embedding（关闭则本地回退） |
| `embedding.dimension` | int | `1024` | 向量维度 |
| `embedding.batch_size` | int | `32` | 批量请求大小 |
| `embedding.max_concurrent` | int | `5` | 最大并发请求数 |
| `embedding.openapi.base_url` | string | `""` | Embedding API Base URL（启用远程 embedding 时必填，可不带 `/v1`） |
| `embedding.openapi.api_key` | string | `""` | Embedding API Key |
| `embedding.openapi.model` | string | `""` | Embedding 模型名（启用远程 embedding 时必填） |
| `embedding.openapi.timeout_seconds` | float | `30` | Embedding 请求超时（秒） |
| `embedding.openapi.max_retries` | int | `3` | Embedding 请求重试次数 |

### 检索（retrieval）

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `retrieval.top_k_final` | int | `10` | 默认返回结果数 |
| `retrieval.auto_inject.enabled` | bool | `true` | 每次 LLM 请求前自动检索并注入相关长期记忆 |
| `retrieval.auto_inject.top_k` | int | `5` | 自动注入最多包含的记忆条数 |
| `retrieval.auto_inject.min_query_chars` | int | `4` | 当前消息少于该长度时不触发自动检索 |
| `retrieval.enable_ppr` | bool | `true` | 启用 PageRank 重排 |
| `retrieval.enable_parallel` | bool | `true` | 并行检索 |
| `retrieval.temporal.enabled` | bool | `true` | 启用时序检索 |
| `retrieval.temporal.default_top_k` | int | `10` | 时序检索默认 top_k |
| `retrieval.auto_route.enabled` | bool | `true` | 自动选择检索模式（语义/时序/混合） |
| `retrieval.auto_route.enable_time_intent` | bool | `true` | 识别自然语言时间表达（昨天/上周等） |
| `retrieval.aggregate.rrf_k` | int | `60` | 聚合检索 RRF 融合 K 值 |
| `retrieval.aggregate.weights.search` | float | `1.0` | 语义检索通道权重 |
| `retrieval.aggregate.weights.time` | float | `1.0` | 时序检索通道权重 |
| `retrieval.aggregate.weights.episode` | float | `1.0` | 事件摘要检索通道权重 |
| `retrieval.vector_pools.mode` | string | `dual` | 向量池模式：`dual` 与上游默认对齐；缺少 `dual_ready.json` 时运行时降级为 `single` |

### 记忆维护（memory）

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `memory.enabled` | bool | `true` | 启用记忆维护 |
| `memory.half_life_hours` | float | `24.0` | 半衰期（小时） |
| `memory.base_decay_interval_hours` | float | `1.0` | 衰减执行周期（小时） |
| `memory.prune_threshold` | float | `0.1` | 剪枝阈值 |
| `memory.freeze_duration_hours` | float | `24.0` | 冻结保留时长（小时） |
| `memory.max_weight` | float | `10.0` | 关系边最大权重 |
| `memory.auto_protect_ttl_hours` | float | `24.0` | 强化自动保护时长（小时） |

### 人物画像（person_profile）

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `person_profile.enabled` | bool | `true` | 启用人物画像 |
| `person_profile.profile_ttl_minutes` | int | `360` | 画像缓存 TTL（分钟） |
| `person_profile.top_k_evidence` | int | `12` | 画像生成证据数量 |
| `person_profile.injection_max_profiles` | int | `3` | 每轮自动注入的人物画像数量上限 |

### 总结（summarization）

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `summarization.enabled` | bool | `true` | 启用总结导入 |
| `summarization.source_mode` | string | `transcript` | 总结来源模式：`transcript`（推荐） / `astrbot` / `hybrid` |
| `summarization.context_length` | int | `50` | 总结上下文长度 |
| `summarization.default_knowledge_type` | string | `narrative` | 总结知识类型（narrative / factual / mixed / structured / auto） |
| `summarization.auto_import.enabled` | bool | `true` | 启用自动总结 |
| `summarization.auto_import.after_reply_only` | bool | `true` | 只在机器人回复后才尝试总结 |
| `summarization.auto_import.min_new_messages` | int | `12` | 触发总结需要的最少新消息数 |
| `summarization.auto_import.cooldown_minutes` | int | `30` | 两次自动总结的最短间隔（分钟） |

### 事件摘要（episode）

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `episode.enabled` | bool | `true` | 启用事件摘要 |
| `episode.generation_enabled` | bool | `true` | 后台自动生成事件摘要 |
| `episode.generation_interval_seconds` | int | `30` | 后台检查间隔（秒） |
| `episode.generation_batch_size` | int | `20` | 每次处理的聊天来源数 |
| `episode.max_retry` | int | `3` | 生成失败重试次数 |
| `episode.segmentation_model` | string | `auto` | 话题分段模型（auto 使用默认 Provider） |

### 聊天过滤（filter）

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `filter.enabled` | bool | `true` | 启用聊天过滤 |
| `filter.mode` | string | `blacklist` | 过滤模式：`whitelist`（白名单）/ `blacklist`（黑名单） |
| `filter.chats` | list | `[]` | 聊天标识列表（格式：`group:123456`、`user:789012`、`stream:xxx`） |

### WebUI

插件只保留 **AstrBot Dashboard 内嵌页**：AstrBot `>=4.24.2` 可在插件详情页的 `Memorix 控制台` 页面中直接打开，接口经 AstrBot Dashboard 鉴权后转发到当前 Memorix runtime。

图谱页可通过 **群过滤** 下拉选择 scope，显示格式为 `平台:群号`，例如 `aiocqhttp:123456`。

### 导入中心（web.import）

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `web.import.max_queue_size` | int | `20` | 导入任务队列上限 |
| `web.import.max_files_per_task` | int | `200` | 单任务最大文件数 |
| `web.import.max_file_size_mb` | int | `20` | 单文件大小上限（MB） |
| `web.import.max_paste_chars` | int | `200000` | 粘贴导入字符上限 |
| `web.import.default_file_concurrency` | int | `2` | 默认文件并发（预留） |
| `web.import.default_chunk_concurrency` | int | `4` | 默认分块并发（预留） |
| `web.import.path_aliases.raw` | string | `raw` | 原始目录扫描别名（相对 `storage.data_dir`） |
| `web.import.path_aliases.plugin_data` | string | `.` | 插件数据目录别名（相对 `storage.data_dir`） |

### 模糊修正（integration.fuzzy_modify）

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `integration.fuzzy_modify.enabled` | bool | `true` | 启用记忆模糊修正功能；默认与上游 A_memorix 对齐 |
| `integration.fuzzy_modify.auto_execute_enabled` | bool | `false` | 是否允许跳过显式确认直接执行；默认必须显式 confirmed |
| `integration.fuzzy_modify.confirm_threshold` | float | `0.85` | 计划确认阈值，低于该阈值需人工复核 |
| `integration.fuzzy_modify.max_targets` | int | `5` | 单个计划允许修改的目标上限 |
| `integration.fuzzy_modify.allow_global_scope` | bool | `false` | 是否允许跨作用域的全局修改；默认禁止 |

</details>

## 前端目录说明

- AstrBot Dashboard 插件内嵌页读取：`pages/memorix/*`

## 依赖

| 包 | 用途 |
|---|---|
| `numpy` | 向量计算 & 降级向量存储 |
| `scipy` | 图谱稀疏矩阵 |
| `faiss-cpu` | 高性能向量索引（失败自动降级 Numpy） |
| `fastapi` | Dashboard 内嵌 WebUI API 应用 |
| `httpx` | AstrBot Dashboard 内嵌页到 FastAPI WebUI 的进程内请求转发 |
| `pydantic` | 数据校验 |
| `jieba` | 中文分词（BM25 检索） |
| `openai` | OpenAI-compatible Embedding 客户端 |

## 常见问题

<details>
<summary>没有配置 Embedding API 能用吗？</summary>

可以。`embedding.enabled=false`（默认值）时，插件使用本地确定性向量回退，所有功能正常加载。启用远程 embedding 后必须在插件配置中填写 `embedding.openapi.base_url` 和 `embedding.openapi.model`；插件不会从 `OPENAPI_*` / `OPENAI_*` 环境变量兜底读取。

</details>

<details>
<summary>FAISS 安装失败怎么办？</summary>

插件会自动降级到 Numpy 余弦相似度实现，功能完全可用，仅在超大规模数据时性能有差异。无需手动干预。

</details>

<details>
<summary>PageRank 重排需要额外模型吗？</summary>

不需要。`retrieval.enable_ppr` 基于图谱拓扑结构计算，是纯算法重排，不依赖任何外部模型。

</details>

<details>
<summary>如何清理旧记忆？</summary>

记忆系统自带生命周期管理：衰减 → 冻结 → 剪枝自动进行。也可通过内嵌 WebUI 手动管理，或由 LLM 在用户明确要求管理记忆时调用 `maintain_memory` 保护重要记忆。

</details>

<details>
<summary>在 group_global 模式下提示日志显示图已保存，但 WebUI 还是空白</summary>

大概率是 WebUI 当前绑定的 `scope` 和你实际有数据的对话范围不一致。

如果日志里已经出现 `graph saved`，但同时还有 `图为空，无法计算PageRank`，说明当前 WebUI 读到的那份图仍然是空的。

建议在你要查看的那个群或私聊里按顺序执行：

1. 先在目标群或私聊里发送一条普通消息，让插件记录并刷新当前作用域
2. 在 AstrBot Dashboard 插件详情页打开 `Memorix 控制台`
3. 在图谱页的 **群过滤** 下拉中选择对应的 `平台:群号`

</details>

<details>
<summary>如何只让部分群使用记忆功能？</summary>

启用聊天过滤功能。

</details>

### TODO
- 优化plugin page性能
- 提高图谱可读性

## 特别感谢

- [ARC](https://github.com/A-Dawn)
- [A_memorix](https://github.com/A-Dawn/A_memorix/tree/basic)

## 许可证

本项目遵循 [AGPLv3 License](LICENSE)。
