<script setup lang="ts">
defineProps<{ title: string }>()
</script>

<template>
  <section class="placeholder" aria-labelledby="placeholder-title">
    <div class="placeholder-icon" aria-hidden="true">◇</div>
    <h2 id="placeholder-title">{{ title }}</h2>
    <p>基础工作区已准备就绪，业务数据与操作能力将在接口接入后呈现。</p>
    <span>模块建设中</span>
  </section>
</template>

<style scoped>
.placeholder{min-height:460px;border:1px solid #e7ecf3;border-radius:14px;background:#fff;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center}.placeholder-icon{width:58px;height:58px;border-radius:17px;display:grid;place-items:center;color:#5d80dc;background:#eaf0ff;font-size:25px}.placeholder h2{margin:17px 0 7px;color:#28354a;font-size:20px}.placeholder p{max-width:420px;margin:0;color:#8d98aa;font-size:12px;line-height:1.7}.placeholder span{margin-top:16px;padding:6px 11px;border-radius:7px;color:#70809a;background:#f1f4f8;font-size:10px}
</style>