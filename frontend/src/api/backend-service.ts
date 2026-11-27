import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

export type DependentStatus = {
  module: string
  entry_id: number
  entry: Record<string, unknown>
  upstream: { module: string; id: number; name: string; status: string }[]
  downstream: { module: string; id: number; name: string; status: string }[]
}

export async function fetchDependentStatus(
  moduleKey: string,
  entryId: number,
): Promise<DependentStatus> {
  const response = await request(`/api/cross-module/dependent-status/${moduleKey}/${entryId}`)
  if (!response.ok) throw new Error('跨模块状态查询失败')
  return await response.json()
}

export async function fetchDependencyGraph(): Promise<{ nodes: unknown[]; edges: unknown[] }> {
  const response = await request('/api/cross-module/dependency-graph')
  if (!response.ok) throw new Error('依赖图加载失败')
  return await response.json()
}

// ── 验收记录：批量退回整改 / 备案冲突 / 整改资格 ──

export type ReceiptTodo = {
  回执编号: string
  批次编号: string
  现场: string
  验收报告id: number
  验收工序?: string
  对应工序?: string
  连带缺陷: string[]
  整改期限: string
  状态: string
  已关闭: boolean
  整改日志id: number | null
}

export type BatchFailure = {
  验收报告id: number
  验收工序: string
  原结论: string
  原因: string
}

export type BatchSuccess = {
  验收报告id: number
  验收工序: string
  回执编号: string
  整改期限: string
  连带缺陷: string[]
}

export type EligibilityItem = {
  验收报告id: number
  验收工序: string
  当前结论: string
  备案状态?: string
  可补写: boolean
  回执编号: string
  说明: string
}

export type BatchReturnResult = {
  批次编号: string
  提交报告数: number
  批次内重复份数: number
  成功份数: number
  失败份数: number
  成功明细: BatchSuccess[]
  失败明细: BatchFailure[]
  资格说明?: EligibilityItem[]
}

export type RectificationOverview = {
  待办回执: ReceiptTodo[]
  全部回执: ReceiptTodo[]
  批次记录: {
    批次编号: string
    现场: string
    提交报告数: number
    批次内重复份数: number
    成功份数: number
    失败份数: number
    成功明细: BatchSuccess[]
    失败明细: BatchFailure[]
    创建时间: string
  }[]
  资格说明: EligibilityItem[]
}

export type RectificationTodos = {
  待办数: number
  待办: ReceiptTodo[]
  全部回执: ReceiptTodo[]
}

export async function batchReturnAcceptance(payload: {
  acceptance_ids: number[]
  defects_by_report: Record<string, string>
  site: string
  deadline?: string
  remark?: string
}): Promise<BatchReturnResult> {
  const response = await request('/api/acceptance_record/batch-return', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  if (!response.ok) throw new Error('批量退回请求未被接受')
  return await response.json()
}

export async function fetchRectificationOverview(): Promise<RectificationOverview> {
  const response = await request('/api/acceptance_record/rectification-overview')
  if (!response.ok) throw new Error('整改资格视图读取失败')
  return await response.json()
}

export async function beginAcceptanceFiling(id: number): Promise<ActionResult> {
  const response = await request(`/api/acceptance_record/${id}/filing/begin`, { method: 'POST' })
  return await response.json()
}

export async function completeAcceptanceFiling(id: number): Promise<ActionResult> {
  const response = await request(`/api/acceptance_record/${id}/filing/complete`, { method: 'POST' })
  return await response.json()
}

export async function fetchRectificationTodos(): Promise<RectificationTodos> {
  const response = await request('/api/restoration_log/rectification-todos')
  if (!response.ok) throw new Error('整改待办读取失败')
  return await response.json()
}
