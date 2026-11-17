/* 只定位岗位卡片并正常滚动到可见位置；点击由 CDP 鼠标事件完成。 */
(input => {
  const here=new URL(location.href);
  const keys=['query','city','salary','experience','degree','scale','industry','jobType','sortType'];
  const context=Object.fromEntries(keys.filter(k=>here.searchParams.has(k)).map(k=>[k,here.searchParams.getAll(k)]));
  if (here.protocol!=='https:' || !/(^|\.)zhipin\.com$/.test(here.hostname) ||
      !['/web/geek/jobs','/web/geek/job'].includes(here.pathname.replace(/\/$/,'')) ||
      !keys.every(k=>JSON.stringify(context[k]??null)===JSON.stringify(input.context[k]??null))) return {status:'page_changed'};
  const visible=n=>{if(!n?.getClientRects().length)return false;for(let p=n;p;p=p.parentElement){const s=getComputedStyle(p);if(s.display==='none'||s.visibility!=='visible'||Number(s.opacity)===0)return false;}return true;};
  const safeURL=a=>{try{const u=new URL(a.href);return u.origin+u.pathname;}catch{return null;}};
  const cards=[...document.querySelectorAll('.job-card-wrap')].filter(n=>visible(n)&&[...n.querySelectorAll('a.job-name[href]')].some(a=>safeURL(a)===input.url));
  if(cards.length!==1)return {status:'card_not_found'};
  const card=cards[0];
  const title=[...card.querySelectorAll('a.job-name[href]')].find(a=>safeURL(a)===input.url)?.innerText.trim();
  if(title!==input.title)return {status:'title_changed'};
  if(input.scroll) {card.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'});return {status:'scrolled'};}
  // 只选择卡片的非交互区域；绝不点击职位链接、公司链接、收藏或沟通按钮。
  const box=card.querySelector('li.job-card-box')||card;
  const r=box.getBoundingClientRect();
  for(const fy of [.2,.5,.8])for(const fx of [.08,.35,.65,.92]){
    const x=r.left+r.width*fx,y=r.top+r.height*fy;
    if(x<1||y<1||x>=innerWidth-1||y>=innerHeight-1)continue;
    const hit=document.elementFromPoint(x,y);
    if(hit && box.contains(hit) && !hit.closest('a,button,input,textarea,select,[role="button"],[contenteditable="true"]'))
      return {status:'ready',x,y};
  }
  return {status:'no_safe_point'};
})
