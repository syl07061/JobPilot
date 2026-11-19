import {useEffect,useState} from 'react'
import type {FormEvent} from 'react'
import ResumeProfile from './ResumeProfile'
import type {ProfileState} from './ResumeProfile'

interface ResumeVersion {id:string;version:number;filename:string;extension:string;size_bytes:number;uploaded_at:string;parsed:boolean}

export default function ResumeUploads(){
  const [items,setItems]=useState<ResumeVersion[]>([]),[file,setFile]=useState<File|null>(null)
  const [loading,setLoading]=useState(true),[uploading,setUploading]=useState(false)
  const [error,setError]=useState(''),[notice,setNotice]=useState(''),[inputKey,setInputKey]=useState(0)
  const [profile,setProfile]=useState<ProfileState|null>(null),[parsing,setParsing]=useState(false)
  async function loadProfile(){
    const response=await fetch('/api/resumes/profile')
    if(!response.ok)throw new Error('无法读取个人档案状态')
    const value:ProfileState=await response.json();setProfile(value);return value
  }
  async function load(){
    setLoading(true);setError('')
    try{const response=await fetch('/api/resumes');if(!response.ok)throw new Error('无法读取简历版本，请稍后刷新')
      const body=await response.json();setItems(body.items);await loadProfile()}
    catch(err){setError(err instanceof Error ? err.message : '读取失败')}
    finally{setLoading(false)}
  }
  useEffect(()=>{void load()},[])
  useEffect(()=>{
    if(profile?.attempt?.state!=='running')return
    const controller=new AbortController()
    let timer:ReturnType<typeof setTimeout>
    async function poll(){
      try{const response=await fetch('/api/resumes/profile',{signal:controller.signal});if(!response.ok)throw new Error('无法读取解析进度')
        const value:ProfileState=await response.json();if(!controller.signal.aborted){setProfile(value);if(value.attempt?.state==='running')timer=setTimeout(()=>void poll(),2000)}}
      catch(err){if(!controller.signal.aborted)setError('解析状态暂无法确认，未自动重新提交；请刷新版本列表。')}
    }
    timer=setTimeout(()=>void poll(),1000)
    return ()=>{controller.abort();clearTimeout(timer)}
  },[profile?.attempt?.job_id,profile?.attempt?.state])
  async function parseLatest(){
    setParsing(true);setError('');setNotice('')
    try{const response=await fetch('/api/resumes/parse-latest',{method:'POST'});const value=await response.json()
      if(!response.ok)throw new Error(typeof value.detail==='string' ? value.detail : '解析启动失败')
      setProfile(value)
    }catch(err){setError((err instanceof Error ? err.message : '解析请求失败')+'。未自动重发，请刷新状态核对。')}
    finally{setParsing(false)}
  }
  async function upload(event:FormEvent){
    event.preventDefault();setError('');setNotice('')
    if(!file){setError('请先选择简历文件');return}
    if(!/\.(pdf|docx?)$/i.test(file.name)){setError('仅支持PDF、DOC、DOCX文件');return}
    if(file.size===0 || file.size>10*1024*1024){setError('请选择非空且不超过10MB的文件');return}
    setUploading(true)
    try{
      const body=new FormData();body.append('file',file)
      const response=await fetch('/api/resumes',{method:'POST',body})
      const result=await response.json()
      if(!response.ok)throw new Error(typeof result.detail==='string' ? result.detail : '上传失败')
      setItems(previous=>[result,...previous.filter(r=>r.id!==result.id)].sort((a,b)=>b.version-a.version))
      setFile(null);setInputKey(k=>k+1);setNotice(`简历版本 V${result.version} 已保存，尚未解析。`)
      try{await loadProfile()}catch{setError('文件已上传，但档案状态刷新失败，请刷新版本列表核对。')}
    }catch(err){setError((err instanceof Error ? err.message : '上传失败')+'。未自动重传；可刷新列表确认是否已保存。')}
    finally{setUploading(false)}
  }
  const running=parsing || profile?.attempt?.state==='running'
  return <section className="resume-workspace" aria-label="简历上传">
    <header className="resume-page-heading"><div><h2>我的简历</h2><p>保留每次上传，只维护一份当前生效的个人档案。</p></div>
      <button type="button" disabled={loading || uploading} onClick={()=>void load()}>{loading ? '正在刷新…' : '刷新版本列表'}</button>
    </header>
    <section className="resume-card" aria-labelledby="resume-upload-heading">
      <header className="resume-card-heading"><div><h3 id="resume-upload-heading">上传与解析</h3><p>先保存文件，再手动解析最新版本。</p></div><span className="resume-badge neutral">PDF / Word · 最大 10 MB</span></header>
      <div className="resume-action-grid">
        <form onSubmit={event=>void upload(event)} className="resume-action-block">
          <div className="resume-step-heading"><span className="resume-step-number">1</span><h4>上传简历文件</h4></div>
          <p className="note">每次上传新增一个版本，同名文件也会保留。</p>
          <label className="resume-file-picker">选择简历文件<input key={inputKey} type="file" accept=".pdf,.doc,.docx" disabled={uploading} onChange={event=>setFile(event.target.files?.[0] || null)}/></label>
          <button className="primary" disabled={!file || uploading}>{uploading ? '正在上传…' : '上传简历'}</button>
        </form>
        <div className="resume-action-block">
          <div className="resume-step-heading"><span className="resume-step-number">2</span><h4>解析最新版本</h4></div>
          <p className="note">{items.length ? `最新上传 V${items[0].version} · ${items[0].filename}` : '上传后，可在这里生成个人档案。'}</p>
          <p className="note">仅解析文字版 PDF / DOCX；扫描件暂不支持，旧 DOC 请先另存为 DOCX 或 PDF。</p>
          <button type="button" className="primary" disabled={loading || uploading || running || !items.length} onClick={()=>void parseLatest()}>{running ? '正在解析最新简历…' : '解析最新简历'}</button>
        </div>
      </div>
      <p className="resume-privacy-note">点击解析会将简历文字发送给已配置的大模型，可能产生 API 费用。上传和下载本身不调用 AI。</p>
      {profile?.attempt && <div role="status" className={`resume-parse-status ${profile.attempt.state==='failed' ? 'failed' : profile.attempt.state==='completed' ? 'completed' : 'running'}`}><strong>解析 V{profile.attempt.version} · {profile.attempt.state==='running' ? '进行中' : profile.attempt.state==='completed' ? '已完成' : '失败'}</strong><span>{profile.attempt.message}</span></div>}
      {error && <p role="alert" className="error">{error}</p>}{notice && <p role="status" className="resume-upload-notice">{notice}</p>}
    </section>
    <div className="resume-content-grid">
    <ResumeProfile state={profile}/>
    <section className="resume-card resume-history" aria-labelledby="resume-history-heading">
    <header className="resume-card-heading"><div><h3 id="resume-history-heading">历史简历版本</h3><p>原件按上传时间保留，方便回溯与下载。</p></div><span className="resume-badge neutral">{items.length} 份</span></header>
    {loading ? <p>正在读取版本记录…</p> : !items.length ? <p className="empty">尚未上传简历。</p> :
      <ul className="resume-versions">{items.map((r,index)=><li key={r.id}>
        <div className="resume-version-title"><strong>V{r.version}</strong>{index===0 && <span className="version-badge">最新上传</span>}{profile?.profile?.resume_id===r.id && <span className="resume-badge success">档案来源</span>}</div>
        <p className="resume-filename">{r.filename}</p>
        <p className="note">{r.extension.slice(1).toUpperCase()} · {(r.size_bytes/1024).toFixed(1)} KB<br/>{new Date(r.uploaded_at).toLocaleString('zh-CN')}</p>
        {index===0 && profile?.profile?.resume_id!==r.id && <p className="note">最新上传，尚未生效</p>}
        <a href={`/api/resumes/${encodeURIComponent(r.id)}/download`}>下载原件</a>
      </li>)}</ul>}
    <p className="note">历史文件不单独维护解析档案。新版本解析成功后，才会替换当前档案。</p>
    </section>
    </div>
  </section>
}
