"""Configuration loading for Memorix runtime."""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import tomllib

from .common.logging import get_logger

logger = get_logger("A_Memorix.Settings")


DEFAULT_CONFIG: Dict[str, Any] = {
    "cors": {"allow_origins": []},
    "storage": {"data_dir": "./data"},
    "embedding": {
        "dimension": 1024,
        "quantization_type": "int8",
        "batch_size": 32,
        "max_concurrent": 5,
        "retry": {"max_attempts": 5, "max_wait_seconds": 30, "min_wait_seconds": 2},
        "openai": {
            "base_url": "",
            "api_key": "",
            "model": "",
            "timeout_seconds": 30,
            "max_retries": 3,
        },
    },
    "retrieval": {
        "top_k_relations": 10,
        "top_k_paragraphs": 20,
        "top_k_final": 10,
        "auto_inject": {
            "enabled": True,
            "top_k": 5,
            "min_query_chars": 4,
        },
        "alpha": 0.5,
        "enable_ppr": True,
        "ppr_alpha": 0.85,
        "ppr_timeout_seconds": 1.5,
        "ppr_concurrency_limit": 4,
        "enable_parallel": True,
        "relation_semantic_fallback": True,
        "relation_fallback_min_score": 0.3,
        "relation_vectorization": {
            "enabled": False,
            "backfill_enabled": False,
            "write_on_import": True,
            "backfill_batch_size": 50,
            "max_retry": 3,
        },
        "paragraph_vectorization": {
            "backfill_enabled": True,
            "backfill_interval_seconds": 60,
            "backfill_batch_size": 50,
            "backfill_scan_multiplier": 20,
        },
        "temporal": {
            "enabled": True,
            "allow_created_fallback": True,
            "candidate_multiplier": 8,
            "default_top_k": 10,
            "max_scan": 1000,
        },
        "aggregate": {
            "rrf_k": 60,
            "weights": {"search": 1.0, "time": 1.0, "episode": 1.0},
        },
        "search": {
            "smart_fallback": {"enabled": True, "threshold": 0.6},
            "safe_content_dedup": {"enabled": True},
            "relation_intent": {
                "enabled": True,
                "alpha_override": 0.35,
                "relation_candidate_multiplier": 4,
                "preserve_top_relations": 3,
                "force_relation_sparse": True,
                "pair_predicate_rerank_enabled": True,
                "pair_predicate_limit": 3,
            },
            "graph_recall": {
                "enabled": True,
                "candidate_k": 24,
                "max_hop": 1,
                "allow_two_hop_pair": True,
                "max_paths": 4,
            },
            "posterior_graph": {
                "enabled": True,
                "drop_ratio": 0.15,
                "min_core_results": 2,
                "max_graph_slots": 2,
                "gate_scan_top_k": 5,
            },
        },
        "time": {"skip_threshold_when_query_empty": True},
        "sparse": {
            "enabled": True,
            "backend": "fts5",
            "lazy_load": True,
            "mode": "auto",
            "tokenizer_mode": "jieba",
            "jieba_user_dict": "",
            "char_ngram_n": 2,
            "candidate_k": 80,
            "max_doc_len": 2000,
            "enable_ngram_fallback_index": True,
            "enable_like_fallback": False,
            "enable_relation_sparse_fallback": True,
            "relation_candidate_k": 60,
            "relation_max_doc_len": 512,
            "unload_on_disable": True,
            "shrink_memory_on_unload": True,
        },
        "fusion": {
            "method": "weighted_rrf",
            "rrf_k": 60,
            "vector_weight": 0.7,
            "bm25_weight": 0.3,
            "normalize_score": True,
            "normalize_method": "minmax",
        },
        # 双池检索配置：默认 dual，与上游 A_memorix 对齐；manifest 缺失时运行时降级 single。
        # mode=dual 时需 ready manifest 就绪，否则降级为 single。
        "vector_pools": {
            "mode": "dual",
            "paragraph_top_k": 20,
            "graph_top_k": 40,
            "graph_expand_paragraph_k": 80,
            "relation_expand_per_hit": 5,
            "entity_expand_per_hit": 8,
            "relation_evidence_weight": 1.0,
            "entity_evidence_weight": 0.55,
            "semantic_weight": 0.65,
            "sparse_weight": 0.20,
            "graph_weight": 0.15,
            "score_calibration_method": "none",
            "score_calibration_rrf_k": 60,
            "relation_intent": {
                "graph_top_k": 80,
                "semantic_weight": 0.45,
                "sparse_weight": 0.15,
                "graph_weight": 0.40,
                "return_relation_items": False,
            },
        },
    },
    "runtime": {
        # 双池就绪标志：bootstrap 在构造时根据 manifest 探测结果回填。
        "vector_pools_ready": False,
    },
    "threshold": {
        "min_threshold": 0.3,
        "max_threshold": 0.95,
        "percentile": 75.0,
        "min_results": 3,
        "enable_auto_adjust": True,
    },
    "advanced": {
        "enable_auto_save": True,
        "auto_save_interval_minutes": 5,
        "debug": False,
    },
    "memory": {
        "enabled": True,
        "half_life_hours": 24.0,
        "base_decay_interval_hours": 1.0,
        "prune_threshold": 0.1,
        "freeze_duration_hours": 24.0,
        "enable_auto_reinforce": True,
        "reinforce_buffer_max_size": 1000,
        "reinforce_cooldown_hours": 1.0,
        "max_weight": 10.0,
        "revive_boost_weight": 0.5,
        "auto_protect_ttl_hours": 24.0,
    },
    "summarization": {
        "enabled": True,
        "source_mode": "transcript",
        "context_length": 50,
        "include_personality": True,
        "default_knowledge_type": "narrative",
    },
    "person_profile": {
        "enabled": True,
        "profile_ttl_minutes": 360.0,
        "refresh_interval_minutes": 30,
        "active_window_hours": 72.0,
        "max_refresh_per_cycle": 50,
        "top_k_evidence": 12,
        "injection_max_profiles": 3,
        "registry": {
            "page_size_default": 20,
            "page_size_max": 100,
            "match_strategy": "contains",
        },
    },
    "episode": {
        "enabled": True,
        "generation_enabled": True,
        "generation_interval_seconds": 30,
        "generation_batch_size": 20,
        "max_retry": 3,
        "window_seconds": 3600,
        "min_group_size": 2,
        "max_group_size": 24,
        "segmentation_model": "auto",
        "segmentation_temperature": 0.2,
        "segmentation_max_tokens": 1500,
    },
    "filter": {"enabled": True, "mode": "blacklist", "chats": []},
    "ingest": {
        "record_all_events": True,
        "skip_empty_text": True,
        "skip_command_messages": True,
        "command_prefixes": ["/"],
        "memory_write_mode": "transcript_only",
        "direct_write_assistant": True,
        "content_router": {
            "enabled": True,
            "drop_ephemeral_transcript": False,
            "auto_direct_min_chars": 12,
        },
        "skip_placeholder_only": True,
        "max_message_chars": 2000,
        "max_forward_fetch": 8,
        "image_caption": {
            "enabled": False,
            "provider_id": "",
            "max_count": 1,
            "prompt": "请简洁描述这张图片中对长期记忆有价值的内容。",
        },
    },
    "person_fact_writeback": {
        "enabled": False,
        "queue_maxsize": 256,
        "min_user_text_chars": 4,
        "max_facts_per_turn": 5,
        "max_registry_facts": 30,
        "max_evidence_chars": 800,
        "update_registry_memory_points": True,
        "chat_provider_id": "",
        "temperature": 0.1,
        "max_tokens": 800,
    },
    "routing": {
        "search_owner": "action",
        "tool_search_mode": "forward",
        "enable_request_dedup": True,
        "request_dedup_ttl_seconds": 2,
    },
    "tasks": {
        "import_workers": 1,
        "summary_workers": 1,
        "queue_maxsize": 1024,
        "summary_poll_interval_seconds": 1,
    },
    "integration": {
        "feedback_correction_enabled": False,
        "feedback_correction_window_hours": 12.0,
        "feedback_correction_check_interval_minutes": 30,
        "feedback_correction_batch_size": 20,
        "feedback_correction_auto_apply_threshold": 0.85,
        "feedback_correction_max_feedback_messages": 30,
        "feedback_correction_prefilter_enabled": True,
        "feedback_correction_paragraph_mark_enabled": True,
        "feedback_correction_paragraph_hard_filter_enabled": True,
        "feedback_correction_profile_refresh_enabled": True,
        "feedback_correction_profile_force_refresh_on_read": True,
        "feedback_correction_episode_rebuild_enabled": True,
        "feedback_correction_episode_query_block_enabled": True,
        "feedback_correction_reconcile_interval_minutes": 5,
        "feedback_correction_reconcile_batch_size": 20,
        # 记忆模糊修正：默认启用，与上游 A_memorix 对齐；执行仍需显式确认
        "fuzzy_modify": {
            "enabled": True,
            "candidate_limit": 20,
            "confirm_threshold": 0.85,
            "auto_execute_enabled": False,
            "max_targets": 5,
            "allow_global_scope": False,
        },
    },
}


def _deep_merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _parse_env_value(raw: str) -> Any:
    text = raw.strip()
    lowered = text.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none"}:
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    if (text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]")):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return text


def _set_nested(config: Dict[str, Any], path: list[str], value: Any) -> None:
    cur: Dict[str, Any] = config
    for key in path[:-1]:
        existing = cur.get(key)
        if not isinstance(existing, dict):
            existing = {}
            cur[key] = existing
        cur = existing
    cur[path[-1]] = value


def _apply_env_overrides(config: Dict[str, Any], prefix: str = "AMEMORIX__") -> Dict[str, Any]:
    out = copy.deepcopy(config)
    for env_key, env_value in os.environ.items():
        if not env_key.startswith(prefix):
            continue
        tail = env_key[len(prefix) :].strip()
        if not tail:
            continue
        parts = [p.lower() for p in tail.split("__") if p.strip()]
        if not parts:
            continue
        _set_nested(out, parts, _parse_env_value(env_value))
    return out


def _overlay_non_empty(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (overlay or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _overlay_non_empty(out[key], value)
            continue
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        out[key] = value
    return out


def resolve_openapi_endpoint_config(config: Dict[str, Any], *, section: str = "embedding") -> Dict[str, Any]:
    """
    Resolve OpenAI-compatible endpoint config.

    Compatibility rules:
    - Preferred: `[embedding.openapi]`
    - Legacy compatible: `[embedding.openai]`
    """
    root = config.get(section, {}) if isinstance(config, dict) else {}
    if not isinstance(root, dict):
        root = {}

    legacy_cfg = root.get("openai", {})
    if not isinstance(legacy_cfg, dict):
        legacy_cfg = {}

    openapi_cfg = root.get("openapi", {})
    if not isinstance(openapi_cfg, dict):
        openapi_cfg = {}

    # Start from legacy config, then apply non-empty openapi overrides.
    merged = _overlay_non_empty(legacy_cfg, openapi_cfg)

    if "timeout_seconds" not in merged:
        merged["timeout_seconds"] = 30
    if "max_retries" not in merged:
        merged["max_retries"] = 3
    return merged


def mask_sensitive(config: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(config)

    def _mask(value: Any) -> str:
        text = str(value or "")
        if len(text) <= 4:
            return "*" * len(text)
        return f"{text[:2]}***{text[-2:]}"

    emb = out.get("embedding", {})
    if isinstance(emb, dict):
        for key in ("openai", "openapi"):
            endpoint_cfg = emb.get(key, {})
            if isinstance(endpoint_cfg, dict) and "api_key" in endpoint_cfg:
                endpoint_cfg["api_key"] = _mask(endpoint_cfg["api_key"])
    return out


@dataclass(slots=True)
class AppSettings:
    """Resolved runtime settings."""

    config: Dict[str, Any]
    config_path: Optional[Path] = None

    @classmethod
    def load(cls, path: Optional[str] = None) -> "AppSettings":
        base = copy.deepcopy(DEFAULT_CONFIG)
        resolved_path: Optional[Path] = None

        if path:
            resolved_path = Path(path).expanduser().resolve()
            if not resolved_path.exists():
                raise FileNotFoundError(f"Config file not found: {resolved_path}")
            with resolved_path.open("rb") as f:
                parsed = tomllib.load(f)
            base = _deep_merge(base, parsed)
        else:
            default_path = Path.cwd() / "config.toml"
            if default_path.exists():
                resolved_path = default_path
                with default_path.open("rb") as f:
                    parsed = tomllib.load(f)
                base = _deep_merge(base, parsed)

        resolved = _apply_env_overrides(base)
        logger.info(
            "Settings loaded: config_path=%s",
            str(resolved_path) if resolved_path else "<defaults/env>",
        )
        return cls(config=resolved, config_path=resolved_path)

    def get(self, key: str, default: Any = None) -> Any:
        current: Any = self.config
        for part in key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current

    def get_openapi_endpoint_config(self) -> Dict[str, Any]:
        return resolve_openapi_endpoint_config(self.config)


    @property
    def workers(self) -> int:
        return 1

    @property
    def data_dir(self) -> Path:
        raw = str(self.get("storage.data_dir", "./data"))
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        return path
