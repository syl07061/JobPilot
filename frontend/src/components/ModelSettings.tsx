import {useEffect,useState} from 'react'
import type {FormEvent} from 'react'

interface ModelConfig {base_url:string;model:string;has_api_key:boolean;configured:boolean;source:string}
async function request(options?:RequestInit):Promise<ModelConfig>{
  const response=await fetch('/api/settings/model',options)
  const body=await response.json()
  if(!response.ok)throw new Error(typeof body.detail==='string' ? body.detail : '模型配置请求失败')
  return body
}

export default function ModelSettings(){
  const [saved,setSaved]=useState<ModelConfig|null>(null)
  const [url,setUrl]=useState(''),[model,setModel]=useState(''),[key,setKey]=useState('')
  const [clear,setClear]=useState(false),[busy,setBusy]=useState(true)
  const [error,setError]=useState(''),[notice,setNotice]=useState('')
  async function load(){
    setBusy(true);setError('')
    try{const data=await request();setSaved(data);setUrl(data.base_url);setModel(data.model)}
    catch(err){setError(err instanceof Error ? err.message : '配置加载失败')}
    finally{setBusy(false)}
  }
  useEffect(()=>{void load()},[])
  async function save(event:FormEvent){
    event.preventDefault();setBusy(true);setError('');setNotice('')
    try{
      const data=await request({method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({base_url:url,model,api_key:key || null,clear_api_key:clear})})
      setSaved(data);setUrl(data.base_url);setModel(data.model);setKey('');setClear(false)
      setNotice('模型配置已保存。尚未发起模型请求或验证连通性。')
    }catch(err){setKey('');setError(err instanceof Error ? err.message : '保存失败')}
    finally{setBusy(false)}
  }
  return <section className="panel" aria-label="大模型配置"><h2>大模型配置</h2>
    <p>配置 OpenAI 兼容服务，供简历解析和后续 AI 复筛使用。保存配置不会调用模型或产生模型费用。</p>
    {saved && <p>配置状态：{saved.configured ? '必要字段已填写（未验证连接）' : '尚未配齐'} · API Key：{saved.has_api_key ? '已设置' : '未设置'}</p>}
    <form className="model-form" onSubmit={event=>void save(event)}>
      <label>模型 Base URL<input type="url" value={url} maxLength={1000} placeholder="服务商提供的 API 基础地址，例如包含 /v1 的地址" disabled={busy} onChange={e=>setUrl(e.target.value)}/></label>
      <label>模型名称<input value={model} maxLength={200} placeholder="填写服务商支持的模型 ID" disabled={busy} onChange={e=>setModel(e.target.value)}/></label>
      <label>API Key<input type="password" value={key} autoComplete="new-password" spellCheck={false} placeholder={saved?.has_api_key ? '已设置；留空保留原 Key，输入新值替换' : '填写 API Key'} disabled={busy || clear} onChange={e=>setKey(e.target.value)}/></label>
      <label className="inline-check"><input type="checkbox" checked={clear} disabled={busy} onChange={e=>{setClear(e.target.checked);setKey('')}}/>清除已保存的 API Key</label>
      <div className="tabs"><button className="primary" disabled={busy || !saved}>{busy ? '处理中…' : '保存模型配置'}</button>
        <button type="button" disabled={busy} onClick={()=>void load()}>重新读取配置</button></div>
    </form>
    <p className="note">Key 留空不会删除原值。密钥保存在本机后端配置文件中（未加密、已被 Git 忽略），不会回传前端或存入浏览器本地存储。当前仅用于本机开发。</p>
    {error && <p role="alert" className="error">{error}</p>}{notice && <p role="status">{notice}</p>}
  </section>
}
