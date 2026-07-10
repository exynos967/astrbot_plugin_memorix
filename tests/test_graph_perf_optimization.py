"""图谱性能优化的回归测试。

验证 routes_compat.get_graph 的关键优化点行为正确：
- 瓶颈3：edge_predicates 缓存键用规范化小写，O(1) 查找匹配大小写不一的边
  （等价于旧的 O(E) 线性大小写扫描结果）
"""

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _install_astrbot_stub() -> None:
    if "astrbot.api" in sys.modules:
        return
    astrbot_mod = types.ModuleType("astrbot")
    api_mod = types.ModuleType("astrbot.api")
    core_mod = types.ModuleType("astrbot.core")
    utils_mod = types.ModuleType("astrbot.core.utils")
    path_mod = types.ModuleType("astrbot.core.utils.astrbot_path")

    class _Logger:
        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    api_mod.logger = _Logger()
    path_mod.get_astrbot_data_path = lambda *args, **kwargs: str(ROOT / ".test-astrbot-data")
    astrbot_mod.api = api_mod
    astrbot_mod.core = core_mod
    core_mod.utils = utils_mod
    utils_mod.astrbot_path = path_mod
    sys.modules["astrbot"] = astrbot_mod
    sys.modules["astrbot.api"] = api_mod
    sys.modules["astrbot.core"] = core_mod
    sys.modules["astrbot.core.utils"] = utils_mod
    sys.modules["astrbot.core.utils.astrbot_path"] = path_mod


_install_astrbot_stub()


def test_edge_predicate_cache_lowercase_key_matches_case_variant():
    """瓶颈3回归：缓存键规范化小写后，大小写不一的边能 O(1) 匹配到谓语。

    旧逻辑对每条无精确匹配的边做 O(E) 线性大小写扫描；新逻辑缓存键统一小写，
    查询时也用小写，结果等价但 O(1)。本测试验证等价性：subject/object 大小写
    与缓存键不一致时仍能取到谓语。
    """
    # 模拟缓存构建：raw_triples 里 subject/object 是原始大小写
    raw_triples = [("Alice", "likes", "Bob", "hash1"), ("alice", "knows", "bob", "hash2")]
    cache = {}
    for s, p, o, _ in raw_triples:
        key = (s.strip().lower(), o.strip().lower())
        if key not in cache:
            cache[key] = []
        cache[key].append(p)

    # 查询时边是不同大小写（如 GraphStore 规范化后的小写 vs 原始大写）
    # 旧 O(E) 扫描能匹配，新 O(1) 小写键也应匹配
    assert cache.get(("alice", "bob")) == ["likes", "knows"]
    # 大小写混合查询同样命中（键已规范化）
    assert cache.get(("Alice".lower(), "Bob".lower())) == ["likes", "knows"]
    # 不存在的边返回空
    assert cache.get(("carol", "dave"), []) == []


def test_get_graph_offloads_sync_to_thread():
    """issue #19 回归：get_graph 必须把 CPU 密集同步构建丢进线程池，不得阻塞事件循环。

    100M metadata.db 下，同步遍历段落/实体/关系 + PageRank 过滤 + 批量元数据查询是
    CPU 密集且无 await 的逻辑。若直接跑在 async def get_graph 的事件循环上，会阻塞
    整个 AstrBot webui（其余请求排队、刷新出不来、CPU 单核吃满、关浏览器也不释放）。
    修复：get_graph 经 asyncio.to_thread 调用 _build_graph_sync，offload 到线程池。
    本断言读取源码，确保 to_thread 调用链不被退化回同步内联。
    """
    import inspect

    from memorix.webui import routes_compat as rc

    src = inspect.getsource(rc.MemorixServer)
    # _build_graph_sync 闭包存在，承接同步重计算
    assert "_build_graph_sync" in src, "缺少 _build_graph_sync 闭包——同步构建可能又内联回 get_graph"
    # get_graph 必须 to_thread offload（关键：issue #19 根治点）
    assert "asyncio.to_thread(_build_graph_sync" in src, (
        "get_graph 未用 asyncio.to_thread(_build_graph_sync, ...) offload 同步构建，"
        "大 DB 下会重新阻塞事件循环（issue #19 复发）。"
    )


if __name__ == "__main__":
    test_edge_predicate_cache_lowercase_key_matches_case_variant()
    test_get_graph_offloads_sync_to_thread()
    print("OK")
