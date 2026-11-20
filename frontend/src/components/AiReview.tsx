import { useEffect, useState } from 'react'
import { getReview, startReview, type ReviewResult } from '../api/search'
import type { SearchTask } from '../types/search'

export default function AiReview({ task }: { task: SearchTask | null }) {
  const [result, setResult] = useState<ReviewResult | null>(null)
  const [error, setError] = useState('')
  const [starting, setStarting] = useState(false)
  const [ready, setReady] = useState(false)
  const [stale, setStale] = useState(false)
  const id = task?.id

  useEffect(() => {
    if (!id || starting) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    async function poll(taskId: string) {
      try {
        const status = await getReview(taskId, controller.signal)
        if (controller.signal.aborted) return
        setResult(status.result)
        setStale(status.stale)
        setReady(true)
        if (status.result?.state === 'running') timer = setTimeout(() => void poll(taskId), 1500)
      } catch (error) {
        if (!controller.signal.aborted) {
          setReady(false)
          setResult(null)
          setError((error as Error).message)
        }
      }
    }
    void poll(id)
    return () => { controller.abort(); clearTimeout(timer) }
  }, [id, starting])

  async function start() {
    if (!id || !ready) return
    setStarting(true)
    setError('')
    try { setResult(await startReview(id)); setStale(false) }
    catch (error) { setError((error as Error).message) }
    finally { setStarting(false) }
  }

  return <section className="panel review-panel">
    <div className="section-heading">
      <div><h2>岗位匹配报告</h2><p>评估技术栈、相关经历和硬性要求；软性条件不计分。</p></div>
      <button className="primary" disabled={!ready || !task?.jobs.length || task.state !== 'completed' || starting || result?.state === 'running'}
        onClick={() => void start()}>{starting ? '正在启动…' : result || stale ? '重新 AI 复筛' : '开始 AI 复筛'}</button>
    </div>
    {!task?.jobs.length && <p className="empty">请先完成采集；历史样本不参与复筛。</p>}
    {error && <p role="alert" className="error">{error}</p>}
    {stale && <p role="status">旧版复筛结果已隐藏。请按新标准重新复筛，无需重新采集。</p>}
    {result && <>
      <div className="review-progress">
        <p role="status">简历 V{result.resume_version} · {result.state === 'running' ? '复筛中' : result.state === 'completed' ? '复筛结束' : '复筛失败'} · 已处理 {result.processed}/{result.total}</p>
        <progress aria-label="复筛进度" value={result.processed} max={Math.max(1, result.total)}/>
      </div>
      {result.message && <p role="alert">{result.message}</p>}
      <h3>岗位匹配度分析 · {result.matches.length}</h3>
      {result.matches.map(job => {
        const matched = job.requirements.filter(item => item.match === '匹配')
        const gaps = job.requirements.filter(item => item.match !== '匹配')
        return <details className="review-card" key={job.id}>
          <summary><span className="review-score">{job.score}<small> / 100</small></span><span><strong>{job.title}</strong><small>{job.employer}</small></span></summary>
          <div className="review-breakdown">
            <section className="review-match">
              <h4>匹配点</h4>
              {matched.length ? <ul>{matched.map((item, index) => <li key={index}>
                <strong>{item.requirement}</strong>：{item.reason}
              </li>)}</ul> : <p>暂无明确匹配项</p>}
            </section>
            <section className="review-gap">
              <h4>差距与待确认</h4>
              {gaps.length ? <ul>{gaps.map((item, index) => <li key={index}>
                <strong>{item.requirement}</strong>（{item.priority} · {item.match}）：{item.reason}
              </li>)}</ul> : <p>暂无明确差距</p>}
            </section>
          </div>
        </details>
      })}
      {result.excluded.length > 0 && <details>
        <summary>未评分岗位 · {result.excluded.length}</summary>
        <ul>{result.excluded.map(job => <li key={job.id}>{job.title}{job.company && ` · ${job.company}`}：{job.reason}</li>)}</ul>
      </details>}
    </>}
  </section>
}
