# AstrBot 原生记忆插件的能力整合

2026-09-05，目标插件版本 `1.3.0`，自有 schema `23`。功能依据为工作区 MaiBot / A_memorix 的当前实现；取舍和服务边界由 AstrBot 的使用方式决定。

## 采用的能力

| 能力 | AstrBot 实现与实际入口 |
| --- | --- |
| 结构化事实、证据、冲突、撤回与恢复 | 事实存储模块、`FactAdminService`、管理员 FunctionTool、`/v1/facts` 和人物页面表单 |
| 人工人物别名 | 别名存储、人物解析及画像投影、`/v1/person/aliases` 和人物页面表单；歧义不自动选人 |
| 操作级删除恢复 | `MemoryOperationsMixin` 保存资源、关联与外部引用快照；`DeleteService` 统一工具、来源删除、图谱删除、Web API。返回 `operation_id`，支持整批恢复及显式 purge |
| 删除后的一致性 | 段落删除解除事实证据、失效画像、触发 Episode 重建；共享关系有其他活跃证据时保留。恢复使用删除归属记录，避免旧操作撤销新删除或覆盖重写内容 |
| 整库软删除 | 同时撤回当前事实、保存人物覆盖与历史画像。恢复不覆盖后续人工更改；历史画像恢复后过期，重新生成当前画像。聊天 transcript 继续由 AstrBot 原生总结流程管理 |
| 派生索引重试 | SQLite 中的 `memory_projection_jobs` 与内容/生存状态变更一起提交；后台重新读取当前 metadata，同步单池或双池向量及图。按版本完成任务，失败保留错误和退避重试时间 |
| SQLite 线程隔离 | 每线程连接、WAL、嵌套事务/savepoint；内部 CRUD 的 commit 不能提前提交显式外层事务。事务块只包含同步存储操作，不跨 await |
| Episode 并发一致性 | 保留原生 `EpisodeService` 和 `TaskManager`；拆分生成计划与发布。claim、续租、revision、配置指纹和发布共同阻止旧结果覆盖新消息。取消释放租约，崩溃后过期任务可重新领取 |
| 图证据评分 | 独立纯算法模块 `graph_evidence.py`；根据支撑段落中的实体/谓词落地、多通道一致性、支持关系覆盖调整双池图权重，保留评分明细 |
| 聊天记录与文件导入 | 原生 `ImportTaskManager` 接消息边界切块与连续重试；上传通过 Plugin Pages `upload()` 和 `PluginUploadFile`。来源字段贯穿到存储，保留大小、后缀及路径约束 |

导入任务及临时文件保留 24 小时供本次运行中的失败重试；过期终态任务和启动时发现的过期孤立任务目录会回收。仍被新重试任务使用的文件不会提前删除。任务队列保持内存实现，插件重启后未完成的导入需要重新提交。

## 保留的 AstrBot 设计

- Plugin Pages 复用 Dashboard 身份校验；Bot 管理工具复用 AstrBot 管理员权限和当前事件作用域，不接受模型指定跨作用域写入。
- 人物查询、人工覆盖、事实与别名表单统一使用当前页面选择的作用域；旧请求不会覆盖切换后的页面。任务轮询同样校验作用域与任务 ID。
- transcript、原生 ConversationManager/hybrid 总结、scope runtime 和 Provider 选择继续使用 AstrBot 实现，不接入 MaiBot 的消息数据库或全局宿主对象。
- 双向量池继续使用显式离线迁移。没有加入运行时自动搬库或迁移期旧池混合召回，避免在正在使用的记忆库上隐式重写索引。
- 生命周期保留可解释的半衰期、冻结宽限期与回收站流程；补齐重复冻结不重置时间、保护/永久记忆不自动剪枝、剪枝进入操作记录和索引重试。没有直接替换为 MaiBot 的整套自适应保留模型。
- 图谱合法零权重保留为零：元数据可查询，但不贡献图遍历权重；不会偷偷提升到最小正值。同一端点关系投影使用 `batch_update()` 的覆盖语义，避免重复累加。

## 接口和版本

新增 `POST /v1/memory/delete-admin`，请求与其他管理接口一致：

```json
{"action":"restore","payload":{"operation_id":"删除结果中的 operation_id"}}
```

支持 `preview/execute/restore/list_operations/get_operation/purge`。`purge` 显式销毁过期删除操作的资源快照与归属该操作的已删除资源；保留审计摘要，之后不能再恢复该操作。自动维护不调用 purge。

上传使用 `webui/upload`；先通过 `GET /v1/import/limits` 获取限制，再由 AstrBot SDK 发送固定字段 `file`。scope 与白名单导入参数经 query 传递。服务器在解析 multipart 前检查 Content-Length，并在读取文件时再次限制大小。

Web handler 使用公开的 `astrbot.api.web.request/json_response/error_response/PluginUploadFile`，最低版本调整为 **AstrBot 4.26**。已核对本地框架签名和引入提交所在的版本标签，并核对[官方 Web API 实现](https://github.com/AstrBotDevs/AstrBot/blob/v4.26.0/astrbot/api/web.py)与[Plugin Pages 指南](https://github.com/AstrBotDevs/AstrBot/blob/master/docs/zh/dev/star/guides/plugin-pages.md)。Context7 当前文档索引未返回这一新版接口的详细内容，因此接口选择以框架源码为依据。

可识别的旧库（包括 v8、schema 9–22 及字段指纹匹配的无版本库）首次打开时自动备份并事务迁移到 schema 23；详见 [自动迁移说明](database-migrations.md)。离线脚本保留为批量预检和停机维护入口。版本号从本版起表达本插件自己的存储演进，不表示与 MaiBot schema 一一对应。未修改 MaiBot、AstrBot 框架或提取的 amemorix 目录。

## Review 与验证边界

独立复核覆盖存储、任务/投影和前后端接口。已修正作用域竞态、上传来源丢失、临时文件保留期、图谱孤立边删除的锁，以及历史画像快照恢复。保留零权重语义；复核提出的重复累加疑虑经检查 `GraphStore.batch_update()` 后排除，该上下文会切到 LIL 覆盖模式。

新增回归用例覆盖事务回滚、线程隔离、共享证据恢复、旧删除归属保护、Episode 版本/租约、持久化重试和图证据评分。依用户约定，未运行测试、前端构建、实际数据库迁移或真实 Bot/模型调用。静态核验包含 Python AST、Ruff、Vue/TypeScript `vue-tsc --noEmit`、UTF-8 无 BOM 和 diff 空白检查。

**新增页面仅更新 Vue 源码，尚未重新生成随插件分发的静态页面。** 发布前仍需用户允许后构建前端并运行回归与 AstrBot 实机验证。上述静态检查不能证明运行时回归通过。
