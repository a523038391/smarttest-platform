<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { getAuthErrorMessage, login, setupAdmin } from '../services/auth'
import type { AuthUser } from '../types/auth'

const props = defineProps<{ setupRequired: boolean }>()
const emit = defineEmits<{ authenticated: [user: AuthUser] }>()

const username = ref('')
const displayName = ref('')
const password = ref('')
const confirmation = ref('')
const passwordVisible = ref(false)
const submitting = ref(false)
const error = ref('')

const title = computed(() => props.setupRequired ? '创建首位管理员' : '欢迎回来')
const description = computed(() => props.setupRequired
  ? '首次使用需要初始化平台管理员账户。'
  : '登录后继续管理测试资产与执行任务。')

watch(() => props.setupRequired, () => {
  password.value = ''
  confirmation.value = ''
  error.value = ''
})

async function submit() {
  error.value = ''
  const normalizedUsername = username.value.trim()
  const normalizedDisplayName = displayName.value.trim()
  if (!normalizedUsername || !password.value) {
    error.value = '请填写用户名和密码。'
    return
  }
  if (props.setupRequired && !normalizedDisplayName) {
    error.value = '请填写显示名称。'
    return
  }
  if (props.setupRequired && password.value !== confirmation.value) {
    error.value = '两次输入的密码不一致。'
    return
  }

  submitting.value = true
  try {
    const authenticatedUser = props.setupRequired
      ? await setupAdmin({
          username: normalizedUsername,
          display_name: normalizedDisplayName,
          password: password.value,
        })
      : await login({ username: normalizedUsername, password: password.value })
    password.value = ''
    confirmation.value = ''
    emit('authenticated', authenticatedUser)
  } catch (reason) {
    error.value = getAuthErrorMessage(reason)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <main class="auth-page">
    <section class="auth-intro" aria-label="平台介绍">
      <div class="brand"><span class="brand-mark" aria-hidden="true"><span></span></span><strong>智测云</strong></div>
      <div class="intro-copy">
        <span class="eyebrow">INTELLIGENT QA WORKSPACE</span>
        <h1>让每一次交付<br>都有质量答案</h1>
        <p>连接需求、用例、自动化与执行结果，在一个清晰的工作空间持续提升软件质量。</p>
      </div>
      <div class="feature-list" aria-hidden="true">
        <span><i>✓</i> 全链路质量追踪</span><span><i>⌁</i> 智能测试协作</span><span><i>◇</i> 实时执行洞察</span>
      </div>
    </section>

    <section class="auth-panel" :aria-labelledby="'auth-title'">
      <div class="mobile-brand"><span class="brand-mark" aria-hidden="true"><span></span></span><strong>智测云</strong></div>
      <div class="auth-card">
        <header><span class="security-mark" aria-hidden="true">✓</span><h2 id="auth-title">{{ title }}</h2><p>{{ description }}</p></header>
        <form novalidate @submit.prevent="submit">
          <div class="field">
            <label for="username">用户名</label>
            <input id="username" v-model="username" name="username" autocomplete="username" required maxlength="128" :disabled="submitting" placeholder="请输入用户名">
          </div>
          <div v-if="setupRequired" class="field">
            <label for="display-name">显示名称</label>
            <input id="display-name" v-model="displayName" name="display-name" autocomplete="name" required maxlength="128" :disabled="submitting" placeholder="例如：质量管理员">
          </div>
          <div class="field">
            <label for="password">密码</label>
            <div class="password-field">
              <input id="password" v-model="password" name="password" :type="passwordVisible ? 'text' : 'password'" :autocomplete="setupRequired ? 'new-password' : 'current-password'" required :disabled="submitting" placeholder="请输入密码">
              <button type="button" :aria-label="passwordVisible ? '隐藏密码' : '显示密码'" :aria-pressed="passwordVisible" @click="passwordVisible = !passwordVisible">{{ passwordVisible ? '隐藏' : '显示' }}</button>
            </div>
          </div>
          <div v-if="setupRequired" class="field">
            <label for="confirmation">确认密码</label>
            <input id="confirmation" v-model="confirmation" name="confirmation" :type="passwordVisible ? 'text' : 'password'" autocomplete="new-password" required :disabled="submitting" placeholder="请再次输入密码">
          </div>
          <p v-if="error" class="form-error" role="alert">{{ error }}</p>
          <button class="submit-button" type="submit" :disabled="submitting" :aria-busy="submitting">
            <span v-if="submitting" class="spinner" aria-hidden="true"></span>{{ submitting ? '请稍候…' : setupRequired ? '创建管理员并进入平台' : '登录平台' }}
          </button>
        </form>
        <footer><span aria-hidden="true">●</span> 安全会话由同源加密 Cookie 保护</footer>
      </div>
    </section>
  </main>
</template>

<style scoped>
.auth-page{width:100%;min-height:100vh;margin:0;display:grid;grid-template-columns:minmax(360px,.9fr) minmax(520px,1.1fr);padding:0;background:#f7f9fc}.auth-intro{position:relative;overflow:hidden;min-height:100vh;padding:48px clamp(40px,6vw,88px);display:flex;flex-direction:column;color:#fff;background:linear-gradient(145deg,#111c32 0%,#192b50 52%,#264b98 100%)}.auth-intro::before,.auth-intro::after{content:"";position:absolute;border-radius:50%;border:1px solid rgba(255,255,255,.1)}.auth-intro::before{width:520px;height:520px;right:-260px;top:10%}.auth-intro::after{width:300px;height:300px;left:-170px;bottom:-100px}.brand,.mobile-brand{display:flex;align-items:center;gap:12px;font-size:20px;letter-spacing:.08em}.brand-mark{position:relative;width:38px;height:38px;border-radius:11px;display:grid;place-items:center;background:linear-gradient(145deg,#5b8cff,#8067ff);box-shadow:0 9px 26px rgba(91,140,255,.3)}.brand-mark::before,.brand-mark::after,.brand-mark span{content:"";position:absolute;width:14px;height:4px;border-radius:4px;background:#fff;transform:rotate(-35deg)}.brand-mark::before{margin:-8px 0 0 -7px}.brand-mark::after{margin:8px 0 0 7px}.brand-mark span{width:19px}.intro-copy{position:relative;z-index:1;margin:auto 0;max-width:520px}.eyebrow{color:#91adf7;font-size:11px;font-weight:700;letter-spacing:.2em}.intro-copy h1{margin:18px 0 20px;font-size:clamp(36px,4vw,58px);line-height:1.2;letter-spacing:-.04em}.intro-copy p{max-width:480px;margin:0;color:#b5c3dc;font-size:15px;line-height:1.9}.feature-list{position:relative;z-index:1;display:flex;flex-wrap:wrap;gap:12px 24px;color:#afbdd5;font-size:12px}.feature-list i{margin-right:6px;color:#79a1ff;font-style:normal}.auth-panel{min-height:100vh;padding:48px clamp(30px,7vw,110px);display:grid;place-items:center}.mobile-brand{display:none}.auth-card{width:min(100%,430px);padding:40px;border:1px solid #e5eaf2;border-radius:18px;background:#fff;box-shadow:0 22px 60px rgba(28,43,75,.1)}header{text-align:center}.security-mark{width:44px;height:44px;margin:0 auto 16px;border-radius:13px;display:grid;place-items:center;color:#fff;background:linear-gradient(145deg,#4d7bea,#735fe0);box-shadow:0 8px 22px rgba(79,113,211,.25);font-weight:800}h2{margin:0;color:#1c273a;font-size:24px}header p{margin:9px 0 28px;color:#8994a7;font-size:13px;line-height:1.6}form{display:grid;gap:17px}.field{display:grid;gap:7px}.field label{color:#3b465a;font-size:12px;font-weight:650}.field input{width:100%;height:46px;padding:0 13px;border:1px solid #dfe5ee;border-radius:9px;color:#253044;background:#fbfcfe;font-size:13px;transition:.18s}.field input:hover{border-color:#c8d2e3}.field input:focus{border-color:#6b8de2;background:#fff;box-shadow:0 0 0 3px rgba(80,121,223,.1);outline:0}.field input::placeholder{color:#afb7c4}.field input:disabled{cursor:not-allowed;opacity:.7}.password-field{position:relative}.password-field input{padding-right:58px}.password-field button{position:absolute;right:7px;top:7px;height:32px;padding:0 8px;border:0;border-radius:6px;color:#5875bc;background:transparent;font-size:11px;cursor:pointer}.password-field button:hover{background:#eef3ff}.form-error{margin:0;padding:10px 12px;border:1px solid #ffd7db;border-radius:8px;color:#b84350;background:#fff5f6;font-size:12px;line-height:1.5}.submit-button{height:48px;margin-top:3px;border:0;border-radius:9px;display:flex;align-items:center;justify-content:center;gap:8px;color:#fff;background:linear-gradient(100deg,#456fd6,#6b60dc);box-shadow:0 8px 18px rgba(71,103,196,.22);font-size:13px;font-weight:700;cursor:pointer;transition:.18s}.submit-button:hover:not(:disabled){transform:translateY(-1px);box-shadow:0 11px 22px rgba(71,103,196,.28)}.submit-button:disabled{cursor:wait;opacity:.75}.spinner{width:15px;height:15px;border:2px solid rgba(255,255,255,.35);border-top-color:#fff;border-radius:50%;animation:spin .7s linear infinite}.auth-card footer{margin-top:25px;color:#a0a8b6;text-align:center;font-size:10px}.auth-card footer span{margin-right:5px;color:#54b893;font-size:7px}@keyframes spin{to{transform:rotate(360deg)}}
@media(max-width:900px){.auth-page{grid-template-columns:1fr}.auth-intro{display:none}.auth-panel{padding:35px 20px;align-content:center;gap:28px}.mobile-brand{display:flex;color:#1e2b43}.auth-card{padding:34px}}
@media(max-width:480px){.auth-panel{padding:26px 15px}.auth-card{padding:28px 21px;border-radius:14px;box-shadow:0 14px 35px rgba(28,43,75,.08)}h2{font-size:21px}}
</style>