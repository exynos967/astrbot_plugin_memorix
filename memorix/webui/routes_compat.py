
import asyncio
import inspect
import json
import socket
import threading
import time
import uvicorn
from fastapi import FastAPI, HTTPException, Body, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from typing import Dict, Any, List, Optional, Set, Tuple
from pydantic import BaseModel

from astrbot.api import logger
from ..amemorix.settings import mask_sensitive
from ..core.utils.hash import compute_hash

class EdgeWeightUpdate(BaseModel):
    source: str
    target: str
    weight: float

class NodeDelete(BaseModel):
    node_id: str

class EdgeDelete(BaseModel):
    source: str
    target: str

class NodeCreate(BaseModel):
    node_id: str
    label: Optional[str] = None

class EdgeCreate(BaseModel):
    source: str
    target: str
    weight: float = 1.0
    predicate: Optional[str] = None

class NodeRename(BaseModel):
    old_id: str
    new_id: str

class AutoSaveConfig(BaseModel):
    enabled: bool

class SourceListRequest(BaseModel):
    node_id: Optional[str] = None
    edge_source: Optional[str] = None
    edge_target: Optional[str] = None

class SourceDeleteRequest(BaseModel):
    paragraph_hash: str

class BatchSourceDeleteRequest(BaseModel):
    source: str

class PersonProfileQueryRequest(BaseModel):
    person_id: Optional[str] = None
    person_keyword: Optional[str] = None
    top_k: int = 12
    force_refresh: bool = False

class PersonProfileOverrideUpsertRequest(BaseModel):
    person_id: str
    override_text: str
    updated_by: Optional[str] = None

class PersonProfileOverrideDeleteRequest(BaseModel):
    person_id: str

class MemorixServer:
    def __init__(self, plugin_instance, host="0.0.0.0", port=8082):
        self.plugin = plugin_instance
        self.host = host
        self.port = port
        self.app = FastAPI(title="A_Memorix 可视化编辑器")
        self.server_thread = None
        self._server = None
        self._startup_error = None
        self.should_exit = False

        # 缓存 relations predicate map
        self._relation_cache = None
        self._relation_cache_timestamp = 0
        self._relation_cache_lock = threading.Lock()
        
        # 配置 CORS（默认不放开跨域，允许通过配置白名单）
        allowed_origins = self.plugin.get_config("cors.allow_origins", []) if hasattr(self.plugin, "get_config") else []
        if not isinstance(allowed_origins, list):
            allowed_origins = []

        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=[str(x) for x in allowed_origins],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        
        self._setup_routes()

    @staticmethod
    def _canonical_node_id(value: Any) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def _probe_host(host: str) -> str:
        normalized = str(host or "").strip().lower()
        if normalized in {"0.0.0.0", "::", "[::]"}:
            return "127.0.0.1"
        return str(host or "").strip() or "127.0.0.1"

    def _reset_relation_cache(self) -> None:
        with self._relation_cache_lock:
            self._relation_cache = None
            self._relation_cache_timestamp = 0

    def _get_relation_cache(self) -> Dict[Tuple[str, str], List[str]]:
        if self.plugin.metadata_store is None:
            return {}

        with self._relation_cache_lock:
            if self._relation_cache is None:
                start_t = time.time()
                raw_triples = self.plugin.metadata_store.get_all_triples()
                cache: Dict[Tuple[str, str], List[str]] = {}
                count = 0
                for s, p, o, _ in raw_triples:
                    key = (self._canonical_node_id(s), self._canonical_node_id(o))
                    cache.setdefault(key, []).append(p)
                    count += 1
                self._relation_cache = cache
                logger.info("[Cache] 重新构建关系缓存，共 %s 条关系，耗时 %.4fs", count, time.time() - start_t)
            return self._relation_cache

    def _lookup_edge_predicates(
        self,
        edge_predicates: Dict[Tuple[str, str], List[str]],
        source: str,
        target: str,
    ) -> List[str]:
        return edge_predicates.get(
            (self._canonical_node_id(source), self._canonical_node_id(target)),
            [],
        )

    def _build_graph_by_source(self, *, exclude_leaf: bool, source: str) -> Dict[str, Any]:
        if self.plugin.metadata_store is None:
            raise HTTPException(status_code=503, detail="Metadata store not initialized")

        paragraphs = self.plugin.metadata_store.get_paragraphs_by_source(source)

        found_nodes: Set[str] = set()
        found_edges: List[Dict[str, Any]] = []
        processed_edge_keys: Set[Tuple[str, str]] = set()
        node_map: Dict[str, str] = {}

        for paragraph in paragraphs:
            p_entities = self.plugin.metadata_store.get_paragraph_entities(paragraph["hash"])
            for entity in p_entities:
                raw_name = entity["name"]
                lower_id = self._canonical_node_id(raw_name)
                if not lower_id:
                    continue
                node_map[lower_id] = raw_name
                found_nodes.add(lower_id)

            p_relations = self.plugin.metadata_store.get_paragraph_relations(paragraph["hash"])
            for relation in p_relations:
                s_raw = relation["subject"]
                t_raw = relation["object"]
                s_id = self._canonical_node_id(s_raw)
                t_id = self._canonical_node_id(t_raw)
                if not s_id or not t_id:
                    continue

                node_map.setdefault(s_id, s_raw)
                node_map.setdefault(t_id, t_raw)
                found_nodes.add(s_id)
                found_nodes.add(t_id)

                key = (s_id, t_id)
                if key in processed_edge_keys:
                    continue
                found_edges.append({
                    "id": f"{s_id}_{t_id}",
                    "from": s_id,
                    "to": t_id,
                    "value": float(relation["confidence"]),
                    "label": relation["predicate"],
                    "arrows": "to",
                })
                processed_edge_keys.add(key)

        nodes = [{"id": nid, "label": node_map.get(nid, nid)} for nid in found_nodes]
        edges = found_edges

        if exclude_leaf:
            degrees: Dict[str, int] = {}
            for edge in edges:
                degrees[edge["from"]] = degrees.get(edge["from"], 0) + 1
                degrees[edge["to"]] = degrees.get(edge["to"], 0) + 1

            nodes = [node for node in nodes if degrees.get(node["id"], 0) != 1]
            node_ids = {node["id"] for node in nodes}
            edges = [edge for edge in edges if edge["from"] in node_ids and edge["to"] in node_ids]

        return {
            "nodes": nodes,
            "edges": edges,
            "debug": {
                "source": source,
                "nodes": len(nodes),
                "edges": len(edges),
            },
        }

    def _build_graph_snapshot(self, *, exclude_leaf: bool, density: float) -> Dict[str, Any]:
        if self.plugin.graph_store is None:
            raise HTTPException(status_code=503, detail="Graph store not initialized")

        node_names = self.plugin.graph_store.get_nodes()

        if exclude_leaf:
            scores = self.plugin.graph_store.get_saliency_scores()
            if not scores:
                filtered_nodes = node_names
                node_status = {name: {"is_ghost": False} for name in node_names}
            else:
                sorted_scores = sorted(scores.values())
                n = len(sorted_scores)
                threshold_idx = min(int(n * (1.0 - density)), n - 5) if n else 0
                threshold_idx = max(0, threshold_idx)
                min_score = sorted_scores[threshold_idx] if sorted_scores else 0

                hub_threshold = sorted_scores[int(n * 0.9)] if n > 10 else 0
                hubs = {node for node, score in scores.items() if score >= hub_threshold}

                filtered_nodes: List[str] = []
                node_status: Dict[str, Dict[str, Any]] = {}

                ghost_threshold_idx = max(0, threshold_idx - int(n * 0.2))
                ghost_min_score = sorted_scores[ghost_threshold_idx] if sorted_scores else 0

                for name in node_names:
                    score = scores.get(name, 0)
                    is_hub_neighbor = any(self.plugin.graph_store.get_edge_weight(name, hub) > 0 for hub in hubs) or any(
                        self.plugin.graph_store.get_edge_weight(hub, name) > 0 for hub in hubs
                    )

                    if score >= min_score or is_hub_neighbor:
                        filtered_nodes.append(name)
                        node_status[name] = {"is_ghost": False}
                    elif score >= ghost_min_score:
                        filtered_nodes.append(name)
                        node_status[name] = {"is_ghost": True}
        else:
            filtered_nodes = node_names
            node_status = {name: {"is_ghost": False} for name in node_names}

        filtered_node_set = set(filtered_nodes)
        nodes = [
            {
                "id": name,
                "label": name,
                "is_ghost": node_status.get(name, {}).get("is_ghost", False),
            }
            for name in filtered_nodes
        ]
        edges: List[Dict[str, Any]] = []
        processed_edges: Set[Tuple[str, str]] = set()

        edge_predicates: Dict[Tuple[str, str], List[str]] = {}
        relation_count = 0

        if self.plugin.metadata_store:
            try:
                edge_predicates = self._get_relation_cache()
                relation_count = -1
            except Exception as exc:
                logger.error(f"Error fetching relations for graph: {exc}")
        else:
            logger.warning("[DEBUG] MetadataStore 未初始化或不可用")

        for source_name in filtered_nodes:
            neighbors = self.plugin.graph_store.get_neighbors(source_name)
            for target_name in neighbors:
                if target_name not in filtered_node_set:
                    continue

                edge_key = (source_name, target_name)
                if edge_key in processed_edges:
                    continue

                weight = self.plugin.graph_store.get_edge_weight(source_name, target_name)
                predicates = self._lookup_edge_predicates(edge_predicates, source_name, target_name)

                if predicates:
                    display_label = ", ".join(predicates[:3])
                    if len(predicates) > 3:
                        display_label += "..."
                else:
                    display_label = f"{weight:.2f}"

                edges.append({
                    "id": f"{source_name}_{target_name}",
                    "from": source_name,
                    "to": target_name,
                    "value": float(weight),
                    "label": display_label,
                    "predicates": predicates,
                    "arrows": "to",
                })
                processed_edges.add(edge_key)

        gst = self.plugin.graph_store
        idx_to_node = gst._nodes
        for (s_idx, t_idx), hashes in gst._edge_hash_map.items():
            if not hashes:
                continue
            if s_idx >= len(idx_to_node) or t_idx >= len(idx_to_node):
                continue

            s_name = idx_to_node[s_idx]
            t_name = idx_to_node[t_idx]
            if s_name not in filtered_node_set or t_name not in filtered_node_set:
                continue

            edge_key = (s_name, t_name)
            if edge_key in processed_edges:
                continue

            predicates = self._lookup_edge_predicates(edge_predicates, s_name, t_name)
            display_label = ", ".join(predicates[:3]) if predicates else "(冷冻)"
            edges.append({
                "id": f"{s_name}_{t_name}",
                "from": s_name,
                "to": t_name,
                "value": 0.05,
                "physics": False,
                "label": display_label,
                "predicates": predicates,
                "arrows": "to",
                "is_active": False,
                "dashes": True,
                "color": {"color": "rgba(203, 213, 225, 0.4)"},
            })
            processed_edges.add(edge_key)

        if self.plugin.metadata_store and nodes:
            try:
                node_hash_map: Dict[str, int] = {}
                node_hashes: List[str] = []
                for i, node in enumerate(nodes):
                    nid = node["id"]
                    canon_name = self._canonical_node_id(nid)
                    h = compute_hash(canon_name)
                    node_hashes.append(h)
                    node_hash_map[h] = i

                if node_hashes:
                    node_status_map = self.plugin.metadata_store.get_entity_status_batch(node_hashes)
                    for h, status in node_status_map.items():
                        if h not in node_hash_map:
                            continue
                        node_ref = nodes[node_hash_map[h]]
                        if status.get("is_deleted"):
                            node_ref["is_deleted"] = True
                            node_ref["color"] = {"background": "#ef4444", "border": "#fee2e2"}
                            node_ref["shape"] = "box"
                            node_ref["label"] += " (已删除)"
            except Exception as exc:
                logger.warning(f"Failed to inject node status: {exc}")

        if self.plugin.metadata_store:
            try:
                import datetime

                now = datetime.datetime.now().timestamp()
                all_graph_hashes: List[str] = []
                edge_hash_mapping: Dict[int, List[str]] = {}

                for i, edge in enumerate(edges):
                    s = edge["from"]
                    t = edge["to"]
                    s_canon = gst._canonicalize(s)
                    t_canon = gst._canonicalize(t)
                    if s_canon not in gst._node_to_idx or t_canon not in gst._node_to_idx:
                        continue
                    s_idx = gst._node_to_idx[s_canon]
                    t_idx = gst._node_to_idx[t_canon]
                    hashes = gst._edge_hash_map.get((s_idx, t_idx), set())
                    if not hashes:
                        continue
                    h_list = list(hashes)
                    all_graph_hashes.extend(h_list)
                    edge_hash_mapping[i] = h_list

                if all_graph_hashes:
                    status_map = self.plugin.metadata_store.get_relation_status_batch(all_graph_hashes)
                    for i, h_list in edge_hash_mapping.items():
                        is_pinned = False
                        max_protected = 0
                        all_inactive = True

                        for h in h_list:
                            st = status_map.get(h)
                            if not st:
                                continue
                            if st.get("is_pinned"):
                                is_pinned = True
                            p_until = st.get("protected_until") or 0
                            if p_until > max_protected:
                                max_protected = p_until
                            if not st.get("is_inactive"):
                                all_inactive = False

                        edge_ref = edges[i]
                        edge_ref["is_pinned"] = is_pinned
                        edge_ref["protected_until"] = max_protected
                        edge_ref["is_protected"] = max_protected > now
                        edge_ref["is_active"] = not all_inactive

                        if all_inactive:
                            edge_ref["color"] = {"color": "rgba(203, 213, 225, 0.4)"}
                            edge_ref["dashes"] = True

                        if is_pinned or max_protected > now:
                            edge_ref["shadow"] = {
                                "enabled": True,
                                "color": "rgba(251, 191, 36, 0.6)",
                                "size": 5,
                            }
            except Exception as exc:
                logger.warning(f"Failed to inject V5 metadata: {exc}")

        debug_info = {
            "relation_count": relation_count,
            "sample_key": list(edge_predicates.keys())[0] if edge_predicates else None,
            "edge_count": len(edges),
            "exclude_leaf": exclude_leaf,
        }
        return {"nodes": nodes, "edges": edges, "debug": debug_info}

    def _build_graph_payload(self, *, exclude_leaf: bool = False, source: Optional[str] = None, density: float = 1.0) -> Dict[str, Any]:
        density = max(0.0, min(1.0, float(density)))
        if source:
            return self._build_graph_by_source(exclude_leaf=exclude_leaf, source=source)
        return self._build_graph_snapshot(exclude_leaf=exclude_leaf, density=density)

    def _setup_routes(self):
        def _build_person_profile_service():
            from ..core.utils.person_profile_service import PersonProfileService

            return PersonProfileService(
                metadata_store=self.plugin.metadata_store,
                graph_store=self.plugin.graph_store,
                vector_store=self.plugin.vector_store,
                embedding_manager=self.plugin.embedding_manager,
                sparse_index=getattr(self.plugin, "sparse_index", None),
                plugin_config=getattr(self.plugin, "config", {}) or {},
            )

        def _resolve_person_id_for_web(service, raw_value: str) -> str:
            value = str(raw_value or "").strip()
            if not value:
                return ""
            if len(value) == 32 and all(ch in "0123456789abcdefABCDEF" for ch in value):
                return value.lower()
            resolved = service.resolve_person_id(value)
            return resolved or ""

        def _parse_group_nicks(raw_value: Any) -> List[str]:
            if not raw_value:
                return []
            try:
                data = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
            except Exception:
                return []
            if not isinstance(data, list):
                return []
            out: List[str] = []
            for item in data:
                if isinstance(item, dict):
                    nick = str(item.get("group_nick_name", "")).strip()
                    if nick:
                        out.append(nick)
                elif isinstance(item, str):
                    nick = item.strip()
                    if nick:
                        out.append(nick)
            return out

        
        @self.app.get("/api/graph")
        async def get_graph(exclude_leaf: bool = False, source: Optional[str] = None, density: float = 1.0):
            """获取图谱数据，支持过滤叶子节点、来源及信息密度控制。"""
            try:
                return await asyncio.to_thread(
                    self._build_graph_payload,
                    exclude_leaf=exclude_leaf,
                    source=source,
                    density=density,
                )
            except HTTPException:
                raise
            except Exception as exc:
                logger.error(f"Get graph failed: {exc}")
                raise HTTPException(status_code=500, detail=str(exc))

        @self.app.post("/api/edge/weight")
        async def update_edge_weight(data: EdgeWeightUpdate):
            """更新边权重"""
            if self.plugin.graph_store is None:
                raise HTTPException(status_code=503, detail="Graph store not initialized")
            
            try:
                # 计算增量 (因为 update_edge_weight 是基于增量的)
                # 或者我们需要一个直接设置权重的方法。
                # 查看 GraphStore源码，update_edge_weight 是 add weight.
                # 如果我们要 set weight，我们需要先获取当前权重。
                
                current_weight = self.plugin.graph_store.get_edge_weight(data.source, data.target)
                delta = data.weight - current_weight
                
                new_weight = self.plugin.graph_store.update_edge_weight(data.source, data.target, delta)
                # 持久化保存到磁盘
                self.plugin.graph_store.save()
                return {"success": True, "new_weight": new_weight}
            except Exception as e:
                logger.error(f"Update weight failed: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.delete("/api/node")
        async def delete_node(data: NodeDelete):
            """删除节点"""
            if self.plugin.graph_store is None:
                raise HTTPException(status_code=503, detail="Graph store not initialized")
                
            try:
                # 使用 GraphStore.delete_nodes 方法
                deleted_count = self.plugin.graph_store.delete_nodes([data.node_id])
                
                # 同时从 MetadataStore 删除实体
                if self.plugin.metadata_store:
                    self.plugin.metadata_store.delete_entity(data.node_id)
                
                # 持久化保存
                self.plugin.graph_store.save()
                self._reset_relation_cache()
                return {"success": True, "deleted_count": deleted_count}
            except Exception as e:
                logger.error(f"Delete node failed: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.delete("/api/edge")
        async def delete_edge(data: EdgeDelete):
            """删除边"""
            if self.plugin.graph_store is None:
                raise HTTPException(status_code=503, detail="Graph store not initialized")
            
            try:
                # 将权重设为 0 或移除
                # 简单做法：update_edge_weight 减去当前权重
                current_weight = self.plugin.graph_store.get_edge_weight(data.source, data.target)
                self.plugin.graph_store.update_edge_weight(data.source, data.target, -current_weight)
                
                # 持久化保存
                self.plugin.graph_store.save()
                self._reset_relation_cache()
                return {"success": True}
            except Exception as e:
                logger.error(f"Delete edge failed: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/api/node")
        async def create_node(data: NodeCreate):
            """创建节点"""
            print(f"DEBUG: graph_store={self.plugin.graph_store}")
            if self.plugin.graph_store is None:
                raise HTTPException(status_code=503, detail="Graph store not initialized")
            
            try:
                # 1. 使用 GraphStore.add_nodes 方法建立物理节点
                added_count = self.plugin.graph_store.add_nodes([data.node_id])
                
                # 2. 同时在 MetadataStore 注册实体，保证元数据一致性
                if self.plugin.metadata_store:
                    self.plugin.metadata_store.add_entity(name=data.node_id)
                
                # 持久化保存
                self.plugin.graph_store.save()
                return {"success": True, "added_count": added_count, "node_id": data.node_id}
            except Exception as e:
                logger.error(f"Create node failed: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/api/edge")
        async def create_edge(data: EdgeCreate):
            """创建边 (支持语义关系)"""
            if self.plugin.graph_store is None:
                raise HTTPException(status_code=503, detail="Graph store not initialized")
            
            try:
                # 确保节点存在
                self.plugin.graph_store.add_nodes([data.source, data.target])
                
                # 1. 如果有语义关系，先存入 MetadataStore
                if data.predicate and self.plugin.metadata_store:
                   self.plugin.metadata_store.add_relation(
                       subject=data.source, 
                       predicate=data.predicate, 
                       obj=data.target,
                       confidence=data.weight
                   )

                # 2. 使用 GraphStore.add_edges 方法建立物理连接
                added_count = self.plugin.graph_store.add_edges(
                    [(data.source, data.target)],
                    weights=[data.weight]
                )
                
                # 持久化保存
                self.plugin.graph_store.save()
                self._reset_relation_cache()
                return {"success": True, "added_count": added_count, "predicate": data.predicate}
            except Exception as e:
                logger.error(f"Create edge failed: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.put("/api/node/rename")
        async def rename_node(data: NodeRename):
            """重命名节点 (实际上是创建新节点，复制边，删除旧节点)"""
            if self.plugin.graph_store is None:
                raise HTTPException(status_code=503, detail="Graph store not initialized")
            
            try:
                # 检查旧节点是否存在
                if not self.plugin.graph_store.has_node(data.old_id):
                    raise HTTPException(status_code=404, detail=f"Node '{data.old_id}' not found")
                
                # 获取旧节点的所有边
                neighbors = self.plugin.graph_store.get_neighbors(data.old_id)
                
                # 添加新节点
                self.plugin.graph_store.add_nodes([data.new_id])
                
                # 复制边到新节点
                for neighbor in neighbors:
                    weight = self.plugin.graph_store.get_edge_weight(data.old_id, neighbor)
                    if weight > 0:
                        self.plugin.graph_store.add_edges([(data.new_id, neighbor)], weights=[weight])
                
                # 获取指向旧节点的边 (反向边)
                all_nodes = self.plugin.graph_store.get_nodes()
                for node in all_nodes:
                    if node != data.old_id and node != data.new_id:
                        weight = self.plugin.graph_store.get_edge_weight(node, data.old_id)
                        if weight > 0:
                            self.plugin.graph_store.add_edges([(node, data.new_id)], weights=[weight])
                
                # 删除旧节点
                self.plugin.graph_store.delete_nodes([data.old_id])
                
                # 持久化保存
                self.plugin.graph_store.save()
                self._reset_relation_cache()
                return {"success": True, "old_id": data.old_id, "new_id": data.new_id}
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Rename node failed: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/api/source/list")
        async def list_sources(data: SourceListRequest):
            """获取来源段落列表"""
            if self.plugin.metadata_store is None:
                 raise HTTPException(status_code=503, detail="Metadata store not initialized")
            
            paragraphs = []
            seen_hashes = set()
            
            try:
                # 0. 如果无任何参数，则返回文件列表 (Summary Mode)
                if not data.node_id and not data.edge_source and not data.edge_target:
                    sources = self.plugin.metadata_store.get_all_sources()
                    return {"mode": "summary", "sources": sources}
                # 1. 如果是查节点来源 (By Entity)
                if data.node_id:
                    # 注意: WebUI 传来的 node_id 通常是实体名称 (Node Name)
                    # MetadataStore.get_paragraphs_by_entity 接受 entity_name
                    entity_paras = self.plugin.metadata_store.get_paragraphs_by_entity(data.node_id)
                    for p in entity_paras:
                        if p['hash'] not in seen_hashes:
                            paragraphs.append(p)
                            seen_hashes.add(p['hash'])
                            
                # 2. 如果是查边来源 (By Relation)
                if data.edge_source and data.edge_target:
                    # 查出两点间的所有关系
                    relations = self.plugin.metadata_store.get_relations(
                        subject=data.edge_source, 
                        object=data.edge_target
                    )
                    for rel in relations:
                        rel_paras = self.plugin.metadata_store.get_paragraphs_by_relation(rel['hash'])
                        for p in rel_paras:
                            if p['hash'] not in seen_hashes:
                                paragraphs.append(p)
                                seen_hashes.add(p['hash'])
                                
                # 简化返回结构
                result = []
                for p in paragraphs:
                    result.append({
                        "hash": p["hash"],
                        "content": p["content"], # 全文或截断
                        "created_at": p.get("created_at"),
                        "source": p.get("source", "unknown")
                    })
                    
                return {"sources": result}
                
            except Exception as e:
                logger.error(f"List sources failed: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/api/source/batch_delete")
        async def batch_delete_source(data: BatchSourceDeleteRequest):
            """按来源批量删除（文件删除）"""
            if not self.plugin.metadata_store or not self.plugin.vector_store or not self.plugin.graph_store:
                 raise HTTPException(status_code=503, detail="Stores not fully initialized")
                 
            try:
                # 1. 找出所有相关段落
                paragraphs = self.plugin.metadata_store.get_paragraphs_by_source(data.source)
                if not paragraphs:
                    return {"success": True, "message": "No paragraphs found for this source", "count": 0}
                
                deleted_count = 0
                errors = []
                
                # 2. 逐个删除 (复用原子删除逻辑)
                # 考虑到性能，这里是简单的循环。如果有成千上万条，可能需要优化为批量事务。
                for p in paragraphs:
                    try:
                        # Phase 1: DB Transaction
                        cleanup_plan = self.plugin.metadata_store.delete_paragraph_atomic(p['hash'])
                        
                        # Phase 2: Memory Store Cleanup
                        vec_id = cleanup_plan.get("vector_id_to_remove")
                        if vec_id:
                            try:
                                self.plugin.vector_store.delete([vec_id])
                            except Exception:
                                pass # ignore missing vector
                                
                        edges_to_remove = cleanup_plan.get("edges_to_remove", [])
                        if edges_to_remove:
                            try:
                                self.plugin.graph_store.delete_edges(edges_to_remove)
                            except Exception:
                                pass
                                
                        deleted_count += 1
                        
                    except Exception as pe:
                        logger.error(f"Failed to delete paragraph {p['hash']}: {pe}")
                        errors.append(f"{p['hash']}: {pe}")
                
                # 3. 保存变更
                try:
                    self.plugin.vector_store.save()
                    self.plugin.graph_store.save()
                except Exception as se:
                    logger.warning(f"Auto-save after batch delete failed: {se}")
                    
                msg = f"Successfully deleted {deleted_count} paragraphs from source '{data.source}'"
                if errors:
                    msg += f". Errors: {len(errors)} occurred."
                    
                self._reset_relation_cache()
                return {"success": True, "message": msg, "count": deleted_count, "errors": errors}
                
            except Exception as e:
                logger.error(f"Batch source delete failed: {e}")
                raise HTTPException(status_code=500, detail=str(e))
        @self.app.delete("/api/source")
        async def delete_source(data: SourceDeleteRequest):
            """删除来源段落（两阶段提交）"""
            if not self.plugin.metadata_store or not self.plugin.vector_store or not self.plugin.graph_store:
                 raise HTTPException(status_code=503, detail="Stores not fully initialized")
                 
            try:
                # === Phase 1: DB Transaction & Plan Generation ===
                # 调用我们在 MetadataStore 实现的原子方法
                cleanup_plan = self.plugin.metadata_store.delete_paragraph_atomic(data.paragraph_hash)
                
                # === Phase 2: Post-Commit Cleanup (In-Memory Stores) ===
                # 这一步失败不会回滚 DB，但保证了 DB 的一致性
                errors = []
                
                # 1. 清理向量 (使用稳定 ID)
                vec_id = cleanup_plan.get("vector_id_to_remove")
                if vec_id:
                    try:
                        # VectorStore.delete 接受 ID 列表
                        self.plugin.vector_store.delete([vec_id])
                    except Exception as ve:
                        logger.error(f"Vector cleanup failed for {vec_id}: {ve}")
                        errors.append(f"Vector cleanup error: {ve}")
                        
                # 2. 清理图边 (批量删除)
                edges_to_remove = cleanup_plan.get("edges_to_remove", [])
                if edges_to_remove:
                    try:
                        self.plugin.graph_store.delete_edges(edges_to_remove)
                    except Exception as ge:
                        logger.error(f"Graph cleanup failed: {ge}")
                        errors.append(f"Graph cleanup error: {ge}")
                
                # 如果有非致命错误，记录并在响应中提示
                msg = "来源删除成功"
                if errors:
                    msg += f"，但带有清理警告: {'; '.join(errors)}"
                    
                # 触发保存以持久化内存中的变更
                try:
                    self.plugin.vector_store.save()
                    self.plugin.graph_store.save()
                except Exception as se:
                    logger.warning(f"删除来源后的自动保存失败: {se}")
                
                self._reset_relation_cache()
                return {"success": True, "message": msg, "details": cleanup_plan}
                
            except Exception as e:
                logger.error(f"Delete source failed: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        # --- V5 记忆管理端点 ---
        
        class MemoryProtectRequest(BaseModel):
            id: str # 边 ID "s_t"
            type: str # "pin" (置顶) 或 "ttl" (时间限制)
            duration: Optional[float] = 0.0 # TTL 的小时数
            
        class MemoryActionRequest(BaseModel):
            id: str # 边 ID "s_t"
            
        class MemoryRestoreRequest(BaseModel):
            hash: str
            type: Optional[str] = "relation" # relation (关系) | entity (实体)
        
        @self.app.get("/api/memory/recycle_bin")
        async def get_recycle_bin(limit: int = 50):
            """获取回收站中的记忆 (Entities + Relations)"""
            if not self.plugin.metadata_store:
                raise HTTPException(status_code=503, detail="Metadata store missing")
            
            try:
                # 1. 关系
                deleted_rels = self.plugin.metadata_store.get_deleted_relations(limit)
                for x in deleted_rels: x['type'] = 'relation'
                
                # 2. 实体
                deleted_ents = self.plugin.metadata_store.get_deleted_entities(limit)
                
                # 3. 合并
                combined = deleted_rels + deleted_ents
                combined.sort(key=lambda x: x.get('deleted_at', 0) or 0, reverse=True)
                
                return {"items": combined[:limit]}
            except Exception as e:
                logger.error(f"Recycle bin fetch failed: {e}")
                return {"items": [], "error": str(e)}

        @self.app.post("/api/memory/restore")
        async def restore_memory(data: MemoryRestoreRequest):
            """从回收站恢复记忆"""
            if not self.plugin.metadata_store or not self.plugin.graph_store:
                raise HTTPException(status_code=503, detail="Stores missing")

            try:
                if data.type == "entity":
                    # 复活实体
                    cursor = self.plugin.metadata_store._conn.cursor()
                    cursor.execute("UPDATE entities SET is_deleted=0, deleted_at=NULL WHERE hash=?", (data.hash,))
                    self.plugin.metadata_store._conn.commit()
                    return {"success": True, "type": "entity", "hash": data.hash}

                # relation: 先从回收站恢复元数据，再回灌图边
                record = self.plugin.metadata_store.restore_relation(data.hash)
                if not record:
                    raise HTTPException(status_code=404, detail="回收站中未找到该记忆")

                s, t = record["subject"], record["object"]

                # 若实体处于软删除状态，先复活再补图。
                self.plugin.metadata_store.revive_entities_by_names([s, t])
                self.plugin.graph_store.add_nodes([s, t])
                self.plugin.graph_store.add_edges(
                    [(s, t)],
                    weights=[record["confidence"]],
                    relation_hashes=[data.hash],
                )
                self.plugin.graph_store.save()
                self._reset_relation_cache()

                return {"success": True, "type": "relation", "hash": data.hash}
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Restore failed: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/api/memory/reinforce")
        async def reinforce_memory(data: MemoryActionRequest):
            """强化记忆 (Reset decay)"""
            if "_" not in data.id: raise HTTPException(400, "Invalid ID format")
            s, t = data.id.split("_", 1) 
            
            if not self.plugin.graph_store: raise HTTPException(503, "Graph store missing")
            
            try:
                gst = self.plugin.graph_store
                s_idx = gst._node_to_idx.get(gst._canonicalize(s))
                t_idx = gst._node_to_idx.get(gst._canonicalize(t))
                
                if s_idx is not None and t_idx is not None:
                     hashes = gst._edge_hash_map.get((s_idx, t_idx), set())
                     if hashes:
                         self.plugin.metadata_store.reinforce_relations(list(hashes))
                
                # 稍微提升权重
                self.plugin.graph_store.update_edge_weight(s, t, 0.1) 
                self.plugin.graph_store.save()
                
                return {"success": True}
            except Exception as e:
                logger.error(f"Reinforce failed: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/api/memory/freeze")
        async def freeze_memory(data: MemoryActionRequest):
            """手动冷冻记忆"""
            if "_" not in data.id: raise HTTPException(400, "Invalid ID format")
            s, t = data.id.split("_", 1)
            
            if not self.plugin.graph_store: raise HTTPException(503, "Graph store missing")

            try:
                gst = self.plugin.graph_store
                s_idx = gst._node_to_idx.get(gst._canonicalize(s))
                t_idx = gst._node_to_idx.get(gst._canonicalize(t))
                
                # 1. 在元数据中标记为不活跃
                if s_idx is not None and t_idx is not None:
                     hashes = gst._edge_hash_map.get((s_idx, t_idx), set())
                     if hashes:
                         self.plugin.metadata_store.mark_relations_inactive(list(hashes))
                
                # 2. 在图中停用 (移除边但保留映射)
                gst.deactivate_edges([(s, t)])
                gst.save()
                
                return {"success": True}
            except Exception as e:
                 logger.error(f"Freeze failed: {e}")
                 raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/api/memory/protect")
        async def protect_memory(data: MemoryProtectRequest):
            """设置保护 (Pin/TTL)"""
            if "_" not in data.id: raise HTTPException(400, "Invalid ID format")
            s, t = data.id.split("_", 1)
            
            if not self.plugin.graph_store: raise HTTPException(503, "Graph store missing")

            try:
                gst = self.plugin.graph_store
                s_idx = gst._node_to_idx.get(gst._canonicalize(s))
                t_idx = gst._node_to_idx.get(gst._canonicalize(t))
                
                if s_idx is not None and t_idx is not None:
                     hashes = gst._edge_hash_map.get((s_idx, t_idx), set())
                     if hashes:
                         h_list = list(hashes)
                         is_pinned = (data.type == "pin")
                         ttl = data.duration * 3600 if data.type == "ttl" else 0
                         
                         self.plugin.metadata_store.protect_relations(h_list, is_pinned=is_pinned, ttl_seconds=ttl)
                
                return {"success": True}
            except Exception as e:
                 logger.error(f"Protect failed: {e}")
                 raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/api/person_profile/query")
        async def query_person_profile(data: PersonProfileQueryRequest):
            """查询人物画像（自动画像 + 手工覆盖结果）。"""
            if not bool(self.plugin.get_config("person_profile.enabled", True)):
                raise HTTPException(status_code=400, detail="人物画像功能未启用")
            if self.plugin.metadata_store is None:
                raise HTTPException(status_code=503, detail="Metadata store not initialized")
            try:
                service = _build_person_profile_service()
                ttl_minutes = float(self.plugin.get_config("person_profile.profile_ttl_minutes", 360))
                ttl_seconds = max(60.0, ttl_minutes * 60.0)
                result = await service.query_person_profile(
                    person_id=str(data.person_id or "").strip(),
                    person_keyword=str(data.person_keyword or "").strip(),
                    top_k=max(4, int(data.top_k or 12)),
                    ttl_seconds=ttl_seconds,
                    force_refresh=bool(data.force_refresh),
                    source_note="webui:person_profile_query",
                )
                if not result.get("success", False):
                    raise HTTPException(status_code=400, detail=result.get("error", "人物画像查询失败"))
                return result
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Person profile query failed: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get("/api/person_profile/list")
        async def list_person_profile_candidates(
            keyword: str = Query("", description="关键词（匹配 person_name/nickname/user_id/person_id/group_nick_name）"),
            page: int = Query(1, ge=1, description="页码，从1开始"),
            page_size: int = Query(20, ge=1, le=100, description="每页数量"),
        ):
            """获取人物列表（支持关键词与分页）。"""
            try:
                kw = str(keyword or "").strip()
                conn = self.plugin.metadata_store._conn if self.plugin.metadata_store is not None else None
                if conn is None:
                    raise HTTPException(status_code=503, detail="Metadata store not initialized")
                cursor = conn.cursor()

                where_sql = ""
                params: List[Any] = []
                if kw:
                    where_sql = (
                        "WHERE person_name LIKE ? OR nickname LIKE ? OR user_id LIKE ? "
                        "OR person_id LIKE ? OR group_nick_name LIKE ?"
                    )
                    like_kw = f"%{kw}%"
                    params.extend([like_kw, like_kw, like_kw, like_kw, like_kw])

                cursor.execute(
                    f"SELECT COUNT(*) FROM person_registry {where_sql}",
                    tuple(params),
                )
                total = int(cursor.fetchone()[0] or 0)
                offset = (int(page) - 1) * int(page_size)

                cursor.execute(
                    f"""
                    SELECT person_id, person_name, nickname, user_id, platform, group_nick_name, last_know
                    FROM person_registry
                    {where_sql}
                    ORDER BY last_know DESC, updated_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    tuple(params + [int(page_size), int(offset)]),
                )
                rows = cursor.fetchall()

                items: List[Dict[str, Any]] = []
                for row in rows:
                    pid = str(row[0] or "").strip()
                    person_name = str(row[1] or "").strip()
                    nickname = str(row[2] or "").strip()
                    user_id = str(row[3] or "").strip()
                    aliases = _parse_group_nicks(row[5])

                    has_snapshot = False
                    has_override = False
                    latest_profile_updated_at = None
                    if self.plugin.metadata_store is not None and pid:
                        snapshot = self.plugin.metadata_store.get_latest_person_profile_snapshot(pid)
                        override = self.plugin.metadata_store.get_person_profile_override(pid)
                        has_snapshot = snapshot is not None
                        has_override = override is not None and bool(str(override.get("override_text", "")).strip())
                        if has_override:
                            latest_profile_updated_at = override.get("updated_at")
                        elif has_snapshot:
                            latest_profile_updated_at = snapshot.get("updated_at")

                    display_name = person_name or nickname or user_id or pid
                    items.append(
                        {
                            "person_id": pid,
                            "display_name": display_name,
                            "person_name": person_name,
                            "nickname": nickname,
                            "user_id": user_id,
                            "platform": str(row[4] or ""),
                            "aliases": aliases,
                            "last_know": row[6],
                            "has_snapshot": has_snapshot,
                            "has_override": has_override,
                            "latest_profile_updated_at": latest_profile_updated_at,
                        }
                    )

                return {
                    "success": True,
                    "keyword": kw,
                    "page": int(page),
                    "page_size": int(page_size),
                    "total": int(total),
                    "items": items,
                }
            except Exception as e:
                logger.error(f"List person profile candidates failed: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/api/person_profile/override")
        async def save_person_profile_override(data: PersonProfileOverrideUpsertRequest):
            """保存/更新人物画像手工覆盖。"""
            if not bool(self.plugin.get_config("person_profile.enabled", True)):
                raise HTTPException(status_code=400, detail="人物画像功能未启用")
            if self.plugin.metadata_store is None:
                raise HTTPException(status_code=503, detail="Metadata store not initialized")
            try:
                service = _build_person_profile_service()
                resolved_pid = _resolve_person_id_for_web(service, data.person_id)
                if not resolved_pid:
                    raise HTTPException(status_code=400, detail="person_id 不能为空")

                override = self.plugin.metadata_store.set_person_profile_override(
                    person_id=resolved_pid,
                    override_text=str(data.override_text or ""),
                    updated_by=str(data.updated_by or "webui"),
                    source="webui",
                )
                ttl_minutes = float(self.plugin.get_config("person_profile.profile_ttl_minutes", 360))
                ttl_seconds = max(60.0, ttl_minutes * 60.0)
                merged = await service.query_person_profile(
                    person_id=resolved_pid,
                    top_k=12,
                    ttl_seconds=ttl_seconds,
                    force_refresh=False,
                    source_note="webui:person_profile_override",
                )
                return {
                    "success": True,
                    "person_id": resolved_pid,
                    "override": override,
                    "profile": merged,
                }
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Save person profile override failed: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.delete("/api/person_profile/override")
        async def delete_person_profile_override(data: PersonProfileOverrideDeleteRequest):
            """清除人物画像手工覆盖。"""
            if self.plugin.metadata_store is None:
                raise HTTPException(status_code=503, detail="Metadata store not initialized")
            try:
                service = _build_person_profile_service()
                resolved_pid = _resolve_person_id_for_web(service, data.person_id)
                if not resolved_pid:
                    raise HTTPException(status_code=400, detail="person_id 不能为空")

                deleted = self.plugin.metadata_store.delete_person_profile_override(resolved_pid)
                return {"success": True, "person_id": resolved_pid, "deleted": bool(deleted)}
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Delete person profile override failed: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/api/save")
        async def manual_save():
            """手动保存所有数据到磁盘。"""
            try:
                saved_components = []
                save_all = getattr(self.plugin, "save_all", None)

                if callable(save_all):
                    if inspect.iscoroutinefunction(save_all):
                        await save_all()
                    else:
                        await asyncio.to_thread(save_all)
                    if self.plugin.graph_store is not None:
                        saved_components.append("graph_store")
                    if self.plugin.vector_store is not None:
                        saved_components.append("vector_store")
                else:
                    tasks = []
                    if self.plugin.graph_store is not None:
                        tasks.append(asyncio.to_thread(self.plugin.graph_store.save))
                        saved_components.append("graph_store")
                    if self.plugin.vector_store is not None:
                        tasks.append(asyncio.to_thread(self.plugin.vector_store.save))
                        saved_components.append("vector_store")
                    if tasks:
                        await asyncio.gather(*tasks)

                logger.info(f"手动保存完成: {saved_components}")
                return {"success": True, "saved": saved_components}
            except Exception as exc:
                logger.error(f"Manual save failed: {exc}")
                raise HTTPException(status_code=500, detail=str(exc))

        @self.app.get("/api/config")
        async def get_config():
            """获取配置（脱敏只读）"""
            base_payload = {
                "auto_save_enabled": self.plugin.get_config("advanced.enable_auto_save", True),
                "auto_save_interval": self.plugin.get_config("advanced.auto_save_interval_minutes", 5),
            }
            plugin_config = getattr(self.plugin, "config", None)
            if isinstance(plugin_config, dict):
                base_payload["config"] = mask_sensitive(plugin_config)
            return base_payload

        @self.app.post("/api/config/auto_save")
        async def set_auto_save(data: AutoSaveConfig):
            """设置自动保存开关（仅运行时生效）"""
            self.plugin._runtime_auto_save = data.enabled
            logger.info(f"自动保存已{'启用' if data.enabled else '禁用'}（运行时）")
            return {"success": True, "auto_save_enabled": data.enabled}

        @self.app.get("/import")
        async def import_page():
            """返回导入中心页面"""
            if not bool(self.plugin.get_config("web.import.enabled", False)):
                raise HTTPException(status_code=404, detail="导入功能未启用")
            html_path = Path(__file__).parent / "web" / "import.html"
            if html_path.exists():
                return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
            return HTMLResponse(content="<h1>Import UI Not Found</h1>", status_code=404)

        @self.app.get("/")
        async def index():
            """返回主页"""
            html_path = Path(__file__).parent / "web" / "index.html"
            if html_path.exists():
                return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
            return HTMLResponse(content="<h1>UI Not Found</h1>")

    def run(self):
        """运行服务器 (阻塞)。"""
        logger.info(f"正在启动 A_Memorix WebUI，地址：{self.host}:{self.port}")
        self._startup_error = None
        try:
            config = uvicorn.Config(self.app, host=self.host, port=self.port, log_level="info")
            self._server = uvicorn.Server(config)
            self._server.run()
        except BaseException as exc:
            self._startup_error = exc
            logger.error(f"A_Memorix WebUI 启动失败: {exc}", exc_info=True)
            raise

    def _wait_until_started(self, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + max(0.5, float(timeout))
        probe_host = self._probe_host(self.host)

        while time.monotonic() < deadline:
            if self._startup_error is not None:
                raise RuntimeError(f"A_Memorix WebUI 启动失败: {self._startup_error}") from self._startup_error

            server = self._server
            if server is not None:
                started = getattr(server, "started", None)
                if started is True:
                    return
                if started is None:
                    try:
                        with socket.create_connection((probe_host, self.port), timeout=0.2):
                            return
                    except OSError:
                        pass

            if self.server_thread and not self.server_thread.is_alive():
                raise RuntimeError("A_Memorix WebUI 启动线程已退出")

            time.sleep(0.05)

        if self._startup_error is not None:
            raise RuntimeError(f"A_Memorix WebUI 启动失败: {self._startup_error}") from self._startup_error
        raise TimeoutError(f"A_Memorix WebUI 在 {timeout:.1f}s 内未完成启动 ({probe_host}:{self.port})")

    def start(self, wait: bool = False, timeout: float = 5.0):
        """在独立线程启动。"""
        if self.server_thread and self.server_thread.is_alive():
            if wait:
                self._wait_until_started(timeout=timeout)
            return

        self._startup_error = None
        self.server_thread = threading.Thread(target=self.run, daemon=True, name=f"memorix-webui-{self.port}")
        self.server_thread.start()

        if wait:
            self._wait_until_started(timeout=timeout)

    def stop(self):
        """停止服务器"""
        if self._server:
            self._server.should_exit = True
        if self.server_thread:
            self.server_thread.join(timeout=2)
            logger.info("A_Memorix WebUI 已停止")
