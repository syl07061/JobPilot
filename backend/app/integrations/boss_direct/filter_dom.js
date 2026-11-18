/* 只读实际筛选菜单。标题可能是“工作经验(2)”，值来自选中选项。 */
(() => {
  const options={},effective={city:document.querySelector('.cur-city-label')?.textContent.trim() || null},values={};
  const menus=[...document.querySelectorAll('.condition-filter-select')];
  for(const [key,kind,placeholder] of [['job_type','jobType','求职类型'],['salary','salary','薪资待遇'],['experience','exp','工作经验']]){
    const match=n=>(n.getAttribute('ka')||'').match(/^sel-job-(?:rec-)?(jobType|salary|exp|experience)-(\d+)$/);
    const accepts=m=>m && (m[1]===kind || kind==='exp'&&m[1]==='experience');
    const menu=menus.find(m=>[...m.querySelectorAll('li')].some(n=>accepts(match(n))));
    if(!menu)return {available:false};
    options[key]=[...menu.querySelectorAll('.filter-select-dropdown li')].filter(n=>accepts(match(n))).map(n=>({label:n.textContent.trim(),value:match(n)[2]}));
    const selected=[...menu.querySelectorAll('.filter-select-dropdown li.active')];
    const valid=selected.every(n=>accepts(match(n)));
    const header=menu.querySelector('.current-select')?.textContent.trim();
    values[key]=selected.length ? valid ? selected.map(n=>match(n)[2]) : null : header===placeholder ? ['0'] : null;
    effective[key]=selected.length ? selected.map(n=>n.textContent.trim()).join(' / ') : header===placeholder ? '不限' : header || null;
  }
  const area=document.querySelector('.city-area-label');
  if(area){
    const selected=[...area.querySelectorAll('[ka^="close_select_area_"]')];
    const codes=selected.map(n=>(n.getAttribute('ka')||'').match(/^close_select_area_(\d{6})$/)?.[1]);
    // 本轮只支持行政区；商圈或地铁状态不能冒充整区或不限。
    const other=area.querySelectorAll('[ka^="close_select_subway_"]').length || selected.some((n,i)=>!codes[i]);
    values.regions=other || !selected.length && /\([1-9]/.test(area.querySelector('.cur-area-label')?.textContent||'') ? null : codes;
    effective.regions=other ? null : selected.map(n=>n.parentElement.textContent.trim().replace(/^全/,'')).join(' / ') || '不限';
  }
  return {available:true,options,effective,effective_values:values};
})()
