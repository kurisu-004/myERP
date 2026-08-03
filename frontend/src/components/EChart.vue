<!--
  EChart.vue

  ECharts 5 封装组件（基础设施层，仅负责挂载 + 同步生命周期）。
  - 模块化注册（echarts/core + use），不引入全量 bundle；
  - prop.option 全量替换（notMerge=true）→ 切视图时不残留 series / dataset；
  - ResizeObserver 监听 root 容器，自动响应父级尺寸变化；
  - onBeforeUnmount 主动 dispose()，canvas 必须在 unmount 时释放，否则内存泄漏。

  业务页只负责按数据组装 {option} 传进来；颜色 / 标签 / tooltip 等由调用方按 dataviz
  skill 规范自行决定，本组件不做任何视觉决策。
-->
<template>
  <div ref="rootRef" class="echart" :style="{ height: height }" />
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import * as echarts from 'echarts/core'
import {
  BarChart,
  LineChart,
  PieChart,
} from 'echarts/charts'
import {
  DatasetComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TitleComponent,
  ToolboxComponent,
  TooltipComponent,
} from 'echarts/components'
// ECharts 5.x：LabelLayout / UniversalTransition 不在 echarts/components 而在 features
// 子路径下。详见 https://echarts.apache.org/handbook/en/basics/import/
import { LabelLayout, UniversalTransition } from 'echarts/features'
import { CanvasRenderer } from 'echarts/renderers'
import type { ECharts, EChartsCoreOption } from 'echarts/core'

// 注册三大类：图表 / 组件 / 渲染器。绝对不要 import 'echarts'，
// 否则会把 ~900KB 全量 bundle 拉进来。
echarts.use([
  BarChart,
  LineChart,
  PieChart,
  DatasetComponent,
  GridComponent,
  LabelLayout,
  LegendComponent,
  MarkLineComponent,
  TitleComponent,
  ToolboxComponent,
  TooltipComponent,
  UniversalTransition,
  CanvasRenderer,
])

interface Props {
  /** 完整 ECharts 配置。可选响应式：父组件传 reactive object 时 watch 会自动重设。 */
  option: EChartsCoreOption
  /** CSS height。容器宽度跟随父级，由 ResizeObserver 触发 resize()。 */
  height?: string
  /** 显示 ECharts 内置 loading 蒙层。 */
  loading?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  height: '320px',
  loading: false,
})

const rootRef = ref<HTMLDivElement | null>(null)
// shallowRef：ECharts 实例内部持有 zrender 状态，不需要 deep reactive 包裹。
const chart = shallowRef<ECharts | null>(null)
let resizeObserver: ResizeObserver | null = null

function applyOption(c: ECharts, opt: EChartsCoreOption): void {
  // notMerge=true：切视图时彻底重设，旧 series / dataset / visualMap 全部清掉，
  // 避免"切换不同口径的同一类图"导致 legend 残留。
  c.setOption(opt, true)
}

function initChart(el: HTMLDivElement): void {
  const c = echarts.init(el, undefined, { renderer: 'canvas' })
  chart.value = c
  if (props.option) applyOption(c, props.option)
  if (props.loading) c.showLoading()
  resizeObserver = new ResizeObserver(() => c.resize())
  resizeObserver.observe(el)
}

onMounted(() => {
  const el = rootRef.value
  if (el) initChart(el)
})

watch(
  () => props.option,
  (next) => {
    const c = chart.value
    if (!c) return
    applyOption(c, next)
  },
  { deep: true },
)

watch(
  () => props.loading,
  (next) => {
    const c = chart.value
    if (!c) return
    if (next) {
      c.showLoading()
    } else {
      c.hideLoading()
    }
  },
)

onBeforeUnmount(() => {
  if (resizeObserver) {
    resizeObserver.disconnect()
    resizeObserver = null
  }
  if (chart.value) {
    chart.value.dispose()
    chart.value = null
  }
})
</script>

<style lang="scss" scoped>
.echart {
  width: 100%;
  min-width: 0;
}
</style>
