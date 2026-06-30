"""issue #22 回归：embedding `dimensions` 参数兼容性测试。

SiliconFlow 等 OpenAI 兼容服务商不支持请求体的 `dimensions` 参数，携带即返回
`20015 parameter is invalid`，导致记忆写入阶段全部失败。

修复策略：EmbeddingAPIAdapter 默认假设支持 `dimensions`（保持旧行为），但
`_detect_dimension` 探测时若发现 provider 拒绝该参数（带 dimensions 失败、不带成功），
则置 `_supports_dimensions=False`，后续 `encode` 永不再发送该参数。

本测试用 mock 的 `_request_embeddings` 模拟两种 provider，验证：
1. SiliconFlow 式 provider（带 dimensions 抛错、不带成功）→ encode 不发 dimensions
2. OpenAI 式 provider（带 dimensions 成功）→ encode 仍发 dimensions
3. 默认值 True，未探测时保持旧行为（发 dimensions）
"""
import asyncio
import sys
import types
from pathlib import Path
from typing import List, Optional, Union

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "astrbot_plugin_memorix") not in sys.path:
    sys.path.insert(0, str(ROOT / "astrbot_plugin_memorix"))


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
            return lambda *a, **k: None

    api_mod.logger = _Logger()
    path_mod.get_astrbot_data_path = lambda *a, **k: str(ROOT / ".test-astrbot-data")
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

from memorix.core.embedding.api_adapter import EmbeddingAPIAdapter  # noqa: E402


def _make_adapter(dim: int = 1024) -> EmbeddingAPIAdapter:
    """构造一个不走网络的 adapter（base_url/model 仅满足构造校验）。"""
    return EmbeddingAPIAdapter(
        batch_size=8,
        max_concurrent=2,
        default_dimension=dim,
        base_url="https://example.test/v1",
        api_key="EMPTY",
        openai_model="test-model",
    )


def _patch_request(adapter: EmbeddingAPIAdapter, siliconflow_like: bool):
    """替换 _request_embeddings 为 mock。

    siliconflow_like=True  → 模拟 SiliconFlow：带 dimensions 抛 20015，不带返回原生向量
    siliconflow_like=False → 模拟 OpenAI：带/不带都成功，带 dimensions 返回截断维度
    """
    calls: List[dict] = []

    async def fake_request(inputs: Union[str, List[str]], dimensions: Optional[int] = None):
        calls.append({"inputs": inputs, "dimensions": dimensions})
        if siliconflow_like and dimensions is not None:
            raise RuntimeError("parameter is invalid (20015)")
        # 返回固定 1024 维向量（input 条数 = 1）
        return [[0.01] * 1024]

    adapter._request_embeddings = fake_request  # type: ignore[assignment]
    return calls


def test_siliconflow_like_provider_drops_dimensions():
    """SiliconFlow 式 provider：带 dimensions 探测失败 → 标记不支持 → encode 不发。"""
    adapter = _make_adapter()
    calls = _patch_request(adapter, siliconflow_like=True)

    asyncio.run(adapter._detect_dimension())

    assert adapter._supports_dimensions is False, "探测失败后应置 False"
    assert adapter._dimension == 1024

    # encode 走业务路径：不应再发送 dimensions
    calls.clear()
    out = asyncio.run(adapter.encode("hello"))
    assert isinstance(out, np.ndarray)
    sent_dims = [c["dimensions"] for c in calls]
    assert all(d is None for d in sent_dims), (
        f"SiliconFlow 式 provider 不应再发送 dimensions，实际: {sent_dims}"
    )


def test_openai_like_provider_keeps_dimensions():
    """OpenAI 式 provider：带 dimensions 探测成功 → 保持 True → encode 继续发。"""
    adapter = _make_adapter()
    calls = _patch_request(adapter, siliconflow_like=False)

    asyncio.run(adapter._detect_dimension())

    assert adapter._supports_dimensions is True

    calls.clear()
    asyncio.run(adapter.encode("hello"))
    sent_dims = [c["dimensions"] for c in calls]
    assert all(d == 1024 for d in sent_dims), (
        f"OpenAI 式 provider 应继续发送 dimensions=1024，实际: {sent_dims}"
    )


def test_default_supports_dimensions_before_probe():
    """未探测时默认 True（保持旧行为，不退步）。"""
    adapter = _make_adapter()
    assert adapter._supports_dimensions is True


def test_encode_detects_dimensions_support_without_dimension_probe():
    """方案 A 核心：即便维度探测（auto_detect）关闭，encode 仍独立探测 dimensions 兼容性。

    模拟 SiliconFlow：_detect_dimension 不被调用，encode 入口应自行触发
    _detect_dimensions_support，识别出 provider 不支持 dimensions 后不再发送。
    这是与维度探测解耦的关键——auto_detect_dimension 关闭时 issue #22 修复仍生效。
    """
    adapter = _make_adapter()
    calls = _patch_request(adapter, siliconflow_like=True)
    # 不调 _detect_dimension，直接 encode（模拟 auto_detect 关闭路径）
    out = asyncio.run(adapter.encode("hello"))
    assert isinstance(out, np.ndarray)

    assert adapter._dimensions_support_detected is True
    assert adapter._supports_dimensions is False, "encode 应自行探测出 provider 不支持 dimensions"
    # encode 写入阶段不应发 dimensions
    write_dims = [c["dimensions"] for c in calls if c["inputs"] != "dimension_probe"]
    assert write_dims, "应有写入请求"
    assert all(d is None for d in write_dims), (
        f"SiliconFlow 式 provider 探测后 encode 不应发 dimensions，实际: {write_dims}"
    )


if __name__ == "__main__":
    test_siliconflow_like_provider_drops_dimensions()
    test_openai_like_provider_keeps_dimensions()
    test_default_supports_dimensions_before_probe()
    test_encode_detects_dimensions_support_without_dimension_probe()
    print("OK")
