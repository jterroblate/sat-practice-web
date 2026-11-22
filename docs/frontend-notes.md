# 前端说明与数据接口

这份文档写给两类读者：继续改前端的人（包括之后的 Claude / Codex 会话），以及正在整理后端和题库数据库的人。它说明前端现在长什么样、哪些地方不能随便动、前端从数据里读哪些字段，以及数据补齐后前端会自动发生什么变化。

前端只有两个模板，都在 `skills/skill-sat-rw-classification/assets/templates/`：

| 文件 | 页面 |
| --- | --- |
| `learning_index_v2.html` | 学习中心：首页、我的复习、学习诊断、生成今日练习、全库搜索 |
| `html_template_v1.1.html` | 练习页（套题、专项、题型分类共用） |

改完保存后 `dev.py` 会自动重建。不要改 `build/` 里的文件。

## 1. 设计约定

两个页面共用一套视觉：浅灰页面底色 `#f5f5f7`、白色大圆角卡片、胶囊按钮、毛玻璃顶栏、蓝色强调色 `#0071e3`。学习中心的变量以 `--zen-` 开头，练习页的变量以 `--sat-` 开头，数值保持一致。新增样式请直接用这些变量，不要写新的颜色值。

- 正文灰字用 `#6e6e73`，在白底和浅灰底上都满足 WCAG AA。
- 蓝底白字只用 `#0071e3` 或更深的蓝。
- 文章、题干和选项用衬线字体（`--sat-font-reading`），界面文字用系统字体。
- 手机上可点的控件高度不低于 36px，主要操作不低于 44px。

## 2. 练习页结构

布局参考机考界面：

- **顶栏**：左边是套题标题和「学习首页」链接；右边是「题目列表」「保存选中文本」和「更多」菜单（提交已作答题、导出 JSON / PDF、重置本页、正式模考）。
- **主体**：左文章、右题目，中间一条分隔线，两栏各自滚动。宽度 1100px 以上时最左边多一栏题号列表，可以收起。
- **题目栏**：题号徽标、加入复习、题干、选项、提交本题。提交前只显示「答题信心」；提交后才出现笔记，答错才出现错因。「查看答案与解析」在可用之前不显示。
- **底栏**：左边保存状态，中间「第 x/N 题」按钮（点开是答题卡），右边上一题 / 下一题。

宽度 820px 以下变成单栏，整页滚动，底栏固定在屏幕底部。

### 不能改的东西

浏览器回归脚本 `scripts/verify-sat-learning-browser.mjs` 和页面脚本都靠这些 id 工作。可以移动位置、改样式，不能改名或删除：

`progressText` `questionNumber` `overviewBtn` `overviewPanel` `overviewGrid` `checkCurrentBtn` `checkAllBtn` `revealBtn` `retryBtn` `returnOriginalBtn` `highlightBtn` `starBtn` `resetBtn` `resetConfirm*` `exportJsonBtn` `exportPdfBtn` `examModeBtn` `prevBtn` `nextBtn` `syncStatus` `modeBanner` `reflectionPanel` `errorTagsRow` `noteInput` `reflectionHint` `sourceNotice` `cloudMigrationNotice` `satLegacyBackup*` `practiceLayout` `practiceNav` `examShell`，以及类名 `.choice` `.export-group` `.export-label`。

- `questionNumber` 的文字内容仍是 `Question 9`，其中 `Question ` 用视觉隐藏的 `span` 包着，屏幕上只看到数字。
- 「更多」菜单里的按钮全部被隐藏时（空白重做、模考），菜单自己会隐藏，不需要手动处理。
- 题号列表是否展开存在 `sessionStorage` 的 `sat-ui:question-list` 里，只是界面偏好，不属于学习记录，不要挪进 `learningStorage`。

### 保留的说明文字

`AGENTS.md` 要求保留「来源未经认证、编辑校订」这类限定。它们现在显示在文章下方的小字脚注里（`#sourceNotice`、`#cloudMigrationNotice`），首页题库卡片上的「来源整理练习 · 出处未认证 · 非完整模考」也保留。其他面向维护者的备注已经从页面上移除，新增功能时不要再把内部状态、迁移进度、数据校验情况写到学生能看到的地方。

## 3. 生成今日练习

入口是 `#practice`，代码在 `learning_index_v2.html` 里以 `/* ---------- 生成练习 ----------` 开头的一段。

### 筛选项

| 界面 | 表单字段 | 取值 | 选择方式 |
| --- | --- | --- | --- |
| 选择科目 | `subject` | `reading_writing` / `math` | 单选 |
| 题目范围 | `kind` | `real_exam`（真题）/ `practice`（模拟题） | 多选，不选即不限 |
| 选择专项 | `domain` | 题目的 `domain` 原文 | 多选，不选即全部 |
| 选择知识点 | `skill` | 题目的 `skill` 原文 | 多选，不选即全部 |
| 选择分类 | `genre` | `poetry` / `fiction`，以及数据里出现的其他值 | 多选，不选即全部 |
| 按作答情况 | `source` | 见下表 | 多选，不选即不限 |
| 选择难度 | `level` | `easy` / `medium` / `hard` | 多选，不选即不限 |
| 选择题量 | `count` / `customCount` | 5、10、15、20、50、100，或 1–200 的自定义值 | 单选 |

不同维度之间是「并且」，同一维度内是「或者」。每个选项后面的数字是「在其他维度的当前条件下，选它能得到多少题」，数字为 0 的选项会变灰。

作答情况的判定（数据来自本机学习记录 `qState()`）：

| 取值 | 界面文字 | 判定 |
| --- | --- | --- |
| `unseen` | 未作答 | `!submitted` |
| `wrong` | 作答错误 | `firstWrongAt && !mastered` |
| `repeat_wrong` | 多次答错（≥2次） | 首次答错，且最近一次重做仍然答错 |
| `redo_correct` | 错题重做后做对 | `firstWrongAt && lastRedoCorrect === true` |
| `due` | 到期复习 | `nextDueAt` 已到且未掌握 |
| `starred` | 星标题 | `starred` |
| `confidence` | 低信心题 | `confidence` 是 `unsure` 或 `guess` |
| `weak` | 薄弱题型 | 该题型已作答 ≥5 题且正确率低于 70% |

首页可以带着预设跳进来：`#practice/due`、`#practice/confidence`，其他取值同理。

### 前端从题目里读的字段

所有字段只在 `builderMeta(question)` 一个函数里归一化。数据库字段有变化时，只改这个函数。

| 用途 | 现在读取 | 现状 | 希望后端提供 |
| --- | --- | --- | --- |
| 科目 | `question.subject` → `question.section` → 所在页面的 `section` | 题目上没有这两个字段，靠页面的 `section` 推断，全部是 `reading_writing` | 每道题带 `subject`：`reading_writing` 或 `math` |
| 专项 | `question.domain` | 四个读写专项都有 | 数学题用 `Algebra`、`Advanced Math`、`Problem-Solving and Data Analysis`、`Geometry and Trigonometry`，中文名已经在 `BUILDER_DOMAINS` 里 |
| 知识点 | `question.skill` | 11 个读写题型，中文名在 `SAT_SKILL_ZH` | 数学知识点需要补中文名，否则显示英文原文 |
| 分类 | `question.genre` → `question.passageGenre` → 从文章开头推断 | 没有字段。现在靠「The following text is from …'s 1857 novel / poem …」这句话识别，只认出小说类 68 题、诗歌类 8 题 | 每道题带 `genre`，如 `poetry`、`fiction`。出现新值时界面会自动多出一个选项，但需要在 `BUILDER_GENRES` 里补中文名 |
| 题目范围 | `question.sourceKind` | 数据库分支合并前没有这个字段，两个选项都是 0 题并且变灰 | `real_exam` / `practice`。为空的题只在「不限」时出现 |
| 难度 | `question.difficultyLevel` → 旧的 `question.difficulty`，只认 `easy` / `medium` / `hard` | 只有 145 题有标注，其余当作未标注 | 未标注的题在选了难度之后不会被抽到 |
| 多次答错 | 记录里没有答错次数，用「首次答错 + 最近重做仍错」近似 | 只能区分「至少错两次」 | 学习记录里加 `wrongCount`，并在 `normalize()` 里保留，然后把 `builderRows()` 里的 `wrongCount` 改成直接读它 |

数学题进来之后，「Math」会自动从「即将开放」变成可选，不需要改界面代码。预计用时按读写每题 1–1.4 分钟、数学每题 1.3–1.8 分钟估算（`BUILDER_MINUTES`）。

### 建立的会话

点「开始练习」后写入 `learningStorage` 的 `sat-learning:today-session:v2`，然后跳到第一题的练习页。结构在原有基础上只多了 `filters`，练习页不读这个字段：

```json
{
  "id": "session-1795238234300",
  "createdAt": "2026-11-21T05:17:14.300Z",
  "sources": ["unseen"],
  "filters": {
    "subject": "reading_writing",
    "domains": ["Craft and Structure"],
    "skills": ["Words in Context"],
    "genres": [],
    "statuses": ["unseen"],
    "difficulties": ["hard"],
    "count": 7
  },
  "answers": {},
  "questions": [{ "questionId": "wic-hard-153579", "route": "collections/words-in-context-hard.html" }]
}
```

`sources` 在没选作答情况时是 `["all"]`，和以前「全部题库」的含义一致。题目是在本机从符合条件的题里随机抽的；以后如果改成由后端出题，保持这个结构、把 `questions` 换成后端返回的列表即可。

## 4. 来源、年份和题号

规则：**题目的来源、年份和原卷题号只在提交答案之后显示**，错题本里同样显示；提交之前不出现在界面上。

- 练习页的题号牌、题号列表和答题卡显示的是练习内的序号（第 1 题、第 2 题……），今日练习会话里是本次练习的第几题。原卷题号只出现在提交后的来源一行里。
- 提交后，反馈下方出现一行「来源：…」（`#sourceLine`）。内容来自 `source_refs[0]`：来源名称、年份（名称里没有时才补）、模块、原卷题号。字段缺失时退回到套题名称和 `question_number`。
- 错题本和搜索结果的卡片只给已提交的题显示来源；没提交的题只显示题型。
- 今日练习会话里，顶栏标题显示「今日练习」而不是套题名称。
- 文案由共享脚本里的 `describeSatSource(refs, fallback)` 生成。`real_exam` 标注为「回忆版真题，来源未经认证」，`practice` 标注为「模拟题」，不要改成「官方真题」之类的说法。
- 没有处理的地方：浏览器标签页标题和网址里仍然有套题名称；自己从题库入口选套题进入时，标题本来就是套题名称。

读取的字段（首页驼峰、练习页下划线）：`sourceKind` / `source_kind`、`sourceYear` / `source_year`、`difficultyLevel` / `difficulty_level`、`sourceRefs` / `source_refs`。`undefined`、空字符串、`null`、空数组都当作未知。

## 5. 模考

模考在练习页里进行，入口是 `套题页?mode=exam`；学习中心多了一个「模考」页面（`#exam`）作为入口和记录列表。

- **资格没有变。** 只有 `examEligible === true` 并且带有合法 `moduleSpec`、`examVariants` 的套题才能模考，判定逻辑（`normalizeExamConfig()`）没有动。当前题库里没有一套符合，所以「可用模考」是空的。要让某套题可以模考，需要数据侧在 registry 里补齐模块划分，前端不需要改。
- **界面**：开始页 → 模块做题（顶栏是模块名、倒计时和「隐藏计时」，题目栏有「标记待查」，底栏中间的按钮打开题目导航）→ 最后一题的「下一题」进入检查页 → 提交 → 模块间休息 → 第二个模块 → 成绩页 → 答案与解析。做题过程中不显示对错和解析。
- **标记待查**存在会话的 `modules[key].marked` 数组里，是新增的可选字段，旧会话没有它也能正常读取。
- **模考记录**：每场模考完成时归档一份到按账号隔离的本机存储，键是 `sat-exam-history:v1`，最多 50 条。结构是 `{ version, records: [{ id, pageId, title, variantId, branch, createdAt, completedAt, modules: [{ key, label, durationSeconds, startedAt, submittedAt, submitReason, questionIds, answers, marked, score }] }] }`。只在本机，不上传云端。
- **回看记录**：`套题页?mode=exam&history=<id>`，先显示成绩，再进解析。回看只需要题目还在，不要求这套题现在仍可模考。成绩按当前的正确答案重新计算。
- 成绩页和解析页的文案保留了「不是 SAT 换算分」「不自适应分流」的限定，不要删。
- 本地想看模考界面，可以运行工作区里的 `tools/make_exam_fixture.py`（在仓库外面），它只改 `build/demo` 里的生成物，把演示题临时标成可模考。

## 6. 首页与学习时长

首页由七个板块组成，在 `renderHome()` 里按固定顺序输出：今日练习、练习日历、错题复习、全真模考、学习概览、周学习报告、最近练习。排版只靠 CSS 网格的 `grid-template-areas` 决定，板块自己用容器查询适应宽度。

- **排版**：三列网格（两列等宽 + 右侧 330px），`.home` 的 `grid-template-areas` 为 `today today calendar / review exam stats / weekly weekly recent`，外层 `.home-wrap` 最宽 1120px；860px 以下改成单列，按上面的输出顺序排列。
- **练习日历**：`calendarBlock()`。有练习的日期 = 当天有学习时长，或者当天有提交记录。可以切换本周 / 本月。连续学习天数在今天还没练时从昨天往前数。
- **今日练习**：今日学习时长、今日已做题目（当天的提交次数，含重做）。
- **错题复习**：待掌握错题、今日到期复习。
- **全真模考**：可用模考数量、上次成绩，按钮进入模考中心；有进行中的模考时变成「继续模考」。

学习时长由练习页记录，代码在共享脚本 `learning_storage.js` 的末尾：

- 只在练习页计时（含模考），首页不计。页面可见、并且最近 3 分钟内有过鼠标、键盘、滚动或触摸操作时才累计，每 15 秒写入一次，切走或关闭页面时再写一次。
- 存在按账号隔离的本机存储里，键是 `sat-learning:study-time:v1`，内容是 `{ "version": 1, "days": { "2026-11-21": 1500 } }`，值是当天的秒数，日期按本机时区，最多保留 400 天。
- **只在本机，不上传云端，也不跨设备合并。** 换设备或清除浏览器数据后会归零。如果后端要同步它，建议按天取各设备的较大值或按设备分别累加，不要简单覆盖。
- 首页用 `summarizeSatStudyTime(learningStorage)` 读取，返回 `{ today, week, total }`（秒）；显示用 `formatSatStudyTime()`。

## 7. 已知问题和建议

- **套题标题是内部标识。** 例如 `01 2025 10 Apac B`、`04 OCT SAT Na 2`。建议数据库里给每套题一个显示用的标题（如「2025 年 10 月 · 亚太 B 卷」）以及年份、地区、卷别字段，首页卡片就能按年份和地区分组。
- **学习诊断的细分题型是原始标签。** 例如 `precise_word_choice`、`vocabulary_in_context`、「未细分」，同一个意思有多种写法。建议数据库把 `subtype` 归并成固定的一组，并提供中文名。
- **解析末尾的 `Skills:` 一行显示的是 `skills_tested` 原文。** 归并之后可以换成中文知识点。
- **刷新后已揭示的题目**，选项仍标着对错颜色，但解析面板是收起的。
- **练习页没有键盘快捷键。** 可以考虑 A–D 选答案、←/→ 翻题、Enter 提交。
- **`返回学习项目` 链接指向 `/app/`**，本地预览是 404，只在生产环境有效。
