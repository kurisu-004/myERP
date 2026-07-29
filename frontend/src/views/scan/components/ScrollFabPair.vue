<template>
  <div class="scroll-fab-pair">
    <button
      class="scroll-btn"
      :disabled="atTop"
      aria-label="向上滚动"
      @pointerdown.prevent="onPressDown('up')"
      @pointerup.prevent="onPressUp"
      @pointerleave.prevent="onPressUp"
      @pointercancel.prevent="onPressUp"
      @contextmenu.prevent
    >
      <el-icon :size="28"><ArrowUp /></el-icon>
    </button>
    <button
      class="scroll-btn"
      :disabled="atBottom"
      aria-label="向下滚动"
      @pointerdown.prevent="onPressDown('down')"
      @pointerup.prevent="onPressUp"
      @pointerleave.prevent="onPressUp"
      @pointercancel.prevent="onPressUp"
      @contextmenu.prevent
    >
      <el-icon :size="28"><ArrowDown /></el-icon>
    </button>
  </div>
</template>

<script setup lang="ts">
import { watch, onMounted, onBeforeUnmount, shallowRef } from 'vue'
import { ArrowUp, ArrowDown } from '@element-plus/icons-vue'
import { useHoldToScroll } from '@/composables/useHoldToScroll'

const props = defineProps<{
  target: HTMLElement | null
}>()

const containerRef = shallowRef<HTMLElement | null>(props.target)

watch(() => props.target, (el) => {
  containerRef.value = el
  if (el) bindContainer()
})

const { atTop, atBottom, onPressDown, onPressUp, bindContainer, unbindContainer } =
  useHoldToScroll(containerRef)

onMounted(() => {
  bindContainer()
})

onBeforeUnmount(() => {
  unbindContainer()
})
</script>

<style scoped>
.scroll-fab-pair {
  position: fixed;
  right: 24px;
  top: 50%;
  transform: translateY(-50%);
  display: flex;
  flex-direction: column;
  gap: 16px;
  z-index: 100;
}

.scroll-btn {
  width: 68px;
  height: 68px;
  border-radius: 50%;
  border: none;
  background-color: #409eff;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.2);
  cursor: pointer;
  touch-action: none;
  user-select: none;
  -webkit-touch-callout: none;
  -webkit-user-select: none;
  transition: background-color 0.2s, opacity 0.2s, transform 0.1s;
}

.scroll-btn:active:not(:disabled) {
  transform: scale(0.96);
}

.scroll-btn:disabled {
  background-color: #a0cfff;
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
