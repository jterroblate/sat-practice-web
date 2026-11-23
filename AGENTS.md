# 项目开发约定

适用于 Codex、Claude 和人工开发。先读 `README.md`、`CONTRIBUTING.md`、`docs/architecture.md`、`docs/data-contract.md`、`docs/production-handoff.md`。

## 修改入口

- 本仓库管理共同开发主线。任务从最新 `main` 建 `feat/*` 或 `fix/*`；开始前检查当前分支与 `git status`，保留已有未提交修改。
- 真实结构化题库与必要题图已获仓库所有者授权纳入本仓，可进行有依据的内容修订。源码在生成器、模板和运行时修改，题目在 `content/canonical/` 修改；不从生成 HTML 反向提取内容，不只手改生成页。首次导出工具不是日常更新入口，禁止用重导出覆盖已经评审的协作内容。
- `content/db/` 是题库的规范化表，其中已发布的题须与 `content/canonical/` 保持一致。改了 canonical 后运行 `python3 scripts/content_db.py normalize`，改了表后运行 `python3 scripts/content_db.py export`，两边一起提交。表里的草稿题（`status = draft`）不在站点上，发布前须逐题核对答案、转换正文并补齐必要图；说明见 `docs/content-db.md`。第三方教辅（来源等级 3）的来源和题目不进入这些表，导入脚本默认跳过。每道读写题须有细分题型和文章类别：导入或发布之后运行 `python3 scripts/classify_questions.py --apply`，词表拿不准的文章要逐篇判定，否则测试不通过。
- 词汇解释在 `content/db/` 的 `vocabulary`、`vocabulary_example`、`vocabulary_form`、`question_vocabulary` 四张表里，用 `python3 scripts/annotate_vocabulary.py` 写入。每个词至少三个例句，例句必须另写，不取自任何题目；释义和例句都不得抄自牛津等词典（条目归出版方所有，页面只链接官网）；`basis` 如实填写（AI 写的是 `ai_draft`，有人逐条核对后才改成 `human_checked`）；选项里的词和「most nearly mean」题问的词只能在提交后打开，不要为了方便改成提交前可见。
- 核心题的讲解在 `walkthrough`、`walkthrough_segment`、`walkthrough_choice` 三张表里，用 `python3 scripts/write_walkthrough.py` 写入，英文和中文都要写。讲解只依据题目和答案键；说「正确」的选项必须就是答案键里的那个，对不上时先核对题目，不要硬写。`basis` 如实填写。讲解只在提交并查看答案之后显示，不进 `explanation`，不改变 `gradingRevision`。
- `content_db.py export` 只写 `content/canonical/sets/<名称>.json` 和 `content/canonical/collections/<名称>.json`，写之前核对全部目标（不接受绝对路径、`..`、符号链接），任一失败就一个文件都不改。仍放在某份材料里的草稿题会被 `check` 和 `export` 拒绝：撤回已发布的题，先把它从 canonical 材料里移出并 `normalize`，再把表里的状态改成 `draft`。
- `build/site/` 是按 `sat-release.json` 构建的发布产物，`build/site-preview/`（`python3 scripts/build-site.py --preview-all`）是全部发布开关打开的预览，`build/demo/` 是原创 fixture 产物，均不提交。测试账号、状态和学习记录使用合成数据；本仓公开题目不等于可以读取真实学生数据。
- 题型工具是开发工具，不是自动确认答案、题源身份或模考资格的审核者。内容更改在 PR 中列明 `question_id`、字段、理由与可公开依据；不编造来源或共识记录。

## 兼容要求

- 保留永久 `question_id`、`contentId`、公开路由 / `pageId`、题目顺序与既有迁移。身份调整、删除或评分变化先写兼容方案，再实现并测试。
- `gradingRevision` 由构建器计算。题面、选项、答案、解析变动须检查版本与旧成绩行为；不要手工钉旧 hash 或让未知版本旧记录冒充当前成绩。
- 保留 owner 隔离、异步身份 fence、提交与主动 Reveal 分离、刷新恢复、重置 / 重做和云端 DTO / 事件约定。禁止自动认领未知归属旧记录。
- 模考资格只由 `scripts/exam_modules.py` 从 `content/db/` 推出（真题来源的读写 Module 1 + 第二模块，固定两模块，不做自适应分流，不给换算分）；不要手工把 `examEligible` 改成 `true`，不把历史模考状态机或测试当作题库资格。题目缺失、退为草稿或来源消失时，重算会关闭已开放的模考（保留手工模块供修复）。保留来源未经认证、编辑校订等限定。
- 导入基线之后加入的内容由 `sat-release.json` 的四个发布开关控制：扩充题库（registry 中带 `releaseGate: expandedQuestionBank` 的材料）、固定两模块模考、AI 词汇解释与生词本、AI 讲解。默认全部关闭，缺少这个文件时也全部关闭。打开开关是内容所有者的决定：PR 不把开关改成 `true`，不删除 `releaseGate`；修改本文件或 README 也不等于所有者认可。`scripts/publish_drafts.py` 新发布的材料自动带 `releaseGate`。打开扩充题库会为题型页加入旧页键迁移，生产门禁按 `sat-practice-migrations.json` 的批准哈希核对，开启前需所有者另行审核迁移契约。
- 公开题库是去敏协作副本；私有生产 canonical 还有来源和审查证据。回流按 `question_id` 和字段级 diff 合并，不整包覆盖生产来源 / 证据，不用公开版本 hash 代替生产版本证明。

## 公开与生产边界

- 不加入凭证、认证配置、私有主机路径、真实联系方式、学生记录、原始 PDF、审查截图或历史 run / audit 档案。新增题目正文与必要图可以提交，但须清除无关水印和隐私信息，并保留内容来源限定。
- 不把另一项目的登录 / 云函数 / 部署工程整树搬入；不请求共享账号或生产密钥。本地预览、显式 mock 与生产认证分别处理，不增加登录绕过。
- 本任务分支通过 PR 交付，默认由另一位开发者评审。不要自行合并自己的 PR、强推 `main`、覆盖他人工作或自动部署生产。生产由维护者从固定提交另行核验发布。
- 新图片或媒体任务遵守操作者环境中适用的生成和验收规则；本仓不绑定某台机器的插件路径。不要未经核实为题库或素材添加开源许可。

## 验证与交付

```sh
python3 scripts/build-site.py
python3 scripts/build-site.py --preview-all
python3 scripts/build-demo.py
python3 scripts/check-repository-scope.py
python3 scripts/content_db.py check
python3 scripts/exam_modules.py --check
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m unittest discover -s skills/skill-sat-rw-classification/tests -p 'test_*.py'
```

交互变更还应在具备 Chrome 的环境运行下面两项，并补充实际操作观察：

```sh
node scripts/verify-sat-learning-browser.mjs --root build/site-preview --report build/reports/browser.json
node scripts/verify-sat-release-and-isolation.mjs --release-root build/site --preview-root build/site-preview
```

交付说明具体变化、验证结果和未验证范围。源码 / 语法检查不等于浏览器交互、教学审查、生产登录或跨设备 PASS。发现同步、评分或数据兼容问题时，在 PR 中明确写出影响，不静默清空记录。
