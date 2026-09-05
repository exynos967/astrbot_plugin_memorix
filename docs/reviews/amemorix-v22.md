# A_memorix schema 22 升级与 review

> 这是 v1.2.0 阶段记录。后续 v1.3.0 的原生 schema、删除恢复、任务一致性、图证据评分和前端入口见 [AstrBot 原生设计](../architecture/astrbot-native-memory.md)。以下“未实现”仅描述当时范围。

日期：2026-09-05。插件基线：`7148eb0b5f0b05b234eaaaa3a84f76d8c66505dc`。目标版本：`v1.2.0`。

上游依据为工作区 `amemorix/`，对应 MaiBot 提交 `fc82c1f2d091345104b5746fe19c1258a214a369` 中的 `src/A_memorix`。此次修改仅位于 AstrBot 插件内，不改变 MaiBot、AstrBot 框架或提取的上游目录。

## Standards：AstrBot 接入与模块边界

| 已核实的问题 | 修复与边界 |
| --- | --- |
| Provider 选择使用同步接口，不能适配当前异步选择流程 | 统一调用 `Context.get_current_chat_provider_id()`；显式 Provider 配置保持优先，图片处理调用链同步改为 await |
| 初始化失败只关闭部分服务；scope 未进入缓存时失败可能泄漏资源 | 入口失败使用统一终止流程，scope 创建失败关闭 TaskManager 和 AppContext |
| 工具管理器容易与通用 ToolSet 混淆 | 按实际 `Context.get_llm_tool_manager()` 返回的 `FunctionToolManager` 使用 `remove_func()`；不采用 ToolSet 的 `remove_tool()` |
| 工具和 WebUI 各自实现图谱写操作，行为不一致 | 共用 `GraphMutationService`，重命名事务和派生索引更新独立为 `GraphRenameService`；同一 AppContext 的图谱写操作串行执行 |
| 新管理能力可能扩大作用域 | 新事实工具沿用管理员检查与事件 scope；新 Web API 使用 Dashboard 已鉴权隧道绑定的 scope，并丢弃 payload 的 scope_key |

核对来源：本地 AstrBot 的 `core/star/context.py`、`core/provider/func_tool_manager.py`、`core/agent/tool.py`、Dashboard plugin API；并通过 Context7 查询了[官方插件文档](https://github.com/astrbotdevs/astrbot-docs/blob/v4/zh/dev/star/plugin.md)。Provider 方法选择以本地框架实际实现为准。

入口继续负责编排；存储新增 mixin、schema 增量模块，管理员业务保留在插件服务层。未引入 MaiBot 的 `src.*` 依赖或全局宿主替身。

## Spec：新版行为与一致性

| 已核实的问题或缺口 | 本次处理 |
| --- | --- |
| 插件停留在 schema 21，事实表没有完整使用链路 | 迁移到 22，移植事实 claim/证据/转换记录与明确冲突、取代、撤回、恢复状态机；接入工具和 API |
| 新版人工人物别名未接入 | 新增别名存储和管理操作，画像使用人工集合；支持人工别名解析，同名歧义要求 person_id |
| 删除段落后事实仍保留支持证据，画像缓存未及时失效 | 删除、软恢复与自动复活处理事实证据快照并使相关画像失效；新事实和别名管理中的状态变更与刷新队列共享事务 |
| 已撤回事实可被自动写回再次恢复 | 非人工重复断言只补证据，保留终止状态，显式管理操作才能恢复 |
| 旧 registry 字符串会重新注入已撤回事实 | 保留原始登记数据供管理查看，不再直接投影进自动画像；自动提取信息以待确认账本事实呈现 |
| 已删除或不完整的 Episode 证据仍可查询 | Episode SQL 要求原 paragraph_count 与现存活跃证据段落数相等 |
| 图谱重命名只改内存图，且合并节点丢失 mention_count | 同步 metadata 实体、关系哈希与段落关联，重建别名映射、保存审计；合并时累计提及次数 |
| 重命名后实体或关系向量遗漏、失败后无法恢复 | 删除旧索引并重建新实体/关系索引，保留待更新记录；启动/后续重命名前重试，未完成时阻止新的重命名 |
| JSON 导入丢失 person_ids，重复导入不补关联 | 校验字符串数组，合并已有和新增人物关联，刷新画像；覆盖实际 native 导入与保留的兼容导入代码 |
| 聊天记录按普通文本切块，连续重试改变分块编号 | 引入消息边界切块、窗口参数与警告，保留原始分块索引；切块参数变化时整文件重试 |
| 双池时间过滤后候选不足、图谱零权重被默认值覆盖 | 根据时间候选倍率扩容并限制 max_scan；写接口验证有限数值并保留零值 |

## 升级范围

- 保留 AstrBot 的 scope 路由、Provider 桥接、transcript、person_registry、异步任务、Episode 兼容队列和 Plugin Pages。
- 新增 `memory_fact_admin`、`memory_profile_admin` 的别名 actions、`POST /v1/facts` 和 `POST /v1/person/aliases`。没有新增前端表单，也未构建静态页面。
- schema 9–21 运行时迁移到 22；v8 及更早版本继续使用原离线脚本。新旧路径都支持回填明确关联人物的原有事实段落，不猜测人物 ID。
- 事实撤回保留原始证据。结构化状态用于画像投影；需要移除历史原文时，仍使用段落/来源删除。
- 本次没有整体替换上游 SDK 内核：上游的图证据 grounding/可靠度重新加权、迁移期旧单池召回合并、MaiBot 的完整投影任务调度仍未全量移植。现有双池迁移方式和 AstrBot 调度方式继续生效。

## 验证与限制

已执行：全部插件 Python 源码 AST 解析、改动适配层的 Ruff 检查、整个 memorix 的语法/未定义名称级静态检查、diff 空白检查、修改/新增文件 UTF-8 无 BOM 检查。两轮只读独立 review 的具体问题已逐项核对和修正。

新增 `tests/test_upstream_v22.py`，覆盖迁移保留数据、事实冲突与显式取代、刷新队列失败回滚、删除恢复、禁止自动恢复人工撤回、别名歧义、重命名关系关联与合并计数、聊天记录边界、JSON 人物校验、重导入关联合并和连续重试索引。已同步原有 schema/API/人物事实用例的预期。

依照用户约定，**未运行 pytest、构建、真实 AstrBot 启动、Embedding/聊天模型请求或实际数据库迁移**。上述静态检查不构成运行时回归通过的结论。代码尚未提交或发布。
