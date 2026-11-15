# SAT Practice Web

SAT Reading and Writing 学习站的协作开发仓库，包含当前结构化题库、必要题图、静态生成器、练习与复习界面、本地学习记录逻辑及开发测试。Python 构建页面，原生 JavaScript 负责交互；不需要安装前端框架。

导入基线的维护代码对应 2026-11-05 已发布的提交反馈修复。导入后的开发准备修订另见 [readiness-fixes.md](docs/readiness-fixes.md)，其中公共副本的内容对齐不代表已修改生产题库。题库包含 **34 个套题、3 个专项、11 个题型、48 个练习路由、1,752 个唯一公共 question_id**；37 份 canonical JSON 是 set / collection 的内容输入，题型页由它们派生。同一道题可能出现在多份材料中，记录数不能当作唯一题数。

题库是供协作维护的去敏副本，包含经过编辑校订、出处尚未认证的材料，不能称为官方认证真题。当前全部 `examEligible=false`，仅提供普通练习；历史路由中的 `fixed-hard` 不代表具备正式模考资格。原始 PDF、审查截图、历史运行档案、学生记录、生产登录服务与凭证不在本仓库。

## 本地启动

使用 Python 3.11+、Node.js 22。构建只使用 Python 标准库；Node 用于 JavaScript 检查，不需要 `npm install`。

```sh
git clone https://github.com/jterroblate/sat-practice-web.git
cd sat-practice-web
python3 scripts/build-site.py
python3 -m http.server 8000 --bind 127.0.0.1 --directory build/site
```

打开 `http://127.0.0.1:8000/`。本地预览不连接正式账号，不提供真实跨设备同步；练习数据使用隔离的本机记录。请使用 HTTP 服务，不要双击生成的 HTML。

需要小规模调试时，可构建 11 道原创 fixture，覆盖 11 类题型：

```sh
python3 scripts/build-demo.py
python3 -m http.server 8001 --bind 127.0.0.1 --directory build/demo
```

全库与演示分别写入 `build/site/`、`build/demo/`，可以并存。`build/` 是可重建产物，不提交；内容修改应写回 canonical，界面修改应写回模板或运行时。`scripts/export-current-content.py` 是维护者首次导入工具，不是日常构建或更新命令；已导入仓库通过 PR 修改，禁止重新导出覆盖当前协作内容。

## 检查

```sh
python3 scripts/check-repository-scope.py
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m unittest discover -s skills/skill-sat-rw-classification/tests -p 'test_*.py'
```

GitHub Actions `checks` 会执行公开范围检查、全库与演示构建、Python 测试和真实 Chrome 浏览器回归，浏览器中的学习记录与同步响应使用合成夹具。它不配置生产自动部署，也不能代替逐题教学审核或真实账号跨设备验收。

安装 Chrome 后还可运行本地浏览器开发回归；自动发现失败时设置 `CHROME_PATH` 为 Chrome 可执行文件：

```sh
node scripts/verify-sat-learning-browser.mjs --report build/reports/browser.json
```

该驱动默认检查 `build/site/`，报告仅属于本地开发回归，不是生产发布证明。

## 主要文件

| 路径 | 用途 |
| --- | --- |
| `content/canonical/` | 34 个 set 与 3 个 collection 的公开结构化内容 |
| `sat-content-registry.json` | 内容身份、发布状态与稳定路由 |
| `sat-practice-migrations.json` | 历史记录迁移协议，不是学生记录 |
| `assets/`、`categories/chart_images/` | 构建需要的题图与页面资源 |
| `assets/content-resources.json` | 当前必要题图的相对路径、SHA-256 与大小，新增 / 替换图时同步评审 |
| `skills/skill-sat-rw-classification/scripts/` | 站点生成器及题型分类工具 |
| `skills/skill-sat-rw-classification/assets/templates/` | 练习页、首页与共享存储运行时 |
| `scripts/build-site.py`、`scripts/build-demo.py` | 全库与小规模演示构建入口 |
| `fixtures/original-demo.json` | 与真实题库分开的原创测试内容 |
| `tests/` | 干净环境构建、数据与仓库边界检查 |

## 两人协作

共同源码和内容修改通过 `main`、`feat/*` / `fix/*` 分支与 PR 管理。双方使用 Codex 或 Claude 都读同一份 [AGENTS.md](AGENTS.md)；Claude Code 的入口见 [CLAUDE.md](CLAUDE.md)。另一位开发者评审后再合并，所有者在本地拉取固定提交、核验生产候选，最后另行发布。推送 GitHub 或合并 PR 不会直接更新学生站点。

- [贡献和完整 Git 操作流程](CONTRIBUTING.md)
- [架构与兼容边界](docs/architecture.md)
- [题库数据与修改约定](docs/data-contract.md)
- [本地回流、生产发布与回滚](docs/production-handoff.md)

当前尚未选择统一开源许可证。请勿自行添加许可声明，或把仓库公开视为所有题目、图像与第三方素材均已获得任意用途授权。
