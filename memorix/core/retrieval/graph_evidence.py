"""查询局部图证据评分；仅依赖候选信号，不依赖存储或 Bot 框架。"""

import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

_RELATION_BOTH_ENTITIES_GROUNDING_FACTOR = 0.78
_RELATION_SINGLE_ENTITY_GROUNDING_FACTOR = 0.55
_RELATION_PREDICATE_ONLY_GROUNDING_FACTOR = 0.4
_RELATION_UNGROUNDED_FACTOR = 0.3
_ENTITY_UNGROUNDED_FACTOR = 0.4
_GRAPH_RELIABILITY_GROUNDING_WEIGHT = 0.65
_GRAPH_RELIABILITY_AGREEMENT_WEIGHT = 0.10
_GRAPH_RELIABILITY_SUPPORT_WEIGHT = 0.25
_GRAPH_RELIABILITY_SUPPORT_TARGET = 2
_GRAPH_RELIABILITY_WEIGHT_FLOOR = 0.15
_GRAPH_RELIABILITY_CURVE_EXPONENT = 3.0


@dataclass(frozen=True)
class GraphReliabilityEstimate:
    """当前查询命中的图证据可信度估计。"""

    score: float
    grounding_quality: float
    channel_agreement: float
    support_coverage: float
    evidence_count: int
    grounded_relation_count: int
    relation_count: int


def clip_unit(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def grounding_factor(
    evidence: Dict[str, Any],
    paragraph: Dict[str, Any],
) -> float:
    """按证据在当前支撑段落中的逐字落地程度进行软衰减。"""

    paragraph_text = unicodedata.normalize(
        "NFKC",
        str(paragraph.get("content", "") or ""),
    ).casefold()

    def is_grounded(value: Any) -> bool:
        token = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
        return bool(token and token in paragraph_text)

    if str(evidence.get("type", "") or "") == "entity":
        return 1.0 if is_grounded(evidence.get("name", "")) else _ENTITY_UNGROUNDED_FACTOR

    subject_grounded = is_grounded(evidence.get("subject", ""))
    predicate_grounded = is_grounded(evidence.get("predicate", ""))
    object_grounded = is_grounded(evidence.get("object", ""))
    if subject_grounded and predicate_grounded and object_grounded:
        return 1.0
    if subject_grounded and object_grounded:
        return _RELATION_BOTH_ENTITIES_GROUNDING_FACTOR
    if subject_grounded or object_grounded:
        return _RELATION_SINGLE_ENTITY_GROUNDING_FACTOR
    if predicate_grounded:
        return _RELATION_PREDICATE_ONLY_GROUNDING_FACTOR
    return _RELATION_UNGROUNDED_FACTOR


def estimate_reliability(
    candidates: List[Dict[str, Any]],
    *,
    scan_limit: int,
) -> GraphReliabilityEstimate:
    """用证据落地、多通道一致性和关系覆盖估计查询局部可信度。"""

    ranked_graph_candidates: List[Tuple[float, Dict[str, Any]]] = []
    best_independent_score = 0.0
    for candidate in candidates:
        score_meta = candidate["scores"]
        semantic_score = max(0.0, float(score_meta.get("semantic", 0.0) or 0.0))
        sparse_score = max(0.0, float(score_meta.get("sparse", 0.0) or 0.0))
        best_independent_score = max(best_independent_score, semantic_score, sparse_score)

        graph_score = max(
            0.0,
            float(score_meta.get("graph_evidence", 0.0) or 0.0),
        )
        if graph_score > 0.0:
            ranked_graph_candidates.append((graph_score, candidate))

    ranked_graph_candidates.sort(key=lambda pair: pair[0], reverse=True)
    ranked_graph_candidates = ranked_graph_candidates[: max(1, int(scan_limit))]
    if not ranked_graph_candidates:
        return GraphReliabilityEstimate(0.0, 0.0, 0.0, 0.0, 0, 0, 0)

    graph_score_total = sum(graph_score for graph_score, _ in ranked_graph_candidates)
    agreement_total = 0.0
    grounding_weight_total = 0.0
    grounding_quality_total = 0.0
    evidence_count = 0
    grounded_relation_hashes = set()
    relation_hashes = set()

    for graph_score, candidate in ranked_graph_candidates:
        score_meta = candidate["scores"]
        independent_score = max(
            0.0,
            float(score_meta.get("semantic", 0.0) or 0.0),
            float(score_meta.get("sparse", 0.0) or 0.0),
        )
        if best_independent_score > 0.0:
            agreement_total += graph_score * clip_unit(independent_score / best_independent_score)

        for evidence in candidate["evidence"]:
            evidence_type = str(evidence.get("type", "") or "").strip().lower()
            if evidence_type not in {"relation", "entity"}:
                continue

            normalized_score = max(0.0, float(evidence.get("normalized_score", 0.0) or 0.0))
            if normalized_score <= 0.0:
                continue

            grounding_factor = clip_unit(float(evidence.get("grounding_factor", 0.0) or 0.0))
            if evidence_type == "relation":
                normalized_grounding = clip_unit(
                    (grounding_factor - _RELATION_UNGROUNDED_FACTOR) / (1.0 - _RELATION_UNGROUNDED_FACTOR)
                )
                relation_hash = str(evidence.get("hash", "") or "").strip()
                if relation_hash:
                    relation_hashes.add(relation_hash)
                    if grounding_factor >= _RELATION_BOTH_ENTITIES_GROUNDING_FACTOR:
                        grounded_relation_hashes.add(relation_hash)
            else:
                # 实体在段落中出现只能证明局部落地，不能单独证明关系链可靠。
                normalized_grounding = 0.35 * clip_unit(
                    (grounding_factor - _ENTITY_UNGROUNDED_FACTOR) / (1.0 - _ENTITY_UNGROUNDED_FACTOR)
                )

            grounding_weight_total += normalized_score
            grounding_quality_total += normalized_score * normalized_grounding
            evidence_count += 1

    grounding_quality = grounding_quality_total / grounding_weight_total if grounding_weight_total > 0.0 else 0.0
    channel_agreement = agreement_total / graph_score_total if graph_score_total > 0.0 else 0.0
    support_quantity = clip_unit(len(grounded_relation_hashes) / float(_GRAPH_RELIABILITY_SUPPORT_TARGET))
    support_precision = len(grounded_relation_hashes) / float(len(relation_hashes)) if relation_hashes else 0.0
    support_coverage = clip_unit(support_quantity * support_precision)
    reliability = clip_unit(
        _GRAPH_RELIABILITY_GROUNDING_WEIGHT * grounding_quality
        + _GRAPH_RELIABILITY_AGREEMENT_WEIGHT * channel_agreement
        + _GRAPH_RELIABILITY_SUPPORT_WEIGHT * support_coverage
    )
    return GraphReliabilityEstimate(
        score=reliability,
        grounding_quality=grounding_quality,
        channel_agreement=channel_agreement,
        support_coverage=support_coverage,
        evidence_count=evidence_count,
        grounded_relation_count=len(grounded_relation_hashes),
        relation_count=len(relation_hashes),
    )


def calibrate_weights(
    candidates: List[Dict[str, Any]],
    *,
    semantic_weight: float,
    sparse_weight: float,
    graph_weight: float,
    scan_limit: int,
) -> Tuple[float, float, float, GraphReliabilityEstimate]:
    """图权重高于常规值时，按查询局部可信度连续缩放。"""

    estimate = estimate_reliability(candidates, scan_limit=scan_limit)
    graph_weight_floor = min(float(graph_weight), _GRAPH_RELIABILITY_WEIGHT_FLOOR)
    graph_weight_range = max(0.0, float(graph_weight) - graph_weight_floor)
    independent_weight = float(semantic_weight) + float(sparse_weight)
    if graph_weight_range <= 0.0 or independent_weight <= 0.0:
        return semantic_weight, sparse_weight, graph_weight, estimate

    effective_graph_weight = graph_weight_floor + graph_weight_range * estimate.score**_GRAPH_RELIABILITY_CURVE_EXPONENT
    released_weight = float(graph_weight) - effective_graph_weight
    effective_semantic_weight = float(semantic_weight) + released_weight * (float(semantic_weight) / independent_weight)
    effective_sparse_weight = float(sparse_weight) + released_weight * (float(sparse_weight) / independent_weight)
    return effective_semantic_weight, effective_sparse_weight, effective_graph_weight, estimate
