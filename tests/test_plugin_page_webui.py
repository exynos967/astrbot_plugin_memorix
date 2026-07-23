import re
from pathlib import Path

import pytest
from astrbot_plugin_memorix.memorix.webui.plugin_page_bridge import PluginPageWebUIBridge

ROOT = Path(__file__).resolve().parents[1]
VUE_PAGE = ROOT / "pages" / "memorix" / "index.html"
FRONTEND_SRC = ROOT / "frontend" / "src"
SIDEBAR_SOURCE = FRONTEND_SRC / "components" / "shell" / "Sidebar.vue"


def test_plugin_page_embeds_dashboard_bridge() -> None:
    """memorix 页产物可达性断言。

    验证 Vite 构建产物的关键结构：
    - index.html 存在且为 Vite 产物（引用相对路径的 JS/CSS chunk + #app 挂载点）
    - 显式注入 AstrBot 桥接 SDK（运行时由 AstrBot rewrite 为带鉴权 URL）
    - 资源路径为相对路径（base:'./'，不出现 / 开头绝对资源路径）
    - vis-network 独立 chunk 懒加载（不内联进主 bundle，控体积）
    """
    assert VUE_PAGE.exists(), "memorix 产物 index.html 缺失，请先执行 npm run build"
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
    """memorix 产物引用的资源文件实际存在（无悬空引用）。"""
    assets_dir = VUE_PAGE.parent / "assets"
    assert assets_dir.is_dir(), "memorix/assets 目录缺失"
    html = VUE_PAGE.read_text(encoding="utf-8")

    refs = re.findall(r'(?:src|href)="(\./assets/[^"]+)"', html)
    assert refs, "index.html 未引用任何 assets 资源"
    for ref in refs:
        asset = VUE_PAGE.parent / ref
        assert asset.exists(), f"产物引用的资源缺失：{ref}"


def test_plugin_page_avoids_sandbox_blocked_native_modals() -> None:
    """AstrBot sandbox 未开放 allow-modals，源码和发布产物不得调用原生弹窗。"""

    pattern = re.compile(r"window\.(?:alert|confirm|prompt)\s*\(")
    source_offenders = []
    for source in FRONTEND_SRC.rglob("*"):
        if source.suffix not in {".ts", ".vue"}:
            continue
        if pattern.search(source.read_text(encoding="utf-8")):
            source_offenders.append(source.relative_to(ROOT).as_posix())

    assert not source_offenders, f"源码仍调用 sandbox 禁止的原生弹窗：{source_offenders}"

    assets_dir = VUE_PAGE.parent / "assets"
    bundle_offenders = []
    for bundle in assets_dir.glob("*.js"):
        if pattern.search(bundle.read_text(encoding="utf-8")):
            bundle_offenders.append(bundle.name)

    assert not bundle_offenders, f"发布产物仍调用 sandbox 禁止的原生弹窗：{bundle_offenders}"


def test_plugin_page_sidebar_is_collapsible() -> None:
    """侧栏应提供可访问的折叠控件，且发布产物包含对应交互。"""

    source = SIDEBAR_SOURCE.read_text(encoding="utf-8")
    assert "sidebarCollapsed" in source
    assert 'aria-expanded="!sidebarCollapsed"' in source
    assert "ui.toggleSidebar()" in source

    assets_dir = VUE_PAGE.parent / "assets"
    bundles = list(assets_dir.glob("*.js"))
    assert bundles, "memorix/assets 下无 JS bundle"
    bundle_text = "\n".join(bundle.read_text(encoding="utf-8") for bundle in bundles)
    assert "展开侧栏" in bundle_text
    assert "折叠侧栏" in bundle_text


def test_plugin_page_single_bundle_no_subchunks() -> None:
    """单 bundle：禁用代码分割，所有 JS/CSS 内联进单文件（无子 chunk）。

    AstrBot iframe sandbox 不带 allow-same-origin → iframe origin=null → 动态 import/
    modulepreload/CSS 子 chunk 请求被浏览器 CORS 拦截（ACAO:* 对 origin=null 不生效），
    唯独 graph 页依赖动态 import vis/GraphView 子 chunk → 加载失败画布空。
    根治：vite.config.ts inlineDynamicImports:true + cssCodeSplit:false，全打成单 bundle，
    HTML <script>/<link> 同源直接加载不走 fetch CORS。
    本断言确保不回退到多 chunk（多 chunk 会在 sandbox 下 CORS 失败）。
    """
    assets_dir = VUE_PAGE.parent / "assets"
    js_files = list(assets_dir.glob("*.js"))
    css_files = list(assets_dir.glob("*.css"))
    # 单 bundle：恰好 1 个 JS + 1 个 CSS（index-*.js / style-*.css）
    assert len(js_files) == 1, (
        f"期望单 JS bundle，实际 {len(js_files)} 个 JS 文件：{[f.name for f in js_files]}。"
        "多 chunk 在 AstrBot sandbox iframe(origin=null) 下会 CORS 失败，请确认 "
        "vite.config.ts inlineDynamicImports:true 未被移除。"
    )
    assert len(css_files) == 1, (
        f"期望单 CSS bundle，实际 {len(css_files)} 个 CSS 文件。"
        "多 CSS chunk 同样 CORS 失败，请确认 cssCodeSplit:false。"
    )
    # 主 bundle 必须内联 vis（含 Network 构造），证明 vis 已打进单 bundle
    main_js = js_files[0].read_text(encoding="utf-8")
    assert "forceAtlas2Based" in main_js or "vis-network" in main_js, (
        "主 bundle 未内联 vis-network，可能仍走动态 import（会 CORS 失败）。"
    )


def test_plugin_page_chunks_use_spaced_esm_imports() -> None:
    """跨 chunk 静态 import 必须「带空格」形式（vite.config.ts astrbotEsmImportCompat 回归）。

    AstrBot 的 _JS_MODULE_FROM_RE 要求 `import\\s+`（import 后至少一空格）才 rewrite 注
    asset_token。Vite/rollup minified 默认产出 `import{a,b}from"./x"`（零空格）→ 正则不匹配
    → 跨 chunk 静态 import 不被 rewrite → 资源 URL 无 token → 401 → 图谱页空白
    （仅 graph view 真·动态 import 触发跨 chunk 引用，故唯独图谱受影响）。
    astrbotEsmImportCompat 插件在 generateBundle 阶段补回空格修复此缺陷。本断言防止
    该插件被误删/失效后回退到 401 状态。
    """
    assets_dir = VUE_PAGE.parent / "assets"
    js_chunks = list(assets_dir.glob("*.js"))
    assert js_chunks, "memorix/assets 下无 JS chunk"
    for chunk in js_chunks:
        code = chunk.read_text(encoding="utf-8")
        # 不得残留零空格的 import{...}from"/export{...}from" 静态模块引用
        assert not re.search(r'import\{[^}]*\}from"', code), (
            f"{chunk.name} 残留 minified `import{{...}}from\"`（无空格），"
            "AstrBot 不会 rewrite 注 token → 跨 chunk 加载 401。请确认 astrbotEsmImportCompat 插件生效。"
        )
        assert not re.search(r'export\{[^}]*\}from"', code), (
            f"{chunk.name} 残留 minified `export{{...}}from\"`（无空格），同上会导致 401。"
        )


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
