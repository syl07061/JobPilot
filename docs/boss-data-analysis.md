# BOSS 岗位数据分层分析：当前独立DOM与历史接口对照

## 当前独立版本说明

**当前新任务不调用BossHunter。下文51字段表及原版方法分析是历史证据，不能当作独立读取器已获得这些字段。**

新模式由用户先在Chrome手动登录并设置关键词，JobPilot配置城市、行政区、求职类型、薪资待遇、工作经验和每组max_pages。经验、区域原生复选；薪资、求职类型原生单选，复选时拆成串行组合，逐组核对DOM选中编码集合后才读取列表与JD。跨组相同岗位及已读JD合并，原始快照保留组号。保留卡片原文、节点文本、类名、安全链接和可确定字段；不按原版规则在本地丢弃岗位，不按历史ID排除。详情见[原生初筛说明](native-prefilter.md)。

| 当前数据 | 来源 | 当前验证状态 |
| --- | --- | --- |
| title/company/salary/location/experience/education/tags/url | 实际列表DOM语义节点、公司链接与标签，歧义为null | 已有30条用户主动读取的真实记录 |
| card_text/fragments/links/container | 卡片可见DOM文本、节点和安全链接；JobPilot定义读取结构 | 离线DOM/真实本机CDP协议测试通过 |
| jd / jd_tags | 搜索页右侧详情面板，按岗位URL和选中卡片匹配，标题单独保留 | 此前真实1条503字符；后续用户任务已保存4条，并观察到第5条1395字符因标题后缀不同被旧规则误判 |
| detail_raw | 单独保存详情DOM原文、选择器、岗位身份和时间 | source_layer=detail_dom，不覆盖列表原始记录 |
| work_address | JD面板地址正文可见文字，与列表location分开 | 已通过真实当前页面核验及离线DOM测试；缺失为null |
| work_address_region_text | 工作地址区域原文，包括标题/地图提示，保存在detail_raw.payload中 | 原始区域文本保留供核对；展示字段剔除已确认的界面标题和“点击查看地图” |
| 详细企业资料 | 本版不遍历企业或独立详情页 | 未采集，不推断或用历史值补齐 |
| salary_raw / salary_decoding | DOM专用字形及JobPilot的独立显示处理依据 | 30条薪资已还原，原始值不变；未知字体不猜测 |

新来源层级为 `search_list_dom`，不是BossHunter `local_api`，也不是BOSS网络API原始JSON。仅在同一任务中合并跨批相同链接，完整批次快照仍保留实际读取内容。页数按“确认新增的一批结果”计数，不固定每页条数。

详细工作地址随同一JD面板读取。2026-11-19只读核验当前真实页面，确认地址在`.job-detail-body .job-address .job-address-desc`，与`.job-address-title`标题及`.map-box-wrapper`地图区域分开；读取到400字JD及对应详细地址，岗位身份一致。现在直接取地址正文节点的innerText，保留多行地址，不点地图、不读取地图URL或坐标，不额外打开岗位或增加读取次数。仅在JD岗位身份通过校验后赋给对应岗位；不从列表location、公司注册地址或城市条件推断。无地址、多个地址区域或正文节点不唯一时返回null，保留区域原文。历史已保存JD不会因为新增地址字段就自动重新打开；再次读取或刷新对应面板后才能补入地址。此阶段只采集和展示，尚未实现地址筛选或通勤计算。

### 薪资与JD修复的真实证据

2026-11-18对已经打开的同一搜索页做一次只读增补，无导航、点击或滚动：30条已存岗位的薪资可还原，当前选中的1条JD为503字符；增补前后的列表raw逐条相同。第一条薪资原文为`\ue033\ue031-\ue035\ue031K·\ue032\ue036薪`，显示值为`20-40K·15薪`。

字体证据为该页面实际加载的2022年TTF资源和2023年mix WOFF2资源。使用fontTools逐个比较PUA字符与常规数字的完整字形轮廓，10个均唯一相同。mix资源SHA256为`37be9f6d4984819476d27d4862faa8d23181acc32bed2edafd4f42e5184cd924`。映射独立实现，不复制BossHunter解码器；应用按已核对资源URL和字体族识别版本，不假设任意PUA字符均可用此表还原，也不声称实时校验浏览器缓存的哈希。

JD实测节点为`.job-detail-body > .desc`；身份由同一面板`.more-job-btn`的岗位链接与当前选中卡片确定，`.job-detail-header .job-name`保留为详情标题，不强制与列表标题相同。只采集职位描述及岗位标签，不读取旁边的招聘者区域。JD仍保存为整段原文，职责/要求未做语义拆分。尚未打开的描述标记为未读取，不用历史JD填充。

后续已增加自动逐个打开卡片与JD补齐，数据来源与字段定义保持不变。自动选择卡片、异步等待、身份匹配及无沟通/收藏操作已通过离线真实CDP浏览器测试；这不代表已对真实平台自动采集29条JD。真实自动验证需用户从前端触发，共用原当日预算，额度/加载/验证异常即停止并保留部分结果。

### 标题差异导致的误判修复

用户任务`0772a8e2-997b-4593-b063-4445b9ed9df4`中，列表已读取30条，JD成功4条后停止，原因是`load_failed`而非额度耗尽。只读检查发现第5条列表标题为“FDE/AI现场应用工程师-出差”，详情标题为“FDE/AI现场应用工程师”；两者详情URL相同，当前选中卡片也是该URL，描述已有1395字符。旧代码强制标题完全相同，误判为超时，不能据此声称网站访问受限。

现按岗位URL和选中卡片校验，允许两边标题不同；保留列表title、详情detail_title和原始detail_raw，以及jd_identity校验依据。未知/错配URL、空JD、多个选中状态仍拒绝。失败记录增加面板诊断，前端区分JD加载/匹配失败与列表加载失败。本次不自动重新运行剩余岗位采集，也不修改上限或清空已有结果。

**以下章节均为此前BossHunter方案和已保存API样本的历史对照。**

## 证据与限制

源码基线为本机 BossHunter main `12d6891ef7db59970019e1b5ad238a4bbe7f2e5c`，核对路径：

- `collection/platforms/boss.py`：搜索URL、原生参数、列表/详情解析、预筛与合并。
- `browser/client.py`、`browser/runtime/cdp-proxy.mjs`：原版本地HTTP到Node CDP的桥接；不把这些执行接口开放给JobPilot前端。
- `collection/models.py`、`collection/text.py`、`collection/orchestrator.py`：候选数据、JD清洗、去重、过滤、run归属。
- `web/server.py`、`web/tasks.py`、`collection_run_store.py`、`db.py`：HTTP返回、任务与数据库持久化。

真实证据来自此前已采集并存在本地工作台的5条记录，由 `scripts/capture_bosshunter_sample.py` 调用 `GET /api/jobs?limit=5` 保存到被忽略的 `data/samples/bosshunter_jobs.json`。这5条是**真实历史API样本**，不是新任务测试，也不代表所有BOSS岗位的字段覆盖率。公开招聘者及JD原文不写入本报告。

原版POC另已观察：同一新建搜索页最初0张岗位卡，约2秒后变为15张卡。因此HTTP200、通用标题、readyState=complete或某个页面外壳类名存在，均不足以证明岗位列表已加载。Issue [275](https://github.com/shengjidaguai-china/BossHunter/issues/275) / PR [281](https://github.com/shengjidaguai-china/BossHunter/pull/281) 的空壳诊断不应与真实无结果混同；本轮不套用补丁或重复采集。

## 一、搜索条件如何传递

原版搜索URL模板使用 `/web/geek/job?query={keyword}&city={city_code}`，实际浏览器可跳到 `/web/geek/jobs`。关键词URL编码，城市由本地目录得到编码。原版页面/滚动批次由max_pages约束，后续批次会由网站自身滚动加载；不是“返回N个岗位”的上限。

| 条件 | 原版参数 | 源码中的可选值举例 | 本轮 |
| --- | --- | --- | --- |
| 关键词 | query | 用户输入，URL编码 | 支持 |
| 城市 | city | 由本地城市名映射编码 | 支持，不联网猜测城市 |
| 最大页数 | max_pages（采集器参数） | 1–10；列表形态可能是滚动批次 | 支持 |
| 薪资 | salary | 402–407；例如10–20K=405、20–50K=406 | 暂不开放 |
| 经验 | experience | 101–108；例如1–3年=104、3–5年=105 | 暂不开放 |
| 学历 | degree | 201–205、206、208、209；本科=203 | 暂不开放 |
| 公司规模 | scale | 301–306；例如100–499人=303 | 暂不开放 |
| 职位类型 | jobType | 全职1901、兼职1903、实习1902 | 暂不开放 |
| 行业 | industry | 原生过滤映射中存在，不能凭此保证任何自定义值有效 | 待验证 |
| 排序 | sortType | 原版newest时附加2 | 本轮default |

这些是原版适配源码支持的参数，不是本轮逐项在平台验证成功的承诺。请求显式清空原生filters，避免继承上一次条件；但原版内部profile预筛仍保留，详见下文。

## 二、四个来源层级不能混为一谈

### 搜索列表解析对象

原版 `JS_EXTRACT_LIST` 输出8个键：`title`、`salary`、`experience`、`education`、`company`、`location`、`url`。姓名、薪资和公司取自当前选择器，经验/学历由tag-list前两个标签按位置读取。并未返回完整标签数组；链接缺失的卡片直接跳过。这里的对象已经是DOM解析结果，也不是网络原始JSON。

### 详情页解析对象

`JS_EXTRACT_DETAIL` 尝试补充title、salary、experience、education、jd、company、company_size、company_industry、hr_name、hr_title、hr_active、url，并给出规则推导的recruitment_type。名称/公司等字段有DOM或页面标题回退逻辑。JD为选定正文节点的文本，不保证覆盖网页所有内容。岗位职责和任职要求没有独立结构字段，不能自动断言已获得。

### 采集器候选及加工

原版 `JobCandidate` 的完整字段为：`platform`、`source_job_id`、`title`、`company`、`salary`、`city`、`city_code`、`experience`、`education`、`recruitment_type`、`jd`、`hr_name`、`hr_title`、`hr_active`、`company_size`、`company_industry`、`url`、`source_keyword`。其中platform、检索上下文和链接派生ID不是网页原生字段；持久化时city_code映射为source_city_code，并可能再次推导招聘类型。

- `_list_candidate` 将city赋为检索条件，将city_code/source_keyword赋为上下文；**列表location没有保留到JobCandidate**，所以城市不能冒充实际区县/办公地址。
- `_merge_detail` 详情值优先，空值回退列表字段，URL组装为绝对地址；source_job_id由链接派生。
- 薪资字体数字会经原版字体映射解码；无法确认时可能按配置停止。这里不修改该机制。
- recruitment_type由标题、经验、JD中的显式词语规则推导；不是BOSS原生明确枚举的保证。
- `as_job_record` 清理NBSP、来源文案和重复空白；JD不是未处理的DOM文本。
- 原版按已有source ID去重，重复岗位不再进入详情；标题排除词、公司黑名单、匿名公司、实习/管培、薪资规则、JD排除词等可过滤候选。缺链接或详情缺名称/公司/JD会记为解析失败。

因此最终入库数量不等于列表卡片数量，也不等于网页可提供的全部字段。`auto_score=false` 禁止LLM评分，不会移除既有本地预筛。

### 最终数据库与本地API

数据库还包含状态、时间、历史评分、招呼语、简历路径、外包判断等应用字段。`_serialize_job` 还加入就业类型/审查及招呼语活动状态，并将部分JSON字符串转成数组。JobPilot只消费HTTP，不依赖这些表结构。

原生HTTP不暴露该次列表/详情解析原对象。JobPilot JSON视图因此只标记 `source_layer=local_api`，保留字段和嵌套结构，经脱敏后保存；不能称作BOSS原始响应。历史API中的score/score_reason可能来自用户之前的操作，不代表JobPilot本轮执行AI评分。

## 三、真实样本逐字段统计

统计基于样本采集时的原API值，空字符串、null及空列表计为缺失，0/false计为提供值。敏感路径在快照中统一屏蔽，表中不披露原值。字段“实际获取”仅表示5条样本中的非空数；不保证后续每条具备。

<!-- SAMPLE_TABLE_START -->
采样时间：`2026-11-16T21:26:02.910363+00:00`；记录数：**5**；字段数：**51**。JD长度：164、321、104、367、294 字符。

| 字段名 | 中文含义 | 数据类型 | 来源层级 | 实际是否获取 | 是否适合筛选 |
| --- | --- | --- | --- | --- | --- |
| `city` | 检索城市（非办公地址） | string | 采集器检索上下文 → 本地API | 5/5非空 | 可用于规则/去重；需保留原值与缺失状态 |
| `company` | 公司名称 | string | 列表/详情解析 → 采集器 → 本地API | 5/5非空 | 可用于规则/去重；需保留原值与缺失状态 |
| `company_industry` | 企业行业 | string | 详情解析 → 采集器 → 本地API | 0/5非空 | 有值时可筛，当前样本缺失 |
| `company_size` | 企业规模 | string | 详情解析 → 采集器 → 本地API | 0/5非空 | 有值时可筛，当前样本缺失 |
| `created_at` | 本地创建时间 | string | 应用存储/处理 → 本地API | 5/5非空 | 非岗位事实；不用于岗位匹配硬筛 |
| `deleted_at` | 本地删除时间 | null | 应用存储/处理 → 本地API | 0/5非空 | 非岗位事实；不用于岗位匹配硬筛 |
| `deleted_reason` | 本地删除原因 | null | 应用存储/处理 → 本地API | 0/5非空 | 非岗位事实；不用于岗位匹配硬筛 |
| `education` | 学历要求 | string | 列表/详情解析 → 采集器 → 本地API | 5/5非空 | 可用于规则/去重；需保留原值与缺失状态 |
| `employment_review` | API就业规则审查 | string | 本地API动态加工 | 0/5非空 | 非岗位事实；不用于岗位匹配硬筛 |
| `employment_type` | API计算的就业类型 | string | 本地API动态加工 | 5/5非空 | 非岗位事实；不用于岗位匹配硬筛 |
| `experience` | 经验要求 | string | 列表/详情解析 → 采集器 → 本地API | 5/5非空 | 可用于规则/去重；需保留原值与缺失状态 |
| `greeting` | 应用招呼语 | null | 应用存储/处理 → 本地API | 0/5非空 | 非岗位事实；不用于岗位匹配硬筛 |
| `greeting_activity` | API招呼语活动状态 | null | 本地API动态加工 | 0/5非空 | 非岗位事实；不用于岗位匹配硬筛 |
| `greeting_optimized` | 招呼语优化稿 | null | 应用存储/处理 → 本地API | 0/5非空 | 非岗位事实；不用于岗位匹配硬筛 |
| `greeting_original` | 招呼语原稿 | null | 应用存储/处理 → 本地API | 0/5非空 | 非岗位事实；不用于岗位匹配硬筛 |
| `greeting_reviewed_at` | 招呼语审查时间 | null | 应用存储/处理 → 本地API | 0/5非空 | 非岗位事实；不用于岗位匹配硬筛 |
| `greeting_selection` | 招呼语选择状态 | string | 应用存储/处理 → 本地API | 5/5非空 | 非岗位事实；不用于岗位匹配硬筛 |
| `greeting_style_issues` | 招呼语质量提醒列表 | array | 应用存储/处理 → 本地API | 0/5非空 | 非岗位事实；不用于岗位匹配硬筛 |
| `hr_active` | 公开活跃标签 | string | 详情解析 → 采集器 → 本地API | 5/5非空 | 非岗位事实；不用于岗位匹配硬筛 |
| `hr_name` | 公开招聘者姓名 | string | 详情解析 → 采集器 → 本地API | 5/5非空 | 非岗位事实；不用于岗位匹配硬筛 |
| `hr_title` | 公开招聘者职位 | string | 详情解析 → 采集器 → 本地API | 5/5非空 | 非岗位事实；不用于岗位匹配硬筛 |
| `id` | BossHunter岗位标识 | string | 采集器链接派生 → 本地API | 5/5非空 | 非岗位事实；不用于岗位匹配硬筛 |
| `jd` | 已清洗的可用JD | string | 详情解析 → 采集器 → 本地API | 5/5非空 | 后续AI语义复筛（结合简历与偏好） |
| `last_error` | 应用处理错误 | null | 应用存储/处理 → 本地API | 0/5非空 | 非岗位事实；不用于岗位匹配硬筛 |
| `last_error_code` | 应用错误码 | null | 应用存储/处理 → 本地API | 0/5非空 | 非岗位事实；不用于岗位匹配硬筛 |
| `outsourcing_confirmed` | 外包确认标记 | boolean | 应用存储/处理 → 本地API | 5/5非空 | 非岗位事实；不用于岗位匹配硬筛 |
| `outsourcing_layers` | 外包链条规则结果列表 | array | 应用存储/处理 → 本地API | 0/5非空 | 非岗位事实；不用于岗位匹配硬筛 |
| `outsourcing_level` | 规则判断的外包级别 | string | 应用存储/处理 → 本地API | 5/5非空 | 非岗位事实；不用于岗位匹配硬筛 |
| `outsourcing_matches` | 外包规则命中列表 | array | 应用存储/处理 → 本地API | 0/5非空 | 非岗位事实；不用于岗位匹配硬筛 |
| `outsourcing_rules_version` | 外包规则版本 | string | 应用存储/处理 → 本地API | 5/5非空 | 非岗位事实；不用于岗位匹配硬筛 |
| `outsourcing_updated_at` | 外包规则更新时间 | string | 应用存储/处理 → 本地API | 5/5非空 | 非岗位事实；不用于岗位匹配硬筛 |
| `quick_score` | 原版本地预筛分 | integer | 应用存储/处理 → 本地API | 5/5非空 | 非岗位事实；不用于岗位匹配硬筛 |
| `recruitment_type` | 规则推导的招聘类型 | string | 详情/候选语义规则 → 本地API | 5/5非空 | 可作候选信号，规则推导不当成平台事实 |
| `resume_failure_reason` | 简历失败原因 | null | 应用存储/处理 → 本地API | 0/5非空 | 非岗位事实；不用于岗位匹配硬筛 |
| `resume_generation_source` | 简历生成来源 | null | 应用存储/处理 → 本地API | 0/5非空 | 非岗位事实；不用于岗位匹配硬筛 |
| `resume_image_path` | 简历图像路径（屏蔽） | null | 应用存储/处理 → 本地API | 0/5非空；快照统一屏蔽 | 非岗位事实；不用于岗位匹配硬筛 |
| `resume_path` | 本地简历路径（屏蔽） | null | 应用存储/处理 → 本地API | 0/5非空；快照统一屏蔽 | 非岗位事实；不用于岗位匹配硬筛 |
| `resume_review_status` | 简历审查状态 | string | 应用存储/处理 → 本地API | 5/5非空 | 非岗位事实；不用于岗位匹配硬筛 |
| `resume_reviewed_at` | 简历审查时间 | null | 应用存储/处理 → 本地API | 0/5非空 | 非岗位事实；不用于岗位匹配硬筛 |
| `resume_source_path` | 简历源路径（屏蔽） | null | 应用存储/处理 → 本地API | 0/5非空；快照统一屏蔽 | 非岗位事实；不用于岗位匹配硬筛 |
| `salary` | 薪资文本 | string | 列表/详情解析 → 采集器 → 本地API | 5/5非空 | 可用于规则/去重；需保留原值与缺失状态 |
| `score` | 历史评分/默认值 | integer | 应用存储/处理 → 本地API | 5/5非空 | 非岗位事实；不用于岗位匹配硬筛 |
| `score_reason` | 历史评分解释 | string | 应用存储/处理 → 本地API | 5/5非空 | 非岗位事实；不用于岗位匹配硬筛 |
| `source_city_code` | 检索城市编码上下文 | string | 采集器检索上下文 → 本地API | 5/5非空 | 非岗位事实；不用于岗位匹配硬筛 |
| `source_job_id` | 链接派生来源ID | string | 采集器链接派生 → 本地API | 5/5非空 | 可用于规则/去重；需保留原值与缺失状态 |
| `source_keyword` | 检索关键词上下文 | string | 采集器检索上下文 → 本地API | 5/5非空 | 非岗位事实；不用于岗位匹配硬筛 |
| `source_platform` | 来源平台 | string | 采集器检索上下文 → 本地API | 5/5非空 | 可用于规则/去重；需保留原值与缺失状态 |
| `status` | 岗位应用状态 | string | 应用存储/处理 → 本地API | 5/5非空 | 非岗位事实；不用于岗位匹配硬筛 |
| `title` | 岗位名称 | string | 列表/详情解析 → 采集器 → 本地API | 5/5非空 | 可作候选信号，规则推导不当成平台事实 |
| `updated_at` | 本地更新时间 | string | 应用存储/处理 → 本地API | 5/5非空 | 非岗位事实；不用于岗位匹配硬筛 |
| `url` | 岗位详情URL | string | 列表/详情解析 → 采集器 → 本地API | 5/5非空 | 可用于规则/去重；需保留原值与缺失状态 |
<!-- SAMPLE_TABLE_END -->

## 四、适合怎样筛选

| 类别 | 建议字段/内容 | 限制 |
| --- | --- | --- |
| BOSS原生筛选 | 城市、薪资档、经验、学历、规模、职位类型 | 本轮仅关键词/城市/页数；源码参数与实时平台能力还需核对 |
| 本地规则 | 来源ID/URL去重、明确薪资文本、经验/学历枚举、公司/排除词、显式缺失值 | 薪资存在单位、薪数或字体差异；缺失不能被当作不符合要求，外包规则不等于平台事实 |
| 后续AI语义复筛 | JD里的技术栈、职责、经验深度、业务方向，与个人简历和偏好的匹配证据 | 三者共同判断，区分明确事实与推断；本轮不实现AI或设计权重 |

样本中企业规模/行业缺失，当前API未保留地区和完整岗位标签；JD长度与格式不一致，不能用固定标题正则硬拆职责/要求。薪资、经验、学历均为字符串，筛选前应保留原值并显式规范化；不能仅按字符串排序薪资。招聘者活跃标签是时效性文本，不适合作为稳定硬条件。

## 五、接口能力与本次任务识别

工作台动态options可以传检索条件，但原版会保存它们，所以采用JobPilot私有配置副本的原版HTTP进程；原用户配置和访问账本不清空。Agent API只接受mode/confirm，不能直接带动态条件。CLI支持--limit，但HTTP任务没有等价的逐条数量上限，页数和安全额度不可混为岗位数。

原生collection run含task_id、options、platform_states、collected_job_ids。JobPilot按该关联核对本次参数，只查询本次新ID的详情；原版历史重复只有计数，没有该run的完整重复岗位详情。失败或空结果不退回全局最新岗位。

新用户点击的真实检索尚待验收；本轮本地API样本读取和离线任务模拟不作为这项验收证据。已观察到原工作台此前存在daily_platform_page_limit停止记录，因此新的测试可能直接达到原额度；这属于保留安全策略的预期结果，不应清零或绕过。
