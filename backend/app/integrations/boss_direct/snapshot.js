/* JobPilot 独立 DOM 快照：只读当前可见岗位区域，不返回 HTML、输入框或存储。 */
((filtersOnly = false, filterState = {}) => {
  const visible = node => {
    if (!node || !node.getClientRects().length) return false;
    for (let p=node; p; p=p.parentElement) {
      const style=getComputedStyle(p);
      if (style.visibility !== 'visible' || style.display === 'none' || Number(style.opacity) === 0) return false;
    }
    return true;
  };
  const read = node => visible(node) ? node.innerText.trim() : '';
  const linkURL = (node, kind) => {
    try {
      const u = new URL(node.href, location.href);
      if (u.protocol !== 'https:' || !/(^|\.)zhipin\.com$/.test(u.hostname) ||
          !(kind === 'job' ? /^\/job_detail\// : /^\/gongsi\//).test(u.pathname)) return null;
      return u.origin + u.pathname;
    } catch { return null; }
  };
  const unique = values => [...new Set(values.filter(Boolean))];
  const one = values => { const u = unique(values); return u.length === 1 ? u[0] : null; };
  const url = new URL(location.href);
  const contextKeys = ['query', 'city', 'salary', 'experience', 'degree', 'scale', 'industry', 'jobType', 'sortType', 'multiBusinessDistrict', 'multiSubway', 'areaBusiness'];
  const context = Object.fromEntries(contextKeys.filter(k => url.searchParams.has(k)).map(k => [k, url.searchParams.getAll(k)]));
  const nativeFilters=filterState.available ? filterState.effective : null;
  if(nativeFilters)context.native_filters=nativeFilters;
  const anchors = [...document.querySelectorAll('a[href]')].filter(visible);
  const jobLinks = anchors.filter(a => linkURL(a, 'job'));
  const dialogs = [...document.querySelectorAll('[role="dialog"], [class*="login-dialog"], [class*="login-modal"]')].filter(visible);
  const modalText = dialogs.map(read).join('\n');
  const verificationNodes = [...document.querySelectorAll('[class*="captcha"], [id*="captcha"], [class*="geetest"], iframe[src*="verify"], iframe[src*="captcha"]')].filter(visible);
  const body = document.body?.innerText || ''; // 只用于本地状态判断，绝不将整页正文导出。
  const resultsRoot = jobLinks[0]?.closest('[class*="job-list"], main') || document.querySelector('main');
  const resultText = resultsRoot?.innerText || (jobLinks.length ? '' : body);
  let restriction = null;
  if (/security.check|captcha|\/verify/i.test(url.pathname) || verificationNodes.length ||
      /安全验证|访问被拒绝|操作过于频繁|请求过于频繁|账号异常/.test(modalText + (jobLinks.length ? '' : body))) restriction = 'platform_restricted';
  else if (/\/user\/login/.test(url.pathname) || /扫码登录|短信登录|请先登录|登录后查看/.test(modalText + (jobLinks.length ? '' : body))) restriction = 'login_required';
  const page = {url: url.protocol === 'about:' ? location.href : url.origin + url.pathname,
    title: document.title, ready_state: document.readyState, context,
    restriction, explicit_empty: /暂无相关职位|暂无职位|没有找到相关职位|未找到符合条件的职位|暂无搜索结果/.test(resultText),
    explicit_end: /没有更多职位|没有更多了|已经到底了/.test(resultText), cards: [], truncated: false,
    viewport: {width: innerWidth, height: innerHeight}};
  page.native_filters=nativeFilters;
  page.native_filter_values=filterState.effective_values || {};
  page.visibility_state=document.visibilityState;
  page.has_focus=document.hasFocus();
  if (restriction || filtersOnly) return page; // 筛选元数据检查不导出岗位字段或JD。
  // 只记录已加载的公开字体资源，不读取或保存认证参数。
  const salaryNode = [...document.querySelectorAll('.job-salary')].find(visible);
  page.salary_font = {family: salaryNode ? getComputedStyle(salaryNode).fontFamily : '',
    resources: unique(performance.getEntriesByType('resource').map(r => r.name).filter(n => /\.(?:woff2?|ttf|otf)(?:\?|$)/i.test(n)).map(n => {
      try { const u=new URL(n); return u.protocol==='https:' && u.hostname==='img.bosszhipin.com' ? u.origin+u.pathname : null; } catch { return null; }
    }))};
  // 当前搜索页真实 DOM：右侧详情面板。用“查看完整职位”的链接核对岗位身份。
  const panels = [...document.querySelectorAll('.job-detail-container')].filter(visible);
  const activeJobURLs = unique([...document.querySelectorAll('.job-card-wrap.active a.job-name[href]')].filter(visible).map(a=>linkURL(a,'job')));
  page.detail_probe = {panel_count: panels.length, active_job_urls: activeJobURLs};
  if (panels.length === 1) {
    const panel=panels[0], description=panel.querySelector('.job-detail-body > .desc');
    const detailURL=linkURL(panel.querySelector('.job-detail-body .more-job-btn') || {}, 'job');
    // 真实页面已核验：地址正文与标题、地图分别位于独立子节点。
    const addressRegions=[...panel.querySelectorAll('.job-detail-body .job-address')].filter(visible);
    const addressRegionText=addressRegions.length===1 ? read(addressRegions[0]) : null;
    const addressNodes=addressRegions.length===1 ? [...addressRegions[0].querySelectorAll('.job-address-desc')].filter(visible) : [];
    const workAddress=addressNodes.length===1 ? read(addressNodes[0]) || null : null;
    Object.assign(page.detail_probe, {url: detailURL, title: read(panel.querySelector('.job-detail-header .job-name')) || null,
      description_chars: read(description).length});
    if (detailURL && read(description)) page.detail = {url: detailURL,
      title: read(panel.querySelector('.job-detail-header .job-name')) || null,
      active_job_urls: activeJobURLs,
      jd: read(description), tags: unique([...panel.querySelectorAll('.job-label-list > *')].map(read)),
      work_address: workAddress, work_address_region_text: addressRegionText,
      source_selectors: {description: '.job-detail-body > .desc', identity: '.job-detail-body .more-job-btn',
        work_address: '.job-detail-body .job-address .job-address-desc'}};
  }
  const seen = new Set();
  for (const anchor of jobLinks) {
    const jobURL = linkURL(anchor, 'job');
    if (seen.has(jobURL)) continue;
    seen.add(jobURL);
    if (page.cards.length >= 300) { page.truncated = true; break; }
    let card = anchor, fallback = true;
    // 从实际链接向上找岗位容器，不假设列表一定采用某个固定分页或类名。
    for (let p = anchor; p && p !== document.body; p = p.parentElement) {
      const ids = unique([...p.querySelectorAll('a[href]')].filter(visible).map(a => linkURL(a, 'job')));
      if (ids.some(id => id !== jobURL)) break;
      if (p.tagName === 'LI') { card = p; fallback = false; break; }
      if ([...p.classList].some(c => /job.*card|card.*job/i.test(c))) { card = p; fallback = false; }
      if (fallback && (p === anchor.parentElement || p === anchor.parentElement?.parentElement)) card = p;
    }
    const nodes = [card, ...card.querySelectorAll('*')].filter(visible);
    const hint = regex => one(nodes.filter(n => [...n.classList].some(c => regex.test(c))).map(read));
    const companyLinks = [...card.querySelectorAll('a[href]')].filter(visible).filter(a => linkURL(a, 'company'));
    const fragments = nodes.filter(n => !n.children.length && read(n)).map(n => ({tag: n.tagName, classes: [...n.classList], text: read(n)}));
    const tags = unique(nodes.filter(n => !n.children.length && n.closest('[class*="tag"]')).map(read));
    const observed = {
      title: hint(/^job[-_](?:name|title)$/i) || (anchor.children.length === 0 ? read(anchor) : null),
      salary: hint(/^(?:job[-_])?salary$/i),
      company: one(companyLinks.map(read)),
      location: hint(/^(?:company[-_]location|job[-_](?:area|location))$/i),
      experience: one(tags.filter(t => /^(?:经验不限|不限经验|应届生|在校生|\d+[-–至]\d+年|\d+年以上|\d+年以内)$/.test(t))),
      education: one(tags.filter(t => /^(?:学历不限|初中及以下|高中|中专|中技|大专|本科|硕士|博士)$/.test(t))),
      tags, url: jobURL,
    };
    let scrolling = card.parentElement;
    while (scrolling && scrolling !== document.body && scrolling !== document.documentElement) {
      if (scrolling.scrollHeight > scrolling.clientHeight + 20 && /auto|scroll/.test(getComputedStyle(scrolling).overflowY)) break;
      scrolling = scrolling.parentElement;
    }
    const rect = scrolling && scrolling !== document.body ? scrolling.getBoundingClientRect() : null;
    if (!page.scroll) page.scroll = {x: rect ? Math.max(1, Math.min(innerWidth-1, rect.x + rect.width/2)) : innerWidth/2,
      y: rect ? Math.max(1, Math.min(innerHeight-1, rect.y + rect.height/2)) : innerHeight/2,
      distance: Math.max(200, Math.floor((rect?.height || innerHeight) * 1.5))};
    page.cards.push({url: jobURL, observed_fields: observed, raw: {
      card_text: read(card), container: {tag: card.tagName, classes: [...card.classList], fallback},
      fragments, links: [...card.querySelectorAll('a[href]')].filter(visible).map(a => ({text: read(a), url: linkURL(a,'job') || linkURL(a,'company')})).filter(a => a.url)}});
  }
  return page;
})
