<template>
  <section class="rect-panel" data-testid="acceptance-rect-panel">
    <div class="rect-head">
      <div>
        <h3>监理批量退回整改</h3>
        <p class="rect-hint">
          勾选同一现场已签署、未备案的验收报告并填写连带缺陷；已备案或正在备案的报告不会被打开写入。
        </p>
      </div>
      <button class="btn" type="button" data-testid="reload-rect" @click="reload">刷新整改视图</button>
    </div>

    <div class="rect-toolbar">
      <label class="toolbar-item">
        <span>现场</span>
        <input v-model="site" data-testid="batch-site" placeholder="如：东厢房现场" />
      </label>
      <label class="toolbar-item">
        <span>统一整改期限（留空取报告期限）</span>
        <input v-model="deadline" data-testid="batch-deadline" type="date" />
      </label>
      <button
        class="btn primary"
        type="button"
        data-testid="open-batch-modal"
        :disabled="!selectedIds.length"
        @click="openBatch"
      >
        批量退回选中报告（{{ selectedIds.length }}）
      </button>
    </div>

    <table class="data-table rect-table">
      <thead>
        <tr>
          <th>选择</th>
          <th>报告id</th>
          <th>验收工序（现场）</th>
          <th>当前结论</th>
          <th>备案状态</th>
          <th>整改期限</th>
          <th>补写资格</th>
          <th>备案操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`rect-row-${row.id}`">
          <td>
            <input
              v-model="selected"
              type="checkbox"
              :value="Number(row.id)"
              :data-testid="`select-${row.id}`"
              :disabled="!isSelectable(row)"
            />
          </td>
          <td>{{ row.id }}</td>
          <td>{{ row.验收工序 }}</td>
          <td>{{ row.status }}</td>
          <td>
            <span :class="{ 'lock-tag': isFiling(row) || isFiled(row) }">{{ filingText(row) }}</span>
          </td>
          <td>{{ row.整改期限 || '—' }}</td>
          <td>
            <span :class="eligibility(row).可补写 ? 'tag ok' : 'tag block'">
              {{ eligibility(row).可补写 ? '可补写' : '不可补写' }}
            </span>
            <p class="cell-reason">{{ eligibility(row).说明 }}</p>
          </td>
          <td class="row-actions">
            <button
              v-if="!isFiled(row) && !isFiling(row)"
              class="link"
              type="button"
              :data-testid="`filing-begin-${row.id}`"
              @click="onBeginFiling(Number(row.id))"
            >
              提交备案
            </button>
            <button
              v-if="isFiling(row)"
              class="link"
              type="button"
              :data-testid="`filing-complete-${row.id}`"
              @click="onCompleteFiling(Number(row.id))"
            >
              完成备案
            </button>
            <span v-else-if="isFiled(row)" class="lock-tag">已锁定</span>
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="lastResult" class="result-card" data-testid="batch-result">
      <h4>批次 {{ lastResult.批次编号 }} 退回结果</h4>
      <p class="result-summary">
        去重后 {{ lastResult.提交报告数 }} 份（批次内重复 {{ lastResult.批次内重复份数 }} 份），
        成功 {{ lastResult.成功份数 }} 份，失败 {{ lastResult.失败份数 }} 份
      </p>
      <table v-if="lastResult.成功明细.length" class="data-table sub-table">
        <thead>
          <tr><th>报告</th><th>工序</th><th>整改回执</th><th>连带缺陷</th><th>整改期限</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in lastResult.成功明细" :key="item.回执编号">
            <td>{{ item.验收报告id }}</td>
            <td>{{ item.验收工序 }}</td>
            <td>{{ item.回执编号 }}</td>
            <td>{{ item.连带缺陷.join('；') }}</td>
            <td>{{ item.整改期限 }}</td>
          </tr>
        </tbody>
      </table>
      <table v-if="lastResult.失败明细.length" class="data-table sub-table">
        <thead>
          <tr><th>报告</th><th>工序</th><th>保留的原结论</th><th>失败原因</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in lastResult.失败明细" :key="item.验收报告id" class="fail-row">
            <td>{{ item.验收报告id }}</td>
            <td>{{ item.验收工序 }}</td>
            <td>{{ item.原结论 }}</td>
            <td>{{ item.原因 }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="overview-card" data-testid="eligibility-overview">
      <h4>整组操作后的补写资格解释</h4>
      <ul class="eligibility-list">
        <li v-for="item in overview?.资格说明 ?? []" :key="item.验收报告id">
          <span :class="item.可补写 ? 'tag ok' : 'tag block'">{{ item.可补写 ? '可补写' : '不可补写' }}</span>
          <strong>#{{ item.验收报告id }} {{ item.验收工序 }}</strong>
          <em>{{ item.当前结论 }}</em>
          <span class="cell-reason">{{ item.说明 }}</span>
          <span v-if="item.回执编号" class="receipt-no">回执 {{ item.回执编号 }}</span>
        </li>
      </ul>
    </div>

    <div v-if="showModal" class="modal-backdrop" data-testid="defect-modal">
      <form class="modal" @submit.prevent="confirmBatch">
        <h3>连带缺陷与退回确认（现场：{{ site || '未填写' }}）</h3>
        <p class="rect-hint">同一报告重复勾选只保留一条整改回执；重复提交批次也不会再生成新回执。</p>
        <label v-for="id in selectedIds" :key="id" class="create-field">
          <span>#{{ id }} {{ processName(id) }} 连带缺陷（多个用分号分隔）</span>
          <input v-model="defects[id]" :data-testid="`defect-${id}`" :placeholder="defaultDefect(id)" />
        </label>
        <label class="create-field">
          <span>退回备注</span>
          <input v-model="remark" data-testid="batch-remark" placeholder="可选" />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showModal = false">取消</button>
          <button class="btn primary" data-testid="confirm-batch" type="submit" :disabled="submitting">
            {{ submitting ? '提交中…' : '确认批量退回' }}
          </button>
        </div>
        <p v-if="submitError" class="error-text">{{ submitError }}</p>
      </form>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  batchReturnAcceptance,
  beginAcceptanceFiling,
  completeAcceptanceFiling,
  fetchRectificationOverview,
  type BatchReturnResult,
  type EligibilityItem,
  type RectificationOverview,
} from '@/api/backend-service'
import type { EntryRow } from '@/data/types'

const props = defineProps<{ rows: EntryRow[] }>()
const emit = defineEmits<{ changed: [] }>()

const selected = ref<number[]>([])
const site = ref('')
const deadline = ref('')
const remark = ref('')
const defects = ref<Record<number, string>>({})
const showModal = ref(false)
const submitting = ref(false)
const submitError = ref('')
const lastResult = ref<BatchReturnResult | null>(null)
const overview = ref<RectificationOverview | null>(null)

const selectedIds = computed(() => Array.from(new Set(selected.value)))

const eligibilityMap = computed(() => {
  const map = new Map<number, EligibilityItem>()
  for (const item of overview.value?.资格说明 ?? []) map.set(item.验收报告id, item)
  return map
})

function isFiled(row: EntryRow): boolean {
  return Boolean(row.已归档) || row.备案状态 === '已备案'
}
function isFiling(row: EntryRow): boolean {
  return row.备案状态 === '备案中'
}
function filingText(row: EntryRow): string {
  if (isFiled(row)) return '已备案'
  if (isFiling(row)) return '备案中'
  return '未备案'
}
function hasOpenReceipt(row: EntryRow): boolean {
  const item = eligibilityMap.value.get(Number(row.id))
  return Boolean(item?.回执编号) && item?.可补写 === true
}
function isSelectable(row: EntryRow): boolean {
  return !isFiled(row) && !isFiling(row) && !hasOpenReceipt(row) && row.status === '已签署'
}
function eligibility(row: EntryRow): EligibilityItem {
  return (
    eligibilityMap.value.get(Number(row.id)) ?? {
      验收报告id: Number(row.id),
      验收工序: String(row.验收工序 ?? ''),
      当前结论: String(row.status ?? ''),
      可补写: false,
      回执编号: '',
      说明: '整改视图尚未加载',
    }
  )
}
function processName(id: number): string {
  return String(props.rows.find((r) => Number(r.id) === id)?.验收工序 ?? '')
}
function defaultDefect(id: number): string {
  return String(props.rows.find((r) => Number(r.id) === id)?.遗留问题 ?? '遗留缺陷')
}

function openBatch() {
  submitError.value = ''
  for (const id of selectedIds.value) {
    if (!defects.value[id]) defects.value[id] = defaultDefect(id)
  }
  showModal.value = true
}

async function confirmBatch() {
  submitting.value = true
  submitError.value = ''
  try {
    const defectsByReport: Record<string, string> = {}
    for (const id of selectedIds.value) defectsByReport[String(id)] = defects.value[id] ?? ''
    lastResult.value = await batchReturnAcceptance({
      acceptance_ids: selected.value,
      defects_by_report: defectsByReport,
      site: site.value,
      deadline: deadline.value || undefined,
      remark: remark.value || undefined,
    })
    showModal.value = false
    selected.value = []
    await reload()
    emit('changed')
  } catch (e) {
    submitError.value = e instanceof Error ? e.message : '批量退回失败'
  } finally {
    submitting.value = false
  }
}

async function onBeginFiling(id: number) {
  const r = await beginAcceptanceFiling(id)
  if (!r.ok) submitError.value = r.message
  await reload()
  emit('changed')
}

async function onCompleteFiling(id: number) {
  const r = await completeAcceptanceFiling(id)
  if (!r.ok) submitError.value = r.message
  await reload()
  emit('changed')
}

async function reload() {
  try {
    overview.value = await fetchRectificationOverview()
  } catch (e) {
    submitError.value = e instanceof Error ? e.message : '整改视图读取失败'
  }
}

onMounted(reload)
defineExpose({ reload })
</script>

<style scoped>
.rect-panel { border: 1px solid var(--border-color, #e2e8f0); border-radius: 8px; padding: 16px; margin: 20px 0; background: #f8fafc; }
.rect-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.rect-hint { font-size: 12px; color: var(--text-secondary, #4a5568); margin: 4px 0; }
.rect-toolbar { display: flex; gap: 12px; align-items: flex-end; margin: 12px 0; flex-wrap: wrap; }
.toolbar-item { display: flex; flex-direction: column; gap: 4px; font-size: 12px; }
.toolbar-item input { padding: 6px 8px; border: 1px solid var(--border-color, #e2e8f0); border-radius: 4px; }
.rect-table { background: #fff; }
.cell-reason { font-size: 12px; color: var(--text-secondary, #4a5568); margin: 2px 0 0; }
.tag { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 12px; }
.tag.ok { background: #c6f6d5; color: #22543d; }
.tag.block { background: #fed7d7; color: #742a2a; }
.lock-tag { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 12px; background: #e2e8f0; color: #4a5568; }
.result-card, .overview-card { background: #fff; border: 1px solid var(--border-color, #e2e8f0); border-radius: 8px; padding: 12px; margin-top: 16px; }
.result-summary { font-size: 13px; }
.sub-table { margin-top: 8px; }
.fail-row { background: #fff5f5; }
.eligibility-list { list-style: none; padding: 0; margin: 8px 0 0; display: flex; flex-direction: column; gap: 8px; }
.eligibility-list li { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; font-size: 13px; }
.receipt-no { font-size: 12px; color: #2b6cb0; }
.modal-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,.35); display: flex; align-items: center; justify-content: center; z-index: 200; }
.modal { background: #fff; border-radius: 8px; padding: 24px; width: 520px; max-height: 80vh; overflow-y: auto; }
.create-field { display: flex; flex-direction: column; gap: 4px; margin-bottom: 12px; }
.create-field span { font-size: 13px; color: var(--text-secondary, #4a5568); }
.create-field input { padding: 6px 8px; border: 1px solid var(--border-color, #e2e8f0); border-radius: 4px; font-size: 13px; }
</style>
