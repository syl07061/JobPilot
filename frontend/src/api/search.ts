import type { CollectorStatus, Preview, SearchRequest, SearchTask, ReadingBudget, FilterOptions } from '../types/search'

export interface ReviewResult {
  rule_version: string
  state: 'running' | 'completed' | 'failed'
  total: number
  processed: number
  resume_version: number
  message: string
  matches: { id: string; title: string; employer: string; score: number; requirements: {
    requirement: string; priority: string; match: string; reason: string; evidence: string
  }[] }[]
  excluded: { id: string; title: string; company: string; reason: string }[]
}

interface ReviewStatus {
  rule_version: string
  stale: boolean
  result: ReviewResult | null
}

const reviewRule = 'company-field-v3'
const reviewVersionError = '当前后端仍在运行旧版复筛接口。请在原启动窗口按 Ctrl+C 停止服务，再运行 start.bat 并刷新页面。'

export async function getReview(id: string, signal: AbortSignal) {
  const status = await request<ReviewStatus | null>(`/tasks/${encodeURIComponent(id)}/ai-review`, { signal, cache: 'no-store' })
  if (status?.rule_version !== reviewRule || typeof status.stale !== 'boolean') throw new Error(reviewVersionError)
  return status
}

export async function startReview(id: string) {
  const result = await request<ReviewResult>(`/tasks/${encodeURIComponent(id)}/ai-review`, { method: 'POST' })
  if (result.rule_version !== reviewRule) throw new Error(reviewVersionError)
  return result
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api/search${path}`, options)
  if (!response.ok) {
    const data = await response.json().catch(() => null)
    throw new Error(typeof data?.detail === 'string' ? data.detail : `请求失败（HTTP ${response.status}），未自动重新提交检索。`)
  }
  return response.json() as Promise<T>
}

export const collectorStatus = () => request<CollectorStatus>('/collector-status')
export const getFilterOptions = (refresh=false, city?:string) => {
  const params=new URLSearchParams()
  if(refresh)params.set('refresh','true')
  if(city)params.set('city',city)
  return request<FilterOptions>(`/filter-options?${params}`)
}
export const getBudget = () => request<ReadingBudget>('/budget')
export const setDailyLimit = (daily_limit: number) => request<ReadingBudget>('/budget', {
  method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify({daily_limit}),
})
export const getPreview = () => request<Preview>('/preview')
export const getTask = (id: string, signal?: AbortSignal) => request<SearchTask>(`/tasks/${encodeURIComponent(id)}`, { signal })
export const startSearch = (body: SearchRequest) => request<SearchTask>('/tasks', {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
})
export const stopSearch = (id: string) => request<SearchTask>(`/tasks/${encodeURIComponent(id)}/stop`, { method: 'POST' })
export const refreshCurrent = (id: string) => request<SearchTask>(`/tasks/${encodeURIComponent(id)}/refresh-current`, { method: 'POST' })
export const readJobDetail = (id: string, jobId: string) => request<SearchTask>(`/tasks/${encodeURIComponent(id)}/jobs/${encodeURIComponent(jobId)}/detail`, { method: 'POST' })
export const startAutoDetails = (id: string) => request<SearchTask>(`/tasks/${encodeURIComponent(id)}/auto-details`, { method: 'POST' })
