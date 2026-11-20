import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { collectorStatus, getPreview, getTask, startSearch, stopSearch, refreshCurrent, readJobDetail, startAutoDetails, getBudget, setDailyLimit, getFilterOptions } from '../api/search'
import ResultsWorkspace, { states } from '../components/ResultsWorkspace'
import FilterChoices from '../components/FilterChoices'
import ModelSettings from '../components/ModelSettings'
import ResumeUploads from '../components/ResumeUploads'
import AiReview from '../components/AiReview'
import WorkspaceIcon from '../components/WorkspaceIcon'
import type { CollectorStatus, Preview, SearchTask, ReadingBudget, FilterOptions, NativeFilters } from '../types/search'


const workspace = {
  filters: { label: '筛选条件', title: '筛选条件', subtitle: '选择本次读取的岗位范围；关键词沿用 BOSS 当前搜索页。' },
  results: { label: '采集结果', title: '采集结果', subtitle: '追踪当前任务，查看岗位信息、职位描述与原始快照。' },
  resumes: { label: '简历上传', title: '简历档案', subtitle: '上传并解析最新简历，管理 AI 复筛使用的个人档案。' },
  review: { label: 'AI 复筛', title: 'AI 岗位复筛', subtitle: '按匹配度整理本次岗位，快速定位技术匹配点和关键差距。' },
  config: { label: '配置', title: '工作台配置', subtitle: '配置模型连接与读取额度，管理本机运行参数。' },
} as const
const tabs = ['filters', 'results', 'resumes', 'review', 'config'] as const

export default function HomePage() {
  const [tab,setTab]=useState<keyof typeof workspace>('filters')
  const [pages, setPages] = useState(1)
  const [filterOptions, setFilterOptions] = useState<FilterOptions | null>(null)
  const [filters, setFilters] = useState<NativeFilters>({city:'',job_type:['0'],salary:['0'],experience:['0'],regions:[]})
  const filterLoad = useRef(0)
  const [loadingFilters, setLoadingFilters] = useState(false)
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

  async function refreshFilters(refresh=false, city?:string) {
    const sequence=++filterLoad.current
    setLoadingFilters(true)
    try {
      const value=await getFilterOptions(refresh,city)
      if(sequence!==filterLoad.current)return
      setFilterOptions(value)
      setFilters(previous=>({...previous,city:value.cities.some(c=>c.value===previous.city) ? previous.city :
        value.cities.find(c=>c.label===value.current.city)?.value || value.cities[0]?.value || ''}))
    } catch(err) {if(sequence===filterLoad.current)setError(err instanceof Error ? err.message : '无法读取BOSS筛选选项')}
    finally {if(sequence===filterLoad.current)setLoadingFilters(false)}
  }

  useEffect(()=>{
    if(filters.city && filterOptions?.region_city!==filters.city)void refreshFilters(false,filters.city)
  },[filters.city])

  useEffect(() => {
    void refreshStatus()
    void refreshFilters()
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
  const groupCount=filters.job_type.length*filters.salary.length
  const plannedBatches=pages*groupCount
  const regionsReady=filterOptions?.region_city===filters.city
  const jobs = mode === 'preview' ? preview?.jobs || [] : task?.jobs || []
  const current = jobs.find(job => job.id === selected) || jobs[0]

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSubmitting(true); setError(''); setMode('task'); setSelected(null)
    try {
      await requireBudget()
      if(!filterOptions || !filters.city || !regionsReady) throw new Error('请先读取当前城市的BOSS筛选和区域选项。')
      if(plannedBatches>20)throw new Error('每组批次×组合数不能超过20批，请缩小本次范围。')
      const value = await startSearch({ max_pages: pages, filters })
      setTask(value)
      setTab('results')
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

  return <div className="workspace-shell">
    <aside className="workspace-sidebar">
      <div className="workspace-brand"><span className="brand-icon"><WorkspaceIcon name="brand"/></span><div><strong>JobPilot<span>.</span></strong><small>求职工作台</small></div></div>
      <p className="sidebar-caption">工作区</p>
      <div className="workspace-tabs" role="tablist" aria-label="工作台栏目">
      {tabs.map((id,index)=><button key={id} id={`tab-${id}`} role="tab" tabIndex={tab===id ? 0 : -1} aria-selected={tab===id} aria-controls={`panel-${id}`} onClick={()=>setTab(id)} onKeyDown={event=>{
        if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End'].includes(event.key))return
        event.preventDefault()
        const next=event.key==='Home' ? 0 : event.key==='End' ? tabs.length-1 : (index+(['ArrowRight','ArrowDown'].includes(event.key) ? 1 : tabs.length-1))%tabs.length
        setTab(tabs[next]);document.getElementById(`tab-${tabs[next]}`)?.focus()
      }}><WorkspaceIcon name={id}/>{workspace[id].label}</button>)}
      </div>
      <div className="sidebar-footer"><span className="status-dot" data-online={status?.available === true}/><div><strong>专用 Chrome</strong><small>{status ? status.available ? '可读取搜索页' : '搜索页未就绪' : '检查连接中'}</small></div></div>
    </aside>
    <main>
    <header className="page-header">
      <div><h1>{workspace[tab].title}</h1><p>{workspace[tab].subtitle}</p></div>
      <div className="page-actions">
        {tab==='results' && task && <span className="status-badge" data-state={task.state}>{states[task.state]}{task.state==='running' && task.phase==='details' ? ' · 获取 JD' : ''}</span>}
        {tab==='filters' && <button className="primary" form="search-filters" disabled={busy || quotaBlocked || loadingFilters || !regionsReady || plannedBatches>20 || !status?.available}>{submitting ? '正在启动…' : '开始读取'}</button>}
      </div>
    </header>
    {error && (tab==='filters' || tab==='results') && <p role="alert" className="error">{error}</p>}
    <div role="tabpanel" id="panel-review" aria-labelledby="tab-review" hidden={tab!=='review'}>
      {tab==='review' && <AiReview key={task?.id} task={mode==='task' ? task : null}/>}
    </div>
    <div role="tabpanel" id="panel-resumes" aria-labelledby="tab-resumes" hidden={tab!=='resumes'}>
      {tab==='resumes' && <ResumeUploads/>}
    </div>
    <div role="tabpanel" id="panel-config" aria-labelledby="tab-config" hidden={tab!=='config'}>
    <div className="settings-grid">
    {tab==='config' && <ModelSettings/>}
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
    </div>
    </div>
    <div role="tabpanel" id="panel-filters" aria-labelledby="tab-filters" hidden={tab!=='filters'}>
    <section className="panel">
      <div className="section-heading"><div><h2>岗位初筛条件</h2><p>先在专用 Chrome 手动登录并搜索关键词，再设置本次读取范围。</p></div><span className="version-badge">BOSS 直读</span></div>
      <form id="search-filters" onSubmit={event => void submit(event)} className="filter-form">
        <label>工作城市<select aria-label="工作城市" required value={filters.city} disabled={busy} onChange={e=>setFilters({...filters,city:e.target.value,regions:[]})}>
          <option value="">请选择城市</option>{filterOptions?.cities.map(c=><option key={c.value} value={c.value}>{c.label}</option>)}</select></label>
        <label>每组读取几页 / 批次<input type="number" required min={1} max={20} value={pages} disabled={busy} onChange={e => setPages(Number(e.target.value))} /></label>

        <div className="filter-options-grid">
          <FilterChoices label="工作区域" regions options={regionsReady ? filterOptions?.regions || [] : []} value={filters.regions} disabled={busy || loadingFilters || !regionsReady} onChange={regions=>setFilters({...filters,regions})}/>
          {([['job_type','求职类型'],['salary','薪资待遇'],['experience','工作经验']] as const).map(([key,label])=><FilterChoices key={key} label={label} value={filters[key]} options={filterOptions?.[key] || []} disabled={busy || loadingFilters || !filterOptions} onChange={value=>setFilters({...filters,[key]:value})}/>)}
        </div>
      </form>
      <button type="button" className="link-button" disabled={busy || loadingFilters} onClick={()=>void refreshFilters(true,filters.city || undefined)}>{loadingFilters ? '正在读取选项…' : '刷新BOSS筛选选项'}</button>
      <p className="notice">本次 {groupCount} 组检索 × 每组 {pages} 批，列表最多读取 {plannedBatches} 次；另计需要打开的 JD。{budget && `今日剩余 ${budget.remaining} 次。`}{plannedBatches>20 ? '超过单任务20批，请缩小范围。' : '额度不足时保留部分结果并停止。'}</p>
      <p role="status">{status?.message || '正在检查本机 Chrome…'}
        <button type="button" className="link-button" onClick={() => void refreshStatus()}>检查当前搜索页</button></p>
      {status?.source_page && <p className="note">已找到：{status.source_page.title} · {status.source_page.url}</p>}
      <p className="notice">自动读取JD时会将专用Chrome的BOSS标签页切到前台，并等待页面绘制。请勿切换该窗口的标签或最小化；可在另一个浏览器窗口查看JobPilot进度。</p>
      <details className="operating-notes">
        <summary>查看筛选与读取规则</summary>
        <p className="note">同一条件内任一满足，不同条件同时满足。“经验不限”是平台的一种经验要求，与不筛选的“不限”不同。经验和行政区使用原生多选；求职类型、薪资原生只能单选，JobPilot拆成组合依次检索，结果按岗位链接合并去重。</p>
        <p className="note">每组先提交条件，核对BOSS实际选中项后再读列表与JD。保留当前公开关键词，清除其他旧条件（包括商圈、地铁）。每组搜索加载与首批读取合计一次额度；遇限制或加载失败停止全部组合，不自动重试。</p>
        <p className="note">首次建议1页；任务期间请勿操作该结果页，也请停止其他程序对同一 Chrome 的控制。遇登录或安全验证立即停止，不绕过。</p>
      </details>
    </section>
    </div>
    <div role="tabpanel" id="panel-results" aria-labelledby="tab-results" hidden={tab!=='results'}>
      <ResultsWorkspace task={task} preview={mode==='preview' ? preview : null} budget={budget} jobs={jobs} current={current}
        busy={busy || quotaBlocked} onSelect={setSelected} onPreview={() => void loadPreview()}
        onShowTask={() => { setMode('task'); setSelected(null) }} onStop={() => void stop()}
        onRefresh={() => void enrich()} onAutoDetails={() => void autoDetails()} onReadJD={id => void enrich(id)}/>
    </div>
    </main>
  </div>
}
