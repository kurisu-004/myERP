<!--
  Barcode.vue

  用 jsbarcode 渲染 SVG 条形码。
  默认格式 CODE 39（适用于字母+数字，例如 F1000 / L1234）。

  Props:
  - value: 必填，条形码内容
  - format: 条形码格式，默认 CODE39
  - width / height: 条宽 / 条高
  - displayValue: 是否显示底部文字
-->
<template>
  <svg ref="svgRef" class="barcode-svg" />
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import JsBarcode from 'jsbarcode'

interface Props {
  value: string
  format?: string
  width?: number
  height?: number
  displayValue?: boolean
}
const props = withDefaults(defineProps<Props>(), {
  format: 'CODE39',
  width: 2,
  height: 60,
  displayValue: true,
})

const svgRef = ref<SVGSVGElement | null>(null)

function render(): void {
  if (!svgRef.value || !props.value) return
  try {
    JsBarcode(svgRef.value, props.value, {
      format: props.format,
      width: props.width,
      height: props.height,
      displayValue: props.displayValue,
      margin: 8,
      fontSize: 14,
    })
  } catch (e) {
    console.warn('Barcode render failed:', (e as Error).message)
  }
}

onMounted(render)
watch(
  () => [props.value, props.format, props.width, props.height, props.displayValue],
  render,
)
</script>

<style lang="scss" scoped>
.barcode-svg {
  display: block;
  max-width: 100%;
  height: auto;
}
</style>