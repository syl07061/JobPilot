export interface NativeFilters {city: string; job_type: string[]; salary: string[]; experience: string[]; regions: string[]}
export interface FilterOption {label: string; value: string}
export interface FilterOptions {
  cities: FilterOption[]; job_type: FilterOption[]; salary: FilterOption[]; experience: FilterOption[]
  regions: FilterOption[]; region_city: string
  current: {city: string | null}; source: string; captured_at: string
}
export interface SearchRequest { max_pages: number; filters?: NativeFilters | null }
export interface ReadingBudget {
  date: string; timezone: string; daily_limit: number; used: number; remaining: number
  exhausted: boolean; reset_at: string; limit_source: 'local_settings' | 'environment_default'
}
export interface RawSnapshot {
  source_layer: 'local_api' | 'search_list_dom' | 'detail_dom'; source: string; endpoint: string; captured_at: string
  payload: Record<string, unknown>; redactions: string[]; note: string
}
export interface JobView { id: string; fields: Record<string, unknown>; raw: RawSnapshot; detail_raw?: RawSnapshot | null }
export interface SearchTask {
  id: string; request: SearchRequest; state: 'running' | 'stopping' | 'completed'
  phase?: 'filters' | 'list' | 'details' | 'completed'
  applied_filters?: {requested: Record<string, FilterOption>; active_group?: Record<string,FilterOption>; effective?: Record<string,string>; verified: boolean; group_index?: number; group_count?: number} | null
  search_groups?: {group_index:number;outcome:string;message:string;pages_read:number}[]
  outcome: string | null; reason_code: string; message: string
  counts: Record<string, number>; jobs: JobView[]
  source_page: { url: string; title: string } | null
  page_snapshots: { source_layer?: string; payload: { batch: number; url: string; cards: unknown[] } }[]
}
export interface CollectorStatus { available: boolean; message: string; collector?: string; code?: string; source_page?: {url: string; title: string} }
export interface Preview { historical_sample: true; captured_at: string; message: string; jobs: JobView[] }
