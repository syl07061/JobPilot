interface Item {organization:string|null;title:string|null;start_date:string|null;end_date:string|null;description:string|null}
export interface ProfileData {name:string|null;email:string|null;phone:string|null;location:string|null;summary:string|null;skills:string[];education:Item[];work_experience:Item[];projects:Item[];certifications:string[];languages:string[]}
export interface ProfileState {latest_version:number|null;is_current:boolean;profile:null|{resume_id:string;version:number;filename:string;parsed_at:string;model:string;data:ProfileData};attempt:null|{job_id:string;version:number;state:string;code:string;message:string}}

export default function ResumeProfile({state}:{state:ProfileState|null}){
  const profile=state?.profile,data=profile?.data
  return <section aria-label="当前个人档案" className="resume-card resume-profile">
    <header className="resume-card-heading"><div><h3>当前个人档案</h3><p>最新一次成功解析的结构化信息。</p></div><span className={`resume-badge ${profile ? state?.is_current ? 'success' : 'pending' : 'neutral'}`}>{profile ? state?.is_current ? '已同步最新' : '待更新' : '尚未生成'}</span></header>
    {!profile || !data ? <div className="resume-empty-state"><strong>个人档案等待生成</strong><p>上传简历并点击“解析最新简历”，即可在这里查看整理后的个人信息与经历。</p></div> : <>
      <p className="resume-profile-source">来源：V{profile.version} · {profile.filename}<br/><span>解析时间：{new Date(profile.parsed_at).toLocaleString('zh-CN')}</span></p>
      {!state?.is_current && <p className="notice">有更新的上传版本尚未成功解析，当前继续使用 V{profile.version} 的档案。</p>}
      <p className="note">AI仅根据提取文字整理，请核对结果。未提供的信息显示为空，不代表模型已完成岗位匹配。</p>
      <section className="resume-profile-section"><h4>基本信息</h4><dl>{([['name','姓名'],['email','邮箱'],['phone','电话'],['location','所在地'],['summary','个人概述']] as const).map(([key,label])=><div key={key}><dt>{label}</dt><dd>{data[key] || '未提供'}</dd></div>)}</dl></section>
      <section className="resume-profile-section"><h4>专业技能</h4>{data.skills.length ? <ul className="resume-skill-list" aria-label="技能">{data.skills.map((skill,index)=><li key={index}>{skill}</li>)}</ul> : <p className="note">未提供</p>}</section>
      {([['education','教育经历'],['work_experience','工作经历'],['projects','项目经历']] as const).map(([key,label])=><section className="resume-profile-section" key={key}><h4>{label}<span className="resume-section-count">{data[key].length}</span></h4>{data[key].length ? <ul className="resume-experience-list">{data[key].map((item,index)=><li key={index}><strong>{[item.organization,item.title].filter(Boolean).join(' · ') || '未提供名称'}</strong><p className="note">{[item.start_date,item.end_date].filter(Boolean).join(' — ') || '时间未提供'}</p><p className="jd">{item.description || '描述未提供'}</p></li>)}</ul> : <p className="note">未提供</p>}</section>)}
      <section className="resume-profile-section"><h4>其他信息</h4><dl><div><dt>证书</dt><dd>{data.certifications.join('、') || '未提供'}</dd></div><div><dt>语言</dt><dd>{data.languages.join('、') || '未提供'}</dd></div></dl></section>
      <details className="resume-profile-json"><summary>查看当前档案 JSON</summary><pre>{JSON.stringify(data,null,2)}</pre></details>
    </>}
  </section>
}
