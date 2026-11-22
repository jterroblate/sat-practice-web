# 题库数据与修改约定

本仓库中的 canonical 是共同开发的公开内容入口，生成页面只能从它和 registry 构建。生产内容另外保留私有来源与审查证据；公开副本不会替代这些证据，内容改善需要受控回流。

## 导入基线

| 项目 | 数量 |
| --- | --- |
| set canonical / 套题路由 | 34 |
| collection canonical / 专项路由 | 3 |
| 派生 category / 题型路由 | 11 |
| 唯一公共 `question_id` | 1,752 |
| 37 份 canonical 内的 occurrence | 1,765 |
| 可进行正式模考的注册项 | 0 |

这些数字描述导入基线。新增、隔离或撤回内容时，PR 须解释预期规模变化，同时更新对应契约与测试；不能只修改期望数字让失败消失。

## 当前规模

| 项目 | 数量 |
| --- | --- |
| set canonical / 套题路由 | 70 |
| collection canonical / 专项路由 | 37 |
| 派生 category / 题型路由 | 11 |
| 唯一公共 `question_id` | 4,054 |
| 107 份 canonical 内的 occurrence | 4,067 |
| 可进行正式模考的注册项 | 0 |

基线之外的 36 个套题、34 个专项和 2,302 道题由 `scripts/publish_drafts.py` 从 `content/db/` 的草稿生成，没有改动任何基线文件，也没有共用基线题号。它们是导入的回忆版真题与练习题：答案和文字未经逐题核对，1,397 道没有解析，`sourceFamily` 为 `question-bank-import-unverified` 或 `SAT机考题库/skill-difficulty`。发布条件、近似重复的处理和题型 / 难度的依据见 [content-db.md](content-db.md)。题型页因新增题目而变长，原有题型页的本地记录由 `baselineQuestionIds` 迁移。

[content-export.json](content-export.json) 记录导入的文件摘要、ID / 答案保留校验、必要素材和去敏改动。公开副本保留完整题文、A–D 选项、答案、学生可见解析、分类、模块 / 表格及可见图；移除私有来源、审查、模型运行元数据，以及无关的来源邮箱尾注。原始 PDF、审查截图和历史档案未导入。

该报告是首次导入 provenance，不是以后每次提交的当前文件锁。正常修题无需重导出，也不要修改报告里的历史源 SHA、首次答案保留事实来让测试通过。未来 PR 按当前 canonical 与生成结果验证自一致，另行记录修订依据；首次报告不能为后续内容背书。`scripts/export-current-content.py` 仅用于首次导入，已有导入内容时拒绝覆盖；日常开发只构建和修订当前仓库，不重跑导出。

首次导入的去敏造成 **241 个唯一题的评分 hash 与生产含证据 canonical 不同**，不表示 241 题改了答案：导入没有改答案；227 题涉及解析内部元数据移除，14 题涉及来源尾注移除，另有与前者重叠的必要资源引用调整。因此，公开构建自行计算的 `gradingRevision` 只证明本公开副本一致，不能用于直接证明生产版本一致。

## Registry 和材料身份

`sat-content-registry.json` 使用 `entries` 登记内容。主要字段如下：

| 字段 | 约定 |
| --- | --- |
| `contentId` | 材料稳定身份，不能重复 |
| `kind` | `set` / `collection` / `category` |
| `section` | 当前公开题库为 `reading_writing` |
| `dataPath` | set / collection 的仓库内相对 canonical 路径 |
| `route` | 已有 `sets/`、`collections/`、`categories/` 稳定 HTML 路由 |
| `title` | 学生界面的公开材料名称 |
| `questionIds` | 材料编排使用的题目 ID；保持顺序与数据一致 |
| `status` | 控制是否纳入公开构建 |
| `examEligible` / `examVariants` | 当前全部关闭正式模考 |
| `sourceFamily` 等来源限定 | 描述已知来源状态，不等于官方认证 |

类别页从已登记内容聚合，不应另造一份题库。对于重复 `question_id`，先找出它在所有材料里的 occurrence，核对展示优先级和上下文差异；不要只改一份再假定全站一致。同一 ID 在本公开副本中的评分字段须一致，测试会比较 `grading_revision()`；确有不同题面或解析语义时先评审身份 / 迁移方案，不能让共享记录同时绑定相互矛盾的版本。

## 题目字段

题目以 `question_id` 标识。题文使用 `passage`、`prompt`，选项是按顺序排列的 `choices`（`label` 为 A、B、C、D，`text` 为正文），答案为对应 `correct_answer`。学生解析字段为 `short`、`key_evidence`、`why_correct`、`detailed`、`choice_analysis`；保持答案与 A–D 分析一致。

`classification` 描述 domain / skill / subtype；分类配置有 11 个 skill。模块、表格和可见素材字段用于展示，不能因本轮只修改文字就整段删掉。允许的正文内联标记与公共 DTO 以生成器为准，不加入脚本、事件处理器或任意外链执行内容。

必要图片保留仓库内引用，构建时验证路径与资源存在。当前可维护题图清单为 `assets/content-resources.json`，使用 `resources` 数组；每项含仓库相对 `publicPath`、`sha256` 和 `sizeBytes`。范围检查要求题图登记且实际 SHA / 大小匹配，未登记图片不会自动放行。该清单用于源码维护，不需要发布到学生页面。

新增或替换必要题图时，在同一 PR 更新图片、内容引用与当前清单，由另一人核对用途和摘要；采用内容寻址新文件并补充可理解的替代文字。保留 `docs/content-export.json` 的首次导入证据，不改其历史摘要来登记新图。不要改成依赖来源网站的临时 URL；路径变化后检查所有引用和构建结果。

## 规范化表

`content/db/` 以第三范式保存题库：每个 `question_id` 一行，题型、来源和套题编排各有自己的表。已发布的题与 canonical 是同一份内容，不改变本文其余约定；表里另有从外部题库导入的草稿题，它们不属于导入基线的 1,752 题，不进入构建，也不出现在 registry 中。`scripts/content_db.py` 负责两种形式互相生成，测试要求从表导出的结果与 `content/canonical/` 逐字节相同，因此 `gradingRevision` 不受影响。

修改任一边后运行对应命令并一起提交，详见 [content-db.md](content-db.md)。构建器目前仍只读取 canonical。

## 一次内容 PR 应交代什么

- 列出受影响 `question_id`、材料 / 路由和具体字段，说明修订理由及可公开依据。
- 区分答案改正、题面校订、解析改善和分类调整；来源不确定时保留限定，不虚构认证、审核票据或多 reviewer 共识。
- 检查同 ID 的其他 occurrence、选项标签和顺序、图表、解析及评分版本。不要顺带批量重新格式化全库。
- 构建全库并执行仓库测试；针对变化验证学生页面。答案和评分语义变化还须在生产候选复核旧记录行为。
- 如需删除、隔离、换 ID / 路由或迁移，单独说明旧记录保留及恢复方案。不要直接清除学生状态。

## 与生产内容合并

维护者从固定 Git commit 生成字段级变更计划，按 `question_id`、具体 occurrence 和预期旧值合并。私有 `source` / 审查证据保留原位，新增修订附新的依据；不要整份复制公开 canonical 覆盖生产文件，也不要反向覆盖仓库里已经评审的内容。

`gradingRevision`、迁移和来源限定必须在生产候选重新审查。实际回流与上线步骤见 [production-handoff.md](production-handoff.md)。
