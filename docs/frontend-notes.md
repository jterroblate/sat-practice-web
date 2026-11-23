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

- **顶栏**：左边是套题标题和「学习首页」链接；右边是「题目列表」「保存选中文本」「加入生词本」和「更多」菜单（提交已作答题、导出 JSON / PDF、重置本页、正式模考）。
- **主体**：左文章、右题目，中间一条分隔线，两栏各自滚动。宽度 1100px 以上时最左边多一栏题号列表，可以收起。普通练习、练习会话（今日练习、生成练习、错题复习）和空白重做都有这一栏：会话里列的是本次练习的题，点题号会跳到那道题所在的页面；空白重做里当前题显示为未作答，不露出原来的对错。模考用自己的题号面板。
- **题目栏**：题号徽标、加入复习、单题计时（右端）、题干、选项、提交本题。提交前只显示「答题信心」；提交后才出现笔记，答错才出现错因。「查看答案与解析」在可用之前不显示。
- **底栏**：左边只在同步或保存出问题时显示状态（本机保存成功不提示），中间「第 x/N 题」按钮（点开是答题卡，和题号列表显示同一组题，各种宽度下都可用），右边上一题 / 下一题。

宽度 820px 以下变成单栏，整页滚动，底栏固定在屏幕底部。

### 词汇解释和生词本

数据从哪里来见 [content-db.md](content-db.md#词汇解释)。页面上的行为：

- **文章和题干里的难词**带一条虚线下划线，点一下（或用键盘聚焦后按 Enter）在词旁边弹出解释卡片。卡片是分层的：先是英文释义；「例句」默认展开；「中文解释」默认收起，里面是中文释义和例句译文。Esc、点别处或滚动页面会关掉卡片。
- **选项里的词提交前不显示任何标记**，也不能点（选项本身是按钮，而且词汇题考的就是这几个词）。提交之后，解析下方出现「本题词汇」，按文章、题干、选项的顺序列出这道题全部带解释的词，每个词一行，可以单独展开，选项里的词带「选项」标签。
- **「most nearly mean」题问的那个词**，提交前在文章里也不能点。这是数据里的 `reveal: "after_submit"`，页面只按这个字段判断。
- **加入生词本**有三个入口：解释卡片里的按钮、「本题词汇」每一行里的按钮、以及顶栏的「加入生词本」（先在文章、题干或选项里选中一个英文单词或短语，再点）。最后一种可以加入任何词；如果选中的词有解释而且当时可以打开，解释会一起带上，否则只记下这个词，标为「自己添加」。已经在生词本里的词在文章里有浅黄色底。
- **模考**里没有词汇解释，顶栏的「加入生词本」也不显示。空白重做和练习会话按当前这一遍是否已提交来判断，和普通练习一样。

生词本存在按账号隔离的本机存储里，键是 `sat-learning:vocabulary:v1`，内容是 `{ "version": 1, "words": [ … ] }`，最多 2,000 个词。每个词存的是加入时的一份完整内容（词、词性、英文释义、中文释义、例句、例句译文、词条的 `vocabId`、所在题目的 `questionId`、题目原句、加入时间、是否已掌握、自己的笔记）。学习中心显示生词本时，凡是词汇表里还有的词条，一律用词汇表里**当前**的释义和例句，所以以后释义改正了，已经加入的词会跟着变；只有词条已经不在词汇表里（或者是自己添加的词）时，才用加入时存下的那一份。读写它的函数在共享脚本 `learning_storage.js` 的末尾（`readSatVocabulary`、`saveSatVocabularyWord`、`updateSatVocabularyWord`、`removeSatVocabularyWord`），每次改动都先重新读一遍，所以同时开着两个页面不会互相覆盖。**只在本机，不上传云端，也不跨设备合并。**

学习中心导航里的「词汇」下面有两页，用页面上方的「词汇表 / 生词本」切换：

- **词汇表**（`#vocabulary/list`，`renderGlossary()`）：全部带解释的词，数据是 `MANIFEST.vocabulary`。可以搜索（单词、英文释义、中文释义），可以排序：出现题数多到少（默认）、真题出现题数多到少、出现题数少到多、字母顺序、按词性；可以按词性筛选，也可以只看还没加入或已经加入生词本的词。每次显示 60 个，点「再显示 60 个」继续。每个词一行，写着「出现 N 题 · 真题 M」；展开后是英文释义、例句、收起的中文解释、出现次数的说明和「加入生词本」。出现次数怎么算、有什么局限见 [content-db.md](content-db.md#拼写和出现次数)。
- **生词本**（`#vocabulary`，`renderVocabulary()`）：分「待掌握」「已掌握」「全部」三栏；每个词默认只显示词和词性，展开后依次是英文释义、例句、收起的中文解释、题目原句和「回到这道题」、笔记、「标为已掌握」和「移出生词本」。上方的输入框可以手动添加一个英文单词或短语。

### 不能改的东西

浏览器回归脚本 `scripts/verify-sat-learning-browser.mjs` 和页面脚本都靠这些 id 工作。可以移动位置、改样式，不能改名或删除：

`progressText` `questionNumber` `overviewBtn` `overviewPanel` `overviewGrid` `checkCurrentBtn` `checkAllBtn` `revealBtn` `retryBtn` `returnOriginalBtn` `highlightBtn` `starBtn` `resetBtn` `resetConfirm*` `exportJsonBtn` `exportPdfBtn` `examModeBtn` `prevBtn` `nextBtn` `syncStatus` `modeBanner` `reflectionPanel` `errorTagsRow` `noteInput` `reflectionHint` `sourceNotice` `cloudMigrationNotice` `satLegacyBackup*` `practiceLayout` `practiceNav` `examShell`，以及类名 `.choice` `.export-group` `.export-label`。

- `questionNumber` 的文字内容仍是 `Question 9`，其中 `Question ` 用视觉隐藏的 `span` 包着，屏幕上只看到数字。
- 「更多」菜单里的按钮全部被隐藏时（空白重做、模考），菜单自己会隐藏，不需要手动处理。
- 题号列表是否展开存在 `sessionStorage` 的 `sat-ui:question-list` 里，只是界面偏好，不属于学习记录，不要挪进 `learningStorage`。
- 题号列表和答题卡都从 `navigatorEntries()` 取数据：当前题属于练习会话时返回会话里的题，否则返回本页的题。要加新的做题模式，改这一个函数。

### 单题计时

题目栏右端的计时器记录每道题单独的作答用时，代码在练习页模板里以 `/* ---------- 单题计时 ----------` 开头的一段。

- 进入一道题开始计，切到别的题、页面不可见或点「暂停」时不计；回到这道题接着累计。提交后定格，显示「用时 01:23」，暂停按钮消失。
- 普通练习按题目记（`q:<题号>`），练习会话按会话和题目记（`s:<会话 id>:<题号>`），空白重做每次从零开始、不落盘（`r:<题号>`）。「重置本页」会清掉本页各题的用时。
- 用时存在 `learningStorage` 的 `sat-learning:question-time:v1`，和学习时长一样只在本机，不进云端 DTO，不参与合并。写入失败时静默忽略，不影响作答。
- 在加这个功能之前就已提交的题没有用时记录，计时器不显示。
- 模考有自己的模块计时，不显示单题计时。
- 元素 id：`questionTimer` `questionTimerValue` `questionTimerToggle`，回归脚本会用到。

### 页面上不放的文字

练习页只在有事要说的时候才出现提示：

- 选项下方的反馈区在提交前是空的（不再写「请选择答案，准备好后提交」），提交后才显示「回答正确 / 回答错误」。
- 笔记区下方不再有「会保存到统一复习中心」之类的说明；那个位置只留给操作出错时的提示。
- 练习会话不再有顶部横幅，进度看底栏和题目列表。横幅只在空白重做和重做链接失效时出现。
- 底栏在本机保存成功时不显示任何文字。
- 首页各页面标题下面只是复述标题的一句话、生成练习里每一组重复的「可多选，不选即不限」都去掉了，生成练习页顶部保留一句总的说明。

新增功能时照这个标准：说明性的句子如果去掉以后用户照样会用，就不要加。出错信息、来源限定和模考的限定不在此列。

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
| 细分题型 | `task` | `MANIFEST.taxonomy.tasks` 里属于所选知识点的那几项 | 多选。只在选了知识点之后出现，取消知识点时它下面选中的细分一起取消 |
| 文章类别 | `genre` | `MANIFEST.taxonomy.passageTypes` 的 id：`science` / `social` / `humanities` / `fiction` / `poetry` / `drama` / `nonfiction` | 多选，不选即全部。没有 taxonomy 的构建退回三个文体 |
| 重要性 | `importance` | `core` / `important` / `basic` | 多选，不选即不限。数据里没有 `importance` 时整组隐藏 |
| 按作答情况 | `source` | 见下表 | 多选，不选即不限 |
| 解析情况 | `explanation` | `full`（完整解析）/ `brief`（简要解析）/ `none`（暂无解析） | 多选，不选即不限。数据里没有 `explanationLevel` 时整组隐藏 |
| 选择难度 | `level` | `easy` / `medium` / `hard` | 多选，不选即不限。按钮上标出其中有多少题的难度是估计 |
| 难度依据 | `basis` | `stated`（来源标注）/ `estimated`（估计） | 多选，不选即不限。数据里没有 `difficultyBasis` 时整组隐藏 |
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
| 细分题型 | `question.task` | `content/db` 里按规则给每道读写题定的细分题型；它所属的知识点和专项在 `MANIFEST.taxonomy.tasks` 里。个别题记录的知识点和题目本身不符时，生成练习以细分题型为准 | 没有这个字段时按 `question.skill`、`question.domain` |
| 文章类别 | `question.passageType` → 旧的 `question.genre` → 从文章开头推断 | 每道读写题都有，名称在 `MANIFEST.taxonomy.passageTypes` | 出现新值时界面会自动多出一个选项 |
| 重要性 | `question.importance`，另有 `question.examSittings`（在几场考试里出现过） | 按来源算：核心 345、重要 2,703、基础 1,006 | 规则在视图 `question_importance` |
| 解析情况 | `question.explanationLevel` | 构建时从题目的解析内容算出：`full` 四段解析和每个选项的分析都有，`brief` 只写了一部分，`none` 没有。合并后是 1,752 / 905 / 1,397 | 没有这个字段的旧构建不显示这一组 |
| 题目范围 | `question.sourceKind` | 数据库分支合并前没有这个字段，两个选项都是 0 题并且变灰 | `real_exam` / `practice`。为空的题只在「不限」时出现 |
| 难度 | `question.difficultyLevel` → 旧的 `question.difficulty`，只认 `easy` / `medium` / `hard` | 合并后 4,054 题都有难度，但只有 899 题的难度来自题库文件标题，其余是估计 | 未标注的题在选了难度之后不会被抽到 |
| 难度依据 | `question.difficultyBasis` | `source_file` 算「来源标注」；`model`（模型只看题面推断，3,119 题）和 `estimate`（套题文件原有的估计，36 题）都算「估计」。真题的难度几乎全是估计 | 页面上不要把估计的难度当成确定的难度展示 |
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
    "tasks": [],
    "genres": [],
    "importances": [],
    "statuses": ["unseen"],
    "explanations": [],
    "bases": ["stated"],
    "difficulties": ["hard"],
    "count": 7
  },
  "answers": {},
  "questions": [{ "questionId": "wic-hard-153579", "route": "collections/words-in-context-hard.html" }]
}
```

`sources` 在没选作答情况时是 `["all"]`，和以前「全部题库」的含义一致。题目是在本机从符合条件的题里随机抽的；以后如果改成由后端出题，保持这个结构、把 `questions` 换成后端返回的列表即可。

### 回到上一次生成的那组题

练习页每次显示会话里的题，都会在会话记录里写下 `lastQuestionId` 和 `lastVisitedAt`。首页的主按钮据此决定去哪：

- 最近一次用的是生成的那组题（会话比最近打开的套题更晚用过）：回到那组题里停下的那一题；那一题已经提交就去第一道还没提交的。全部做完时按钮写成「回看上次练习」。
- 否则回到最近打开的套题，和以前一样。

### 复习星标和错题集在会话里

- 「加入复习」在普通练习、练习会话和空白重做里都能用，提交前后都可以。会话和空白重做是在一份空白副本上作答，所以星标记在这道题的长期记录上（`globalState.questions[题号].starred`），不记在空白副本里。
- 在会话或空白重做里第一次答错，会给长期记录写上 `firstWrongAt`，题目进入「我的复习 → 错题」。原套题页上的作答不动。
- 没有改的一点：在会话里第一次做的题，长期记录的 `submitted` 仍然是否，所以它在「按作答情况 → 未作答」里还会出现，首页的「已提交题目」也不计它。

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

- **资格由数据决定。** 只有 `examEligible === true` 并且带有合法 `moduleSpec`、`examVariants` 的套题才能模考，判定逻辑是 `normalizeExamConfig()`。数据侧的 `scripts/exam_modules.py` 从题库表推出读写 Module 1 + 第二模块的固定两模块模考（不做自适应分流，没有换算分），合并后有 19 套；只有前端分支时没有一套可模考，「可用模考」是空的。题号允许下划线（`safeExamToken`），否则真实题库的 `question_id` 会被判为非法。
- **成绩页只给两样东西**：总正确率，以及每个模块的正确率和错题题号（未作答的题号单独列出）。题号是模块内的顺序号，点题号直接进入这道题的答案与解析。
- **界面**：开始页 → 模块做题（顶栏是模块名、倒计时和「隐藏计时」，题目栏有「标记待查」，底栏中间的按钮打开题目导航）→ 最后一题的「下一题」进入检查页 → 提交 → 模块间休息 → 第二个模块 → 成绩页 → 答案与解析。做题过程中不显示对错和解析。
- **标记待查**存在会话的 `modules[key].marked` 数组里，是新增的可选字段，旧会话没有它也能正常读取。
- **模考记录**：每场模考完成时归档一份到按账号隔离的本机存储，键是 `sat-exam-history:v1`，最多 50 条。结构是 `{ version, records: [{ id, pageId, title, variantId, branch, createdAt, completedAt, modules: [{ key, label, durationSeconds, startedAt, submittedAt, submitReason, questionIds, answers, marked, score }] }] }`。只在本机，不上传云端。
- **回看记录**：`套题页?mode=exam&history=<id>`，先显示成绩，再进解析。回看只需要题目还在，不要求这套题现在仍可模考。成绩按当前的正确答案重新计算。
- 没有解析的题在解析页只显示「这道题暂无解析」，不再出现空的「关键证据」「正确思路」标题；普通练习的解析面板同样处理。
- 首页不再列出全部模考卡片（原来的 `#examLibrary`），只保留「全真模考」入口块，列表在 `#exam`。
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
- **词汇解释只做了核心题，释义全部是 AI 写的草稿**，没有经过人工核对。选项里的词不能在选项上直接点，只能提交后在「本题词汇」里看。
- **练习页没有键盘快捷键。** 可以考虑 A–D 选答案、←/→ 翻题、Enter 提交。
- **`返回学习项目` 链接指向 `/app/`**，本地预览是 404，只在生产环境有效。
