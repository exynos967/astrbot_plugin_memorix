"""
人物画像服务

person_id -> 别名解析 -> 图谱证据 + 向量证据 -> 画像快照
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional, Tuple

from ...amemorix.common.logging import get_logger

from ..embedding.api_adapter import EmbeddingAPIAdapter
from ..retrieval import (
    DualPathRetriever,
    DualPathRetrieverConfig,
    FusionConfig,
    RetrievalStrategy,
    SparseBM25Config,
    VectorPoolsConfig,
)
from ..storage import GraphStore, MetadataStore, VectorStore

logger = get_logger("A_Memorix.PersonProfileService")


class PersonProfileService:
    def __init__(
        self,
        metadata_store: MetadataStore,
        graph_store: Optional[GraphStore] = None,
        vector_store: Optional[VectorStore] = None,
        paragraph_vector_store: Optional[VectorStore] = None,
        graph_vector_store: Optional[VectorStore] = None,
        embedding_manager: Optional[EmbeddingAPIAdapter] = None,
        sparse_index: Any = None,
        plugin_config: Optional[dict] = None,
        retriever: Optional[DualPathRetriever] = None,
    ):
        self.metadata_store = metadata_store
        self.graph_store = graph_store
        self.vector_store = vector_store
        # 双池检索：未提供时回退到 vector_store，保证降级安全
        self.paragraph_vector_store = paragraph_vector_store
        self.graph_vector_store = graph_vector_store
        self.embedding_manager = embedding_manager
        self.sparse_index = sparse_index
        self.plugin_config = plugin_config or {}
        self.retriever = retriever or self._build_retriever()

    def _cfg(self, key: str, default: Any = None) -> Any:
        current: Any = self.plugin_config if isinstance(self.plugin_config, dict) else {}
        for part in key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current

    def _build_retriever(self) -> Optional[DualPathRetriever]:
        if not all(
            [
                self.vector_store is not None,
                self.graph_store is not None,
                self.metadata_store is not None,
                self.embedding_manager is not None,
            ]
        ):
            return None

        try:
            sparse_cfg_raw = self._cfg("retrieval.sparse", {}) or {}
            fusion_cfg_raw = self._cfg("retrieval.fusion", {}) or {}
            vector_pools_cfg_raw = self._cfg("retrieval.vector_pools", {}) or {}
            if not isinstance(sparse_cfg_raw, dict):
                sparse_cfg_raw = {}
            if not isinstance(fusion_cfg_raw, dict):
                fusion_cfg_raw = {}
            if not isinstance(vector_pools_cfg_raw, dict):
                vector_pools_cfg_raw = {}

            # 双池就绪判定：runtime 配置显式指定优先，否则依赖两个独立向量池实例
            runtime_cfg = self._cfg("runtime", {}) or {}
            if isinstance(runtime_cfg, dict) and "vector_pools_ready" in runtime_cfg:
                vector_pools_ready = bool(runtime_cfg.get("vector_pools_ready", False))
            else:
                vector_pools_ready = (
                    self.paragraph_vector_store is not None
                    and self.graph_vector_store is not None
                )
            # mode=dual 但双池未就绪时降级 single，避免运行期崩溃
            configured_mode = str(vector_pools_cfg_raw.get("mode", "dual") or "dual").strip().lower()
            if configured_mode == "dual" and not vector_pools_ready:
                vector_pools_cfg_raw = dict(vector_pools_cfg_raw)
                vector_pools_cfg_raw["mode"] = "single"

            vector_pools_cfg = VectorPoolsConfig(**vector_pools_cfg_raw)
            config = DualPathRetrieverConfig(
                top_k_paragraphs=int(self._cfg("retrieval.top_k_paragraphs", 20)),
                top_k_relations=int(self._cfg("retrieval.top_k_relations", 10)),
                top_k_final=int(self._cfg("retrieval.top_k_final", 10)),
                alpha=float(self._cfg("retrieval.alpha", 0.5)),
                enable_ppr=bool(self._cfg("retrieval.enable_ppr", True)),
                ppr_alpha=float(self._cfg("retrieval.ppr_alpha", 0.85)),
                ppr_concurrency_limit=int(self._cfg("retrieval.ppr_concurrency_limit", 4)),
                enable_parallel=bool(self._cfg("retrieval.enable_parallel", True)),
                retrieval_strategy=RetrievalStrategy.DUAL_PATH,
                debug=bool(self._cfg("advanced.debug", False)),
                sparse=SparseBM25Config(**sparse_cfg_raw),
                fusion=FusionConfig(**fusion_cfg_raw),
                vector_pools=vector_pools_cfg,
            )
            return DualPathRetriever(
                vector_store=self.vector_store,
                paragraph_vector_store=self.paragraph_vector_store or self.vector_store,
                graph_vector_store=self.graph_vector_store or self.vector_store,
                graph_store=self.graph_store,
                metadata_store=self.metadata_store,
                embedding_manager=self.embedding_manager,
                sparse_index=self.sparse_index,
                config=config,
            )
        except Exception as exc:
            logger.warning("Build profile retriever failed: %s", exc)
            return None

    def resolve_person_id(self, identifier: str) -> str:
        value = str(identifier or "").strip()
        if not value:
            return ""
        if len(value) == 32 and all(ch in "0123456789abcdefABCDEF" for ch in value):
            return value.lower()
        manual_matches = self.metadata_store.find_person_ids_by_manual_alias(value)
        if len(manual_matches) > 1:
            raise ValueError("人物别名不唯一，请使用 person_id")
        if manual_matches:
            return manual_matches[0]
        try:
            return self.metadata_store.resolve_person_registry(value) or ""
        except Exception:
            return ""

    def _parse_group_nicks(self, raw_value: Any) -> List[str]:
        if not raw_value:
            return []
        if isinstance(raw_value, list):
            items = raw_value
        else:
            try:
                items = json.loads(raw_value)
            except Exception:
                return []
        names: List[str] = []
        for item in items:
            if isinstance(item, dict):
                val = str(item.get("group_nick_name", "")).strip()
                if val:
                    names.append(val)
            elif isinstance(item, str):
                val = item.strip()
                if val:
                    names.append(val)
        return names

    def _parse_memory_traits(self, raw_value: Any) -> List[str]:
        if not raw_value:
            return []
        try:
            values = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
        except Exception:
            return []
        if not isinstance(values, list):
            return []
        traits: List[str] = []
        for item in values:
            text = str(item).strip()
            if not text:
                continue
            if ":" in text:
                parts = text.split(":")
                if len(parts) >= 3:
                    content = ":".join(parts[1:-1]).strip()
                    if content:
                        traits.append(content)
                        continue
            traits.append(text)
        return traits[:10]

    def get_person_aliases(self, person_id: str, *, include_override: bool = True) -> Tuple[List[str], str, List[str]]:
        aliases: List[str] = []
        primary_name = ""
        memory_traits: List[str] = []
        if not person_id:
            return aliases, primary_name, memory_traits

        try:
            record = self.metadata_store.get_person_registry(person_id) or {}

            person_name = str(record.get("person_name", "") or "").strip()
            nickname = str(record.get("nickname", "") or "").strip()
            group_nicks = self._parse_group_nicks(record.get("group_nick_name"))
            memory_traits = self._parse_memory_traits(record.get("memory_points"))

            primary_name = (
                person_name
                or nickname
                or str(record.get("user_id", "") or "").strip()
                or person_id
            )

            candidates = [person_name, nickname] + group_nicks
            seen = set()
            for item in candidates:
                norm = str(item or "").strip()
                if not norm or norm in seen:
                    continue
                seen.add(norm)
                aliases.append(norm)
        except Exception as exc:
            logger.warning("Parse person aliases failed: %s", exc)
        if include_override:
            override = self.metadata_store.get_person_profile_alias_override(person_id)
            if override is not None:
                aliases = override["aliases"]
        return aliases, primary_name, memory_traits

    def get_person_alias_details(self, person_id: str) -> Dict[str, Any]:
        derived, primary_name, _ = self.get_person_aliases(person_id, include_override=False)
        override = self.metadata_store.get_person_profile_alias_override(person_id)
        return {
            "person_id": person_id,
            "person_name": primary_name,
            "derived_aliases": derived,
            "manual_aliases": override["aliases"] if override else None,
            "effective_aliases": override["aliases"] if override else derived,
        }

    def _with_fact_claims(self, person_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        # 每次读取当前账本，避免事实到期后仍沿用 TTL 内的缓存投影。
        claims = self.metadata_store.list_person_profile_fact_claims(person_id)
        result = dict(payload)
        result["fact_claims"] = claims
        result["alias_details"] = self.get_person_alias_details(person_id)
        lines = [str(result.get("profile_text", "") or "")]
        for uncertain, heading in ((False, "当前确认事实"), (True, "待确认事实线索")):
            values = [str(claim["value_text"]) for claim in claims if (claim["stability"] == "uncertain") == uncertain]
            if uncertain:
                if not self._cfg("person_fact_writeback.update_registry_memory_points", True):
                    continue
                values = values[:max(1, int(self._cfg("person_fact_writeback.max_registry_facts", 30)))]
            if values:
                lines.append(heading + ":\n" + "\n".join(f"- {value}" for value in values))
        result["profile_text"] = "\n".join(lines)
        return result

    def _collect_relation_evidence(self, aliases: List[str], limit: int = 30) -> List[Dict[str, Any]]:
        relation_by_hash: Dict[str, Dict[str, Any]] = {}
        for alias in aliases:
            for rel in self.metadata_store.get_relations(subject=alias):
                h = str(rel.get("hash", ""))
                if h:
                    relation_by_hash[h] = rel
            for rel in self.metadata_store.get_relations(object=alias):
                h = str(rel.get("hash", ""))
                if h:
                    relation_by_hash[h] = rel

        relations = list(relation_by_hash.values())
        relations.sort(key=lambda item: float(item.get("confidence", 0.0)), reverse=True)
        relations = relations[: max(1, int(limit))]

        edges: List[Dict[str, Any]] = []
        for rel in relations:
            edges.append(
                {
                    "hash": str(rel.get("hash", "")),
                    "subject": str(rel.get("subject", "")),
                    "predicate": str(rel.get("predicate", "")),
                    "object": str(rel.get("object", "")),
                    "confidence": float(rel.get("confidence", 1.0) or 1.0),
                }
            )
        return edges

    @staticmethod
    def _normalize_aliases(aliases: List[str]) -> List[str]:
        normalized: List[str] = []
        seen = set()
        for alias in aliases:
            text = str(alias or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            normalized.append(text)
        normalized.sort(key=len, reverse=True)
        return normalized

    @staticmethod
    def _text_matches_alias(value: str, aliases: List[str]) -> bool:
        text = str(value or "").strip()
        if not text:
            return False
        lowered = text.lower()
        for alias in aliases:
            target = alias.lower()
            if len(target) <= 1:
                if lowered == target:
                    return True
                continue
            if target in lowered:
                return True
        return False

    def _is_person_related_vector_item(self, item: Dict[str, Any], aliases: List[str]) -> bool:
        if not aliases:
            return False

        paragraph = self.metadata_store.get_paragraph(str(item.get("hash", "")))
        if paragraph is not None:
            if paragraph.get("is_deleted"):
                return False
            paragraph_metadata = paragraph.get("metadata") or {}
            # 人物事实由账本投影，避免撤回后仍从向量证据重新注入。
            if isinstance(paragraph_metadata, dict) and paragraph_metadata.get("fact_claim_ids"):
                return False

        if self._text_matches_alias(str(item.get("content", "") or ""), aliases):
            return True

        metadata = item.get("metadata", {}) or {}
        if not isinstance(metadata, dict):
            return False

        for key in ("subject", "object", "predicate", "entity", "pivot_entity"):
            if self._text_matches_alias(str(metadata.get(key, "") or ""), aliases):
                return True

        entities = metadata.get("entities")
        if isinstance(entities, list):
            for entity in entities:
                if self._text_matches_alias(str(entity or ""), aliases):
                    return True
        return False

    async def _collect_vector_evidence(self, aliases: List[str], top_k: int = 12) -> List[Dict[str, Any]]:
        alias_queries = self._normalize_aliases([a for a in aliases if a])
        if not alias_queries:
            return []

        if self.retriever is None:
            fallback: List[Dict[str, Any]] = []
            seen_hash = set()
            for alias in alias_queries:
                for para in self.metadata_store.search_paragraphs_by_content(alias)[: max(2, top_k // 2)]:
                    h = str(para.get("hash", ""))
                    if not h or h in seen_hash:
                        continue
                    seen_hash.add(h)
                    fallback.append(
                        {
                            "hash": h,
                            "type": "paragraph",
                            "score": 0.0,
                            "content": str(para.get("content", ""))[:180],
                            "metadata": {},
                        }
                    )
            return [item for item in fallback if self._is_person_related_vector_item(item, alias_queries)][:top_k]

        per_alias_top_k = max(2, int(top_k / max(1, len(alias_queries))))
        seen_hash = set()
        evidence: List[Dict[str, Any]] = []
        timeout_seconds = float(self._cfg("person_profile.vector_evidence_timeout_seconds", 3.0) or 3.0)
        for alias in alias_queries:
            try:
                results = await asyncio.wait_for(
                    self.retriever.retrieve(alias, top_k=per_alias_top_k),
                    timeout=max(0.2, timeout_seconds),
                )
            except asyncio.TimeoutError:
                logger.warning("Vector evidence retrieve timed out: alias=%s", alias)
                continue
            except Exception as exc:
                logger.warning("Vector evidence retrieve failed: %s", exc)
                continue
            for item in results:
                h = str(getattr(item, "hash_value", "") or "")
                if not h or h in seen_hash:
                    continue
                seen_hash.add(h)
                evidence.append(
                    {
                        "hash": h,
                        "type": str(getattr(item, "result_type", "")),
                        "score": float(getattr(item, "score", 0.0) or 0.0),
                        "content": str(getattr(item, "content", "") or "")[:220],
                        "metadata": dict(getattr(item, "metadata", {}) or {}),
                    }
                )
        filtered = [item for item in evidence if self._is_person_related_vector_item(item, alias_queries)]
        filtered.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        return filtered[:top_k]

    def _build_profile_text(
        self,
        person_id: str,
        primary_name: str,
        aliases: List[str],
        relation_edges: List[Dict[str, Any]],
        vector_evidence: List[Dict[str, Any]],
        memory_traits: List[str],
    ) -> str:
        lines: List[str] = []
        lines.append(f"人物ID: {person_id}")
        if primary_name:
            lines.append(f"主称呼: {primary_name}")
        if aliases:
            lines.append(f"别名: {', '.join(aliases[:8])}")
        # registry.memory_points 没有证据生命周期，保留原数据供管理查看，不参与自动注入。

        if relation_edges:
            lines.append("关系证据:")
            for rel in relation_edges[:6]:
                s = rel.get("subject", "")
                p = rel.get("predicate", "")
                o = rel.get("object", "")
                conf = float(rel.get("confidence", 0.0))
                lines.append(f"- {s} {p} {o} (conf={conf:.2f})")

        if vector_evidence:
            lines.append("向量证据摘要:")
            for item in vector_evidence[:4]:
                content = str(item.get("content", "")).strip()
                if content:
                    lines.append(f"- {content}")

        if len(lines) <= 2:
            lines.append("暂无足够证据形成稳定画像。")
        return "\n".join(lines)

    @staticmethod
    def _is_snapshot_stale(snapshot: Optional[Dict[str, Any]], ttl_seconds: float) -> bool:
        if not snapshot:
            return True
        now = time.time()
        expires_at = snapshot.get("expires_at")
        if expires_at is not None:
            try:
                return now >= float(expires_at)
            except Exception:
                return True
        updated_at = float(snapshot.get("updated_at") or 0.0)
        return (now - updated_at) >= ttl_seconds

    def _apply_manual_override(self, person_id: str, profile_payload: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(profile_payload or {})
        auto_text = str(payload.get("profile_text", "") or "")
        payload["auto_profile_text"] = auto_text
        payload["has_manual_override"] = False
        payload["manual_override_text"] = ""
        payload["override_updated_at"] = None
        payload["override_updated_by"] = ""
        payload["profile_source"] = "auto_snapshot"

        if not person_id:
            return payload
        override = self.metadata_store.get_person_profile_override(person_id)
        if not override:
            return payload
        manual_text = str(override.get("override_text", "") or "").strip()
        if not manual_text:
            return payload

        payload["has_manual_override"] = True
        payload["manual_override_text"] = manual_text
        payload["override_updated_at"] = override.get("updated_at")
        payload["override_updated_by"] = str(override.get("updated_by", "") or "")
        payload["profile_text"] = manual_text
        payload["profile_source"] = "manual_override"
        return payload

    async def query_person_profile(
        self,
        person_id: str = "",
        person_keyword: str = "",
        top_k: int = 12,
        ttl_seconds: float = 6 * 3600,
        force_refresh: bool = False,
        source_note: str = "",
    ) -> Dict[str, Any]:
        pid = str(person_id or "").strip()
        if not pid and person_keyword:
            try:
                pid = self.resolve_person_id(person_keyword)
            except ValueError as error:
                return {"success": False, "error": str(error)}
        if not pid and person_keyword:
            # basic 场景里常见“人物”只作为图谱实体存在，尚未进入 person_registry。
            # 允许直接使用关键词生成轻量画像，避免候选表为空时功能不可用。
            pid = str(person_keyword or "").strip()
        if not pid:
            return {"success": False, "error": "person_id 无效，且未能通过别名解析"}

        latest = self.metadata_store.get_latest_person_profile_snapshot(pid)
        if not force_refresh and not self._is_snapshot_stale(latest, ttl_seconds):
            aliases, primary_name, _ = self.get_person_aliases(pid)
            payload = {
                "success": True,
                "person_id": pid,
                "person_name": primary_name,
                "from_cache": True,
                **(latest or {}),
            }
            if aliases and not payload.get("aliases"):
                payload["aliases"] = aliases
            return self._apply_manual_override(pid, self._with_fact_claims(pid, payload))

        aliases, primary_name, memory_traits = self.get_person_aliases(pid)
        if not aliases and person_keyword:
            aliases = [person_keyword.strip()]
            primary_name = person_keyword.strip()
        relation_edges = self._collect_relation_evidence(aliases, limit=max(10, top_k * 2))
        vector_evidence = await self._collect_vector_evidence(aliases, top_k=max(4, top_k))

        evidence_ids = [
            str(item.get("hash", ""))
            for item in (relation_edges + vector_evidence)
            if str(item.get("hash", "")).strip()
        ]
        dedup_ids: List[str] = []
        seen = set()
        for item in evidence_ids:
            if item in seen:
                continue
            seen.add(item)
            dedup_ids.append(item)

        profile_text = self._build_profile_text(
            person_id=pid,
            primary_name=primary_name,
            aliases=aliases,
            relation_edges=relation_edges,
            vector_evidence=vector_evidence,
            memory_traits=memory_traits,
        )

        expires_at = time.time() + float(ttl_seconds) if ttl_seconds > 0 else None
        snapshot = self.metadata_store.upsert_person_profile_snapshot(
            person_id=pid,
            profile_text=profile_text,
            aliases=aliases,
            relation_edges=relation_edges,
            vector_evidence=vector_evidence,
            evidence_ids=dedup_ids,
            expires_at=expires_at,
            source_note=source_note,
        )
        payload = {
            "success": True,
            "person_id": pid,
            "person_name": primary_name,
            "from_cache": False,
            **snapshot,
        }
        return self._apply_manual_override(pid, self._with_fact_claims(pid, payload))

    @staticmethod
    def format_persona_profile_block(profile: Dict[str, Any]) -> str:
        if not profile or not profile.get("success"):
            return ""
        text = str(profile.get("profile_text", "") or "").strip()
        if not text:
            return ""
        return (
            "【人物画像-内部参考】\n"
            f"{text}\n"
            "仅供内部推理，不要向用户逐字复述。"
        )
