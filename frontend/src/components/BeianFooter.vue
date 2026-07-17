<template>
  <div class="beian-footer">
    <!-- 公安网安备：警盾 SVG + 备案号 -->
    <a
      class="beian-link"
      href="https://beian.mps.gov.cn/#/query/webSearch?code=35021102002692"
      target="_blank"
      rel="noreferrer"
    >
      <svg class="beian-icon" viewBox="0 0 24 24" aria-hidden="true">
        <path
          d="M12 1l3.09 6.26L22 8.27l-5 4.87L18.18 22 12 18.27 5.82 22 7 13.14l-5-4.87 6.91-1.01L12 1z"
          fill="#c0392b"
        />
      </svg>
      <span>闽公网安备 35021102002692号</span>
    </a>

    <!-- ICP 备案：工信部 LOGO + 备案号 -->
    <a
      class="beian-link"
      href="https://beian.miit.gov.cn/"
      target="_blank"
      rel="noreferrer"
    >
      <svg class="beian-icon" viewBox="0 0 24 24" aria-hidden="true">
        <path
          d="M3 3h18v18H3V3zm2 2v14h14V5H5zm2 2h10v2H7V7zm0 4h10v2H7v-2zm0 4h6v2H7v-2z"
          fill="#2c5aa0"
        />
      </svg>
      <span>闽ICP备2026025675号</span>
    </a>
  </div>
</template>

<script setup lang="ts">
// 无状态展示组件，无 props / emits。
</script>

<style scoped lang="scss">
.beian-footer {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 24px;
  padding: 8px 16px;
  font-size: 12px;
  color: #909399;
  line-height: 1;

  .beian-link {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    color: #909399;
    text-decoration: none;
    transition: color 0.2s;

    &:hover {
      color: #409eff;
    }
  }

  .beian-icon {
    width: 16px;
    height: 16px;
    flex-shrink: 0;
  }
}
</style>