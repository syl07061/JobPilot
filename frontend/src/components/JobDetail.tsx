import { useState } from 'react'
import type { JobView } from '../types/search'

const labels: [string, string][] = [
  ['title', '岗位名称'], ['company', '公司'], ['location', '列表地点原文'], ['city', '城市字段'],
  ['salary', '薪资范围'], ['experience', '经验'], ['education', '学历'],
  ['tags', '列表标签原文'],
]

function display(value: unknown) {
  if (value === null || value === undefined || value === '' || (Array.isArray(value) && !value.length)) return '未提供 / 当前未采集'
  return typeof value === 'string' ? (/[\uE000-\uF8FF]/.test(value) ? '含未验证字形，请刷新薪资；原文保留在 JSON。' : value) : JSON.stringify(value)
}

export default function JobDetail({ job, onReadJD, busy = false }: { job: JobView; onReadJD?: () => void; busy?: boolean }) {
  const [mode, setMode] = useState<'formatted' | 'raw'>('formatted')
  const [copied, setCopied] = useState('')
  const rawText = JSON.stringify({ ...job.raw,
    salary_display: {value: job.fields.salary, raw: job.fields.salary_raw, decoding: job.fields.salary_decoding},
    detail_raw: job.detail_raw || null,
    jd_identity_check: job.fields.jd_identity, jd_diagnostic: job.fields.jd_diagnostic,
  }, null, 2).replace(/[\uE000-\uF8FF]/g, ch => `\\u${ch.charCodeAt(0).toString(16).padStart(4, '0')}`)
  async function copy() {
    try { await navigator.clipboard.writeText(rawText); setCopied('已复制') }
    catch { setCopied('复制失败，请手动选中 JSON 复制。') }
  }
  return <section className="detail" aria-label="岗位详情">
    <div className="tabs">
      <button type="button" aria-pressed={mode === 'formatted'} onClick={() => setMode('formatted')}>格式化详情</button>
      <button type="button" aria-pressed={mode === 'raw'} onClick={() => setMode('raw')}>查看原始数据</button>
    </div>
    <p className="notice">{job.raw.source_layer === 'search_list_dom'
      ? '列表与职位描述分别记录来源：search_list_dom / detail_dom，均为实际 DOM 文本，不是网络原始 JSON。'
      : '旧版历史样本：BossHunter 本地 API 加工结果，不属于本次独立读取。'}</p>
    {mode === 'raw' ? <>
      <p>{job.raw.note}</p><p className="note">{job.raw.endpoint} · {job.raw.captured_at} · 脱敏字段 {job.raw.redactions.length} 项</p>
      <button type="button" onClick={() => void copy()}>复制 JSON</button><span role="status"> {copied}</span>
      <pre tabIndex={0}>{rawText}</pre>
    </> : <>
      <dl>{labels.map(([key, label]) => <div key={key}><dt>{label}</dt><dd>{display(job.fields[key])}</dd></div>)}</dl>
      {typeof job.fields.salary_warning === 'string' && <p className="notice">薪资说明：{job.fields.salary_warning}</p>}
      {job.raw.source_layer === 'search_list_dom' ? <>
        <h3>职位描述（JD）</h3>
        <div className="jd">{job.fields.jd ? display(job.fields.jd) : '尚未读取该岗位的职位描述。列表卡片不包含完整 JD。'}</div>
        {job.fields.jd_title_differs === true && <p className="note">详情标题：{display(job.fields.detail_title)}。列表与详情标题不同，已按同一岗位链接及选中状态确认，原文分别保留。</p>}
        <p className="note">JD 状态：{job.fields.jd_status === 'captured' ? '已获取' : job.fields.jd_status === 'reading' ? '正在获取' : job.fields.jd_status === 'failed' ? '获取停止 / 失败' : '等待自动读取'}</p>
        {typeof job.fields.jd_error === 'string' && job.fields.jd_error && <p className="notice">{job.fields.jd_error}</p>}
        {job.fields.jd_diagnostic != null && <details><summary>JD 读取诊断</summary><pre>{JSON.stringify(job.fields.jd_diagnostic, null, 2)}</pre></details>}
        {onReadJD && <button type="button" disabled={busy} onClick={onReadJD}>读取当前已打开的此岗位 JD</button>}
        <p className="note">新任务自动逐个打开本次岗位面板；已有列表可点“自动补齐本次 JD”。上方单次读取按钮仅用于手动查看后的刷新。不会点击收藏、沟通或投递。</p>
        {job.detail_raw && <p className="note">JD 来源：{job.detail_raw.source_layer} · {job.detail_raw.captured_at}</p>}
        <h3>列表卡片文本</h3><div className="card-text">{display(job.fields.list_text_display ?? job.fields.list_text)}</div>
        <p className="note">可显示文本中的薪资按已验证字体还原；原始字符完整保留在 JSON。未获取的信息不推断或补齐。</p>
      </> : <><h3>历史样本中的可用 JD</h3><div className="jd">{display(job.fields.jd)}</div></>}
      {typeof job.fields.url === 'string' ? <p><a href={job.fields.url} target="_blank" rel="noreferrer">打开 BOSS 岗位页面 ↗</a></p> : <p>岗位链接未提供</p>}
    </>}
  </section>
}
