"""Bootstrap Memorix runtime components."""

from __future__ import annotations

import asyncio
import pickle
from pathlib import Path
from typing import Any, Dict

from ..core.embedding.api_adapter import create_embedding_api_adapter
from ..core.retrieval.dual_path import (
    DualPathRetriever,
    DualPathRetrieverConfig,
    FusionConfig,
    RelationIntentConfig,
)
from ..core.retrieval.graph_relation_recall import GraphRelationRecallConfig
from ..core.retrieval.posterior_graph import PosteriorGraphConfig
from ..core.retrieval.sparse_bm25 import SparseBM25Config, SparseBM25Index
from ..core.retrieval.threshold import DynamicThresholdFilter, ThresholdConfig, ThresholdMethod
from ..core.storage import GraphStore, MetadataStore, QuantizationType, SparseMatrixFormat, VectorStore
from ..core.utils.episode_retrieval_service import EpisodeRetrievalService
from ..core.utils.episode_segmentation_service import EpisodeSegmentationService
from ..core.utils.episode_service import EpisodeService
from ..core.utils.paragraph_vector_service import ParagraphVectorWriteService
from ..core.utils.person_profile_service import PersonProfileService
from ..core.utils.relation_write_service import RelationWriteService

from .common.logging import get_logger
from .context import AppContext
from .llm_client import LLMClient
from .settings import AppSettings, resolve_openapi_endpoint_config

logger = get_logger("A_Memorix.Bootstrap")


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _resolve_vector_dimension(settings: AppSettings, vectors_dir: Path) -> int:
    metadata_path = vectors_dir / "vectors_metadata.pkl"
    if metadata_path.exists():
        try:
            with metadata_path.open("rb") as f:
                data = pickle.load(f)
            dim = _safe_int(data.get("dimension"), 0)
            if dim > 0:
                return dim
        except Exception as exc:
            logger.warning("Failed to read existing vector metadata dimension: %s", exc)
    return _safe_int(settings.get("embedding.dimension", 1024), 1024)


def _probe_embedding_dimension(adapter: Any, fallback_dim: int) -> int:
    """
    Probe real embedding dimension from remote endpoint.

    Only used for fresh stores (no existing vector metadata), so we can
    auto-align vector store dimension with provider output.
    """
    try:
        detected = int(asyncio.run(adapter._detect_dimension()))  # noqa: SLF001
        if detected > 0:
            return detected
    except Exception as exc:
        logger.warning("Embedding dimension probe failed, fallback to configured dimension: %s", exc)
    return int(fallback_dim)


def build_context(settings: AppSettings) -> AppContext:
    data_dir = settings.data_dir
    vectors_dir = data_dir / "vectors"
    graph_dir = data_dir / "graph"
    metadata_dir = data_dir / "metadata"
    data_dir.mkdir(parents=True, exist_ok=True)
    vectors_dir.mkdir(parents=True, exist_ok=True)
    graph_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)

    endpoint_cfg = resolve_openapi_endpoint_config(settings.config, section="embedding")
    retry_cfg = settings.get("embedding.retry", {}) or {}
    configured_dim = _resolve_vector_dimension(settings, vectors_dir)
    adapter = create_embedding_api_adapter(
        batch_size=_safe_int(settings.get("embedding.batch_size", 32), 32),
        max_concurrent=_safe_int(settings.get("embedding.max_concurrent", 5), 5),
        default_dimension=configured_dim,
        model_name=str(settings.get("embedding.model_name", "auto")),
        retry_config=retry_cfg,
        base_url=str(endpoint_cfg.get("base_url", "")),
        api_key=str(endpoint_cfg.get("api_key", "")),
        openai_model=str(endpoint_cfg.get("model", "")),
        timeout_seconds=_safe_float(endpoint_cfg.get("timeout_seconds", 30), 30.0),
        max_retries=_safe_int(endpoint_cfg.get("max_retries", 3), 3),
    )

    metadata_path = vectors_dir / "vectors_metadata.pkl"
    vector_dim = configured_dim
    embedding_enabled = bool(settings.get("embedding.enabled", False))
    auto_detect = embedding_enabled and bool(settings.get("embedding.auto_detect_dimension", True))
    if auto_detect and not metadata_path.exists():
        probed_dim = _probe_embedding_dimension(adapter, configured_dim)
        if probed_dim != configured_dim:
            logger.info(
                "Vector dimension auto-aligned for fresh store: configured=%s, detected=%s",
                configured_dim,
                probed_dim,
            )
        vector_dim = probed_dim
    if hasattr(adapter, "set_embedding_dimension"):
        adapter.set_embedding_dimension(vector_dim)

    quantization_map = {
        "float32": QuantizationType.FLOAT32,
        "int8": QuantizationType.INT8,
        "pq": QuantizationType.PQ,
    }
    quantization_type = quantization_map.get(
        str(settings.get("embedding.quantization_type", "int8")).strip().lower(),
        QuantizationType.INT8,
    )

    vector_store = VectorStore(
        dimension=vector_dim,
        quantization_type=quantization_type,
        data_dir=vectors_dir,
    )
    vector_store.min_train_threshold = _safe_int(settings.get("embedding.min_train_threshold", 40), 40)

    matrix_format_map = {
        "csr": SparseMatrixFormat.CSR,
        "csc": SparseMatrixFormat.CSC,
    }
    matrix_format = matrix_format_map.get(
        str(settings.get("graph.sparse_matrix_format", "csr")).strip().lower(),
        SparseMatrixFormat.CSR,
    )
    graph_store = GraphStore(matrix_format=matrix_format, data_dir=graph_dir)
    metadata_store = MetadataStore(data_dir=metadata_dir)
    metadata_store.connect()

    sparse_index = None
    sparse_raw = settings.get("retrieval.sparse", {}) or {}
    try:
        sparse_cfg = SparseBM25Config(**(sparse_raw if isinstance(sparse_raw, dict) else {}))
        sparse_index = SparseBM25Index(metadata_store=metadata_store, config=sparse_cfg)
        if sparse_cfg.enabled and not sparse_cfg.lazy_load:
            sparse_index.ensure_loaded()
    except Exception as exc:
        logger.warning("Sparse index init failed, disabled: %s", exc)

    if vector_store.has_data():
        try:
            vector_store.load()
            logger.info("Loaded vector store with %s vectors", vector_store.num_vectors)
        except Exception as exc:
            logger.warning("Vector load failed: %s", exc)

    if graph_store.has_data():
        try:
            graph_store.load()
            logger.info("Loaded graph store with %s nodes", graph_store.num_nodes)
        except Exception as exc:
            logger.warning("Graph load failed: %s", exc)

    # Compatibility migration: rebuild edge-hash map when absent.
    try:
        if not getattr(graph_store, "_edge_hash_map", {}):
            triples = metadata_store.get_all_triples()
            if triples:
                rebuilt = graph_store.rebuild_edge_hash_map(triples)
                logger.info("Rebuilt edge hash map entries: %s", rebuilt)
                graph_store.save()
    except Exception as exc:
        logger.warning("Edge hash compatibility rebuild skipped: %s", exc)

    retrieval_raw = settings.get("retrieval", {}) or {}
    sparse_for_retriever = retrieval_raw.get("sparse", {}) if isinstance(retrieval_raw, dict) else {}
    fusion_for_retriever = retrieval_raw.get("fusion", {}) if isinstance(retrieval_raw, dict) else {}
    search_raw = retrieval_raw.get("search", {}) if isinstance(retrieval_raw, dict) else {}
    relation_intent_raw = search_raw.get("relation_intent", {}) if isinstance(search_raw, dict) else {}
    graph_recall_raw = search_raw.get("graph_recall", {}) if isinstance(search_raw, dict) else {}
    posterior_graph_raw = search_raw.get("posterior_graph", {}) if isinstance(search_raw, dict) else {}
    retriever_config = DualPathRetrieverConfig(
        top_k_paragraphs=_safe_int(settings.get("retrieval.top_k_paragraphs", 20), 20),
        top_k_relations=_safe_int(settings.get("retrieval.top_k_relations", 10), 10),
        top_k_final=_safe_int(settings.get("retrieval.top_k_final", 10), 10),
        alpha=_safe_float(settings.get("retrieval.alpha", 0.5), 0.5),
        enable_ppr=bool(settings.get("retrieval.enable_ppr", True)),
        ppr_alpha=_safe_float(settings.get("retrieval.ppr_alpha", 0.85), 0.85),
        ppr_timeout_seconds=_safe_float(settings.get("retrieval.ppr_timeout_seconds", 1.5), 1.5),
        ppr_concurrency_limit=_safe_int(settings.get("retrieval.ppr_concurrency_limit", 4), 4),
        enable_parallel=bool(settings.get("retrieval.enable_parallel", True)),
        debug=bool(settings.get("advanced.debug", False)),
        sparse=SparseBM25Config(**(sparse_for_retriever if isinstance(sparse_for_retriever, dict) else {})),
        fusion=FusionConfig(**(fusion_for_retriever if isinstance(fusion_for_retriever, dict) else {})),
        relation_intent=RelationIntentConfig(
            **(relation_intent_raw if isinstance(relation_intent_raw, dict) else {})
        ),
        graph_recall=GraphRelationRecallConfig(
            **(graph_recall_raw if isinstance(graph_recall_raw, dict) else {})
        ),
        posterior_graph=PosteriorGraphConfig(
            **(posterior_graph_raw if isinstance(posterior_graph_raw, dict) else {})
        ),
    )

    retriever = DualPathRetriever(
        vector_store=vector_store,
        graph_store=graph_store,
        metadata_store=metadata_store,
        embedding_manager=adapter,
        sparse_index=sparse_index,
        config=retriever_config,
    )

    threshold_filter = DynamicThresholdFilter(
        ThresholdConfig(
            method=ThresholdMethod.ADAPTIVE,
            min_threshold=_safe_float(settings.get("threshold.min_threshold", 0.3), 0.3),
            max_threshold=_safe_float(settings.get("threshold.max_threshold", 0.95), 0.95),
            percentile=_safe_float(settings.get("threshold.percentile", 75.0), 75.0),
            std_multiplier=_safe_float(settings.get("threshold.std_multiplier", 1.5), 1.5),
            min_results=_safe_int(settings.get("threshold.min_results", 3), 3),
            enable_auto_adjust=bool(settings.get("threshold.enable_auto_adjust", True)),
        )
    )

    llm_endpoint_cfg = resolve_openapi_endpoint_config(settings.config, section="embedding")
    chat_model = str(
        llm_endpoint_cfg.get("chat_model", "")
        or "gpt-4o-mini"
    )
    llm_client = LLMClient(
        base_url=str(llm_endpoint_cfg.get("base_url", "")),
        api_key=str(llm_endpoint_cfg.get("api_key", "")),
        model=chat_model,
        timeout_seconds=_safe_float(llm_endpoint_cfg.get("timeout_seconds", 60), 60.0),
        max_retries=_safe_int(llm_endpoint_cfg.get("max_retries", 3), 3),
    )

    relation_write_service = RelationWriteService(
        metadata_store=metadata_store,
        graph_store=graph_store,
        vector_store=vector_store,
        embedding_manager=adapter,
    )
    paragraph_vector_service = ParagraphVectorWriteService(
        metadata_store=metadata_store,
        vector_store=vector_store,
        embedding_manager=adapter,
    )

    service_config: Dict[str, Any] = dict(settings.config)
    service_config.update(
        {
            "llm_client": llm_client,
            "paragraph_vector_service": paragraph_vector_service,
            "relation_write_service": relation_write_service,
        }
    )
    episode_service = EpisodeService(
        metadata_store=metadata_store,
        plugin_config=service_config,
        segmentation_service=EpisodeSegmentationService(plugin_config=service_config),
    )
    episode_retrieval_service = EpisodeRetrievalService(
        metadata_store=metadata_store,
        retriever=retriever,
    )

    person_profile_service = PersonProfileService(
        metadata_store=metadata_store,
        graph_store=graph_store,
        vector_store=vector_store,
        embedding_manager=adapter,
        sparse_index=sparse_index,
        plugin_config=service_config,
        retriever=retriever,
    )

    return AppContext(
        settings=settings,
        vector_store=vector_store,
        graph_store=graph_store,
        metadata_store=metadata_store,
        embedding_manager=adapter,
        sparse_index=sparse_index,
        retriever=retriever,
        threshold_filter=threshold_filter,
        person_profile_service=person_profile_service,
        paragraph_vector_service=paragraph_vector_service,
        relation_write_service=relation_write_service,
        episode_service=episode_service,
        episode_retrieval_service=episode_retrieval_service,
        llm_client=llm_client,
        data_dir=data_dir,
        config=settings.config,
    )
