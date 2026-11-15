# 项目开发约定

适用于 Codex、Claude 和人工开发。先读 `README.md`、`CONTRIBUTING.md`、`docs/architecture.md`、`docs/data-contract.md`、`docs/production-handoff.md`。

## 修改入口

- 本仓库管理共同开发主线。任务从最新 `main` 建 `feat/*` 或 `fix/*`；开始前检查当前分支与 `git status`，保留已有未提交修改。
- 真实结构化题库与必要题图已获仓库所有者授权纳入本仓，可进行有依据的内容修订。源码在生成器、模板和运行时修改，题目在 `content/canonical/` 修改；不从生成 HTML 反向提取内容，不只手改生成页。
- `build/site/` 是全库产物，`build/demo/` 是原创 fixture 产物，均不提交。测试账号、状态和学习记录使用合成数据；本仓公开题目不等于可以读取真实学生数据。
- 题型工具是开发工具，不是自动确认答案、题源身份或模考资格的审核者。内容更改在 PR 中列明 `question_id`、字段、理由与可公开依据；不编造来源或共识记录。

## 兼容要求

- 保留永久 `question_id`、`contentId`、公开路由 / `pageId`、题目顺序与既有迁移。身份调整、删除或评分变化先写兼容方案，再实现并测试。
- `gradingRevision` 由构建器计算。题面、选项、答案、解析变动须检查版本与旧成绩行为；不要手工钉旧 hash 或让未知版本旧记录冒充当前成绩。
- 保留 owner 隔离、异步身份 fence、提交与主动 Reveal 分离、刷新恢复、重置 / 重做和云端 DTO / 事件约定。禁止自动认领未知归属旧记录。
- 所有当前内容 `examEligible=false`。不要开启正式模考，不把历史模考状态机或测试当作当前题库资格。保留来源未经认证、编辑校订等限定。
- 公开题库是去敏协作副本；私有生产 canonical 还有来源和审查证据。回流按 `question_id` 和字段级 diff 合并，不整包覆盖生产来源 / 证据，不用公开版本 hash 代替生产版本证明。

## 公开与生产边界

- 不加入凭证、认证配置、私有主机路径、真实联系方式、学生记录、原始 PDF、审查截图或历史 run / audit 档案。新增题目正文与必要图可以提交，但须清除无关水印和隐私信息，并保留内容来源限定。
- 不把另一项目的登录 / 云函数 / 部署工程整树搬入；不请求共享账号或生产密钥。本地预览、显式 mock 与生产认证分别处理，不增加登录绕过。
- 本任务分支通过 PR 交付，默认由另一位开发者评审。不要自行合并自己的 PR、强推 `main`、覆盖他人工作或自动部署生产。生产由维护者从固定提交另行核验发布。
- 新图片或媒体任务遵守操作者环境中适用的生成和验收规则；本仓不绑定某台机器的插件路径。不要未经核实为题库或素材添加开源许可。

## 验证与交付

```sh
python3 scripts/build-site.py
python3 scripts/build-demo.py
python3 scripts/check-repository-scope.py
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m unittest discover -s skills/skill-sat-rw-classification/tests -p 'test_*.py'
```

交互变更还应在具备 Chrome 的环境运行 `node scripts/verify-sat-learning-browser.mjs --report build/reports/browser.json`，并补充实际操作观察。

交付说明具体变化、验证结果和未验证范围。源码 / 语法检查不等于浏览器交互、教学审查、生产登录或跨设备 PASS。发现同步、评分或数据兼容问题时，在 PR 中明确写出影响，不静默清空记录。
