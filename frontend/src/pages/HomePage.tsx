import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { collectorStatus, getPreview, getTask, startSearch, stopSearch, refreshCurrent, readJobDetail, startAutoDetails, getBudget, setDailyLimit } from '../api/search'
import JobDetail from '../components/JobDetail'
import type { CollectorStatus, Preview, SearchTask, ReadingBudget } from '../types/search'

const outcomes: Record<string, string> = {
  collected: '列表与 JD 读取完成', no_results: '搜索页明确无结果', exhausted: '网站明确没有更多岗位',
  load_failed: '页面加载或读取失败', quota_exhausted: 'JobPilot 今日读取额度耗尽', page_changed: '页面或搜索条件变化，已停止',
  platform_restricted: '平台验证码或访问限制', collector_error: '采集器异常', source_unavailable: '采集服务未就绪',
  unknown: '结果无法确认', stopped: '已停止',
}
const states: Record<string, string> = { running: '读取中', stopping: '停止中', completed: '已结束' }

export default function HomePage() {
  const [pages, setPages] = useState(1)
  const [status, setStatus] = useState<CollectorStatus | null>(null)
  const [task, setTask] = useState<SearchTask | null>(null)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [mode, setMode] = useState<'task' | 'preview'>('task')
  const [selected, setSelected] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [enriching, setEnriching] = useState(false)
  const [budget, setBudget] = useState<ReadingBudget | null>(null)
  const [dailyLimit, setDailyLimitDraft] = useState(20)
  const [savingLimit, setSavingLimit] = useState(false)
  const [budgetLoading, setBudgetLoading] = useState(true)
  const [budgetError, setBudgetError] = useState('')
  const [budgetNotice, setBudgetNotice] = useState('')

  async function refreshBudget() {
    setBudgetLoading(true)
    try {
      const value = await getBudget()
      setBudget(value); setBudgetError('')
      return value
    } catch (err) {
      setBudget(null)
      setBudgetError(err instanceof Error ? err.message : '无法确认剩余额度')
      throw err
    } finally { setBudgetLoading(false) }
  }

  async function requireBudget() {
    const value = await refreshBudget()
    if (value.exhausted) throw new Error(`今日已用 ${value.used} / ${value.daily_limit} 次，剩余0次。请先调整每日上限并保存，或等待次日。`)
  }

  async function saveLimit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!Number.isSafeInteger(dailyLimit) || dailyLimit < 1) { setBudgetError('每日上限必须是正整数'); return }
    setSavingLimit(true); setBudgetNotice('')
    try {
      const value = await setDailyLimit(dailyLimit)
      setBudget(value); setDailyLimitDraft(value.daily_limit); setBudgetError('')
      setError('')
      setBudgetNotice(`上限已保存为 ${value.daily_limit} 次；今日已用 ${value.used} 次，计数未清空。`)
    } catch (err) { setBudgetError(err instanceof Error ? err.message : '保存失败') }
    finally { setSavingLimit(false) }
  }

  async function refreshStatus() {
    try { setStatus(await collectorStatus()) }
    catch (err) { setError(err instanceof Error ? err.message : '后端连接失败') }
  }

  useEffect(() => {
    void refreshStatus()
    void refreshBudget().then(value => setDailyLimitDraft(value.daily_limit)).catch(() => {})
    const id = localStorage.getItem('jobpilot-direct-task-v1')
    if (id) void getTask(id).then(setTask).catch(() => localStorage.removeItem('jobpilot-direct-task-v1'))
  }, [])

  useEffect(() => {
    if (!task || task.state === 'completed') return
    const id = task.id
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      try {
        const value = await getTask(id, controller.signal)
        if (!controller.signal.aborted) {
          setTask(value)
          void refreshBudget().catch(() => {})
          if (value.state !== 'completed') timer = setTimeout(() => void poll(), 2000)
        }
      } catch (err) {
        if (!controller.signal.aborted) setError(`轮询中断；没有重新发起检索。${err instanceof Error ? err.message : ''}`)
      }
    }
    timer = setTimeout(() => void poll(), 1000)
    return () => { controller.abort(); clearTimeout(timer) }
  }, [task?.id, task?.state])

  const busy = submitting || enriching || savingLimit || (!!task && task.state !== 'completed')
  const quotaBlocked = budgetLoading || !budget || budget.exhausted
  const jobs = mode === 'preview' ? preview?.jobs || [] : task?.jobs || []
  const current = jobs.find(job => job.id === selected) || jobs[0]

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSubmitting(true); setError(''); setMode('task'); setSelected(null)
    try {
      await requireBudget()
      const value = await startSearch({ max_pages: pages })
      setTask(value)
      localStorage.setItem('jobpilot-direct-task-v1', value.id)
    } catch (err) { setError(err instanceof Error ? err.message : '启动失败，请检查专用 Chrome。') }
    finally { setSubmitting(false) }
  }

  async function loadPreview() {
    try { setPreview(await getPreview()); setMode('preview'); setSelected(null); setError('') }
    catch (err) { setError(err instanceof Error ? err.message : '无法读取样本') }
  }

  async function stop() {
    if (!task) return
    try { setTask(await stopSearch(task.id)) }
    catch (err) { setError(err instanceof Error ? err.message : '停止请求未确认，请检查任务状态。') }
  }

  async function enrich(jobId?: string) {
    if (!task || enriching) return
    setEnriching(true); setError('')
    try { await requireBudget(); setTask(await (jobId ? readJobDetail(task.id, jobId) : refreshCurrent(task.id))) }
    catch (err) { setError(err instanceof Error ? err.message : '读取失败，未自动重试。') }
    finally { setEnriching(false); void refreshBudget().catch(() => {}) }
  }

  async function autoDetails() {
    if (!task || busy) return
    setEnriching(true); setError(''); setMode('task')
    try { await requireBudget(); setTask(await startAutoDetails(task.id)) }
    catch (err) { setError(err instanceof Error ? err.message : '自动JD任务启动失败，没有自动重试。') }
    finally { setEnriching(false) }
  }

  return <main>
    <header><p className="eyebrow">JobPilot · 独立读取</p><h1>BOSS 岗位原始信息</h1>
      <p>先在专用 Chrome 手动登录、选择关键词和城市，打开搜索结果。开始后先读取列表，再自动逐个打开岗位面板获取 JD。</p></header>
    <section className="panel" aria-label="读取额度">
      <h2>每日读取额度</h2>
      {budget ? <p role="status">今日上限 {budget.daily_limit} 次 · 已用 {budget.used} 次 · <strong>剩余 {budget.remaining} 次</strong></p> : <p>{budgetLoading ? '正在读取今日额度…' : '剩余额度尚未确认，暂不能开始读取。'}</p>}
      <form className="search-form" onSubmit={event => void saveLimit(event)}>
        <label>每日读取上限<input type="number" min={1} step={1} required value={dailyLimit} disabled={busy || budgetLoading} onChange={event => setDailyLimitDraft(Number(event.target.value))} /></label>
        <button type="submit" disabled={busy || budgetLoading}>{savingLimit ? '保存中…' : '保存上限'}</button>
      </form>
      <button type="button" className="link-button" disabled={budgetLoading} onClick={() => void refreshBudget().catch(() => {})}>刷新剩余额度</button>
      <p className="note">这是 JobPilot 本地上限，不是 BOSS 官方额度。列表、手动刷新和打开 JD 共用计数，失败尝试也计数；修改上限不清空已用记录。开始前会再次核对剩余额度。</p>
      {budget && <p className="note">日期：{budget.date} · 按北京时间每日 00:00 换日 · 当前设置来源：{budget.limit_source === 'local_settings' ? '已保存的本地设置' : '环境默认值'}</p>}
      {budget?.exhausted && <p className="notice">今日剩余0次；可调整上限并保存，保存不会自动开始或恢复任务。</p>}
      {budgetError && <p role="alert" className="error">{budgetError}</p>}
      {budgetNotice && <p role="status">{budgetNotice}</p>}
    </section>
    <section className="panel">
      <form onSubmit={event => void submit(event)} className="search-form">
        <label>读取几页 / 批次<input type="number" required min={1} max={20} value={pages} disabled={busy} onChange={e => setPages(Number(e.target.value))} /></label>
        <button className="primary" disabled={busy || quotaBlocked || !status?.available}>开始读取</button>
      </form>
      <p className="note">当前已加载的岗位算第1批。列表结束后低频自动读取本次岗位的 JD，无需手动逐个点开。遇访问限制、加载失败或额度耗尽会停止，保留已取得的数据。</p>
      <p role="status">{status?.message || '正在检查本机 Chrome…'}
        <button type="button" className="link-button" onClick={() => void refreshStatus()}>检查当前搜索页</button></p>
      {status?.source_page && <p className="note">已找到：{status.source_page.title} · {status.source_page.url}</p>}
      <p className="note">首次建议1页；任务期间请勿操作该结果页，也请停止其他程序对同一 Chrome 的控制。遇登录或安全验证立即停止，不绕过。</p>
    </section>
    {error && <p role="alert" className="error">{error}</p>}
    {task && <section className="panel" aria-label="当前任务">
      <h2>{states[task.state]}{task.state === 'running' && task.phase === 'details' ? ' · 正在自动获取 JD' : ''}{task.outcome ? ` · ${task.outcome === 'collected' && (task.counts.jd_captured || 0) < task.jobs.length ? '列表读取完成，JD待补齐' : task.outcome === 'load_failed' && task.counts.detail_total ? 'JD 加载或匹配失败' : outcomes[task.outcome] || task.outcome}` : ''}</h2>
      <p>{task.message}</p><p className="note">本次请求：{task.request.max_pages}页/批次 · {task.source_page?.title || '等待读取页面'}</p>
      {task.state === 'completed' && task.outcome === 'quota_exhausted' && budget && !budget.exhausted && <p className="note">当前额度已有剩余；上方是该任务之前的停止原因。保存设置不会自动恢复任务，可手动点击“自动补齐本次 JD”。</p>}
      <p>已读 {task.counts.pages_read || 0} / {task.request.max_pages} 批 · 本次唯一岗位 {task.counts.unique_jobs || 0} · 跨批重复引用 {task.counts.duplicate_observations || 0}</p>
      <p>已读取 JD：{task.counts.jd_captured || 0} / {task.jobs.length} 个{task.phase === 'details' ? ` · 已处理 ${task.counts.detail_processed || 0} / ${task.counts.detail_total || task.jobs.length}` : ''}。未读取的不会用历史数据填充。</p>
      <p className="note">原因码：{task.reason_code || '等待结果'} · JobPilot任务：{task.id}</p>
      {task.state !== 'completed' && <button type="button" disabled={task.state === 'stopping'} onClick={() => void stop()}>停止本次读取</button>}
      <button type="button" className="link-button" onClick={() => { setMode('task'); setSelected(null) }}>查看本次结果</button>
      {task.state === 'completed' && <button type="button" disabled={busy || quotaBlocked} onClick={() => void enrich()}>刷新薪资与当前 JD</button>}
      {task.state === 'completed' && !!task.jobs.length && (task.counts.jd_captured || 0) < task.jobs.length && <button type="button" disabled={busy || quotaBlocked || task.outcome === 'platform_restricted'} onClick={() => void autoDetails()}>自动补齐本次 JD</button>}
      <p className="note">本次操作共用上方显示的每日额度；不足时保留部分结果，不保证全部岗位都能获取JD。</p>
      {!!task.page_snapshots.length && <details><summary>本次读取快照摘要（直接 DOM）</summary><pre>{JSON.stringify(task.page_snapshots.map(p => ({source: p.source_layer, batch: p.payload.batch, url: p.payload.url, card_count: p.payload.cards.length})), null, 2)}</pre></details>}
    </section>}
    <div className="result-heading"><h2>{mode === 'preview' ? '旧版 BossHunter 历史样本' : '本次直接读取的岗位'} · {jobs.length}</h2>
      <button type="button" onClick={() => void loadPreview()}>查看旧版历史样本（非本次读取）</button></div>
    {mode === 'preview' && <p className="notice">{preview?.message} 保存于 {preview?.captured_at}；不会触发平台采集。</p>}
    {!jobs.length ? <p className="empty">尚无本次直接读取的岗位，不会用历史样本填充结果。</p> :
      <div className="results"><nav aria-label="岗位结果"><ul>{jobs.map(job => <li key={job.id}>
        <button type="button" aria-pressed={current?.id === job.id} onClick={() => setSelected(job.id)}>
          <strong>{String(job.fields.title || '岗位名称未提供')}</strong><span>{String(job.fields.company || '公司未提供')}</span>
          <span>{typeof job.fields.salary === 'string' && /[\uE000-\uF8FF]/.test(job.fields.salary) ? '薪资待刷新' : String(job.fields.salary || (job.fields.salary_raw ? '薪资字形未验证' : '薪资未提供'))} · {String(job.fields.location || job.fields.city || '地点未提供')}</span>
        </button></li>)}</ul></nav>{current && <JobDetail key={current.id} job={current} busy={busy || quotaBlocked} onReadJD={mode === 'task' ? () => void enrich(current.id) : undefined} />}</div>}
  </main>
}
