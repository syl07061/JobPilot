import type { CollectorStatus, Preview, SearchRequest, SearchTask, ReadingBudget, FilterOptions } from '../types/search'

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
