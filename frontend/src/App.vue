<template>
  <router-view />
</template>

<script setup lang="ts">
// 工位扫码台已下沉为 /scan/* 下的独立路由 + 独立页面，不再需要 App 根级挂载。
// 全局扫码监听、各页面的 onScan 订阅都在对应 /scan/* 页面里。
</script>