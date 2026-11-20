import { useState } from 'react'
import type { JobView } from '../types/search'

const labels = [
  ['location', '列表地点原文'], ['work_address', '详细工作地址'], ['experience', '经验'], ['education', '学历'],
  ['tags', '列表标签原文'],
] as const

function display(value: unknown) {
  if (value === null || value === undefined || value === '' || (Array.isArray(value) && !value.length)) return '未提供 / 当前未采集'
  return typeof value === 'string' ? (/[\uE000-\uF8FF]/.test(value) ? '含未验证字形，请刷新薪资；原文保留在 JSON。' : value) : JSON.stringify(value)
}

export function jobSalary(job: JobView) {
  const { salary, salary_raw } = job.fields
  if (typeof salary === 'string' && /[\uE000-\uF8FF]/.test(salary)) return '薪资待刷新'
  return String(salary || (salary_raw ? '薪资字形未验证' : '薪资未提供'))
}

function formatJson(value: object) {
  return JSON.stringify(value, null, 2).replace(/[\uE000-\uF8FF]/g, ch => '\\u' + ch.charCodeAt(0).toString(16).padStart(4, '0'))
}

function JsonCode({ value, label }: { value: string; label: string }) {
  const parts = value.split(/("(?:\\.|[^"\\])*"|\b(?:true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g)
  return <pre className="json-code" tabIndex={0} aria-label={label}><code>{parts.map((part, index) =>
    index % 2 ? <span className={part.startsWith('"') ? /^\s*:/.test(parts[index + 1] || '') ? 'json-key' : 'json-string' : 'json-value'} key={index}>{part}</span> : part
  )}</code></pre>
}

export default function JobDetail({ job, onReadJD, busy = false }: { job: JobView; onReadJD?: () => void; busy?: boolean }) {
  const [copied, setCopied] = useState('')
  const [rawOpen, setRawOpen] = useState(false)
  const direct = job.raw.source_layer === 'search_list_dom'
  const listSnapshot = { ...job.raw,
    salary_display: { value: job.fields.salary, raw: job.fields.salary_raw, decoding: job.fields.salary_decoding },
    jd_identity_check: job.fields.jd_identity, jd_diagnostic: job.fields.jd_diagnostic,
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(formatJson({ ...listSnapshot, detail_raw: job.detail_raw || null }))
      setCopied('已复制')
    } catch { setCopied('复制失败，请手动选中 JSON 复制。') }
  }

  return <section className="detail" aria-label="岗位详情">
    <header className="job-detail-heading">
      <div><h2>{display(job.fields.title)}</h2><p>{display(job.fields.company)} <strong>{jobSalary(job)}</strong></p></div>
      {typeof job.fields.url === 'string' ? <a className="button" href={job.fields.url} target="_blank" rel="noreferrer">打开原岗位 ↗</a> : <span className="note">岗位链接未提供</span>}
    </header>
    <h3>岗位基本信息</h3>
    <dl className="detail-facts">{labels.map(([key, label]) => <div key={key}><dt>{label}</dt><dd>{display(job.fields[key])}</dd></div>)}</dl>
    {typeof job.fields.salary_warning === 'string' && <p className="notice">薪资说明：{job.fields.salary_warning}</p>}
    <h3>{direct ? '职位描述（JD）' : '历史样本中的可用 JD'}</h3>
    <div className="jd">{typeof job.fields.jd === 'string' && job.fields.jd ? job.fields.jd : direct ? '尚未读取该岗位的职位描述。列表卡片不包含完整 JD。' : display(job.fields.jd)}</div>
    {direct && <>
      {job.fields.jd_title_differs === true && <p className="note">详情标题：{display(job.fields.detail_title)}。列表与详情标题不同，已按同一岗位链接及选中状态确认，原文分别保留。</p>}
      <p className="note">JD 状态：{job.fields.jd_status === 'captured' ? '已获取' : job.fields.jd_status === 'reading' ? '正在获取' : job.fields.jd_status === 'failed' ? '获取停止 / 失败' : '等待自动读取'}</p>
      {typeof job.fields.jd_error === 'string' && job.fields.jd_error && <p className="notice">{job.fields.jd_error}</p>}
      {job.fields.jd_diagnostic != null && <details><summary>JD 读取诊断</summary><pre>{JSON.stringify(job.fields.jd_diagnostic, null, 2)}</pre></details>}
      {onReadJD && <button type="button" disabled={busy} onClick={onReadJD}>读取当前已打开的此岗位 JD</button>}
      <p className="note">新任务自动逐个打开本次岗位面板；已有列表可点「补齐 JD」。单次读取仅用于手动查看后的刷新，不会点击收藏、沟通或投递。</p>
      {job.detail_raw && <p className="note">JD 来源：{job.detail_raw.source_layer} · {job.detail_raw.captured_at}</p>}
      <details className="raw-data"><summary>列表卡片文本</summary>
        <div className="card-text">{display(job.fields.list_text_display ?? job.fields.list_text)}</div>
        <p className="note">可显示文本中的薪资按已验证字体还原；原始字符完整保留在 JSON。未获取的信息不推断或补齐。</p>
      </details>
    </>}
    <details className="raw-data" onToggle={event => setRawOpen(event.currentTarget.open)}>
      <summary>查看原始 JSON</summary>
      {rawOpen && <>
        <p className="note">{direct ? '列表与职位描述分别记录自实际 DOM 文本，不是网络原始 JSON。' : '旧版历史样本：BossHunter 本地 API 加工结果，不属于本次独立读取。'}</p>
        <div className="tabs"><button type="button" onClick={() => void copy()}>复制 JSON</button><span role="status">{copied}</span></div>
        <h4>{direct ? '搜索列表快照' : '历史样本快照'}</h4>
        <p className="note">{job.raw.note} · {job.raw.endpoint} · {job.raw.captured_at} · 脱敏字段 {job.raw.redactions.length} 项</p>
        <JsonCode label="列表快照 JSON" value={formatJson(listSnapshot)}/>
        {job.detail_raw && <><h4>详情页快照</h4><JsonCode label="详情页快照 JSON" value={formatJson(job.detail_raw)}/></>}
      </>}
    </details>
  </section>
}
