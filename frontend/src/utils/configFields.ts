// A_Memorix v2.0 配置表单字段规范（严格对齐 _conf_schema.json 全部 7 大核心模块 + 运行时阈值）。

export type ConfigFieldType =
  | "boolean"
  | "number"
  | "string"
  | "password"
  | "select"
  | "list";

export type ConfigFieldValue = boolean | number | string | string[];

export interface ConfigSelectOption {
  value: string;
  label: string;
}

export interface ConfigField {
  key: string;
  label: string;
  type: ConfigFieldType;
  default: ConfigFieldValue;
  min?: number;
  max?: number;
  step?: number;
  hint?: string;
  placeholder?: string;
  options?: ConfigSelectOption[];
  /** 仅在内存运行时存在、不在 _conf_schema.json 中的动态参数。 */
  runtimeOnly?: boolean;
}

export type ConfigFieldSpec = ConfigField;

export interface ConfigFieldGroup {
  id: string;
  title: string;
  description?: string;
  fields: ConfigField[];
}

export const CONFIG_FIELDS: ConfigFieldGroup[] = [
  {
    id: "scope_model",
    title: "作用域与模型",
    description: "记忆隔离范围、LLM 总结/画像 Provider 与远程 Embedding 向量模型。",
    fields: [
      {
        key: "scope.mode",
        label: "记忆共享模式",
        type: "select",
        default: "group_global",
        hint: "决定记忆在哪些会话间互通（平台全局 / 按用户 / 按群聊 / 单会话 UMO）。",
        options: [
          { value: "group_global", label: "按群隔离（group_global）" },
          { value: "user_global", label: "按用户隔离（user_global）" },
          { value: "platform_global", label: "同平台共享（platform_global）" },
          { value: "umo", label: "单聊天窗口独立（umo）" },
        ],
      },
      {
        key: "provider.chat_provider_id",
        label: "默认 LLM Provider ID",
        type: "string",
        default: "",
        placeholder: "留空使用当前会话默认 Provider",
        hint: "用于总结、人物画像与事件摘要生成的聊天模型标识。",
      },
      {
        key: "provider.fast_chat_provider_id",
        label: "轻量任务 LLM Provider ID",
        type: "string",
        default: "",
        placeholder: "留空自动回退主模型或当前会话默认 Provider",
        hint: "用于反馈纠错分类、检索调优自然语言改写、聊天日志时间抽取与画像分类等轻量任务。",
      },
      {
        key: "embedding.enabled",
        label: "启用远程 Embedding 服务",
        type: "boolean",
        default: false,
        hint: "开启后调用 OpenAI 兼容 Embedding API；关闭时回退本地确定性向量。",
      },
      {
        key: "embedding.enable_cache",
        label: "启用 Embedding 内存缓存",
        type: "boolean",
        default: false,
        hint: "开启后对重复文本向量查询启用进程内 LRU 缓存，降低远程 API 调用与延迟。",
      },
      {
        key: "embedding.dimension",
        label: "Embedding 向量维度",
        type: "number",
        min: 32,
        max: 8192,
        step: 1,
        default: 1024,
        hint: "必须与所选 Embedding 模型实际输出维度一致。",
      },
      {
        key: "embedding.batch_size",
        label: "Embedding 批处理条数",
        type: "number",
        min: 1,
        max: 256,
        step: 1,
        default: 32,
        hint: "单次请求编码的文本条数，建议 8~64。",
      },
      {
        key: "embedding.max_concurrent",
        label: "Embedding 最大并发请求数",
        type: "number",
        min: 1,
        max: 64,
        step: 1,
        default: 5,
      },
      {
        key: "embedding.openapi.base_url",
        label: "Embedding API 地址",
        type: "string",
        default: "",
        placeholder: "https://api.example.com",
        hint: "OpenAI 兼容服务地址（末尾无需手动拼接 /v1）。",
      },
      {
        key: "embedding.openapi.api_key",
        label: "Embedding API 密钥",
        type: "password",
        default: "",
        placeholder: "未修改时保留已配置的脱敏密钥",
      },
      {
        key: "embedding.openapi.model",
        label: "Embedding 模型名称",
        type: "string",
        default: "",
        placeholder: "例如：BAAI/bge-m3 或 text-embedding-3-small",
      },
      {
        key: "embedding.openapi.timeout_seconds",
        label: "Embedding 请求超时（秒）",
        type: "number",
        min: 1,
        max: 300,
        step: 1,
        default: 30,
      },
      {
        key: "embedding.openapi.max_retries",
        label: "Embedding 失败重试次数",
        type: "number",
        min: 0,
        max: 10,
        step: 1,
        default: 3,
      },
    ],
  },
  {
    id: "retrieval_pools",
    title: "混合检索与双向量池",
    description: "自动注入、PageRank 图谱重排、v2.0 双向量池（段落池 + 图谱池）融合校准与动态阈值。",
    fields: [
      {
        key: "retrieval.top_k_final",
        label: "最终召回记忆条数上限",
        type: "number",
        min: 1,
        max: 100,
        step: 1,
        default: 10,
        hint: "单次检索最终返回/注入上下文的最大记忆条数。",
      },
      {
        key: "retrieval.auto_inject.enabled",
        label: "请求前自动注入长期记忆",
        type: "boolean",
        default: true,
        hint: "每次回复前根据当前消息自动召回长期记忆作为内部参考。",
      },
      {
        key: "retrieval.auto_inject.top_k",
        label: "自动注入最大条数",
        type: "number",
        min: 1,
        max: 50,
        step: 1,
        default: 5,
      },
      {
        key: "retrieval.auto_inject.min_query_chars",
        label: "自动注入最短触发字数",
        type: "number",
        min: 1,
        max: 200,
        step: 1,
        default: 4,
      },
      {
        key: "retrieval.enable_ppr",
        label: "启用 PageRank 图谱关联重排",
        type: "boolean",
        default: true,
      },
      {
        key: "retrieval.enable_parallel",
        label: "启用向量与稀疏（BM25）并行检索",
        type: "boolean",
        default: true,
      },
      {
        key: "retrieval.vector_pools.mode",
        label: "向量池架构模式",
        type: "select",
        default: "dual",
        hint: "dual 将段落池与图谱池物理分离（未就绪时自动安全回退 single）。",
        options: [
          { value: "dual", label: "双向量池（dual：段落池 + 图谱池）" },
          { value: "single", label: "单向量池（single：兼容模式）" },
        ],
      },
      {
        key: "retrieval.vector_pools.paragraph_top_k",
        label: "双池 · 段落池召回上限",
        type: "number",
        min: 1,
        max: 200,
        step: 1,
        default: 20,
      },
      {
        key: "retrieval.vector_pools.graph_top_k",
        label: "双池 · 图谱池召回上限",
        type: "number",
        min: 1,
        max: 300,
        step: 1,
        default: 40,
      },
      {
        key: "retrieval.vector_pools.graph_expand_paragraph_k",
        label: "双池 · 图谱扩展段落上限",
        type: "number",
        min: 1,
        max: 500,
        step: 1,
        default: 80,
      },
      {
        key: "retrieval.vector_pools.relation_expand_per_hit",
        label: "双池 · 单关系命中扩展段落数",
        type: "number",
        min: 1,
        max: 50,
        step: 1,
        default: 5,
      },
      {
        key: "retrieval.vector_pools.entity_expand_per_hit",
        label: "双池 · 单实体命中扩展段落数",
        type: "number",
        min: 1,
        max: 50,
        step: 1,
        default: 8,
      },
      {
        key: "retrieval.vector_pools.relation_evidence_weight",
        label: "双池 · 关系证据映射权重",
        type: "number",
        min: 0,
        max: 5,
        step: 0.05,
        default: 1.0,
      },
      {
        key: "retrieval.vector_pools.entity_evidence_weight",
        label: "双池 · 实体证据映射权重",
        type: "number",
        min: 0,
        max: 5,
        step: 0.05,
        default: 0.55,
      },
      {
        key: "retrieval.vector_pools.semantic_weight",
        label: "双池融合 · 段落语义权重",
        type: "number",
        min: 0,
        max: 2,
        step: 0.05,
        default: 0.65,
      },
      {
        key: "retrieval.vector_pools.sparse_weight",
        label: "双池融合 · BM25 稀疏权重",
        type: "number",
        min: 0,
        max: 2,
        step: 0.05,
        default: 0.2,
      },
      {
        key: "retrieval.vector_pools.graph_weight",
        label: "双池融合 · 图谱召回权重",
        type: "number",
        min: 0,
        max: 2,
        step: 0.05,
        default: 0.15,
      },
      {
        key: "retrieval.vector_pools.score_calibration_method",
        label: "双池分数校准方法",
        type: "select",
        default: "none",
        options: [
          { value: "none", label: "原始加权（none）" },
          { value: "minmax", label: "最小最大归一化（minmax）" },
          { value: "rank_percentile", label: "排名分位数（rank_percentile）" },
          { value: "robust_sigmoid", label: "鲁棒 Sigmoid（robust_sigmoid）" },
          { value: "softmax", label: "Softmax 归一化（softmax）" },
          { value: "weighted_rrf", label: "加权 RRF（weighted_rrf）" },
          { value: "semantic_minmax", label: "语义 MinMax（semantic_minmax）" },
          { value: "semantic_rrf", label: "语义 RRF（semantic_rrf）" },
          { value: "semantic_sparse_rrf", label: "语义+稀疏 RRF（semantic_sparse_rrf）" },
          { value: "semantic_graph_rrf", label: "语义+图谱 RRF（semantic_graph_rrf）" },
        ],
      },
      {
        key: "retrieval.vector_pools.score_calibration_rrf_k",
        label: "双池 RRF 校准常数 K",
        type: "number",
        min: 1,
        max: 500,
        step: 1,
        default: 60,
      },
      {
        key: "retrieval.vector_pools.relation_intent.graph_top_k",
        label: "关系意图 · 图谱候选上限",
        type: "number",
        min: 1,
        max: 300,
        step: 1,
        default: 80,
      },
      {
        key: "retrieval.vector_pools.relation_intent.semantic_weight",
        label: "关系意图 · 语义权重",
        type: "number",
        min: 0,
        max: 2,
        step: 0.05,
        default: 0.45,
      },
      {
        key: "retrieval.vector_pools.relation_intent.sparse_weight",
        label: "关系意图 · 稀疏权重",
        type: "number",
        min: 0,
        max: 2,
        step: 0.05,
        default: 0.15,
      },
      {
        key: "retrieval.vector_pools.relation_intent.graph_weight",
        label: "关系意图 · 图谱权重",
        type: "number",
        min: 0,
        max: 2,
        step: 0.05,
        default: 0.4,
      },
      {
        key: "retrieval.vector_pools.relation_intent.return_relation_items",
        label: "关系意图 · 返回独立关系条目",
        type: "boolean",
        default: false,
      },
      {
        key: "retrieval.temporal.enabled",
        label: "启用时序检索",
        type: "boolean",
        default: true,
      },
      {
        key: "retrieval.temporal.default_top_k",
        label: "时序检索默认返回条数",
        type: "number",
        min: 1,
        max: 100,
        step: 1,
        default: 10,
      },
      {
        key: "retrieval.auto_route.enabled",
        label: "启用智能检索路由",
        type: "boolean",
        default: true,
      },
      {
        key: "retrieval.auto_route.enable_time_intent",
        label: "自动识别自然语言时间意图",
        type: "boolean",
        default: true,
      },
      {
        key: "retrieval.aggregate.rrf_k",
        label: "多通道聚合 RRF K 值",
        type: "number",
        min: 1,
        max: 500,
        step: 1,
        default: 60,
      },
      {
        key: "retrieval.aggregate.weights.search",
        label: "聚合通道 · 语义检索权重",
        type: "number",
        min: 0,
        max: 10,
        step: 0.1,
        default: 1.0,
      },
      {
        key: "retrieval.aggregate.weights.time",
        label: "聚合通道 · 时序检索权重",
        type: "number",
        min: 0,
        max: 10,
        step: 0.1,
        default: 1.0,
      },
      {
        key: "retrieval.aggregate.weights.episode",
        label: "聚合通道 · 事件摘要权重",
        type: "number",
        min: 0,
        max: 10,
        step: 0.1,
        default: 1.0,
      },
      {
        key: "threshold.enable_auto_adjust",
        label: "动态相关性阈值自适应",
        type: "boolean",
        default: true,
        runtimeOnly: true,
        hint: "内核动态分位阈值调节开关（仅当前作用域运行时动态生效）。",
      },
      {
        key: "threshold.min_threshold",
        label: "动态阈值下限",
        type: "number",
        min: 0,
        max: 1,
        step: 0.05,
        default: 0.3,
        runtimeOnly: true,
      },
      {
        key: "threshold.max_threshold",
        label: "动态阈值上限",
        type: "number",
        min: 0,
        max: 1,
        step: 0.05,
        default: 0.95,
        runtimeOnly: true,
      },
      {
        key: "threshold.percentile",
        label: "动态阈值百分位",
        type: "number",
        min: 1,
        max: 99,
        step: 1,
        default: 75,
        runtimeOnly: true,
      },
      {
        key: "threshold.min_results",
        label: "动态阈值保底结果数",
        type: "number",
        min: 1,
        max: 50,
        step: 1,
        default: 3,
        runtimeOnly: true,
      },
    ],
  },
  {
    id: "lifecycle_filter",
    title: "生命周期与聊天过滤",
    description: "记忆半衰期衰减、冻结保护、回收站阈值及黑白名单会话控制。",
    fields: [
      {
        key: "memory.enabled",
        label: "启用记忆生命周期维护",
        type: "boolean",
        default: true,
        hint: "开启后自动执行权重衰减、冻结期唤醒与过期回收。",
      },
      {
        key: "memory.half_life_hours",
        label: "记忆半衰期（小时）",
        type: "number",
        min: 0.1,
        max: 8760,
        step: 0.5,
        default: 24,
      },
      {
        key: "memory.base_decay_interval_hours",
        label: "衰减计算周期（小时）",
        type: "number",
        min: 0.1,
        max: 168,
        step: 0.5,
        default: 1,
      },
      {
        key: "memory.prune_threshold",
        label: "剪枝移入回收站阈值",
        type: "number",
        min: 0,
        max: 1,
        step: 0.01,
        default: 0.1,
      },
      {
        key: "memory.freeze_duration_hours",
        label: "冻结缓冲保留时长（小时）",
        type: "number",
        min: 0,
        max: 8760,
        step: 1,
        default: 24,
      },
      {
        key: "memory.max_weight",
        label: "单条记忆关系权重上限",
        type: "number",
        min: 1,
        max: 100,
        step: 0.5,
        default: 10,
      },
      {
        key: "memory.auto_protect_ttl_hours",
        label: "手动强化后免遗忘保护期（小时）",
        type: "number",
        min: 0,
        max: 8760,
        step: 1,
        default: 24,
      },
      {
        key: "filter.enabled",
        label: "启用聊天会话过滤",
        type: "boolean",
        default: true,
      },
      {
        key: "filter.mode",
        label: "聊天过滤模式",
        type: "select",
        default: "blacklist",
        options: [
          { value: "blacklist", label: "黑名单（blacklist：排除列表会话）" },
          { value: "whitelist", label: "白名单（whitelist：仅允许列表会话）" },
        ],
      },
      {
        key: "filter.chats",
        label: "过滤会话列表（每行一个）",
        type: "list",
        default: [],
        placeholder: "例如：group:123456 或 user:789012",
        hint: "按行或逗号填写会话标识，格式如 group:群号、user:账号。",
      },
    ],
  },
  {
    id: "ingest_summary",
    title: "消息摄取与自动总结",
    description: "聊天流水记录、智能内容路由、图片转述及会话自动总结策略。",
    fields: [
      {
        key: "ingest.record_all_events",
        label: "自动记录全部聊天消息",
        type: "boolean",
        default: true,
      },
      {
        key: "ingest.skip_command_messages",
        label: "忽略命令前缀消息",
        type: "boolean",
        default: true,
      },
      {
        key: "ingest.command_prefixes",
        label: "命令前缀列表（每行或逗号分隔）",
        type: "list",
        default: ["/"],
        placeholder: "/, !, .",
      },
      {
        key: "ingest.memory_write_mode",
        label: "消息记忆写入模式",
        type: "select",
        default: "transcript_only",
        options: [
          { value: "transcript_only", label: "仅写流水待总结（transcript_only，推荐）" },
          { value: "auto", label: "智能直写关键信息（auto）" },
          { value: "direct", label: "每条消息即时直写（direct）" },
          { value: "both", label: "流水与即时直写并行（both）" },
        ],
      },
      {
        key: "ingest.direct_write_assistant",
        label: "直写模式下同时记录机器人回复",
        type: "boolean",
        default: true,
      },
      {
        key: "ingest.content_router.enabled",
        label: "启用智能低信息量消息过滤",
        type: "boolean",
        default: true,
      },
      {
        key: "ingest.content_router.drop_ephemeral_transcript",
        label: "从流水账中直接丢弃纯语气词消息",
        type: "boolean",
        default: false,
      },
      {
        key: "ingest.content_router.auto_direct_min_chars",
        label: "智能直写最短字数门槛",
        type: "number",
        min: 1,
        max: 500,
        step: 1,
        default: 12,
      },
      {
        key: "ingest.skip_placeholder_only",
        label: "忽略纯占位符消息（纯表情/文件）",
        type: "boolean",
        default: true,
      },
      {
        key: "ingest.max_message_chars",
        label: "单条消息截断上限（字符）",
        type: "number",
        min: 100,
        max: 50000,
        step: 100,
        default: 2000,
      },
      {
        key: "ingest.max_forward_fetch",
        label: "合并转发最大抓取批次",
        type: "number",
        min: 0,
        max: 50,
        step: 1,
        default: 8,
      },
      {
        key: "ingest.image_caption.enabled",
        label: "启用视觉模型图片文字转述",
        type: "boolean",
        default: false,
      },
      {
        key: "ingest.image_caption.provider_id",
        label: "图片转述视觉模型 Provider ID",
        type: "string",
        default: "",
        placeholder: "留空使用当前会话 Provider",
      },
      {
        key: "ingest.image_caption.max_count",
        label: "单条消息最大转述图片数",
        type: "number",
        min: 1,
        max: 20,
        step: 1,
        default: 1,
      },
      {
        key: "ingest.image_caption.prompt",
        label: "图片转述提示词",
        type: "string",
        default: "请简洁描述这张图片。",
      },
      {
        key: "summarization.enabled",
        label: "启用对话摘要提炼",
        type: "boolean",
        default: true,
      },
      {
        key: "summarization.source_mode",
        label: "总结聊天记录来源",
        type: "select",
        default: "transcript",
        options: [
          { value: "transcript", label: "插件独立流水（transcript，推荐）" },
          { value: "hybrid", label: "混合优先宿主回退流水（hybrid）" },
          { value: "astrbot", label: "AstrBot 会话历史（astrbot）" },
        ],
      },
      {
        key: "summarization.context_length",
        label: "单次总结提取消息条数",
        type: "number",
        min: 5,
        max: 500,
        step: 5,
        default: 50,
      },
      {
        key: "summarization.default_knowledge_type",
        label: "总结默认生成知识类型",
        type: "select",
        default: "narrative",
        options: [
          { value: "narrative", label: "叙事型（narrative）" },
          { value: "factual", label: "事实型（factual）" },
          { value: "mixed", label: "混合型（mixed）" },
          { value: "structured", label: "结构化（structured）" },
          { value: "auto", label: "自动判断（auto）" },
        ],
      },
      {
        key: "summarization.auto_import.enabled",
        label: "启用聊天自动触发总结",
        type: "boolean",
        default: true,
      },
      {
        key: "summarization.auto_import.after_reply_only",
        label: "仅在机器人回复后检查总结条件",
        type: "boolean",
        default: true,
      },
      {
        key: "summarization.auto_import.min_new_messages",
        label: "自动总结最少新增消息数",
        type: "number",
        min: 1,
        max: 500,
        step: 1,
        default: 12,
      },
      {
        key: "summarization.auto_import.cooldown_minutes",
        label: "自动总结冷却间隔（分钟）",
        type: "number",
        min: 1,
        max: 1440,
        step: 1,
        default: 30,
      },
    ],
  },
  {
    id: "person_episode",
    title: "人物画像与事实写回",
    description: "人物画像缓存/注入、发言自动事实提取（FactClaim 写回）及 Episode 事件摘要生成。",
    fields: [
      {
        key: "person_profile.enabled",
        label: "启用人物画像系统",
        type: "boolean",
        default: true,
      },
      {
        key: "person_profile.profile_ttl_minutes",
        label: "画像快照缓存有效期（分钟）",
        type: "number",
        min: 1,
        max: 525600,
        step: 10,
        default: 360,
      },
      {
        key: "person_profile.top_k_evidence",
        label: "生成画像参考证据条数",
        type: "number",
        min: 1,
        max: 100,
        step: 1,
        default: 12,
      },
      {
        key: "person_profile.injection_max_profiles",
        label: "单次请求最多注入画像人数",
        type: "number",
        min: 0,
        max: 20,
        step: 1,
        default: 3,
      },
      {
        key: "person_fact_writeback.enabled",
        label: "启用发言自动提取人物事实",
        type: "boolean",
        default: true,
      },
      {
        key: "person_fact_writeback.queue_maxsize",
        label: "事实写回异步队列上限",
        type: "number",
        min: 8,
        max: 4096,
        step: 16,
        default: 256,
      },
      {
        key: "person_fact_writeback.min_user_text_chars",
        label: "触发事实提取最短发言字数",
        type: "number",
        min: 1,
        max: 200,
        step: 1,
        default: 4,
      },
      {
        key: "person_fact_writeback.max_facts_per_turn",
        label: "单轮最多提取事实条数",
        type: "number",
        min: 1,
        max: 20,
        step: 1,
        default: 5,
      },
      {
        key: "person_fact_writeback.max_registry_facts",
        label: "画像待确认事实展示上限",
        type: "number",
        min: 1,
        max: 200,
        step: 1,
        default: 30,
      },
      {
        key: "person_fact_writeback.max_evidence_chars",
        label: "事实提取单条消息截断字数",
        type: "number",
        min: 100,
        max: 10000,
        step: 50,
        default: 800,
      },
      {
        key: "person_fact_writeback.update_registry_memory_points",
        label: "在人物画像中投影待确认事实",
        type: "boolean",
        default: true,
      },
      {
        key: "person_fact_writeback.chat_provider_id",
        label: "事实提取专用 LLM Provider ID",
        type: "string",
        default: "",
        placeholder: "留空复用默认 Provider",
      },
      {
        key: "person_fact_writeback.temperature",
        label: "事实提取采样温度",
        type: "number",
        min: 0,
        max: 1,
        step: 0.05,
        default: 0.1,
      },
      {
        key: "person_fact_writeback.max_tokens",
        label: "事实提取最大输出 Token",
        type: "number",
        min: 128,
        max: 8192,
        step: 64,
        default: 800,
      },
      {
        key: "episode.enabled",
        label: "启用 Episode 事件摘要",
        type: "boolean",
        default: true,
      },
      {
        key: "episode.generation_enabled",
        label: "后台自动生成事件摘要",
        type: "boolean",
        default: true,
      },
      {
        key: "episode.generation_interval_seconds",
        label: "事件摘要后台检查间隔（秒）",
        type: "number",
        min: 5,
        max: 86400,
        step: 5,
        default: 30,
      },
      {
        key: "episode.generation_batch_size",
        label: "每轮处理事件来源批大小",
        type: "number",
        min: 1,
        max: 500,
        step: 1,
        default: 20,
      },
      {
        key: "episode.max_retry",
        label: "事件生成失败最大重试次数",
        type: "number",
        min: 0,
        max: 20,
        step: 1,
        default: 3,
      },
      {
        key: "episode.segmentation_model",
        label: "话题分段指定模型",
        type: "string",
        default: "auto",
        placeholder: "auto 使用默认模型",
      },
    ],
  },
  {
    id: "image_memory",
    title: "多模态图片记忆",
    description: "A_Memorix v2.0 静态图片资产入库、带图自动注入与多模态视觉向量相似召回。",
    fields: [
      {
        key: "image_memory.enabled",
        label: "启用图片记忆存储与管理",
        type: "boolean",
        default: true,
        hint: "默认需先配置下方多模态图片向量接口后生效，避免未配置向量模型时识图报错。",
      },
      {
        key: "image_memory.require_embedding",
        label: "未配置图片嵌入模型时自动停用图片记忆",
        type: "boolean",
        default: true,
        hint: "默认开启：未配置多模态图片向量接口时自动停用图片记忆以防识图报错；关闭后可在无向量模型时仅使用同图哈希召回。",
      },
      {
        key: "image_memory.auto_inject_enabled",
        label: "发送带图消息时自动注入历史图片记忆",
        type: "boolean",
        default: true,
      },
      {
        key: "image_memory.max_bytes",
        label: "单张图片最大字节数上限",
        type: "number",
        min: 65536,
        max: 104857600,
        step: 524288,
        default: 10485760,
        hint: "默认 10485760（10 MiB）。",
      },
      {
        key: "image_memory.max_pixels",
        label: "单张图片最大像素数上限",
        type: "number",
        min: 100000,
        max: 200000000,
        step: 1000000,
        default: 40000000,
      },
      {
        key: "image_memory.candidate_limit",
        label: "相似图片召回候选上限",
        type: "number",
        min: 1,
        max: 50,
        step: 1,
        default: 8,
      },
      {
        key: "image_memory.similarity_threshold",
        label: "图片视觉余弦相似度阈值",
        type: "number",
        min: 0,
        max: 1,
        step: 0.02,
        default: 0.72,
      },
      {
        key: "image_memory.openapi.enabled",
        label: "启用多模态图片向量接口（visual_similar）",
        type: "boolean",
        default: false,
        hint: "填写下方接口地址与模型名后开启，解锁跨图视觉相似召回。",
      },
      {
        key: "image_memory.openapi.base_url",
        label: "多模态向量接口地址",
        type: "string",
        default: "",
        placeholder: "https://api.example.com/v1",
      },
      {
        key: "image_memory.openapi.api_key",
        label: "多模态向量接口密钥",
        type: "password",
        default: "",
        placeholder: "未修改时保留已配置的脱敏密钥",
      },
      {
        key: "image_memory.openapi.model",
        label: "多模态图片嵌入模型名",
        type: "string",
        default: "",
        placeholder: "支持图片输入的 Embeddings 模型名",
      },
      {
        key: "image_memory.openapi.dimension",
        label: "图片向量维度",
        type: "number",
        min: 32,
        max: 8192,
        step: 1,
        default: 512,
      },
      {
        key: "image_memory.openapi.timeout_seconds",
        label: "图片嵌入请求超时（秒）",
        type: "number",
        min: 1,
        max: 300,
        step: 1,
        default: 30,
      },
    ],
  },
  {
    id: "v2_integration_import",
    title: "v2 扩展工具与模糊修正",
    description: "v2.0 知识包/修正/图片扩展工具注册、反馈纠错、自然语言模糊修正与 Web 知识导入配额。",
    fields: [
      {
        key: "integration.enable_v2_extended_tools",
        label: "向 LLM 注册 v2.0 扩展管理工具",
        type: "boolean",
        default: false,
        hint: "注册 memory_bundle_admin、memory_correction_admin 与 memory_image_admin 工具。",
      },
      {
        key: "integration.feedback_correction_enabled",
        label: "启用用户反馈后台纠错",
        type: "boolean",
        default: false,
      },
      {
        key: "integration.fuzzy_modify.enabled",
        label: "启用自然语言记忆模糊修正",
        type: "boolean",
        default: true,
      },
      {
        key: "integration.fuzzy_modify.candidate_limit",
        label: "模糊修正候选收集上限",
        type: "number",
        min: 1,
        max: 200,
        step: 1,
        default: 20,
      },
      {
        key: "integration.fuzzy_modify.confirm_threshold",
        label: "模糊修正免确认置信阈值",
        type: "number",
        min: 0,
        max: 1,
        step: 0.05,
        default: 0.85,
      },
      {
        key: "integration.fuzzy_modify.auto_execute_enabled",
        label: "允许高置信模糊修正自动执行",
        type: "boolean",
        default: false,
      },
      {
        key: "integration.fuzzy_modify.max_targets",
        label: "单次模糊修正最大目标数",
        type: "number",
        min: 1,
        max: 50,
        step: 1,
        default: 5,
      },
      {
        key: "integration.fuzzy_modify.allow_global_scope",
        label: "允许模糊修正跨全局作用域",
        type: "boolean",
        default: false,
      },
      {
        key: "web.import.max_queue_size",
        label: "导入队列最大排队任务数",
        type: "number",
        min: 1,
        max: 200,
        step: 1,
        default: 20,
      },
      {
        key: "web.import.max_files_per_task",
        label: "单次导入最大文件数",
        type: "number",
        min: 1,
        max: 2000,
        step: 10,
        default: 200,
      },
      {
        key: "web.import.max_file_size_mb",
        label: "单文件导入体积上限（MB）",
        type: "number",
        min: 1,
        max: 500,
        step: 1,
        default: 20,
      },
      {
        key: "web.import.max_paste_chars",
        label: "粘贴文本导入最大字符数",
        type: "number",
        min: 1000,
        max: 2000000,
        step: 10000,
        default: 200000,
      },
      {
        key: "web.import.llm_retry.max_attempts",
        label: "知识导入 LLM 单次最大尝试次数",
        type: "number",
        min: 1,
        max: 10,
        step: 1,
        default: 4,
        hint: "导入阶段单次 LLM 摘要/抽取请求的最大尝试总次数（含首次调用）。",
      },
      {
        key: "web.import.path_aliases.raw",
        label: "可扫描目录别名 · raw",
        type: "string",
        default: "raw",
        hint: "相对于插件数据目录的子路径。",
      },
      {
        key: "web.import.path_aliases.plugin_data",
        label: "可扫描目录别名 · plugin_data",
        type: "string",
        default: ".",
        hint: "默认「.」代表插件数据根目录。",
      },
    ],
  },
];

/** 读取点分键的值。 */
export function pathValue(root: unknown, path: string): unknown {
  return path.split(".").reduce<unknown>((current, part) => {
    if (current && typeof current === "object" && part in (current as object)) {
      return (current as Record<string, unknown>)[part];
    }
    return undefined;
  }, root);
}

/** 判断密码字段是否为后端返回的脱敏掩码（如 <hidden>、****、sk***12）。 */
export function isMaskedSecret(val: unknown): boolean {
  if (typeof val !== "string") return false;
  const s = val.trim();
  if (!s) return false;
  return s === "<hidden>" || s.includes("***") || /^\*+$/.test(s);
}

export function parseListInput(raw: string): string[] {
  return raw
    .split(/[\r\n,，]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

export function formatListValue(val: unknown): string {
  if (Array.isArray(val)) {
    return val.map((item) => String(item ?? "").trim()).filter(Boolean).join("\n");
  }
  if (typeof val === "string") return val;
  return "";
}

/** 读取字段当前规范值（缺失时回退 default）。 */
export function fieldValue(
  config: Record<string, unknown> | undefined,
  field: ConfigField,
): ConfigFieldValue {
  const raw = pathValue(config, field.key);
  if (raw === undefined || raw === null) {
    return Array.isArray(field.default) ? [...field.default] : field.default;
  }

  switch (field.type) {
    case "boolean":
      return raw === true || raw === "true";
    case "number": {
      const num = Number(raw);
      if (Number.isFinite(num)) return num;
      return typeof field.default === "number" ? field.default : (field.min ?? 0);
    }
    case "list": {
      if (Array.isArray(raw)) {
        return raw.map((x) => String(x ?? "").trim()).filter(Boolean);
      }
      if (typeof raw === "string") return parseListInput(raw);
      return Array.isArray(field.default) ? [...field.default] : [];
    }
    case "password":
    case "string":
    case "select":
    default:
      return String(raw);
  }
}

export function cloneFieldValue(val: ConfigFieldValue): ConfigFieldValue {
  return Array.isArray(val) ? [...val] : val;
}

export function isFieldValueEqual(a: ConfigFieldValue | undefined, b: ConfigFieldValue | undefined): boolean {
  if (Array.isArray(a) || Array.isArray(b)) {
    const arrA = Array.isArray(a) ? a : [];
    const arrB = Array.isArray(b) ? b : [];
    if (arrA.length !== arrB.length) return false;
    return arrA.every((item, idx) => item === arrB[idx]);
  }
  return a === b;
}

const GROUP_QUERY_ALIASES: Record<string, string> = {
  scope: "scope_model",
  provider: "scope_model",
  embedding: "scope_model",
  model: "scope_model",
  retrieval: "retrieval_pools",
  vector_pools: "retrieval_pools",
  pools: "retrieval_pools",
  threshold: "retrieval_pools",
  memory: "lifecycle_filter",
  lifecycle: "lifecycle_filter",
  filter: "lifecycle_filter",
  ingest: "ingest_summary",
  summarization: "ingest_summary",
  summary: "ingest_summary",
  person: "person_episode",
  people: "person_episode",
  person_profile: "person_episode",
  person_fact_writeback: "person_episode",
  episode: "person_episode",
  image: "image_memory",
  images: "image_memory",
  image_memory: "image_memory",
  fuzzy: "v2_integration_import",
  fuzzy_modify: "v2_integration_import",
  integration: "v2_integration_import",
  import: "v2_integration_import",
  web: "v2_integration_import",
  v2: "v2_integration_import",
};

/**
 * 将路由查询参数 `?group=...` 解析为规范分组 ID（支持精确分组 ID、模块别名与点分配置键前缀）。
 */
export function resolveConfigGroupId(rawGroup: unknown): string | null {
  const first = Array.isArray(rawGroup) ? rawGroup[0] : rawGroup;
  if (typeof first !== "string") return null;
  const normalized = first.trim().toLowerCase();
  if (!normalized) return null;
  if (normalized === "all") return "all";

  const exact = CONFIG_FIELDS.find((g) => g.id.toLowerCase() === normalized);
  if (exact) return exact.id;

  const aliased = GROUP_QUERY_ALIASES[normalized];
  if (aliased) return aliased;

  const byFieldKey = CONFIG_FIELDS.find((g) =>
    g.fields.some((f) => {
      const fk = f.key.toLowerCase();
      return fk === normalized || fk.startsWith(`${normalized}.`);
    }),
  );
  return byFieldKey ? byFieldKey.id : null;
}
