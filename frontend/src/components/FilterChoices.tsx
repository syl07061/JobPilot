import type {FilterOption} from '../types/search'

interface Props {label:string;options:FilterOption[];value:string[];onChange:(value:string[])=>void;disabled:boolean;regions?:boolean}

export default function FilterChoices({label,options,value,onChange,disabled,regions=false}:Props){
  const unlimited=regions ? value.length===0 : value.includes('0')
  function toggle(code:string){
    if(code==='0'){onChange(regions ? [] : ['0']);return}
    const next=value.includes(code) ? value.filter(v=>v!==code) : [...value.filter(v=>v!=='0'),code]
    onChange(next.length || regions ? next : ['0'])
  }
  const choices=regions ? [{label:'不限',value:'0'},...options] : options
  return <fieldset className="filter-choices" disabled={disabled}><legend>{label}（可复选）</legend>
    <div className="choice-list">{choices.map(o=><label key={o.value}>
      <input type="checkbox" checked={o.value==='0' ? unlimited : value.includes(o.value)}
        disabled={regions && value.length>=9 && !value.includes(o.value) && o.value!=='0'} onChange={()=>toggle(o.value)}/>{o.label}
    </label>)}</div>
    {regions && <small>当前城市行政区，最多9个；“不限”清除其他选择。</small>}
  </fieldset>
}
