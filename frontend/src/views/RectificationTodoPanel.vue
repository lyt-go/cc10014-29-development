<template>
  <section class="todo-panel" data-testid="log-todo-panel">
    <div class="todo-head">
      <div>
        <h3>整改待办（来自验收批量退回）</h3>
        <p class="todo-hint">
          只有取得实际整改回执的施工班次才能提交整改补写；回执在日志审核通过后关闭，不可重复补写。
        </p>
      </div>
      <button class="btn" type="button" data-testid="reload-todos" @click="reload">刷新待办</button>
    </div>

    <p class="todo-count" data-testid="todo-count">待整改回执：{{ todos.待办数 }} 条</p>

    <table v-if="todos.待办.length" class="data-table">
      <thead>
        <tr>
          <th>回执编号</th>
          <th>批次</th>
          <th>施工班次（工序）</th>
          <th>连带缺陷</th>
          <th>整改期限</th>
          <th>补写</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="todo in todos.待办" :key="todo.回执编号" :data-testid="`todo-${todo.回执编号}`">
          <td>{{ todo.回执编号 }}</td>
          <td>{{ todo.批次编号 }}</td>
          <td>{{ todo.对应工序 }}</td>
          <td>{{ todo.连带缺陷.join('；') }}</td>
          <td>{{ todo.整改期限 }}</td>
          <td>
            <button class="link" type="button" @click="openWrite(todo)">去写整改日志</button>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-else class="empty-tip">当前没有待整改回执，整改待办为空。</p>

    <div v-if="showWrite" class="modal-backdrop" data-testid="write-modal">
      <form class="modal" @submit.prevent="submitWrite">
        <h3>施工班次整改日志</h3>
        <p class="todo-hint">
          回执 {{ active?.回执编号 }} ｜ 班次「{{ active?.对应工序 }}」｜ 期限 {{ active?.整改期限 }}
        </p>
        <label class="write-field">
          <span>施工日期</span>
          <input v-model="form.施工日期" type="date" data-testid="write-date" required />
        </label>
        <label class="write-field">
          <span>施工内容（针对：{{ active?.连带缺陷.join('；') }}）</span>
          <textarea v-model="form.施工内容" rows="3" data-testid="write-content" required></textarea>
        </label>
        <label class="write-field">
          <span>记录人</span>
          <input v-model="form.记录人" data-testid="write-recorder" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showWrite = false">取消</button>
          <button class="btn primary" data-testid="submit-write" type="submit" :disabled="submitting">
            {{ submitting ? '提交中…' : '提交补写' }}
          </button>
        </div>
        <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>
      </form>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import {
  createEntry,
  fetchRectificationTodos,
  runAction,
  type ReceiptTodo,
  type RectificationTodos,
} from '@/api/backend-service'

const emit = defineEmits<{ changed: [] }>()

const empty: RectificationTodos = { 待办数: 0, 待办: [], 全部回执: [] }
const todos = ref<RectificationTodos>(empty)
const showWrite = ref(false)
const submitting = ref(false)
const errorMessage = ref('')
const active = ref<ReceiptTodo | null>(null)
const form = reactive({ 施工日期: '', 施工内容: '', 记录人: '' })

function openWrite(todo: ReceiptTodo) {
  active.value = todo
  errorMessage.value = ''
  form.施工日期 = ''
  form.施工内容 = `整改缺陷：${todo.连带缺陷.join('；')}`
  form.记录人 = ''
  showWrite.value = true
}

async function submitWrite() {
  if (!active.value) return
  submitting.value = true
  errorMessage.value = ''
  try {
    const created = await createEntry('restoration_log', {
      施工日期: form.施工日期,
      对应工序: active.value.对应工序 ?? '',
      施工内容: form.施工内容,
      记录人: form.记录人,
      整改回执编号: active.value.回执编号,
    })
    if (!created.ok || !created.entry) throw new Error(created.message)
    const result = await runAction('restoration_log', Number(created.entry.id), '提交日志')
    if (!result.ok) throw new Error(result.message)
    showWrite.value = false
    await reload()
    emit('changed')
  } catch (e) {
    errorMessage.value = e instanceof Error ? e.message : '整改补写失败'
  } finally {
    submitting.value = false
  }
}

async function reload() {
  try {
    todos.value = await fetchRectificationTodos()
  } catch (e) {
    errorMessage.value = e instanceof Error ? e.message : '整改待办读取失败'
  }
}

onMounted(reload)
defineExpose({ reload })
</script>

<style scoped>
.todo-panel { border: 1px solid var(--border-color, #e2e8f0); border-radius: 8px; padding: 16px; margin: 20px 0; background: #f8fafc; }
.todo-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.todo-hint { font-size: 12px; color: var(--text-secondary, #4a5568); margin: 4px 0; }
.todo-count { font-weight: 600; }
.empty-tip { color: var(--text-secondary, #4a5568); font-size: 13px; }
.modal-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,.35); display: flex; align-items: center; justify-content: center; z-index: 200; }
.modal { background: #fff; border-radius: 8px; padding: 24px; width: 520px; max-height: 80vh; overflow-y: auto; }
.write-field { display: flex; flex-direction: column; gap: 4px; margin-bottom: 12px; }
.write-field span { font-size: 13px; color: var(--text-secondary, #4a5568); }
.write-field input, .write-field textarea { padding: 6px 8px; border: 1px solid var(--border-color, #e2e8f0); border-radius: 4px; font-size: 13px; }
</style>
