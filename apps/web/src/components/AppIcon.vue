<script setup lang="ts">
import type { IconName } from '../types/platform'

defineProps<{ name: IconName }>()
</script>

<template>
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <template v-if="name === 'dashboard'">
      <rect x="3" y="3" width="7" height="7" rx="2" /><rect x="14" y="3" width="7" height="7" rx="2" />
      <rect x="3" y="14" width="7" height="7" rx="2" /><rect x="14" y="14" width="7" height="7" rx="2" />
    </template>
    <template v-else-if="name === 'requirements'">
      <path d="M7 3h10l3 3v15H4V3h3Z" /><path d="M8 8h8M8 12h8M8 16h5" />
    </template>
    <template v-else-if="name === 'cases'">
      <rect x="4" y="3" width="16" height="18" rx="2" /><path d="m8 8 1.5 1.5L12 7M8 14l1.5 1.5L12 13M14 9h2M14 15h2" />
    </template>
    <template v-else-if="name === 'automation'">
      <path d="M8 4h8M9 2v4M15 2v4" /><rect x="4" y="6" width="16" height="14" rx="4" />
      <circle cx="9" cy="12" r="1" fill="currentColor" stroke="none" /><circle cx="15" cy="12" r="1" fill="currentColor" stroke="none" /><path d="M9 16h6" />
    </template>
    <template v-else-if="name === 'plans'">
      <rect x="3" y="5" width="18" height="16" rx="2" /><path d="M16 3v4M8 3v4M3 10h18M8 14h3M8 17h7" />
    </template>
    <template v-else-if="name === 'executions'">
      <circle cx="12" cy="12" r="9" /><path d="m10 8 6 4-6 4V8Z" />
    </template>
    <template v-else-if="name === 'defects'">
      <path d="M8 9h8M7 14h10M12 5v14" /><path d="M6 7 3 4M18 7l3-3M6 17l-3 3M18 17l3 3" /><rect x="6" y="5" width="12" height="14" rx="6" />
    </template>
    <template v-else-if="name === 'reports'">
      <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />
    </template>
    <template v-else-if="name === 'data-factory'">
      <ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" />
    </template>
    <template v-else-if="name === 'version-control'">
      <circle cx="6" cy="5" r="2" /><circle cx="6" cy="19" r="2" /><circle cx="18" cy="8" r="2" />
      <path d="M6 7v10M8 8h4a6 6 0 0 1 6 6v-4" />
    </template>
    <template v-else-if="name === 'menu'"><path d="M4 7h16M4 12h16M4 17h16" /></template>
    <template v-else-if="name === 'bell'"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4" /></template>
    <template v-else-if="name === 'refresh'"><path d="M20 6v5h-5M4 18v-5h5" /><path d="M18.5 9A7 7 0 0 0 6 6.5L4 11M5.5 15A7 7 0 0 0 18 17.5l2-4.5" /></template>
    <template v-else-if="name === 'search'"><circle cx="11" cy="11" r="7" /><path d="m20 20-4-4" /></template>
    <template v-else><path d="M6 6l12 12M18 6 6 18" /></template>
  </svg>
</template>