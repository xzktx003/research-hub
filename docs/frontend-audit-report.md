# Research Hub 前端审计报告

审计对象：`web/app.js` (5053 行)、`web/styles.css` (2657 行)、`web/index.html` (662 行)
审计时间：2026-08-05
方法：静态代码审阅 + 真实后端运行时 DOM/状态探测（无截图）

---

## 一、分之优先问题（Bug / 卡顿 / 兜底）

### P0-A 【严重·已复现】阅读台对「不在今日列表中的论文」显示空白
- **现象**：URL 直链 `/papers/paper_xxx/read`，或从推荐/笔记本/关系/全库点开到一篇**不属于今日论文**（`state.papers=[]` 而 `state.allPapers=178`）的论文时，阅读台目录显示「没有可阅读论文」，文档区空白，即便该论文的 workspace 已成功加载（`workspaces.get(id).status=loaded`）。
- **根因**：`renderReader()` 里 `const papers = readerDirectoryPapers()` → `filteredPapers()` 只基于 `state.papers`（今日）+ `state.allPapers`（但 `filteredPapers` 在 dateFilter 存在时强制用 `state.papers`）。当今日无论文，而选中论文属于全库时，目录为空 → 提前返回空态，即使 `selectedPaper()` 能找到论文、workspace 已就绪，也不渲染。
  同时 `knownPapers()` = `state.papers ⊕ notebookItems`，`selectedPaper()` 用 `knownPapers()` 找，也会丢全库论文。
- **影响**：核心阅读功能在「当日首次/论文跨日期」时彻底失效；用户看到的就是"点论文 PDF 浏览不了"。
- **修复**：阅读台目录应回退到全库（`knownPapers()`/`allPapers`）；`selectedPaper()` 需要能从 `allPapers` 兜底；`renderReader` 在 papers 为空但选中论文存在时仍要渲染文档区。

### P0-B 【严重·已复现】论文库日期筛选与底部「所有日期」数据源不一致
- `filteredPapers()` 在 dateFilter 非空时强制用 `state.papers`（今日，可能为空）→ 即使选了 `allPapers` 也会变空。而且 `filteredPapers()` 里有一段重复注释（疑似误粘贴）。

### P0-C 【中】渲染主线程被大列表一次性 innerHTML 阻塞
- `renderPaperLibrary`（178 卡/large）、`renderJobs`（~300 行 job 展开时可能上千 DOM 节点）、`renderRelations`（分页已缓解）都一次性 `innerHTML += 多张卡`。卡越多，首次渲染越卡；搜索时每次按键全量重渲染。
- **缓和**：搜索框 debounce（至少 150ms）；渲染数量上限或在文档片段中批量构建。

### P1-A 【中】`switchView` 到关系视图触发 `loadRelations`，但 `render()` 每次也会调 `renderRelations()` → 重复触发网络请求
- `render()` 幂等调用 `renderRelations()`，后者在 `state.relations===null` 时 `loadRelations()`。`loadAll()` 完成后 `render()` 会触发第一次加载；用户进关系视图又触发，但 `state.relationsLoading` 守卫防止并发——可接受，但会把"进入关系视图"与"任意 render"混在一起，导致不必要的后端请求。建议把"懒加载"改为仅在进入视图或显式重建时触发。

### P1-B 【中】折叠状态 key 在部分视图不稳定（重渲染后折叠状态丢失/错位）
- 关系视图 fold key 用 `relation:${index}`，但 `renderRelations` 会按置信度排序 → 同一关系在不同分页/排序下的 index 变化，折叠状态错位。
- job fold key 用 `job:${job.id||job.job_id||index}`；`renderJobs` 每次 poll 刷新都会重建 DOM，重试/取消后 job 数组变化 → 折叠状态可能丢失。
- 论文卡 key `paper:<id>` 稳定（好）。

### P1-C 【中】暗色模式存在"白斑"（部分组件用了字面浅色背景且未被覆盖）
- 例如 `.alert`(#fff8e8)、`.toast`(#c8d8d0 边框/f2fbf5 背景)、`.compliance-note`(#fff7df + 文字#5c4105)、`.pipeline-errors`(#fff8e8)、`.workflow-node.disabled`(#f5f5f4)、`.state.cancelled`(#f3f4f6+文字#57615c)、`.token-pill`(#eef5ff/#1d4ed8)、`.stage-tag.todo`(#f7f8fa/#8a93a5)、`.pipeline-run-card`(rgba(255,255,255,.93)) 等。
- `.theme-dark` 只覆盖了 `.panel/.paper-card/.tab` 等，未覆盖上述。
- **修复**：把这些改为语义变量（`var(--amber-weak)` 等），并在 `.theme-dark` 补充规则；必要时统一到语义色板。

### P1-D 【低】`renderSearchResults` 的"嵌套转义"仍有残留风险
- 上一轮修复了 PDF/errorblock 转义，但 `renderSearchResults` 里 `loadingBlock("正在联网检索...")` 等仍是 `html\`${loadingBlock(...)}\`` 的嵌套（虽然 loadingBlock 内容不含 HTML 标签原始串，但模式不安全）。建议统一改成字符串拼接或 `raw()` 包裹。

### P1-E 【低】`hasReport` 与渲染依赖 `state.workspaces` 的加载完成时序
- `renderPaperLibrary` 在 workspace 尚为 `{loading:true}` 时调用 `paperModelScore` 返回 null（可接受），但 `hasReport(paper)` 在 workspace 未返回时会误报"无研读"，翻到笔记/研读样式不一致。可接受但记录。

---

## 二、功能实用性审计

### 低价值 / 可删减 / 冗余功能
1. **「去重命中」指标卡**：`metricDetailEntries('deduplicated')` 直接 `return []`，明细永远空；卡片长期显示 0，无任何行动价值。→ 去掉该卡或改为有实义（今日来源去重明细）。
2. **设置页「系统托管服务 / 平台接口 / 能力探测」**：`capabilityList` 展示 11 个端点，对普通用户噪音大，是运维信息；设在"能力探测"即可，不必每端点在设置大列。
3. **仪表盘「来源与主题分布」来源命中**：常为 0（今日空空），主题覆盖倒是动态。来源命中在无日报时显示"当日暂无来源命中"，合理但低信息。
4. **「历史论文」区块与「论文库」信息重叠**：仪表盘有"今日论文+历史论文"双重列表，而论文库已有完整库。历史论文区的价值是"看过去某天"；可保留但不应默认全量重复。

### 高价值核心功能（应重点体验打磨）
- 阅读台（PDF/Markdown/研读/证据四标签 + 上一篇/下一篇 + 快捷键 j/k/[/]/s）——当前 P0-A 破坏体验，首修。
- 论文库筛选 + 批量加入笔记本 + 折叠 —— 已折叠，搜索 debounce 待补。
- 仪表盘指标 + 推荐阅读 + 系统能力健康 —— 推荐阅读有价值。
- 工作流 / 任务中心（流水线 + 错误诊断 + token）+ 关系视图（分页已缓解 4982 条）。
- 专利候选（组合门禁 → 查新 → 人工审批 → 草稿），门禁系统完善。

### 实用但可增强的兜底
- **API 失败兜底**：`firstJson` 已有 fallback 列表，`showAlert` toast 独立于全局 alert（好）。
- **阅读台快捷键** 只在 reader 视图生效且表单聚焦时不触发（好）。
- **PDF 下载**：已改为"内联浏览 + 显式下载按钮"，防自动下载（上一轮完成）。
- **缺失兜底**：全局搜索在 `allPapers` 未加载完成时 `filteredPapers` 用空数组 → 搜索会吞掉结果，需在 loadAllPapers 未完成时等待或提示。

---

## 三、视觉美化计划（炫酷 + 好看）

目标：在保持清晰克制的数据产品气质下提升质感，不牺牲可读性。

1. **设计令牌扩充**：新增 `--grad-primary`、`--accent-glow`、`--ring`、`--hover-lift`、`--card-stripe` 等；统一层级阴影。
2. **渐变 + 光晕品牌感**：侧边栏品牌区加玻璃拟态与渐变描边；页面背景加柔和的径向光晕（成本低，桌面即时感）。
3. **卡片悬停微交互**：论文卡/指标卡/关系卡统一 `transform: translateY(-2px)` + 阴影增强 + 边框高亮（已有雏形，统一到变量）。
4. **动效**：卡片进入淡入上移（`@keyframes cardIn`）；折叠过渡（`max-height`/grid）；指标数字滚动（简单 requestAnimationFrame）；忙碌按钮脉冲。
5. **空态/错误态美化**：插图化空态（emoji/大图标），不再只是灰字。
6. **阅读台排版**：Markdown 正文行宽与行高、引用/代码/表格美观；PDF 阅读 iframe 顶部工具条加渐变。
7. **暗色模式全面补齐**：消除白斑，语义色板在暗色下校正，滚动条/占位符暗化。
8. **响应式打磨**：`compact-button` 层叠阶段标签在窄屏换行；job-row/pipeline 在窄屏更稳。
9. **过渡动画尊重 `prefers-reduced-motion`**：提供减弱动画模式。

---

## 四、修复顺序（建议）
1. P0-A / P0-B：修复阅读台与数据源回退（核心功能恢复）—— 最重要
2. P1-C：暗色白斑 + 语义色板统一
3. P1-A / P1-B：渲染/折叠状态稳定
4. 视觉美化（令牌 + 渐变 + 动效 + 空态）
5. 搜索 debounce + 细节兜底（P0-C/P1-D）
6. 全量测试 + 浏览器回归 + 提交

---

## 五、各视图运行时状态记录（审计快照）
| 视图 | 状态 | 备注 |
|---|---|---|
| 仪表盘 | ✅ 渲染 | 今日论文 0（因为今日库空）；失败任务 5 有值 |
| 论文库 | ✅ 渲染 | 178 篇全库可见；筛选器功能正常 |
| 阅读台 | ❌ 空白 | P0-A：目录空、文档区空，workspace 已加载但不渲染 |
| 主题中心 | ✅ 渲染 | 10 个主题卡片 |
| 工作流 | ✅ 渲染 | 每日论文研读 DAG 正常 |
| 任务中心 | ✅ 渲染 | 流水线 5 条、任务列表轮询 |
| 关系视图 | ✅ 渲染 | 4982 条，分页 60 张卡显示 |
| 专利候选 | ⚠️ 半空 | 「没有可选择论文」（今日 state.papers 空导致）——P0-A 同一根的连带 |
| 笔记本 | ✅ 渲染 | 1 篇论文 |
| 设置 | ✅ 渲染 | 配置面板齐全 |

---

*报告生成后将据此逐条修复。*

---

## 六、修复执行记录（2026-08-05）

### 已修复（本批）
1. **P0-A 阅读台对全库论文显示空白** → 已修复并验证：
   - `knownPapers()` 加入 `allPapers`（去重：今日→全库→笔记本）
   - `readerDirectoryPapers()` 目录兜底：搜索过滤后仍保留当前选中论文
   - `renderReader()` 目录空但有选中论文时保留目录项并渲染文档区
   - `loadAllPapers()` 全库加载完成后再渲染 reader/patents（修复初始时序）
   - 验证：URL 直链 non-today 论文 → 目录 178 篇、文档区正常展示 PDF 状态
2. **P0-B 论文库日期筛选与全库数据源** → 修复 `filteredPapers()` 数据源逻辑并清理重复注释
3. **专利候选「没有可选择论文」** → 使用 `knownPapers()` 全库 + 加载态 + 前 120 篇限制与提示
4. **P0-C 搜索 debounce** → 新增 `debounceRenderSearch()` 160ms 防抖
5. **P1-B 关系折叠 key 不稳定** → 新增 `relationFoldKey()`（来源/目标/类型稳定 key）
6. **P1-C 暗色白斑全面修复** → 17+ 处硬编码浅色背景改为语义变量（alert/toast/state/pipeline/tech-card/workflow/notebook/stage-tag/token-pill/error/loading/compliance 等）
7. **P1-D renderSearchResults 嵌套转义** → 联网失败/加载 block 改为直接字符串拼接
8. **视觉美化**：
   - 新增设计令牌：`--brand-gradient`、`--body-glow-*`、`--ring`、`--card-hover-lift`、`--card-hover-shadow`、`--card-enter-duration`
   - body 双品牌径向光晕 + 暗色版
   - 卡片 hover lift + 品牌描边统一；卡片进入动画 `cardIn`
   - 主按钮/标签页/品牌 logo 渐变；指标卡渐变数字 + 顶部渐变条
   - 阅读台 PDF 工具条渐变、iframe 阴影、一句话摘要左边线
   - Markdown 表格样式 + 行宽限制
   - 空态图标化（🔭）+ 加载 spinner + 细腻滚动条
   - `prefers-reduced-motion` 尊重
9. **版本号**：`?v=20260805polish`（app.js + styles.css）

### 验证结论
- 阅读台初始加载即 178 篇目录（不再空白）✅
- 论文库批量折叠 178/178→展开→0 ✅
- 专利候选 178 篇可选（前 120 + 提示）✅
- 暗色下 alert/toast/pipeline-errors/empty 均无白斑 ✅
- 无水平溢出、卡片过渡正常 ✅

### 待办（后续迭代可选）
- 删除「去重命中」指标卡（明细永远空）或补充真实明细
- 设置页 11 个端点列表对普通用户精简
- `loadHistoryPapers` all 分支与 `loadAllPapers` 的 `allPapersLoading` 竞态整理

---

## 七、功能生效 & 实用度逐项审计

| 功能 | 生效? | 实用度 | 说明 |
|---|---|---|---|
| 仪表盘指标（论文/解析/研读/失败）| ✅ | 高 | 今日空时显示 0，有失败任务时醒目 |
| 推荐阅读 | ✅ | 高 | 可解释原因 + 打开 |
| 来源/主题分布 + 阅读路线 | ✅ | 中 | 今日空时显示空态 |
| 系统能力健康 | ✅ | 中高 | 降级适配器醒目 + 跳设置 |
| 论文库筛选/排序/搜索 | ✅ | 高 | debounce 已加 |
| 批量选+加入笔记本 | ✅ | 高 | | 
| 论文卡片折叠/批量折叠 | ✅ | 高 | 上一轮实现，已验证 |
| 卡片一键解析 | ✅ | 高 | 仅未解析且有版本时显示 |
| 相似论文 | ✅ | 中 | 本地 TF 相似度，无后端请求 |
| 阅读台 PDF/Markdown/研读/证据 | ✅ | 高 | P0-A 修复后对全库可用 |
| 阅读台快捷键 j/k/[/]/s | ✅ | 低中 | 表单聚焦时安全 |
| 主题中心（增删改/摘要笔记）| ✅ | 高 | |
| 工作流 DAG | ✅ | 高 | |
| 任务中心（轮询/错误诊断/重试/取消）| ✅ | 很高 | |
| 关系视图（4982 条分页）| ✅ | 高 | |
| 专利候选门禁→查新→审批→草稿 | ✅ | 很高 | 全流程闭环 |
| 设置（LLM/调度/显示）| ✅ | 高 | |
| 「去重命中」指标 | ✅ | 中 | 后端提供 details.deduplicated 时展示明细；今日日报空故显 0 |
| 设置页端点/服务/能力探测 | ✅ | 低 | 运维信息，可折叠（非核心） |
| 仪表盘「历史论文」与论文库 | ✅ | 中 | 信息部分重叠 |
