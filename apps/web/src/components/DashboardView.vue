<script setup lang="ts">
import type { NavKey } from '../types/platform'

const emit = defineEmits<{ navigate: [key: NavKey] }>()

const metrics = [
  { label: '需求覆盖率', icon: '◇', tone: 'blue', hint: '已评审用例覆盖' },
  { label: '用例通过率', icon: '✓', tone: 'green', hint: '最终执行结果' },
  { label: '自动化覆盖率', icon: '⌁', tone: 'violet', hint: '有效脚本绑定' },
  { label: '未关闭缺陷', icon: '!', tone: 'orange', hint: '当前项目缺陷' },
]

const shortcuts: { label: string; description: string; key: NavKey; symbol: string }[] = [
  { label: '测试用例', description: '管理与评审用例资产', key: 'cases', symbol: 'T' },
  { label: '测试计划', description: '组织测试范围与策略', key: 'plans', symbol: 'P' },
  { label: '执行中心', description: '查看真实运行状态', key: 'executions', symbol: '▶' },
  { label: '测试报告', description: '沉淀质量结果快照', key: 'reports', symbol: 'R' },
]
</script>

<template>
  <div class="dashboard">
    <section class="welcome" aria-labelledby="welcome-title">
      <div>
        <span class="eyebrow">QUALITY WORKSPACE</span>
        <h2 id="welcome-title">欢迎使用智能测试平台</h2>
        <p>在同一工作空间连接需求、用例、执行、缺陷与质量报告。</p>
      </div>
      <div class="welcome-art" aria-hidden="true"><span></span><i>AI</i></div>
    </section>

    <section aria-labelledby="metrics-title">
      <div class="section-heading"><div><h2 id="metrics-title">质量概览</h2><p>当前项目的核心质量指标</p></div><span class="data-note">数据接口待接入</span></div>
      <div class="metrics-grid">
        <article v-for="metric in metrics" :key="metric.label" class="metric-card">
          <div class="metric-icon" :class="metric.tone">{{ metric.icon }}</div>
          <div><p>{{ metric.label }}</p><strong aria-label="暂无数据">--</strong><small>{{ metric.hint }} · 暂无数据</small></div>
        </article>
      </div>
    </section>

    <div class="dashboard-grid">
      <section class="panel shortcuts" aria-labelledby="shortcut-title">
        <div class="panel-heading"><div><h2 id="shortcut-title">快速入口</h2><p>进入常用测试工作区</p></div></div>
        <div class="shortcut-grid">
          <button v-for="item in shortcuts" :key="item.key" type="button" @click="emit('navigate', item.key)">
            <span>{{ item.symbol }}</span><span class="shortcut-copy"><strong>{{ item.label }}</strong><small>{{ item.description }}</small></span><i aria-hidden="true">→</i>
          </button>
        </div>
      </section>
      <section class="panel activity" aria-labelledby="activity-title">
        <div class="panel-heading"><div><h2 id="activity-title">最近动态</h2><p>项目质量活动</p></div></div>
        <div class="empty-mini"><span>⌁</span><strong>暂无项目动态</strong><p>选择项目并接入数据后，活动记录将显示在这里。</p></div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.dashboard{display:grid;gap:28px}.welcome{min-height:142px;padding:28px 32px;border-radius:16px;overflow:hidden;display:flex;align-items:center;justify-content:space-between;color:#fff;background:linear-gradient(118deg,#416ed8 0%,#6286e6 54%,#786ee4 100%);box-shadow:0 12px 28px rgba(66,105,202,.16);position:relative}.welcome::after{content:"";position:absolute;right:12%;top:-90px;width:230px;height:230px;border:1px solid rgba(255,255,255,.12);border-radius:50%}.eyebrow{font-size:10px;font-weight:700;letter-spacing:.18em;opacity:.75}.welcome h2{margin:8px 0 8px;font-size:24px;color:#fff}.welcome p{font-size:13px;opacity:.82}.welcome-art{position:relative;width:150px;height:95px;margin-right:30px}.welcome-art::before,.welcome-art::after,.welcome-art span{content:"";position:absolute;border:1px solid rgba(255,255,255,.25);transform:rotate(45deg);border-radius:16px}.welcome-art::before{width:72px;height:72px;right:24px;top:10px}.welcome-art::after{width:48px;height:48px;right:73px;top:21px}.welcome-art span{width:32px;height:32px;right:8px;top:30px}.welcome-art i{position:absolute;right:48px;top:35px;font-style:normal;font-size:17px;font-weight:800;letter-spacing:.08em}
.section-heading,.panel-heading{display:flex;justify-content:space-between;align-items:center;margin-bottom:15px}.section-heading h2,.panel-heading h2{margin:0 0 4px;color:#202b3d;font-size:16px}.section-heading p,.panel-heading p{margin:0;color:#97a1b2;font-size:11px}.data-note{padding:5px 9px;border-radius:6px;color:#7d8798;background:#eef1f6;font-size:10px}
.metrics-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.metric-card{min-height:114px;padding:20px;border:1px solid #e8edf4;border-radius:12px;background:#fff;display:flex;align-items:flex-start;gap:15px;box-shadow:0 4px 14px rgba(26,39,66,.035);box-sizing:border-box}.metric-icon{width:38px;height:38px;flex:none;border-radius:10px;display:grid;place-items:center;font-weight:800}.blue{color:#4e7de7;background:#eaf0ff}.green{color:#28a879;background:#e7f8f2}.violet{color:#7769dc;background:#eeebff}.orange{color:#ee8757;background:#fff0e9}.metric-card p{margin:0;color:#748095;font-size:11px}.metric-card strong{display:block;margin:4px 0 1px;color:#263145;font-size:24px;line-height:1}.metric-card small{color:#a1a9b8;font-size:9px}
.dashboard-grid{display:grid;grid-template-columns:minmax(0,1.65fr) minmax(280px,.8fr);gap:16px}.panel{padding:21px;border:1px solid #e8edf4;border-radius:12px;background:#fff}.shortcut-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.shortcut-grid button{min-height:70px;padding:12px;border:1px solid #edf0f5;border-radius:9px;background:#fafbfd;display:flex;align-items:center;gap:11px;text-align:left;cursor:pointer;transition:.18s}.shortcut-grid button:hover{border-color:#cfdafa;background:#f7f9ff;transform:translateY(-1px)}.shortcut-grid button>span:first-child{width:34px;height:34px;border-radius:8px;display:grid;place-items:center;color:#5f7fd2;background:#eaf0ff;font-weight:700}.shortcut-copy{display:flex;flex:1;flex-direction:column}.shortcut-grid strong{color:#344055;font-size:12px}.shortcut-grid small{margin-top:3px;color:#99a3b3;font-size:9px}.shortcut-grid i{color:#a0aabb;font-style:normal}.empty-mini{min-height:150px;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center}.empty-mini>span{width:38px;height:38px;border-radius:50%;display:grid;place-items:center;color:#8091ae;background:#f0f3f8}.empty-mini strong{margin:9px 0 3px;color:#5c687a;font-size:11px}.empty-mini p{max-width:220px;margin:0;color:#a1a9b6;font-size:9px;line-height:1.6}
@media(max-width:1100px){.metrics-grid{grid-template-columns:repeat(2,1fr)}.dashboard-grid{grid-template-columns:1fr}.welcome-art{margin-right:0}}
@media(max-width:600px){.dashboard{gap:22px}.welcome{padding:25px 22px}.welcome h2{font-size:21px}.welcome-art{display:none}.metrics-grid,.shortcut-grid{grid-template-columns:1fr}.metric-card{min-height:100px}.data-note{display:none}.panel{padding:17px}}
</style>