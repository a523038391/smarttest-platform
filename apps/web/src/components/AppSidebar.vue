<script setup lang="ts">
import AppIcon from './AppIcon.vue'
import { navigationItems, type NavKey } from '../types/platform'

defineProps<{ active: NavKey; open: boolean }>()
const emit = defineEmits<{
  navigate: [key: NavKey]
  close: []
}>()

function navigate(key: NavKey) {
  emit('navigate', key)
  emit('close')
}
</script>

<template>
  <aside class="sidebar" :class="{ open }" aria-label="主导航">
    <div class="brand">
      <span class="brand-mark" aria-hidden="true"><span></span></span>
      <span><strong>智测云</strong><small>INTELLIGENT QA</small></span>
      <button class="mobile-close" type="button" aria-label="关闭导航" @click="emit('close')">
        <AppIcon name="close" />
      </button>
    </div>

    <nav>
      <p class="nav-caption">测试工作空间</p>
      <ul>
        <li v-for="item in navigationItems" :key="item.key">
          <button
            type="button"
            :class="{ active: active === item.key }"
            :aria-current="active === item.key ? 'page' : undefined"
            @click="navigate(item.key)"
          >
            <AppIcon :name="item.icon" />
            <span>{{ item.label }}</span>
          </button>
        </li>
      </ul>
    </nav>

    <div class="sidebar-footer">
      <div class="system-state"><span></span>平台基础服务</div>
      <small>智能测试管理平台</small>
    </div>
  </aside>
</template>

<style scoped>
.sidebar{position:fixed;inset:0 auto 0 0;z-index:30;width:248px;padding:0 14px 20px;box-sizing:border-box;color:#aeb9ce;background:linear-gradient(180deg,#121c30 0%,#101827 100%);display:flex;flex-direction:column;box-shadow:8px 0 24px rgba(8,15,28,.08)}
.brand{height:76px;padding:0 8px;display:flex;align-items:center;gap:11px;color:#fff;border-bottom:1px solid rgba(255,255,255,.07)}
.brand-mark{position:relative;width:36px;height:36px;border-radius:10px;display:grid;place-items:center;background:linear-gradient(145deg,#5b8cff,#7b61ff);box-shadow:0 8px 24px rgba(91,140,255,.3)}
.brand-mark::before,.brand-mark::after,.brand-mark span{content:"";display:block;position:absolute;width:13px;height:4px;border-radius:4px;background:#fff;transform:rotate(-35deg)}
.brand-mark::before{margin:-8px 0 0 -7px}.brand-mark::after{margin:8px 0 0 7px}.brand-mark span{width:18px}
.brand>span:nth-child(2){display:flex;flex-direction:column;line-height:1.15}.brand strong{font-size:18px;letter-spacing:.08em}.brand small{margin-top:5px;font-size:8px;letter-spacing:.16em;color:#75829a}
nav{padding-top:22px}.nav-caption{margin:0 12px 9px;color:#637189;font-size:11px;font-weight:600;letter-spacing:.12em}
ul{margin:0;padding:0;list-style:none;display:grid;gap:5px}button{font:inherit}
nav button{position:relative;width:100%;height:44px;padding:0 14px;border:0;border-radius:9px;color:#9facbf;background:transparent;display:flex;align-items:center;gap:13px;text-align:left;cursor:pointer;transition:.18s ease}
nav button svg{width:19px;height:19px;flex:none}nav button:hover{color:#fff;background:rgba(255,255,255,.055)}nav button.active{color:#fff;background:linear-gradient(90deg,rgba(80,127,255,.22),rgba(80,127,255,.09));box-shadow:inset 0 0 0 1px rgba(109,146,255,.12)}
nav button.active::before{content:"";position:absolute;left:-14px;width:3px;height:24px;border-radius:0 4px 4px 0;background:#5c8dff;box-shadow:0 0 12px #5c8dff}
.sidebar-footer{margin-top:auto;padding:17px 12px 0;border-top:1px solid rgba(255,255,255,.07);display:grid;gap:5px}.system-state{font-size:12px;color:#8f9bb0;display:flex;align-items:center;gap:8px}.system-state span{width:7px;height:7px;border-radius:50%;background:#64748b}.sidebar-footer small{color:#526076;font-size:10px}
.mobile-close{display:none;margin-left:auto;width:36px;height:36px;border:0;border-radius:8px;color:#aeb9ce;background:transparent}.mobile-close svg{width:20px}
@media(max-width:800px){.sidebar{transform:translateX(-105%);transition:transform .25s ease}.sidebar.open{transform:translateX(0)}.mobile-close{display:grid;place-items:center}}
</style>