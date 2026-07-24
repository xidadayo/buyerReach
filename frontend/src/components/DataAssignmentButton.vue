<template>
  <el-button
    v-if="canAssign"
    :disabled="!ids.length"
    @click="open('transfer')"
  >
    移交到组{{ ids.length ? ` (${ids.length})` : '' }}
  </el-button>
  <el-button v-if="canAssign" :disabled="!ids.length" @click="open('share')">共享给组</el-button>

  <el-dialog v-model="visible" :title="mode === 'share' ? '共享数据' : '移交数据'" width="520px">
    <el-alert
      :title="mode === 'share' ? '共享不会修改原所属组和负责人；目标组仅获得只读访问权限。' : '移交会修改所属组；原组将失去这些数据的访问权限。'"
      type="warning"
      :closable="false"
      show-icon
      style="margin-bottom: 16px"
    />
    <el-form label-position="top">
      <el-form-item label="目标组" required>
        <el-select v-model="targetUnitId" filterable style="width: 100%" @change="targetOwnerId = ''">
          <el-option
            v-for="unit in units"
            :key="unit.id"
            :label="`${'　'.repeat(unit.depth || 0)}${unit.name}`"
            :value="unit.id"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="负责人（可选）">
        <el-select v-model="targetOwnerId" clearable filterable style="width: 100%">
          <el-option
            v-for="member in availableOwners"
            :key="member.id"
            :label="member.name"
            :value="member.id"
          />
        </el-select>
        <el-text size="small" type="info">只显示目标组内的启用成员；不选择时数据归组共享。</el-text>
      </el-form-item>
      <el-form-item label="分配原因" required>
        <el-input
          v-model="reason"
          type="textarea"
          :rows="3"
          maxlength="500"
          show-word-limit
          placeholder="说明本次分配原因，便于审计和追溯"
        />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="submit">确认分配</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'
import { useAuth } from '../stores/auth'

const props = defineProps<{
  resource: 'tasks' | 'brands' | 'contacts' | 'emails'
  ids: string[]
}>()
const emit = defineEmits<{ (event: 'assigned'): void }>()
const auth = useAuth()
const visible = ref(false)
const mode = ref<'transfer' | 'share'>('transfer')
const saving = ref(false)
const targetUnitId = ref('')
const targetOwnerId = ref('')
const reason = ref('')
const units = ref<Array<{ id: string; name: string; depth: number }>>([])
const users = ref<Array<{ id: string; name: string; organization_unit_id: string | null }>>([])
const canAssign = computed(() => auth.hasPermission(`${props.resource}:assign`))
const availableOwners = computed(() =>
  users.value.filter((user) => user.organization_unit_id === targetUnitId.value),
)

async function open(nextMode: 'transfer' | 'share') {
  try {
    const { data } = await api.get('/data-assignments/targets', {
      params: { resource: props.resource },
    })
    units.value = data.units || []
    mode.value = nextMode
    users.value = data.users || []
    targetUnitId.value = ''
    targetOwnerId.value = ''
    reason.value = ''
    visible.value = true
  } catch (error) {
    ElMessage.error((error as Error).message)
  }
}

async function submit() {
  if (!targetUnitId.value) return ElMessage.warning('请选择目标组')
  if (!reason.value.trim()) return ElMessage.warning('请填写分配原因')
  saving.value = true
  try {
    const { data } = await api.post(mode.value === 'share' ? '/data-shares' : '/data-assignments', {
      resource: props.resource,
      ids: props.ids,
      target_organization_unit_id: targetUnitId.value,
      ...(mode.value === 'transfer' ? { target_owner_id: targetOwnerId.value || null } : {}),
      reason: reason.value.trim(),
    })
    visible.value = false
    ElMessage.success(mode.value === 'share' ? `已共享 ${data.shared} 条数据` : `已移交 ${data.assigned} 条数据`)
    emit('assigned')
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally {
    saving.value = false
  }
}
</script>
