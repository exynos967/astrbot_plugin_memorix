"""
聊天总结与知识导入工具

该模块负责从聊天记录中提取信息，生成总结，并将总结内容及提取的实体/关系
导入到 A_memorix 的存储组件中。
"""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple
from weakref import WeakValueDictionary

import asyncio
import json
import re
import time
import traceback

import numpy as np

from src.common.data_models.message_component_data_model import TextComponent
from src.common.logger import get_logger
from src.common.prompt_i18n import extract_prompt_placeholders, load_prompt, load_prompt_template
from src.config.config import config_manager, global_config
from src.config.model_configs import TaskConfig
from src.services import llm_service as llm_api
from src.services import message_service as message_api

from ...engine.host_shims import llm_shim
from ..embedding import EmbeddingAPIAdapter
from ..image.component_paths import build_chat_external_ref, iter_message_image_components
from ..storage import (
    GraphStore,
    KnowledgeType,
    MetadataStore,
    VectorStore,
    resolve_stored_knowledge_type,
)
from .async_work import cancellation_safe_to_thread
from .fact_deduplication import FactDeduplicator, fact_scope_lock, observation_id
from .metadata import coerce_metadata_dict
from .model_routing import (
    find_text_generation_task_for_model,
    get_text_generation_model_tasks,
)
from .relation_write_service import RelationWriteService
from .runtime_payloads import optional_float
from .runtime_self_check import ensure_runtime_self_check, run_embedding_runtime_self_check
from .summary_identity import SummaryIdentityContext

logger = get_logger("A_Memorix.SummaryImporter")


class _LLMClientBridgeAdapter:
    """将 SummaryImporter 的 llm_client (complete / complete_json) 适配为 ProviderBridge 契约。"""

    enabled = True

    def __init__(self, llm_client: Any) -> None:
        self._client = llm_client
        bridge = getattr(llm_client, "provider_bridge", None)
        self.chat_provider_id = (
            str(getattr(bridge, "chat_provider_id", "") or "llm_client").strip() or "llm_client"
        )

    async def generate_text(
        self,
        prompt: str,
        *,
        temperature: float = 0.2,
        max_tokens: int = 1200,
        **_kwargs: Any,
    ) -> str:
        complete_fn = getattr(self._client, "complete", None)
        if callable(complete_fn):
            res = await complete_fn(prompt, temperature=temperature, max_tokens=max_tokens)
            return str(res or "")
        complete_json_fn = getattr(self._client, "complete_json", None)
        if callable(complete_json_fn):
            ok, payload, raw = await complete_json_fn(
                prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            if isinstance(raw, str) and raw.strip():
                return raw
            if ok and isinstance(payload, (dict, list)):
                return json.dumps(payload, ensure_ascii=False)
            return ""
        bridge = getattr(self._client, "provider_bridge", None)
        gen_fn = getattr(bridge, "generate_text", None)
        if callable(gen_fn):
            res = await gen_fn(prompt, temperature=temperature, max_tokens=max_tokens)
            return str(res or "")
        raise RuntimeError("Configured llm_client does not implement complete, complete_json, or provider_bridge")


@dataclass(frozen=True)
class SummaryImportResult:
    """聊天摘要导入结果。

    保留二元组解包兼容：旧调用方仍可使用 ``success, detail = result``。
    """

    success: bool
    detail: str
    paragraph_hash: str = ""
    source: str = ""
    skipped: bool = False

    def __iter__(self) -> Iterator[bool | str]:
        yield self.success
        yield self.detail


# 默认总结提示词模版
SUMMARY_PROMPT_NAME = "a_memorix_chat_summary"
# 保留只读模板常量供既有扩展与测试检查结构；实际模型调用按当前语言动态加载。
SUMMARY_PROMPT_TEMPLATE = load_prompt(SUMMARY_PROMPT_NAME, locale="zh-CN")


def _normalize_entity_items(raw_entities: Any) -> List[str]:
    if not isinstance(raw_entities, list):
        return []
    entities: List[str] = []
    seen = set()
    for item in raw_entities:
        if isinstance(item, str):
            name = item.strip()
        elif isinstance(item, dict):
            name = str(item.get("name") or item.get("label") or item.get("entity") or "").strip()
        else:
            name = ""
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        entities.append(name)
    return entities


def _normalize_relation_items(raw_relations: Any) -> List[Dict[str, str]]:
    if not isinstance(raw_relations, list):
        return []
    relations: List[Dict[str, str]] = []
    for item in raw_relations:
        if not isinstance(item, dict):
            continue
        subject = str(item.get("subject", "") or "").strip()
        predicate = str(item.get("predicate", "") or "").strip()
        obj = str(item.get("object", "") or "").strip()
        if not (subject and predicate and obj):
            continue
        relations.append({"subject": subject, "predicate": predicate, "object": obj})
    return relations


def _normalize_summary_facts(raw_facts: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_facts, list):
        raise ValueError("增量总结 facts 必须是数组")
    facts: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in raw_facts:
        if not isinstance(item, dict):
            raise ValueError("增量总结 facts 的每一项必须是对象")
        fact_id = item.get("fact_id")
        text = item.get("text")
        if not isinstance(fact_id, str) or not isinstance(text, str):
            raise ValueError("增量总结事实的 fact_id 和 text 必须是字符串")
        fact_id, text = fact_id.strip(), text.strip()
        if not fact_id or not text or fact_id in seen_ids:
            raise ValueError("增量总结事实的 fact_id 和 text 不能为空，fact_id 不得重复")
        evidence = []
        for raw_evidence in item.get("image_evidence") or []:
            if not isinstance(raw_evidence, dict):
                continue
            message_id = str(raw_evidence.get("message_id") or "").strip()
            component_path = str(raw_evidence.get("component_path") or "").strip()
            if message_id and component_path:
                evidence.append({"message_id": message_id, "component_path": component_path})
        seen_ids.add(fact_id)
        facts.append({"fact_id": fact_id, "text": text, "image_evidence": evidence})
    return facts


def _message_timestamp(message: Any) -> Optional[float]:
    for attr_name in ("timestamp", "time"):
        value = getattr(message, attr_name, None)
        if value is None:
            continue
        timestamp_func = getattr(value, "timestamp", None)
        if callable(timestamp_func):
            try:
                return float(timestamp_func())
            except Exception:
                continue
        try:
            return float(value)
        except Exception:
            continue
    return None


def _paragraph_created_at(paragraph: Dict[str, Any]) -> float:
    try:
        return float(paragraph.get("summary_observed_at", paragraph.get("created_at")) or 0.0)
    except Exception:
        return 0.0


class SummaryImporter:
    """总结并导入知识的工具类"""

    def __init__(
        self,
        vector_store: VectorStore,
        graph_store: GraphStore,
        metadata_store: MetadataStore,
        embedding_manager: EmbeddingAPIAdapter,
        plugin_config: dict,
        llm_client: Any = None,
        **kwargs: Any,
    ):
        self.vector_store = vector_store
        self.graph_store = graph_store
        self.metadata_store = metadata_store
        self.embedding_manager = embedding_manager
        self.plugin_config = plugin_config
        self.llm_client = llm_client
        self.extra_kwargs = kwargs
        self._import_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
        self.relation_write_service: Optional[RelationWriteService] = (
            plugin_config.get("relation_write_service") if isinstance(plugin_config, dict) else None
        )

    def _plugin_instance(self) -> Any:
        return self.plugin_config.get("plugin_instance") if isinstance(self.plugin_config, dict) else None

    def _bridge_from_llm_client(self) -> Optional[Any]:
        if self.llm_client is None:
            return None
        return _LLMClientBridgeAdapter(self.llm_client)

    def _has_transcript_llm_source(self) -> bool:
        if self.llm_client is not None:
            return True
        scoped = llm_shim.get_scoped_provider_bridge()
        if scoped is not None and bool(getattr(scoped, "enabled", False)):
            return True
        plugin_bridge = getattr(self._plugin_instance(), "provider_bridge", None)
        return plugin_bridge is not None and bool(getattr(plugin_bridge, "enabled", False))

    @contextmanager
    def _llm_bridge_context(self) -> Iterator[None]:
        scoped = llm_shim.get_scoped_provider_bridge()
        if scoped is not None and bool(getattr(scoped, "enabled", False)):
            yield
            return
        if self.llm_client is not None:
            with llm_shim.use_provider_bridge(self._bridge_from_llm_client()):
                yield
            return
        plugin_bridge = getattr(self._plugin_instance(), "provider_bridge", None)
        if (
            plugin_bridge is not None
            and bool(getattr(plugin_bridge, "enabled", False))
            and llm_shim.get_active_provider_bridge() is None
        ):
            with llm_shim.use_provider_bridge(plugin_bridge):
                yield
            return
        yield

    def _is_active_image_occurrence(self, occurrence: Optional[Dict[str, Any]]) -> bool:
        if not isinstance(occurrence, dict):
            return False
        if str(occurrence.get("status") or "") != "active":
            return False
        asset_id = str(occurrence.get("asset_id") or "").strip()
        get_asset = getattr(self.metadata_store, "get_image_asset", None)
        if asset_id and callable(get_asset):
            asset = get_asset(asset_id)
            if not isinstance(asset, dict) or str(asset.get("status") or "") != "active":
                return False
        return True

    def _append_image_catalog_line(
        self,
        lines: List[str],
        allowed: set[Tuple[str, str]],
        *,
        stream_id: str,
        message_id: str,
        component_path: str,
        occurrence: Optional[Dict[str, Any]] = None,
    ) -> None:
        key = (message_id, component_path)
        if not message_id or not component_path or key in allowed:
            return
        if occurrence is None:
            external_ref = build_chat_external_ref(
                chat_id=stream_id,
                message_id=message_id,
                component_path=component_path,
            )
            occurrence = self.metadata_store.get_image_occurrence_by_ref(external_ref)
        if occurrence is not None and not self._is_active_image_occurrence(occurrence):
            return
        allowed.add(key)
        observations = (
            self.metadata_store.list_image_observations([str(occurrence["occurrence_id"])])
            if occurrence is not None
            else []
        )
        descriptions = [
            f"{item['source_kind']}({item['confirm_status']}): {item['text']}"
            for item in observations
        ]
        suffix = f"；图片上下文={' | '.join(descriptions)}" if descriptions else ""
        lines.append(f"- message_id={message_id}, component_path={component_path}{suffix}")

    def _image_evidence_catalog(self, messages: Sequence[Any], *, stream_id: str) -> Tuple[str, set[Tuple[str, str]]]:
        lines: List[str] = []
        allowed: set[Tuple[str, str]] = set()
        window_message_ids: set[str] = set()
        for message in messages:
            message_id = str(getattr(message, "message_id", "") or "").strip()
            if message_id:
                window_message_ids.add(message_id)
            components = getattr(getattr(message, "raw_message", None), "components", []) or []
            for component_path, _ in iter_message_image_components(components):
                if not message_id:
                    continue
                self._append_image_catalog_line(
                    lines,
                    allowed,
                    stream_id=stream_id,
                    message_id=message_id,
                    component_path=component_path,
                )

        list_visible = getattr(self.metadata_store, "list_visible_image_occurrences", None)
        if window_message_ids and callable(list_visible):
            occurrences = list_visible(chat_ids=[stream_id])
            for occ in reversed(occurrences):
                if str(occ.get("scope_type") or "") != "chat":
                    continue
                if str(occ.get("chat_id") or "").strip() != stream_id:
                    continue
                occ_msg_id = str(occ.get("message_id") or "").strip()
                occ_comp_path = str(occ.get("component_path") or "").strip()
                if occ_msg_id in window_message_ids and occ_comp_path:
                    self._append_image_catalog_line(
                        lines,
                        allowed,
                        stream_id=stream_id,
                        message_id=occ_msg_id,
                        component_path=occ_comp_path,
                        occurrence=occ,
                    )

        return ("\n".join(lines) if lines else "无", allowed)

    def _link_summary_image_evidence(
        self,
        *,
        stream_id: str,
        paragraph_hash: str,
        facts: Sequence[Dict[str, Any]],
        allowed: set[Tuple[str, str]],
    ) -> int:
        linked = 0
        for fact in facts:
            for evidence in fact.get("image_evidence") or []:
                key = (str(evidence["message_id"]), str(evidence["component_path"]))
                if key not in allowed:
                    raise ValueError(f"总结模型返回了窗口外图片证据: {key[0]}:{key[1]}")
                occurrence = self.metadata_store.get_image_occurrence_by_ref(
                    build_chat_external_ref(
                        chat_id=stream_id,
                        message_id=key[0],
                        component_path=key[1],
                    )
                )
                if occurrence is None or not self._is_active_image_occurrence(occurrence):
                    continue
                with self.metadata_store.transaction(immediate=True) as database:
                    self.metadata_store.upsert_image_memory_link(
                        occurrence_id=str(occurrence["occurrence_id"]),
                        target_type="paragraph",
                        target_id=paragraph_hash,
                        link_kind="fact_evidence",
                        evidence={"fact_id": fact["fact_id"], "fact_text": fact["text"]},
                        conn=database,
                    )
                linked += 1
        return linked

    def _allow_metadata_only_write(self) -> bool:
        plugin_instance = self._plugin_instance()
        getter = getattr(plugin_instance, "get_config", None)
        if callable(getter):
            return bool(getter("embedding.fallback.allow_metadata_only_write", True))
        if isinstance(self.plugin_config, dict):
            embedding_cfg = self.plugin_config.get("embedding", {}) or {}
            fallback_cfg = embedding_cfg.get("fallback", {}) if isinstance(embedding_cfg, dict) else {}
            if isinstance(fallback_cfg, dict):
                return bool(fallback_cfg.get("allow_metadata_only_write", True))
        return True

    def _dual_vector_pools_enabled(self) -> bool:
        plugin_instance = self._plugin_instance()
        checker = getattr(plugin_instance, "_dual_vector_pools_enabled", None)
        if callable(checker):
            return bool(checker())
        if isinstance(self.plugin_config, dict):
            retrieval_cfg = self.plugin_config.get("retrieval", {}) or {}
            vector_pools_cfg = retrieval_cfg.get("vector_pools", {}) if isinstance(retrieval_cfg, dict) else {}
            configured_mode = (
                str(vector_pools_cfg.get("mode", "dual") or "dual").strip().lower()
                if isinstance(vector_pools_cfg, dict)
                else "dual"
            )
            runtime_cfg = self.plugin_config.get("runtime", {}) or {}
            if isinstance(runtime_cfg, dict) and "vector_pools_ready" in runtime_cfg:
                return configured_mode == "dual" and bool(runtime_cfg.get("vector_pools_ready"))
            return configured_mode == "dual"
        return False

    def _graph_vector_store(self) -> VectorStore:
        if self._dual_vector_pools_enabled() and isinstance(self.plugin_config, dict):
            store = self.plugin_config.get("graph_vector_store")
            if store is not None:
                return store
        return self.vector_store

    def _graph_batch_update(self):
        from contextlib import nullcontext

        batch_update = getattr(self.graph_store, "batch_update", None)
        return batch_update() if callable(batch_update) else nullcontext()

    def _persist_vector_store(self, store: VectorStore) -> None:
        plugin_instance = self._plugin_instance()
        if plugin_instance is not None:
            plugin_instance.persist_vector_store(store)
            return

        fp_getter = getattr(self.embedding_manager, "get_embedding_fingerprint", None)
        dim = getattr(store, "dimension", None)
        if callable(fp_getter) and dim is not None:
            embedding_fingerprint = fp_getter(dimension=int(dim))
            try:
                store.save(embedding_fingerprint=embedding_fingerprint)
                return
            except TypeError:
                pass
        store.save()

    def _persist_all_vector_stores(self) -> None:
        self._persist_vector_store(self.vector_store)
        if self._dual_vector_pools_enabled():
            graph_store_vec = self._graph_vector_store()
            if graph_store_vec is not None and graph_store_vec is not self.vector_store:
                self._persist_vector_store(graph_store_vec)

    @staticmethod
    def _graph_vector_id(item_type: str, hash_value: str) -> str:
        return f"{str(item_type or '').strip()}:{str(hash_value or '').strip()}"

    def _embedding_write_batch_size(self) -> int:
        batch_size = max(1, int(getattr(self.embedding_manager, "batch_size", 32)))
        max_concurrent = max(1, int(getattr(self.embedding_manager, "max_concurrent", 1)))
        return min(512, batch_size * max_concurrent)

    async def _ensure_entity_vector(self, *, entity_hash: str, name: str) -> None:
        await self._ensure_entity_vectors([(entity_hash, name)])

    async def _ensure_entity_vectors(self, entities: List[Tuple[str, str]]) -> None:
        """批量补齐实体向量，减少导入阶段的串行远程等待。"""
        if not entities or not self._dual_vector_pools_enabled():
            return

        target_store = self._graph_vector_store()
        pending_entities: List[Tuple[str, str, str]] = []
        for entity_hash, name in entities:
            token = str(entity_hash or "").strip()
            text = str(name or "").strip()
            vector_id = self._graph_vector_id("entity", token)
            if token and text and target_store is not None and vector_id not in target_store:
                pending_entities.append((token, text, vector_id))
        if not pending_entities:
            return

        write_batch_size = self._embedding_write_batch_size()
        for offset in range(0, len(pending_entities), write_batch_size):
            batch_entities = pending_entities[offset : offset + write_batch_size]
            try:
                encode_batch_fn = getattr(self.embedding_manager, "encode_batch", None)
                if callable(encode_batch_fn):
                    raw_vectors = await encode_batch_fn([name for _, name, _ in batch_entities])
                else:
                    raw_vectors = [await self.embedding_manager.encode(name) for _, name, _ in batch_entities]
                embeddings = np.asarray(raw_vectors, dtype=np.float32)
                if embeddings.ndim == 1:
                    embeddings = embeddings.reshape(1, -1)
                if embeddings.shape[0] != len(batch_entities):
                    raise ValueError(
                        "实体批量向量数量不匹配: "
                        f"{embeddings.shape[0]} vs {len(batch_entities)}"
                    )
                target_store.add(
                    vectors=embeddings,
                    ids=[vector_id for _, _, vector_id in batch_entities],
                )
                recorder = self.plugin_config.get("record_embedding_input")
                if recorder is not None:
                    for entity_hash, name, _vector_id in batch_entities:
                        recorder("entity", entity_hash, name)
            except Exception as exc:
                if not self._allow_metadata_only_write():
                    raise
                logger.warning(
                    "总结导入实体批量向量写入失败，保留 metadata/graph: "
                    f"count={len(batch_entities)} error={exc}"
                )

    def _normalize_summary_model_selectors(self, raw_value: Any) -> List[str]:
        """标准化 summarization.model_name 配置。"""
        if raw_value is None:
            return ["auto"]
        if isinstance(raw_value, list):
            if any(not isinstance(item, str) for item in raw_value):
                raise ValueError(
                    "summarization.model_name 必须为 List[str]。 请执行 scripts/release_vnext_migrate.py migrate。"
                )
            selectors = [str(x).strip() for x in raw_value if str(x).strip()]
            return selectors or ["auto"]
        raise ValueError(
            "summarization.model_name 必须为 List[str]。 请执行 scripts/release_vnext_migrate.py migrate。"
        )

    def _pick_default_summary_task(
        self, available_tasks: Dict[str, TaskConfig]
    ) -> Tuple[Optional[str], Optional[TaskConfig]]:
        """
        选择总结默认任务，避免错误落到 embedding/voice/vlm 等非文本生成任务。
        优先级：memory > utils > planner > tool_use。
        """
        for task_name in ("memory", "utils", "planner", "tool_use"):
            task_cfg = available_tasks.get(task_name)
            model_list = getattr(task_cfg, "model_list", []) if task_cfg is not None else []
            if any(str(model_name).strip() for model_name in (model_list or [])):
                return task_name, task_cfg
        return None, None

    @staticmethod
    def _current_model_dict() -> Dict[str, Any]:
        try:
            return getattr(config_manager.get_model_config(), "models_dict", {}) or {}
        except Exception as exc:
            logger.warning(f"读取当前模型字典失败: {exc}")
            return {}

    def _resolve_summary_model_task(self) -> Optional[Tuple[str, TaskConfig]]:
        """
        解析 summarization.model_name 为 (task_name, TaskConfig)。
        支持：
        - ["utils:model1", "utils:model2", "replyer"]（数组混合语法）
        - ["auto"]
        - ["some-model-name"]（具体模型名）
        """
        available_tasks = get_text_generation_model_tasks(llm_api)
        if not available_tasks:
            return None

        # vNext 要求该字段为 List[str]；当配置缺失时回退到 ["auto"]，
        # 避免默认值本身触发类型校验异常。
        raw_cfg = self.plugin_config.get("summarization", {}).get("model_name", ["auto"])
        selectors = self._normalize_summary_model_selectors(raw_cfg)
        default_task_name, default_task_cfg = self._pick_default_summary_task(available_tasks)

        base_cfg: Optional[TaskConfig] = None
        base_task_name: Optional[str] = None
        model_dict = self._current_model_dict()

        def _find_task_for_model(model_name: str) -> Tuple[Optional[str], Optional[TaskConfig]]:
            return find_text_generation_task_for_model(available_tasks, model_name)

        for raw_selector in selectors:
            selector = raw_selector.strip()
            if not selector:
                continue

            if selector.lower() == "auto":
                if default_task_cfg:
                    if base_cfg is None:
                        base_cfg = default_task_cfg
                        base_task_name = default_task_name
                continue

            if ":" in selector:
                task_name, model_name = selector.split(":", 1)
                task_name = task_name.strip()
                model_name = model_name.strip()
                task_cfg = available_tasks.get(task_name)
                if not task_cfg:
                    logger.warning(f"总结模型选择器 '{selector}' 的任务 '{task_name}' 不存在，已跳过")
                    continue

                if not model_name or model_name.lower() == "auto":
                    if base_cfg is None:
                        base_cfg = task_cfg
                        base_task_name = task_name
                    continue

                if model_name in task_cfg.model_list:
                    if base_cfg is None:
                        base_cfg = task_cfg
                        base_task_name = task_name
                    logger.info(
                        f"总结模型选择器 '{selector}' 已定位到任务 '{task_name}'；"
                        "当前 LLM 服务按任务候选列表执行，不单独覆盖具体模型。"
                    )
                else:
                    logger.warning(
                        f"总结模型选择器 '{selector}' 的模型 '{model_name}' 不在任务 '{task_name}' 中，已跳过"
                    )
                continue

            task_cfg = available_tasks.get(selector)
            if task_cfg:
                if base_cfg is None:
                    base_cfg = task_cfg
                    base_task_name = selector
                continue

            if selector in model_dict:
                task_name, task_cfg = _find_task_for_model(selector)
                if task_name and task_cfg:
                    if base_cfg is None:
                        base_cfg = task_cfg
                        base_task_name = task_name
                    logger.info(
                        f"总结模型选择器 '{selector}' 已映射到任务 '{task_name}'；"
                        "当前 LLM 服务按任务候选列表执行，不单独覆盖具体模型。"
                    )
                    continue
                logger.warning(f"总结模型选择器 '{selector}' 未归属于任何任务，已跳过")
                continue

            logger.warning(f"总结模型选择器 '{selector}' 无法识别，已跳过")

        if base_cfg is None or not base_task_name:
            if default_task_cfg:
                if base_cfg is None:
                    base_cfg = default_task_cfg
                    base_task_name = default_task_name

        if base_cfg is None or not base_task_name:
            return None

        template_cfg = base_cfg
        task_name_to_use = base_task_name
        return task_name_to_use, TaskConfig(
            model_list=list(template_cfg.model_list),
            max_tokens=template_cfg.max_tokens,
            temperature=template_cfg.temperature,
            selection_strategy=template_cfg.selection_strategy,
            hard_timeout=template_cfg.hard_timeout,
        )

    def _resolve_summary_model_config(self) -> Optional[TaskConfig]:
        resolved = self._resolve_summary_model_task()
        if resolved is None:
            return None
        return resolved[1]

    def _summary_review_count(self, metadata: Optional[Dict[str, Any]]) -> int:
        raw_value: Any = None
        if isinstance(metadata, dict):
            raw_value = metadata.get("summary_review_count")
        if raw_value is None:
            raw_value = self.plugin_config.get("summarization", {}).get("history_review_count", 2)
        try:
            return max(0, int(raw_value or 0))
        except Exception:
            return 2

    @staticmethod
    def _clean_review_summary(text: str) -> str:
        """规范化历史摘要，不按词面猜测事实是否有效。

        事实失效必须由结构化 supersession 状态表达。中文子串无法可靠区分
        “旧值”和“旧金山”、“测试内容”和“测试工程师”，删除命中句会造成
        合法负向事实、职业和地名在递归摘要中无声消失。
        """

        return " ".join(str(text or "").strip().split())[:500]

    def _existing_summary_result(
        self,
        *,
        stream_id: str,
        metadata: Optional[Dict[str, Any]],
    ) -> Optional[SummaryImportResult]:
        """在模型调用前解析 external ID，避免重试重复生成摘要。"""

        if not isinstance(metadata, dict):
            return None
        external_id = str(metadata.get("external_id", "") or "").strip()
        if not external_id:
            return None
        checkpoint = self.metadata_store.get_summary_checkpoint(external_id)
        if checkpoint is not None and checkpoint["chat_id"] != stream_id:
            raise ValueError("摘要标识已绑定到其他聊天流")
        if checkpoint is not None and checkpoint.get("phase") == "pending":
            return None
        existing = self.metadata_store.get_external_memory_ref(external_id)
        if existing is None:
            if checkpoint is not None:
                return SummaryImportResult(
                    True, "该窗口已完成空增量总结", source=f"chat_summary:{stream_id}", skipped=True
                )
            return None
        paragraph_hash = str(existing.get("paragraph_hash", "") or "").strip()
        paragraph = self.metadata_store.get_paragraph(paragraph_hash) if paragraph_hash else None
        if not isinstance(paragraph, dict) or bool(paragraph.get("is_deleted", 0)):
            raise RuntimeError(
                f"摘要 external ID 指向无效段落: external_id={external_id} paragraph={paragraph_hash}"
            )
        expected_source = f"chat_summary:{stream_id}"
        actual_source = str(paragraph.get("source", "") or "").strip()
        # 正文哈希可由多个群共享，窗口归属以独立的 external ID 来源记录为准。
        ref_metadata = coerce_metadata_dict(existing.get("metadata"))
        ref_chat_id = str(ref_metadata.get("chat_id", "") or "").strip()
        origin_matches = (
            existing.get("source_type") == "chat_summary" and ref_chat_id == stream_id
            if ref_chat_id else actual_source == expected_source
        )
        if not origin_matches:
            raise RuntimeError(
                f"摘要 external ID 已绑定到其他聊天流: external_id={external_id} "
                f"expected={expected_source} actual={actual_source}"
            )
        # 公共写入可能已成功，但调用方在记录游标前中断；重试时补齐窗口进度。
        if checkpoint is None and metadata.get("trigger_message_count"):
            self.metadata_store.record_summary_checkpoint(
                external_id=external_id, chat_id=stream_id,
                trigger_message_count=int(metadata["trigger_message_count"]),
            )
        return SummaryImportResult(
            True,
            "总结已存在，跳过重复生成",
            paragraph_hash=paragraph_hash,
            source=expected_source,
        )

    def _build_previous_summary_context(
        self,
        stream_id: str,
        *,
        limit: int,
    ) -> str:
        if limit <= 0:
            return ""
        try:
            paragraphs = self.metadata_store.get_chat_summary_history(stream_id)
        except Exception as exc:
            logger.debug(f"读取历史摘要回顾失败: stream_id={stream_id} error={exc}")
            return ""
        if not paragraphs:
            return ""

        ordered = sorted(paragraphs, key=_paragraph_created_at, reverse=True)
        lines: List[str] = []
        now = time.time()
        for paragraph in ordered:
            paragraph_metadata = coerce_metadata_dict(paragraph.get("metadata"))
            memory_change = (
                paragraph_metadata.get("memory_change")
                if isinstance(paragraph_metadata.get("memory_change"), dict)
                else {}
            )
            valid_to = optional_float(memory_change.get("valid_to"))
            if valid_to is not None and valid_to <= now:
                continue
            cleaned = self._clean_review_summary(str(paragraph.get("content", "") or ""))
            if not cleaned:
                continue
            lines.append(f"- {cleaned}")
            if len(lines) >= limit:
                break
        if not lines:
            return ""

        return (
            "\n\n历史净化摘要回顾（只用于理解上下文和识别变化，不得直接复制到本轮增量）：\n"
            + "\n".join(lines)
            + "\n"
        )

    async def import_from_stream(
        self,
        stream_id: str,
        context_length: Optional[int] = None,
        include_personality: Optional[bool] = None,
        time_end: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
        *,
        bot_name: str = "",
        personality_context: str = "",
    ) -> SummaryImportResult:
        """串行化同一聊天流的摘要生成，使 external ID 检查覆盖并发重试。"""

        stream_token = str(stream_id or "").strip()
        if not stream_token:
            return SummaryImportResult(False, "stream_id 不能为空")
        external_id = str((metadata or {}).get("external_id", "") or "").strip()
        # 始终持有聊天流锁；再锁 external ID，兼顾不同窗口串行化和跨流 ID 冲突。
        stream_lock = self._import_locks.setdefault(f"stream:{stream_token}", asyncio.Lock())
        async with stream_lock:
            if external_id:
                external_lock = self._import_locks.setdefault(f"external:{external_id}", asyncio.Lock())
                async with external_lock:
                    return await self._import_from_stream_unlocked(
                        stream_token,
                        context_length=context_length,
                        include_personality=include_personality,
                        time_end=time_end,
                        metadata=metadata,
                        bot_name=bot_name,
                        personality_context=personality_context,
                    )
            return await self._import_from_stream_unlocked(
                stream_token,
                context_length=context_length,
                include_personality=include_personality,
                time_end=time_end,
                metadata=metadata,
                bot_name=bot_name,
                personality_context=personality_context,
            )

    async def _import_from_stream_unlocked(
        self, stream_id: str, **kwargs: Any,
    ) -> SummaryImportResult:
        with self._llm_bridge_context():
            async with fact_scope_lock(self.metadata_store, "chat", stream_id):
                return await self._import_from_stream_scoped(stream_id, **kwargs)

    async def _finish_fact_summary(self, payload: Dict[str, Any]) -> SummaryImportResult:
        """从已持久化的决定继续写入；失败重试不重新总结、不重新判断重复。"""
        plan = payload["plan"]
        stream_id = plan["scope_id"]
        metadata = payload["metadata"]
        external_id = metadata["external_id"]
        decisions = {row["fact_id"]: row for row in plan["decisions"]}
        kept = [row for row in plan["facts"] if decisions[row["fact_id"]]["action"] != "reuse"]
        paragraph_hash = ""
        if kept:
            text = "\n".join(row["text"] for row in kept)
            person_ids = sorted({person_id for row in kept for person_id in row.get("person_ids", [])})
            bindings = metadata.get("identity_bindings", {})
            metadata = {**metadata, "person_ids": person_ids,
                        "identity_bindings": {name: value for name, value in bindings.items()
                                              if value["person_id"] in person_ids}}
            kept_ids = {row["fact_id"] for row in kept}
            entities = [name for name in payload["entities"] if name in text]
            relations = [relation for index, relation in enumerate(payload["relations"])
                         if kept_ids.intersection(plan["relation_support"].get(str(index), []))]
            paragraph_hash = await self._execute_import(
                text, entities, relations, stream_id, time_meta=payload["time_meta"],
                metadata={**metadata, "dedup_managed": True, "dedup_phase": "pending",
                          "dedup_fact_texts": [row["text"] for row in kept]},
            )
            await cancellation_safe_to_thread(
                self._link_summary_image_evidence, stream_id=stream_id, paragraph_hash=paragraph_hash,
                facts=kept, allowed={tuple(value) for value in payload["allowed_images"]},
            )
        dedup = FactDeduplicator(self.metadata_store)
        claim_ids = await cancellation_safe_to_thread(dedup.commit, plan, paragraph_hash)
        # 被复用的事实也保留本窗口图片证据，但不能把图片挂到无关的新摘要上。
        for decision, claim_id in zip(plan["decisions"], claim_ids, strict=True):
            if decision["action"] == "reuse":
                evidence = await cancellation_safe_to_thread(self.metadata_store.get_fact_evidence, claim_id)
                target = next(
                    (
                        str(row["evidence_id"]).strip()
                        for row in evidence
                        if row.get("evidence_type") == "paragraph" and str(row.get("evidence_id") or "").strip()
                    ),
                    "",
                )
                fact = next((row for row in plan["facts"] if row["fact_id"] == decision["fact_id"]), None)
                if target and fact is not None:
                    await cancellation_safe_to_thread(
                        self._link_summary_image_evidence,
                        stream_id=stream_id,
                        paragraph_hash=target,
                        facts=[fact],
                        allowed={tuple(value) for value in payload["allowed_images"]},
                    )
        await cancellation_safe_to_thread(self._persist_all_vector_stores)
        await cancellation_safe_to_thread(self.graph_store.save)
        if paragraph_hash:
            claim_map = dict(zip([row["fact_id"] for row in plan["decisions"]], claim_ids, strict=True))
            relation_claims = {
                self.metadata_store.compute_relation_hash(row["subject"], row["predicate"], row["object"]):
                    [claim_map[fact_id] for fact_id in plan["relation_support"].get(str(index), [])
                     if fact_id in {fact["fact_id"] for fact in kept}]
                for index, row in enumerate(payload["relations"])
            }
            await cancellation_safe_to_thread(self.metadata_store.update_paragraph_metadata, paragraph_hash,
                                   {"fact_claim_ids": [claim_map[row["fact_id"]] for row in kept], "dedup_phase": "complete",
                                    "dedup_relation_claims": relation_claims}, merge=True)
            await cancellation_safe_to_thread(
                self.metadata_store.upsert_external_memory_ref, external_id=external_id,
                paragraph_hash=paragraph_hash, source_type="chat_summary",
                metadata={"chat_id": stream_id, "person_ids": metadata.get("person_ids", []),
                          "dedup_phase": "complete", "trigger_message_count": metadata.get("trigger_message_count", 0)},
            )
        if paragraph_hash:
            await cancellation_safe_to_thread(self.metadata_store.enqueue_episode_source_rebuild, f"chat_summary:{stream_id}",
                                   reason="dedup_summary_complete")
            if self.plugin_config.get("person_profile", {}).get("enabled", True):
                for person_id in metadata.get("person_ids", []):
                    await cancellation_safe_to_thread(self.metadata_store.enqueue_person_profile_refresh,
                                           person_id=person_id, reason="dedup_summary_complete")
        await cancellation_safe_to_thread(
            self.metadata_store.record_summary_checkpoint, external_id=external_id, chat_id=stream_id,
            trigger_message_count=int(metadata.get("trigger_message_count") or 0), phase="complete",
        )
        return SummaryImportResult(True, "事实去重后总结写入完成" if kept else "该窗口事实均已存在，已追加来源证据",
                                   paragraph_hash=paragraph_hash, source=f"chat_summary:{stream_id}", skipped=not kept)

    async def _summarize_adapted_messages(
        self,
        *,
        stream_id: str,
        messages: Sequence[Any],
        include_personality: bool = True,
        metadata: Optional[Dict[str, Any]] = None,
        time_meta_override: Optional[Dict[str, Any]] = None,
        bot_name_override: str = "",
        personality_context_override: str = "",
        fallback_transcript_rows: Optional[List[Dict[str, Any]]] = None,
    ) -> SummaryImportResult:
        """对消息窗口执行统一的 v2.0 摘要生成、身份恢复、图片证据绑定与事实去重写入。"""
        identity_context = SummaryIdentityContext(self.metadata_store, messages)
        chat_history_text, identity_catalog = await cancellation_safe_to_thread(identity_context.render)
        image_evidence_catalog, allowed_image_evidence = self._image_evidence_catalog(
            messages,
            stream_id=stream_id,
        )
        review_count = self._summary_review_count(metadata)
        previous_summary_context = await cancellation_safe_to_thread(
            self._build_previous_summary_context,
            stream_id,
            limit=review_count,
        )

        bot_name = str(bot_name_override or global_config.bot.nickname or "助手").strip() or "助手"
        personality_context = ""
        if include_personality:
            if personality_context_override:
                personality_context = personality_context_override
            else:
                personality = global_config.personality.personality
                if personality:
                    personality_context = f"你的性格设定是：{personality}"

        template = load_prompt_template(SUMMARY_PROMPT_NAME)
        if "identity_catalog" not in extract_prompt_placeholders(template.template):
            raise ValueError("聊天摘要模板缺少 {identity_catalog}，请更新模板以使用程序确认的身份映射")
        prompt = load_prompt(
            SUMMARY_PROMPT_NAME,
            bot_name=bot_name,
            personality_context=personality_context,
            previous_summary_context=previous_summary_context,
            chat_history=chat_history_text,
            image_evidence_catalog=image_evidence_catalog,
            identity_catalog=identity_catalog,
        )

        resolved_model = self._resolve_summary_model_task()
        if resolved_model is None:
            if fallback_transcript_rows is not None:
                return await self._import_deterministic_transcript_fallback(
                    stream_id=stream_id,
                    transcript_messages=fallback_transcript_rows,
                    time_meta=time_meta_override,
                    metadata=metadata,
                )
            return SummaryImportResult(False, "未找到可用的总结模型配置")
        task_name_to_use, model_config_to_use = resolved_model

        logger.info(
            f"正在为流 {stream_id} 执行总结，消息条数: {len(messages)}|总结模型任务: {task_name_to_use}|候选列表: {model_config_to_use.model_list}"
        )

        result = await llm_api.generate(
            llm_api.LLMServiceRequest(
                task_name=task_name_to_use,
                request_type="A_Memorix.ChatSummarization",
                prompt=prompt,
                temperature=model_config_to_use.temperature,
                max_tokens=model_config_to_use.max_tokens,
            )
        )
        success = bool(result.success)
        response = str(result.completion.response or "")

        if not success or not response:
            return SummaryImportResult(False, "LLM 生成总结失败")

        data = self._parse_llm_response(response)
        if not data or "summary" not in data:
            return SummaryImportResult(False, "解析 LLM 响应失败或总结为空")

        raw_summary = data["summary"]
        if not isinstance(raw_summary, str):
            return SummaryImportResult(False, "增量总结 summary 必须是字符串")
        summary_text = raw_summary.strip()
        entities = _normalize_entity_items(data.get("entities"))
        relations = _normalize_relation_items(data.get("relations"))
        # 无增量时兼容旧响应省略 facts；显式非法值和非空摘要仍严格校验。
        facts = _normalize_summary_facts(data.get("facts", []))
        if summary_text and not facts:
            return SummaryImportResult(False, "非空增量总结必须提供非空 facts，不能跳过事实去重")
        if not summary_text:
            if data.get("entities") != [] or data.get("relations") != [] or facts:
                return SummaryImportResult(False, "空增量必须同时返回空实体、空关系和空事实数组")
            external_id = str((metadata or {}).get("external_id") or "").strip()
            if external_id:
                await cancellation_safe_to_thread(
                    self.metadata_store.record_summary_checkpoint,
                    external_id=external_id,
                    chat_id=stream_id,
                    trigger_message_count=int((metadata or {}).get("trigger_message_count") or 0),
                )
            return SummaryImportResult(
                True,
                "当前窗口没有新增长期记忆，跳过段落写入",
                source=f"chat_summary:{stream_id}",
                skipped=True,
            )

        # 人物归属由程序给出的标记和 ID 确定；名称只在写入前按持久映射恢复。
        identity_metadata = identity_context.metadata(
            [summary_text, *entities, *(value for relation in relations for value in relation.values()),
             *(fact["text"] for fact in facts)]
        )
        summary_text = identity_context.restore_text(summary_text)
        entities = [identity_context.restore_text(name) for name in entities]
        relations = [{key: identity_context.restore_text(value) for key, value in relation.items()}
                     for relation in relations]
        facts = [{**fact, "person_ids": identity_context.metadata([fact["text"]])["person_ids"],
                  "text": identity_context.restore_text(fact["text"])} for fact in facts]
        metadata = {**(metadata or {}), **identity_metadata}

        msg_times = [timestamp for msg in messages if (timestamp := _message_timestamp(msg)) is not None]
        if time_meta_override is not None:
            time_meta = dict(time_meta_override)
        else:
            time_meta = {}
            if msg_times:
                time_meta = {
                    "event_time_start": min(msg_times),
                    "event_time_end": max(msg_times),
                    "time_granularity": "minute",
                    "time_confidence": 0.95,
                }

        # 先验证完整证据集合，避免模型输出错误时留下已提交的段落或部分图片关联。
        for fact in facts:
            for evidence in fact.get("image_evidence") or []:
                key = (str(evidence["message_id"]), str(evidence["component_path"]))
                if key not in allowed_image_evidence:
                    raise ValueError(f"总结模型返回了窗口外图片证据: {key[0]}:{key[1]}")

        observed_at = (
            time_meta.get("event_time_end")
            if time_meta.get("event_time_end") is not None
            else (max(msg_times) if msg_times else None)
        )
        source = {
            "platform": str(messages[0].platform),
            "chat_id": stream_id,
            "message_ids": [str(message.message_id) for message in messages],
            "observed_at": observed_at,
            **time_meta,
        }
        source["observation_id"] = observation_id(
            platform=source["platform"],
            chat_id=stream_id,
            message_ids=source["message_ids"],
            window_id=str(metadata.get("external_id") or ""),
        )
        if not str(metadata.get("external_id") or "").strip():
            metadata["external_id"] = f"summary:{source['observation_id']}"
        searchable_vectors = self.vector_store if hasattr(self.vector_store, "search") else None
        dedup = FactDeduplicator(self.metadata_store, self.embedding_manager, searchable_vectors, task_name_to_use)
        plan = await dedup.plan(scope_type="chat", scope_id=stream_id, facts=facts, source=source, relations=relations)
        payload = {
            "plan": plan,
            "metadata": metadata,
            "entities": entities,
            "relations": relations,
            "time_meta": time_meta,
            "allowed_images": list(allowed_image_evidence),
        }
        await cancellation_safe_to_thread(
            self.metadata_store.record_summary_checkpoint,
            external_id=metadata["external_id"],
            chat_id=stream_id,
            trigger_message_count=int(metadata.get("trigger_message_count") or 0),
            phase="pending",
            result=payload,
        )
        return await self._finish_fact_summary(payload)

    async def _import_from_stream_scoped(
        self,
        stream_id: str,
        context_length: Optional[int] = None,
        include_personality: Optional[bool] = None,
        time_end: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
        bot_name: str = "",
        personality_context: str = "",
    ) -> SummaryImportResult:
        """
        从指定的聊天流中提取记录并执行总结导入

        Args:
            stream_id: 聊天流 ID
            context_length: 总结的历史消息条数
            include_personality: 是否包含人设
            time_end: 用于截取聊天记录的时间上界（闭区间）

        Returns:
            SummaryImportResult: 导入结果；有增量时包含新增段落 hash，无增量时标记为 skipped。
        """
        try:
            existing_result = await cancellation_safe_to_thread(
                self._existing_summary_result,
                stream_id=stream_id,
                metadata=metadata,
            )
            if existing_result is not None:
                return existing_result
            external_id = str((metadata or {}).get("external_id") or "")
            checkpoint = (
                await cancellation_safe_to_thread(self.metadata_store.get_summary_checkpoint, external_id)
                if external_id
                else None
            )
            if checkpoint is not None and checkpoint.get("phase") == "pending":
                return await self._finish_fact_summary(json.loads(checkpoint["result_json"]))

            self_check_ok, self_check_msg = await self._ensure_runtime_self_check()
            if not self_check_ok:
                return SummaryImportResult(False, f"导入前自检失败: {self_check_msg}")

            # 1. 获取配置
            if context_length is None:
                context_length = self.plugin_config.get("summarization", {}).get("context_length", 50)

            if include_personality is None:
                include_personality = self.plugin_config.get("summarization", {}).get("include_personality", True)

            # 2. 获取历史消息（优先宿主 message_api，若为空则回退读取插件本地 transcript_messages）
            query_time_end = time.time() if time_end is None else float(time_end)
            messages = await cancellation_safe_to_thread(
                message_api.get_messages_by_time_in_chat,
                chat_id=stream_id,
                start_time=0.0,
                end_time=query_time_end,
                limit=context_length,
                limit_mode="latest",
            )
            time_meta_override: Optional[Dict[str, Any]] = None
            effective_metadata: Optional[Dict[str, Any]] = dict(metadata) if isinstance(metadata, dict) else metadata
            if not messages:
                transcript_getter = getattr(self.metadata_store, "get_transcript_messages", None)
                if callable(transcript_getter):
                    transcript_rows = await cancellation_safe_to_thread(
                        transcript_getter,
                        stream_id,
                        max(1, int(context_length)),
                    )
                    if time_end is not None and transcript_rows:
                        transcript_rows = [
                            row
                            for row in transcript_rows
                            if (ts := self._extract_transcript_message_timestamp(row)) is None or ts <= query_time_end
                        ]
                    if transcript_rows:
                        session_meta = self._transcript_session_metadata(stream_id)
                        merged_meta = {
                            k: v
                            for k, v in {
                                **session_meta,
                                "source_type": "chat_summary",
                                "chat_id": stream_id,
                                **(effective_metadata or {}),
                            }.items()
                            if v not in (None, "", [])
                        }
                        effective_metadata = merged_meta
                        time_meta_override = self._derive_transcript_time_meta(transcript_rows)
                        messages = self._adapt_transcript_messages(
                            transcript_rows,
                            stream_id=stream_id,
                            session_metadata=session_meta,
                            bot_name=bot_name,
                        )

            if not messages:
                return SummaryImportResult(False, "未找到有效的聊天记录进行总结")

            return await self._summarize_adapted_messages(
                stream_id=stream_id,
                messages=messages,
                include_personality=bool(include_personality),
                metadata=effective_metadata,
                time_meta_override=time_meta_override,
                bot_name_override=bot_name,
                personality_context_override=personality_context,
                fallback_transcript_rows=None,
            )

        except Exception as e:
            logger.error(f"总结导入过程中出错: {e}\n{traceback.format_exc()}")
            return SummaryImportResult(False, f"错误: {str(e)}")

    async def _ensure_runtime_self_check(self) -> Tuple[bool, str]:
        plugin_instance = self.plugin_config.get("plugin_instance") if isinstance(self.plugin_config, dict) else None
        if plugin_instance is not None:
            report = await ensure_runtime_self_check(plugin_instance)
        else:
            report = await run_embedding_runtime_self_check(
                config=self.plugin_config,
                vector_store=self.vector_store,
                embedding_manager=self.embedding_manager,
            )
        if bool(report.get("ok", False)):
            return True, ""
        if self._allow_metadata_only_write():
            msg = (
                f"{report.get('message', 'unknown')} "
                f"(configured={report.get('configured_dimension', 0)}, "
                f"store={report.get('vector_store_dimension', 0)}, "
                f"encoded={report.get('encoded_dimension', 0)})"
            )
            logger.warning(f"总结导入进入 metadata-only 回退模式: {msg}")
            return True, "embedding_degraded_metadata_only"
        return (
            False,
            f"{report.get('message', 'unknown')} "
            f"(configured={report.get('configured_dimension', 0)}, "
            f"store={report.get('vector_store_dimension', 0)}, "
            f"encoded={report.get('encoded_dimension', 0)})",
        )

    def _parse_llm_response(self, response: str) -> Dict[str, Any]:
        """解析 LLM 返回的 JSON"""
        try:
            # 尝试查找 JSON
            json_match = re.search(r"\{.*\}", response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            return {}
        except Exception as e:
            logger.warning(f"解析总结 JSON 失败: {e}")
            return {}

    async def _execute_import(
        self,
        summary: str,
        entities: List[str],
        relations: List[Dict[str, str]],
        stream_id: str,
        time_meta: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """将数据写入存储"""
        external_id = str((metadata or {}).get("external_id", "") or "").strip()
        plugin_instance = self._plugin_instance()
        common_ingest = getattr(plugin_instance, "ingest_text", None)
        if external_id and callable(common_ingest):
            normalized_relations = _normalize_relation_items(relations)
            result = await common_ingest(
                external_id=external_id,
                source_type="chat_summary",
                text=summary,
                chat_id=stream_id,
                person_ids=list((metadata or {}).get("person_ids", [])),
                time_start=(time_meta or {}).get("event_time_start"),
                time_end=(time_meta or {}).get("event_time_end"),
                metadata=metadata,
                entities=_normalize_entity_items(entities),
                relations=normalized_relations,
                respect_filter=False,
            )
            existing = self.metadata_store.get_external_memory_ref(external_id)
            paragraph_hash = str((existing or {}).get("paragraph_hash", "") or "").strip()
            if paragraph_hash:
                return paragraph_hash
            stored_ids = [str(item or "").strip() for item in result.get("stored_ids", []) if str(item or "").strip()]
            if stored_ids:
                return stored_ids[0]
            raise RuntimeError(f"公共 ingest 未返回摘要段落: external_id={external_id}")

        # 获取默认知识类型
        type_str = self.plugin_config.get("summarization", {}).get("default_knowledge_type", "narrative")
        try:
            knowledge_type = resolve_stored_knowledge_type(type_str, content=summary)
        except ValueError:
            logger.warning(f"非法 summarization.default_knowledge_type={type_str}，回退 narrative")
            knowledge_type = KnowledgeType.NARRATIVE

        # 导入总结文本
        hash_value = self.metadata_store.add_paragraph(
            content=summary,
            source=f"chat_summary:{stream_id}",
            metadata=metadata,
            knowledge_type=knowledge_type.value,
            time_meta=time_meta,
        )

        vector_writer = getattr(plugin_instance, "write_paragraph_vector_or_enqueue", None)
        if callable(vector_writer):
            result = await vector_writer(
                paragraph_hash=hash_value,
                content=summary,
                context="summary_import",
            )
            if str(result.get("warning", "") or "").strip():
                logger.warning(f"总结导入段落进入回退写入: {result}")
        else:
            try:
                embedding = await self.embedding_manager.encode(summary)
                self.vector_store.add(vectors=embedding.reshape(1, -1), ids=[hash_value])
            except Exception as exc:
                if not self._allow_metadata_only_write():
                    raise
                logger.warning(f"总结导入段落向量写入失败，改为回填队列: {exc}")
                self.metadata_store.enqueue_paragraph_vector_backfill(hash_value, error=str(exc))

        # 导入实体
        normalized_entities = _normalize_entity_items(entities)
        if normalized_entities:
            with self.metadata_store.transaction(immediate=True), self._graph_batch_update():
                self.graph_store.add_nodes(normalized_entities)
                entity_hashes = self.metadata_store.add_entities_batch(
                    normalized_entities,
                    source_paragraph=hash_value,
                )
            entity_vector_items = list(zip(entity_hashes, normalized_entities, strict=True))
            await self._ensure_entity_vectors(entity_vector_items)

        # 导入关系
        rv_cfg = self.plugin_config.get("retrieval", {}).get("relation_vectorization", {})
        if not isinstance(rv_cfg, dict):
            rv_cfg = {}
        write_vector = bool(rv_cfg.get("enabled", False)) and bool(rv_cfg.get("write_on_import", True))
        normalized_relations = _normalize_relation_items(relations)
        relation_tuples = [
            (rel["subject"], rel["predicate"], rel["object"])
            for rel in normalized_relations
        ]
        if relation_tuples:
            if self.relation_write_service is not None:
                await self.relation_write_service.upsert_relations_with_vectors(
                    relation_tuples,
                    confidence=1.0,
                    source_paragraph=hash_value,
                    write_vector=write_vector,
                )
            else:
                with self.metadata_store.transaction(immediate=True), self._graph_batch_update():
                    # 写入元数据和图数据库（保留 relation_hashes，确保后续可按关系精确修剪）
                    relation_hashes = self.metadata_store.add_relations_batch(
                        relation_tuples,
                        confidence=1.0,
                        source_paragraph=hash_value,
                    )
                    self.graph_store.add_edges(
                        [(subject, obj) for subject, _, obj in relation_tuples],
                        relation_hashes=relation_hashes,
                    )

        logger.info(f"总结导入完成: hash={hash_value[:8]}")
        return hash_value

    def _cfg(self, key: str, default: Any = None) -> Any:
        current: Any = self.plugin_config if isinstance(self.plugin_config, dict) else {}
        for part in key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current

    @staticmethod
    def _transcript_message_metadata(message: Dict[str, Any]) -> Dict[str, Any]:
        metadata = message.get("metadata")
        if not isinstance(metadata, dict):
            return {}
        nested = metadata.get("metadata")
        if isinstance(nested, dict):
            merged = dict(metadata)
            merged.update(nested)
            return merged
        return metadata

    @classmethod
    def _transcript_speaker_identity(cls, message: Dict[str, Any]) -> str:
        metadata = cls._transcript_message_metadata(message)
        for key in ("sender_name", "person_name", "nickname", "group_nick_name"):
            candidate = str(message.get(key) or metadata.get(key) or "").strip()
            if candidate:
                return candidate
        platform = str(message.get("platform") or metadata.get("platform") or "").strip()
        sender_id = str(
            message.get("sender_id")
            or message.get("user_id")
            or metadata.get("sender_id")
            or metadata.get("user_id")
            or ""
        ).strip()
        if sender_id:
            return f"{platform}:{sender_id}" if platform else sender_id
        return ""

    def _build_transcript_chat_text(self, messages: List[Dict[str, Any]], *, bot_name: str = "") -> str:
        lines: List[str] = []
        for item in messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "user") or "user")
            content = str(item.get("content", "") or "").strip()
            if not content:
                continue
            speaker = ""
            if role.strip().lower() == "user":
                speaker = self._transcript_speaker_identity(item)
            elif role.strip().lower() == "assistant":
                speaker = str(bot_name or "").strip()
            label = speaker or role
            lines.append(f"{label}: {content}")
        return "\n".join(lines)

    @classmethod
    def _extract_transcript_message_timestamp(cls, item: Dict[str, Any]) -> Optional[float]:
        def _coerce_ts(val: Any) -> Optional[float]:
            if val is None:
                return None
            try:
                ts = float(val)
            except (TypeError, ValueError):
                return None
            return ts if ts > 0 else None

        if not isinstance(item, dict):
            return None
        metadata = cls._transcript_message_metadata(item)
        for key in ("timestamp", "time", "ts"):
            explicit_ts = _coerce_ts(item.get(key))
            if explicit_ts is None:
                explicit_ts = _coerce_ts(metadata.get(key))
            if explicit_ts is not None:
                return explicit_ts
        return _coerce_ts(item.get("created_at"))

    def _derive_transcript_time_meta(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        def _coerce_ts(val: Any) -> Optional[float]:
            if val is None:
                return None
            try:
                ts = float(val)
            except (TypeError, ValueError):
                return None
            return ts if ts > 0 else None

        event_times: List[float] = []
        fallback_times: List[float] = []
        for item in messages:
            if not isinstance(item, dict):
                continue
            metadata = self._transcript_message_metadata(item)
            explicit_ts = None
            for key in ("timestamp", "time", "ts"):
                explicit_ts = _coerce_ts(item.get(key))
                if explicit_ts is None:
                    explicit_ts = _coerce_ts(metadata.get(key))
                if explicit_ts is not None:
                    break
            if explicit_ts is not None:
                event_times.append(explicit_ts)
                continue
            created_at = _coerce_ts(item.get("created_at"))
            if created_at is not None:
                fallback_times.append(created_at)

        times = event_times or fallback_times
        if not times:
            return {}

        start = min(times)
        end = max(times)
        confidence = 0.95 if event_times else 0.6
        time_meta: Dict[str, Any] = {
            "event_time_start": start,
            "event_time_end": end,
            "time_granularity": "minute",
            "time_confidence": confidence,
        }
        if abs(end - start) <= 1.0:
            time_meta["event_time"] = end
        return time_meta

    def _transcript_session_metadata(self, session_id: str) -> Dict[str, Any]:
        getter = getattr(self.metadata_store, "get_transcript_session", None)
        if not callable(getter):
            return {}
        session = getter(session_id)
        if not isinstance(session, dict):
            return {}
        metadata = session.get("metadata")
        return dict(metadata) if isinstance(metadata, dict) else {}

    @staticmethod
    def _are_persisted_transcript_rows(messages: List[Dict[str, Any]], *, session_id: str) -> bool:
        if not messages:
            return False
        return all(
            isinstance(item, dict)
            and "position" in item
            and "created_at" in item
            and str(item.get("session_id") or "").strip() == session_id
            for item in messages
        )

    def _adapt_transcript_messages(
        self,
        messages: List[Dict[str, Any]],
        *,
        stream_id: str,
        session_metadata: Optional[Dict[str, Any]] = None,
        bot_name: str = "",
    ) -> List[Any]:
        sess_meta = session_metadata if isinstance(session_metadata, dict) else {}
        default_platform = str(sess_meta.get("platform") or "").strip()
        if not default_platform:
            for row in messages:
                if not isinstance(row, dict):
                    continue
                row_meta = self._transcript_message_metadata(row)
                candidate_pf = str(row.get("platform") or row_meta.get("platform") or "").strip()
                if candidate_pf:
                    default_platform = candidate_pf
                    break
        default_platform = default_platform or "astrbot"
        default_group_id = str(sess_meta.get("group_id") or "").strip()
        default_group_name = str(sess_meta.get("group_name") or "").strip()

        adapted: List[Any] = []
        base_now = time.time()
        seen_message_ids: set[str] = set()
        for idx, item in enumerate(messages):
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "") or "").strip()
            if not content:
                continue
            msg_meta = self._transcript_message_metadata(item)
            role = str(item.get("role", "user") or "user").strip().lower()
            platform = (
                str(item.get("platform") or msg_meta.get("platform") or default_platform).strip()
                or default_platform
            )
            group_id = str(item.get("group_id") or msg_meta.get("group_id") or default_group_id).strip()
            group_name = str(item.get("group_name") or msg_meta.get("group_name") or default_group_name).strip()

            raw_msg_id = str(
                msg_meta.get("message_id")
                or item.get("message_id")
                or f"pos-{item.get('position', idx)}"
            ).strip()
            if not raw_msg_id or raw_msg_id in seen_message_ids:
                raw_msg_id = f"{raw_msg_id or 'msg'}-{idx}"
            seen_message_ids.add(raw_msg_id)

            ts_float = self._extract_transcript_message_timestamp(item)
            if ts_float is None:
                ts_float = base_now + idx * 0.001
            timestamp_dt = datetime.fromtimestamp(ts_float)

            if role == "assistant":
                user_id = (
                    str(msg_meta.get("sender_id") or msg_meta.get("bot_id") or "assistant").strip()
                    or "assistant"
                )
                user_nickname = (
                    str(bot_name or msg_meta.get("sender_name") or global_config.bot.nickname or "助手").strip()
                    or "助手"
                )
                user_cardname = ""
            else:
                user_id = str(
                    item.get("sender_id")
                    or item.get("user_id")
                    or msg_meta.get("sender_id")
                    or msg_meta.get("user_id")
                    or ""
                ).strip()
                if not user_id:
                    user_id = f"{role or 'user'}_{stream_id}"
                user_nickname = str(
                    item.get("sender_name")
                    or item.get("person_name")
                    or item.get("nickname")
                    or msg_meta.get("sender_name")
                    or msg_meta.get("person_name")
                    or msg_meta.get("nickname")
                    or ""
                ).strip()
                user_cardname = str(
                    item.get("group_nick_name")
                    or msg_meta.get("group_nick_name")
                    or msg_meta.get("user_cardname")
                    or ""
                ).strip()

            group_info = (
                SimpleNamespace(group_id=group_id, group_name=group_name)
                if group_id
                else None
            )
            user_info = SimpleNamespace(
                user_id=user_id,
                user_nickname=user_nickname,
                user_cardname=user_cardname,
            )
            adapted.append(
                SimpleNamespace(
                    platform=platform,
                    session_id=stream_id,
                    message_id=raw_msg_id,
                    timestamp=timestamp_dt,
                    time=ts_float,
                    message_info=SimpleNamespace(user_info=user_info, group_info=group_info),
                    raw_message=SimpleNamespace(components=[TextComponent(text=content)]),
                )
            )
        return adapted

    @staticmethod
    def _fallback_transcript_summary(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        merged = " ".join(
            str(m.get("content", "") or "").strip()
            for m in messages
            if isinstance(m, dict) and str(m.get("content", "")).strip()
        )
        merged = merged[:500]
        return {"summary": merged or "暂无可总结内容", "entities": [], "relations": []}

    async def _import_deterministic_transcript_fallback(
        self,
        *,
        stream_id: str,
        transcript_messages: List[Dict[str, Any]],
        time_meta: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SummaryImportResult:
        payload = self._fallback_transcript_summary(transcript_messages)
        summary = str(payload.get("summary", "") or "").strip()
        if not summary:
            return SummaryImportResult(success=False, detail="总结为空")
        entities = payload.get("entities", [])
        relations = payload.get("relations", [])
        effective_time_meta = time_meta if time_meta is not None else self._derive_transcript_time_meta(transcript_messages)
        meta = dict(metadata) if isinstance(metadata, dict) else {}
        meta.setdefault("source_type", "chat_summary")
        meta.setdefault("chat_id", stream_id)
        meta = {k: v for k, v in meta.items() if v not in (None, "", [])}

        paragraph_hash = await self._execute_import(
            summary=summary,
            entities=entities if isinstance(entities, list) else [],
            relations=relations if isinstance(relations, list) else [],
            stream_id=stream_id,
            time_meta=effective_time_meta or None,
            metadata=meta,
        )
        await cancellation_safe_to_thread(self._persist_all_vector_stores)
        await cancellation_safe_to_thread(self.graph_store.save)

        external_id = str(meta.get("external_id", "") or "").strip()
        if external_id:
            await cancellation_safe_to_thread(
                self.metadata_store.upsert_external_memory_ref,
                external_id=external_id,
                paragraph_hash=paragraph_hash,
                source_type="chat_summary",
                metadata={
                    "chat_id": stream_id,
                    "person_ids": meta.get("person_ids", []),
                    "trigger_message_count": int(meta.get("trigger_message_count") or 0),
                },
            )
            await cancellation_safe_to_thread(
                self.metadata_store.record_summary_checkpoint,
                external_id=external_id,
                chat_id=stream_id,
                trigger_message_count=int(meta.get("trigger_message_count") or 0),
            )

        return SummaryImportResult(
            success=True,
            detail=f"总结导入成功: session={stream_id}",
            paragraph_hash=paragraph_hash,
            source=f"chat_summary:{stream_id}",
        )

    async def import_from_transcript(
        self,
        *,
        session_id: str,
        messages: List[Dict[str, Any]],
        source: str = "",
        context_length: Optional[int] = None,
        bot_name: str = "",
        personality_context: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SummaryImportResult:
        session_token = str(session_id or "").strip()
        if not session_token:
            return SummaryImportResult(success=False, detail="session_id 不能为空")

        external_id = str((metadata or {}).get("external_id", "") or "").strip()
        stream_lock = self._import_locks.setdefault(f"stream:{session_token}", asyncio.Lock())
        async with stream_lock:
            if external_id:
                external_lock = self._import_locks.setdefault(f"external:{external_id}", asyncio.Lock())
                async with external_lock:
                    return await self._import_from_transcript_locked(
                        session_id=session_token,
                        messages=messages,
                        source=source,
                        context_length=context_length,
                        bot_name=bot_name,
                        personality_context=personality_context,
                        metadata=metadata,
                    )
            return await self._import_from_transcript_locked(
                session_id=session_token,
                messages=messages,
                source=source,
                context_length=context_length,
                bot_name=bot_name,
                personality_context=personality_context,
                metadata=metadata,
            )

    async def _import_from_transcript_locked(
        self,
        *,
        session_id: str,
        messages: List[Dict[str, Any]],
        source: str = "",
        context_length: Optional[int] = None,
        bot_name: str = "",
        personality_context: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SummaryImportResult:
        try:
            async with fact_scope_lock(self.metadata_store, "chat", session_id):
                session_metadata = self._transcript_session_metadata(session_id)
                session_metadata["imported_at"] = time.time()
                session = self.metadata_store.upsert_transcript_session(
                    session_id=session_id,
                    source=source or f"transcript:{session_id}",
                    metadata=session_metadata,
                )
                target_session_id = str(session["session_id"])
                if messages and not self._are_persisted_transcript_rows(messages, session_id=target_session_id):
                    self.metadata_store.append_transcript_messages(
                        session_id=target_session_id,
                        messages=messages,
                    )

                merged_meta = self._transcript_session_metadata(target_session_id)
                merged_meta.update({"source_type": "chat_summary", "chat_id": target_session_id})
                if isinstance(metadata, dict):
                    merged_meta.update(metadata)
                merged_meta = {k: v for k, v in merged_meta.items() if v not in (None, "", [])}

                existing_result = await cancellation_safe_to_thread(
                    self._existing_summary_result,
                    stream_id=target_session_id,
                    metadata=merged_meta,
                )
                if existing_result is not None:
                    return existing_result
                external_id = str(merged_meta.get("external_id") or "").strip()
                checkpoint = (
                    await cancellation_safe_to_thread(self.metadata_store.get_summary_checkpoint, external_id)
                    if external_id
                    else None
                )
                if checkpoint is not None and checkpoint.get("phase") == "pending":
                    return await self._finish_fact_summary(json.loads(checkpoint["result_json"]))

                limit = (
                    int(context_length)
                    if context_length is not None
                    else int(self._cfg("summarization.context_length", 50) or 50)
                )
                transcript_messages = self.metadata_store.get_transcript_messages(
                    target_session_id,
                    limit=max(1, limit),
                )
                time_meta = self._derive_transcript_time_meta(transcript_messages)

                if not self._has_transcript_llm_source() or not transcript_messages:
                    return await self._import_deterministic_transcript_fallback(
                        stream_id=target_session_id,
                        transcript_messages=transcript_messages,
                        time_meta=time_meta,
                        metadata=merged_meta,
                    )

                self_check_ok, self_check_msg = await self._ensure_runtime_self_check()
                if not self_check_ok:
                    return SummaryImportResult(False, f"导入前自检失败: {self_check_msg}")

                adapted_messages = self._adapt_transcript_messages(
                    transcript_messages,
                    stream_id=target_session_id,
                    session_metadata=merged_meta,
                    bot_name=bot_name,
                )
                if not adapted_messages:
                    return await self._import_deterministic_transcript_fallback(
                        stream_id=target_session_id,
                        transcript_messages=transcript_messages,
                        time_meta=time_meta,
                        metadata=merged_meta,
                    )

                include_personality = bool(self._cfg("summarization.include_personality", True))
                with self._llm_bridge_context():
                    return await self._summarize_adapted_messages(
                        stream_id=target_session_id,
                        messages=adapted_messages,
                        include_personality=include_personality,
                        metadata=merged_meta,
                        time_meta_override=time_meta,
                        bot_name_override=bot_name,
                        personality_context_override=personality_context,
                        fallback_transcript_rows=transcript_messages,
                    )
        except Exception as exc:
            logger.error(f"Summary transcript import failed: {exc}\n{traceback.format_exc()}")
            return SummaryImportResult(success=False, detail=str(exc))


