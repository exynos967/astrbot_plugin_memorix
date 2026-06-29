from pathlib import Path

import pytest
from astrbot_plugin_memorix.memorix.webui.plugin_page_bridge import PluginPageWebUIBridge

ROOT = Path(__file__).resolve().parents[1]
VUE_PAGE = ROOT / "pages" / "memorix-vue" / "index.html"


def test_plugin_page_embeds_dashboard_bridge() -> None:
    """memorix-vue 产物可达性断言（P9：替代原 legacy memorix/index.html 断言）。

    验证 Vite 构建产物的关键结构：
    - index.html 存在且为 Vite 产物（引用相对路径的 JS/CSS chunk + #app 挂载点）
    - 显式注入 AstrBot 桥接 SDK（与 legacy 一致，运行时由 AstrBot rewrite 为带鉴权 URL）
    - 资源路径为相对路径（base:'./'，不出现 / 开头绝对资源路径）
    - vis-network 独立 chunk 懒加载（不内联进主 bundle，控体积）
    """
    assert VUE_PAGE.exists(), "memorix-vue 产物 index.html 缺失，请先执行 npm run build"
    html = VUE_PAGE.read_text(encoding="utf-8")

    # 桥接 SDK 显式注入（services/api.ts 依赖 window.AstrBotPluginPage）
    assert "/api/plugin/page/bridge-sdk.js" in html

    # Vite 产物结构
    assert '<div id="app"></div>' in html
    assert 'crossorigin src="./assets/' in html  # JS chunk 相对路径
    assert 'rel="stylesheet" crossorigin href="./assets/' in html  # CSS chunk 相对路径

    # 资源路径必须是相对路径（base:'./' 硬约束；/ 开头绝对路径会被 AstrBot 拒绝）
    assert 'src="./assets/' in html
    assert 'href="./assets/' in html
    assert 'src="/assets/' not in html
    assert 'href="/assets/' not in html


def test_plugin_page_assets_exist() -> None:
    """memorix-vue 产物引用的资源文件实际存在（无悬空引用）。"""
    assets_dir = VUE_PAGE.parent / "assets"
    assert assets_dir.is_dir(), "memorix-vue/assets 目录缺失"
    html = VUE_PAGE.read_text(encoding="utf-8")

    # 收集 index.html 引用的所有 ./assets/ 资源，逐一断言存在
    import re

    refs = re.findall(r'(?:src|href)="(\./assets/[^"]+)"', html)
    assert refs, "index.html 未引用任何 assets 资源"
    for ref in refs:
        asset = VUE_PAGE.parent / ref
        assert asset.exists(), f"产物引用的资源缺失：{ref}"


def test_plugin_page_vis_chunk_isolated() -> None:
    """vis-network 单独 chunk 懒加载：主 bundle 不内联 vis，控体积（P8 硬约束）。

    主入口 chunk（index-*.js）由 GraphView 动态 import vis，配合 vite manualChunks.vis
    把 vis-network+vis-data 归独立 chunk。这里断言 assets 下存在独立的 vis-*.js chunk。
    """
    assets_dir = VUE_PAGE.parent / "assets"
    vis_chunks = list(assets_dir.glob("vis-*.js"))
    assert vis_chunks, "未生成独立 vis chunk，manualChunks.vis 配置可能失效"
    # vis chunk 应有体积（非空占位）
    assert any(p.stat().st_size > 1000 for p in vis_chunks), "vis chunk 体积异常"


def test_plugin_page_bridge_rejects_unexpected_urls() -> None:
    assert PluginPageWebUIBridge._normalize_url("/v1/dashboard/status?x=1") == "/v1/dashboard/status?x=1"
    assert PluginPageWebUIBridge._normalize_url("/api/graph") == "/api/graph"

    with pytest.raises(ValueError):
        PluginPageWebUIBridge._normalize_url("https://example.test/api/graph")

    with pytest.raises(ValueError):
        PluginPageWebUIBridge._normalize_url("/api/plug/other")

    with pytest.raises(ValueError):
        PluginPageWebUIBridge._normalize_url("/../../etc/passwd")


def test_plugin_page_bridge_scope_helpers() -> None:
    target, scope = PluginPageWebUIBridge._extract_scope_override(
        "/api/graph?exclude_leaf=true&_scope=aiocqhttp%3Agroup%3A123&density=1"
    )

    assert target == "/api/graph?exclude_leaf=true&density=1"
    assert scope == "aiocqhttp:group:123"
    assert PluginPageWebUIBridge._format_scope_label("aiocqhttp:group:123") == "aiocqhttp:123"
    assert PluginPageWebUIBridge._format_scope_label("telegram:group:-100") == "telegram:-100"
