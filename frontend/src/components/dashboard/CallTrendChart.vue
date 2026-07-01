<script setup lang="ts">
// 调用趋势图：近 N 小时查询/工具调用频次（柱 + 折线 + 面积）。
// 纯展示组件，几何计算从 legacy renderCallTrend（index.html 行 2943-3002）忠实移植。
// 数据来自 dashboard status services.query.{trend_*}。
// 宽度自适应容器，高度按宽高比 4:1 计算（宽屏可用作 flex:1 撑满视口）。
import { computed, onBeforeUnmount, onMounted, ref, shallowRef } from "vue";
import type { TrendBucket } from "@/services/configApi";

const props = withDefaults(
  defineProps<{
    buckets?: TrendBucket[];
    totalCount?: number;
    bucketSeconds?: number;
    seconds?: number;
  }>(),
  { buckets: () => [], bucketSeconds: 300, seconds: 7200 },
);

const PAD = { left: 18, right: 12, top: 14, bottom: 24 };
const chartEl = ref<HTMLElement | null>(null);
const containerWidth = shallowRef(640);
const containerHeight = shallowRef(148);
let resizeObserver: ResizeObserver | null = null;

onMounted(() => {
  if (chartEl.value && typeof ResizeObserver !== "undefined") {
    resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const w = entry.contentRect.width;
        const h = entry.contentRect.height;
        if (w > 0 && w !== containerWidth.value) containerWidth.value = w;
        if (h > 0 && h !== containerHeight.value) containerHeight.value = h;
      }
    });
    resizeObserver.observe(chartEl.value);
  }
});

onBeforeUnmount(() => {
  resizeObserver?.disconnect();
  resizeObserver = null;
});

const WIDTH = computed(() => Math.max(200, containerWidth.value));
/** 高度：优先容器高度（flex:1 撑高），否则按宽高比回退，最低 120px。 */
const HEIGHT = computed(() => Math.max(120, containerHeight.value > 40 ? containerHeight.value : Math.round(containerWidth.value / 4)));

function fmtTime(sec: number): string {
  return new Date(sec * 1000).toLocaleTimeString("zh-CN", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
  });
}

const compact = computed(() =>
  (props.buckets || []).map((item) => ({
    start: Number(item.start || 0),
    end: Number(item.end || 0),
    total: Number(item.total || 0),
  })),
);

const total = computed(() =>
  Number(props.totalCount ?? compact.value.reduce((sum, item) => sum + item.total, 0)),
);

const bucketSeconds = computed(() => Number(props.bucketSeconds || 300));
const spanSeconds = computed(
  () => Number(props.seconds || compact.value.length * bucketSeconds.value || 7200),
);

const meta = computed(() => {
  const hours = Math.max(1, Math.round(spanSeconds.value / 3600));
  const minutes = Math.max(1, Math.round(bucketSeconds.value / 60));
  return `近 ${hours} 小时 · ${minutes} 分钟区间 · ${total.value} 次`;
});

const empty = computed(() => !compact.value.length);

const maxValue = computed(() => Math.max(1, ...compact.value.map((item) => item.total)));

const innerW = computed(() => WIDTH.value - PAD.left - PAD.right);
const innerH = computed(() => HEIGHT.value - PAD.top - PAD.bottom);

interface Point {
  x: number;
  y: number;
  value: number;
  start: number;
}

const points = computed<Point[]>(() => {
  const len = compact.value.length;
  return compact.value.map((item, index) => {
    const x =
      PAD.left + (len === 1 ? innerW.value : (index / (len - 1)) * innerW.value);
    const y = PAD.top + innerH.value - (item.total / maxValue.value) * innerH.value;
    return { x, y, value: item.total, start: item.start };
  });
});

const linePoints = computed(() =>
  points.value.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" "),
);

const areaPoints = computed(
  () => `${PAD.left},${PAD.top + innerH.value} ${linePoints.value} ${PAD.left + innerW.value},${PAD.top + innerH.value}`,
);

const barWidth = computed(() => Math.max(2, innerW.value / Math.max(1, compact.value.length) - 3));

interface Bar {
  x: number;
  y: number;
  w: number;
  h: number;
}

const bars = computed<Bar[]>(() =>
  points.value.map((p) => ({
    x: p.x - barWidth.value / 2,
    y: p.y,
    w: barWidth.value,
    h: PAD.top + innerH.value - p.y,
  })),
);

const dots = computed(() => points.value.filter((p) => p.value > 0));

const footStart = computed(() => (compact.value.length ? fmtTime(compact.value[0].start) : "-"));
const footEnd = computed(() =>
  compact.value.length ? fmtTime(compact.value[compact.value.length - 1].end) : "-",
);
</script>

<template>
  <div class="band chart-band">
    <div class="panel-title">
      <h2>调用趋势</h2>
      <span class="section-label">{{ meta }}</span>
    </div>
    <div ref="chartEl" class="call-chart">
      <div v-if="empty" class="empty">近 2 小时暂无查询/工具调用记录</div>
      <template v-else>
        <svg
          :viewBox="`0 0 ${WIDTH} ${HEIGHT}`"
          width="100%"
          height="100%"
          preserveAspectRatio="xMidYMid meet"
          role="img"
          aria-label="近 2 小时查询与工具调用趋势"
        >
          <line class="axis" :x1="PAD.left" :y1="PAD.top + innerH" :x2="PAD.left + innerW" :y2="PAD.top + innerH" />
          <line class="axis" :x1="PAD.left" :y1="PAD.top" :x2="PAD.left" :y2="PAD.top + innerH" />
          <rect
            v-for="(bar, i) in bars"
            :key="`bar-${i}`"
            class="bar"
            :x="bar.x.toFixed(1)"
            :y="bar.y.toFixed(1)"
            :width="bar.w.toFixed(1)"
            :height="bar.h.toFixed(1)"
            rx="3"
          />
          <polygon class="area" :points="areaPoints" />
          <polyline class="line" :points="linePoints" />
          <circle
            v-for="(dot, i) in dots"
            :key="`dot-${i}`"
            class="dot"
            :cx="dot.x.toFixed(1)"
            :cy="dot.y.toFixed(1)"
            r="3.5"
          >
            <title>{{ fmtTime(dot.start) }} · {{ dot.value }} 次</title>
          </circle>
        </svg>
        <div class="chart-foot">
          <span>{{ footStart }}</span>
          <span>峰值 {{ maxValue }} 次 / 区间</span>
          <span>{{ footEnd }}</span>
        </div>
      </template>
    </div>
  </div>
</template>
