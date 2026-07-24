<template>
  <span v-if="hasPermission"><slot /></span>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { hasPermission as checkPermission } from '../stores/auth'

const props = defineProps<{ permission: string }>()

const hasPermission = computed(() => checkPermission(props.permission))
</script>
