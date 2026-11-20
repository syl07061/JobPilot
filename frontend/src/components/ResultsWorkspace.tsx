import { useLayoutEffect, useRef, useState } from 'react'
import type { JobView, Preview, ReadingBudget, SearchTask } from '../types/search'
import JobDetail, { jobSalary } from './JobDetail'

export const states = { running: '读取中', stopping: '停止中', completed: '已结束' } as const
const outcomes: Record<string, string> = {
  collected: '列表与 JD 读取完成', no_results: '搜索页明确无结果', exhausted: '网站明确没有更多岗位',
  load_failed: '页面加载或读取失败', quota_exhausted: 'JobPilot 今日读取额度耗尽', page_changed: '页面或搜索条件变化，已停止',
  platform_restricted: '平台验证码或访问限制', collector_error: '采集器异常', source_unavailable: '采集服务未就绪',
  unknown: '结果无法确认', stopped: '已停止', filter_failed: '未能确认 BOSS 原生筛选',
}

interface Props {
  task: SearchTask | null
  preview: Preview | null
  budget: ReadingBudget | null
  jobs: JobView[]
  current?: JobView
  busy: boolean
  onSelect: (id: string) => void
  onPreview: () => void
  onShowTask: () => void
  onStop: () => void
  onRefresh: () => void
  onAutoDetails: () => void
  onReadJD: (id: string) => void
}

function tags(job: JobView) {
  const { experience, education, tags } = job.fields
  return [...new Set([experience, education, ...(Array.isArray(tags) ? tags : [])]
    .filter((value): value is string => typeof value === 'string' && !!value.trim()))].slice(0, 6)
}

function taskOutcome(task: SearchTask) {
  if (task.outcome === 'collected' && (task.counts.jd_captured || 0) < task.jobs.length) return '列表读取完成，JD 待补齐'
  if (task.outcome === 'load_failed' && task.counts.detail_total) return 'JD 加载或匹配失败'
  return task.outcome ? outcomes[task.outcome] || task.outcome : states[task.state]
}

export default function ResultsWorkspace({ task, preview, budget, jobs, current, busy, onSelect,
  onPreview, onShowTask, onStop, onRefresh, onAutoDetails, onReadJD }: Props) {
  const [mobileDetail, setMobileDetail] = useState(false)
  const detailBack = useRef<HTMLButtonElement>(null)
  const detailPane = useRef<HTMLDivElement>(null)
  const jobButtons = useRef(new Map<string, HTMLButtonElement>())
  const outcome = task && taskOutcome(task)
  const issue = task?.outcome && !['collected', 'no_results', 'exhausted', 'stopped'].includes(task.outcome)
  const jdCount = task?.counts.jd_captured || 0

  useLayoutEffect(() => { detailPane.current?.scrollTo(0, 0) }, [current?.id, mobileDetail])

  function selectJob(id: string) {
    onSelect(id)
    setMobileDetail(true)
    requestAnimationFrame(() => detailBack.current?.focus({ preventScroll: true }))
  }

  function showList() {
    setMobileDetail(false)
    requestAnimationFrame(() => { if (current) jobButtons.current.get(current.id)?.focus({ preventScroll: true }) })
  }

  return <>
    <div className="results-toolbar">
      <div className="results-overview">
        <span><strong>{jobs.length}</strong> {preview ? '历史岗位' : '个岗位'}</span>
        {!preview && task && <span>JD <strong>{jdCount}/{task.jobs.length}</strong></span>}
        {budget && <span>今日剩余 <strong>{budget.remaining}</strong> 次</span>}
      </div>
      <div className="tabs">
        {task && task.state !== 'completed' && <button type="button" disabled={task.state === 'stopping'} onClick={onStop}>停止读取</button>}
        {!preview && task?.state === 'completed' && <button type="button" disabled={busy} onClick={onRefresh}>刷新薪资与当前 JD</button>}
        {!preview && task?.state === 'completed' && jdCount < task.jobs.length && <button className="primary" type="button" disabled={busy || task.outcome === 'platform_restricted'} onClick={onAutoDetails}>补齐 JD</button>}
        {preview ? <button type="button" onClick={() => { setMobileDetail(false); onShowTask() }}>返回本次结果</button> :
          <button type="button" onClick={() => { setMobileDetail(false); onPreview() }}>查看历史样本</button>}
      </div>
    </div>
    {task && issue && <p className={task.outcome === 'quota_exhausted' ? 'notice' : 'error'} role="alert"><strong>{outcome}。</strong> {task.message}</p>}
    {preview && <p className="notice">{preview.message} 保存于 {preview.captured_at}；不会触发平台采集。</p>}
    {task && <details className="task-log" key={task.id}>
      <summary>查看采集详情 <span>{outcome}</span></summary>
      <div className="task-log-body">
        <p>{task.message}</p>
        <p>每组 {task.request.max_pages} 页 / 批 · {task.source_page?.title || '等待读取页面'}</p>
        {task.applied_filters && <div>
          <strong>{task.applied_filters.verified ? 'BOSS 筛选已核对' : '尚未确认筛选生效'}</strong>
          <p>检索条件：{Object.values(task.applied_filters.requested).map(option => option.label).join(' · ')}</p>
          {task.applied_filters.active_group && <p>第 {task.applied_filters.group_index}/{task.applied_filters.group_count} 组：{Object.values(task.applied_filters.active_group).map(option => option.label).join(' · ')}</p>}
          {task.applied_filters.effective && <p>页面回显：{Object.values(task.applied_filters.effective).join(' · ')}</p>}
        </div>}
        <p>已读 {task.counts.pages_read || 0} / {task.counts.pages_requested || task.request.max_pages} 批 · 唯一岗位 {task.counts.unique_jobs || 0} · 重复引用 {task.counts.duplicate_observations || 0}</p>
        {!!task.search_groups?.length && <details><summary>各检索组合结果（{task.search_groups.length} 组已结束）</summary><ul>{task.search_groups.map(group => <li key={group.group_index}>第 {group.group_index} 组：{outcomes[group.outcome] || group.outcome} · {group.pages_read} 批 · {group.message}</li>)}</ul></details>}
        <p>JD：{jdCount}/{task.jobs.length}{task.phase === 'details' ? ` · 已处理 ${task.counts.detail_processed || 0}/${task.counts.detail_total || task.jobs.length}` : ''}。不会用历史数据填充。</p>
        {budget && <p>今日额度：已用 {budget.used}/{budget.daily_limit} 次 · 剩余 {budget.remaining} 次。</p>}
        {task.outcome === 'quota_exhausted' && budget && !budget.exhausted && <p>当前已有剩余额度；不会自动恢复，可手动补齐 JD。</p>}
        <p>原因码：{task.reason_code || '等待结果'} · 任务 ID：{task.id}</p>
        {!!task.page_snapshots.length && <details><summary>本次读取快照摘要（直接 DOM）</summary><pre>{JSON.stringify(task.page_snapshots.map(page => ({source: page.source_layer, batch: page.payload.batch, url: page.payload.url, card_count: page.payload.cards.length})), null, 2)}</pre></details>}
      </div>
    </details>}
    {!jobs.length ? <div className="empty">{preview ? '历史样本没有岗位。' : task?.state === 'running' ? '正在读取岗位，结果会陆续出现在这里。' : task ? '本次没有读到岗位。可展开采集详情查看原因。' : '暂无采集结果。请先在筛选条件页开始读取。'}</div> :
      <div className="results" data-mobile-detail={mobileDetail}>
        <section className="job-list-pane" aria-label="岗位列表">
          <h2>{preview ? '历史样本' : '岗位列表'} <span>{jobs.length}</span></h2>
          <nav className="job-list" aria-label="岗位结果"><ul>{jobs.map(job => {
            const jobTags = tags(job)
            return <li key={job.id}>
            <button ref={node => { if (node) jobButtons.current.set(job.id, node); else jobButtons.current.delete(job.id) }}
              type="button" aria-pressed={current?.id === job.id} onClick={() => selectJob(job.id)}>
              <strong className="job-list-title">{String(job.fields.title || '岗位名称未提供')}</strong>
              <span>{String(job.fields.company || '公司未提供')}</span>
              <span className="job-meta"><strong>{jobSalary(job)}</strong>{String(job.fields.location || job.fields.city || '地点未提供')}</span>
              {jobTags.length > 0 && <span className="job-tags">{jobTags.map(tag => <span key={tag}>{tag}</span>)}</span>}
            </button>
          </li>})}</ul></nav>
        </section>
        {current && <div className="job-detail-pane" ref={detailPane} tabIndex={0} aria-label="可滚动岗位详情">
          <button className="mobile-back" ref={detailBack} type="button" onClick={showList}>← 返回岗位列表</button>
          <JobDetail key={current.id} job={current} busy={busy} onReadJD={preview ? undefined : () => onReadJD(current.id)}/>
        </div>}
      </div>}
  </>
}
