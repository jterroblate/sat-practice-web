# 两人协作流程

`main` 是共同开发主线，功能和修复使用短期分支。双方用 Codex 或 Claude 时都遵守 [AGENTS.md](AGENTS.md)。目标、决定、兼容影响与验收结果写入 Issue / PR，聊天记忆不作为唯一交接资料。

## 首次准备

双方分别使用自己的 GitHub 账号和本地 clone：

```sh
git clone https://github.com/jterroblate/sat-practice-web.git
cd sat-practice-web
python3 scripts/build-site.py
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m unittest discover -s skills/skill-sat-rw-classification/tests -p 'test_*.py'
```

上面的 clone 地址适用于已获同仓 write / collaborator 权限的开发者。没有该权限时，先在 GitHub fork 到自己的账号，再 clone 自己的 fork；此时 `origin` 是自己的 fork，`upstream` 是共同原仓：

```sh
git clone https://github.com/YOUR_GITHUB_LOGIN/sat-practice-web.git
cd sat-practice-web
git remote add upstream https://github.com/jterroblate/sat-practice-web.git
git fetch upstream
git switch main
git merge --ff-only upstream/main
```

将 `YOUR_GITHUB_LOGIN` 替换为自己的用户名。在此基础上建任务分支，推送到 `origin`，向原仓 `main` 开 PR；后续更新主线也从 `upstream/main` 获取，而非仅拉取自己 fork 的旧 `origin/main`。如 GitHub 提示首次外部贡献的 Actions 需批准，由原仓所有者批准运行，并等待实际 `checks` 结果。

只有所有者具备 write 权限时，朋友 fork PR 可由所有者评审；朋友的普通外部评审不能代替受保护主线所需的有权限批准。双方对等相互评审需要先授予朋友 write / collaborator 权限。无需共享 GitHub 登录、Claude / Codex 登录或生产凭证。

## 开始一个任务

确认没有未保存工作，再从最新主线开分支。下面的 `pull` 用于同仓 clone；fork 先按上节从 `upstream/main` 更新，再建任务分支：

```sh
git status --short --branch
git switch main
git pull --ff-only
git switch -c feat/short-topic
```

修复使用 `fix/short-topic`，文档可用 `docs/short-topic`。分支名描述具体目标，例如 `fix/submission-feedback`。一个分支处理一组相关变化；提前在 Issue / PR 说明会改的共享模板、存储模块或题库文件，避免两人同时大改同一处。

已有修改时先提交到自己的任务分支，或明确保存后再切分支。不要用 `reset --hard` 清掉本机工作，不用 ZIP 或整目录复制互相覆盖。

## 实现、检查与提交

按 README 构建和运行检查。内容变更另按 [数据契约](docs/data-contract.md) 核对题目 ID、受影响字段、重复 occurrence 和评分版本。提交前查看完整 `git diff`，只暂存明确属于本任务的文件；不要提交生成的 `build/`、凭证或运行记录。

```sh
git diff --check
git status --short
git add path/to/changed-file
git diff --cached
git commit -m "fix: describe the observable change"
git push -u origin HEAD
```

最后的 `path/to/changed-file` 替换为本任务具体文件。无需将全库重新排版；避免无关格式变化掩盖内容修订。

## PR 与另一人评审

向 `main` 开 PR，说明问题、最终行为、验证、兼容和已知限制。评审重点包括内容依据、稳定 ID / 路由、旧记录、主动 Reveal、账号隔离、必要图及公开范围。

由另一位开发者 review / approve；作者修订后重新验证，必要时请对方重新评审。建议 squash merge，完成后删除任务分支。AI 可以辅助写代码和 review，但 PR 的最终责任与合并决定由开发者承担。

项目预期保护 `main`：至少 1 位其他开发者批准、`checks` 成功、讨论解决、分支更新、禁止 force push / 删除；新提交使旧批准失效。项目不另要求“批准者必须不同于最后一次推送者”，避免两人协作时评审者帮推修订后无法满足额外批准条件；仍须由 PR 作者之外的一位有权限开发者批准。具体强制状态以 GitHub 的 Branches / Rules 设置为准，本文不能代替远端规则。不要通过临时关闭保护绕过正常协作。

需要合并主线更新时，先保存本分支工作，再执行 `git fetch origin`、`git merge origin/main`；逐项解决冲突并重跑受影响检查。不要对他人共用分支进行未经约定的 rebase / force push。

## 取回朋友的改动

流程为：朋友推分支 → PR → 另一人评审 → 合并 `main` → 所有者在本机 clone 拉取。

```sh
git switch main
git pull --ff-only
git rev-parse HEAD
```

`push` 把提交送到 GitHub，`pull` 把朋友已经合并的提交取回本地。遇到不能 fast-forward 时先检查分叉原因，保留两边工作后合并，不强制覆盖。

GitHub 保存共同代码和公开内容，clone 是本地工作副本，学生站点由另一次发布更新。合入 `main` 不代表已经上线；固定提交如何回流见 [生产交接](docs/production-handoff.md)。

## 给 Claude 的首次任务提示

```text
这是 SAT Practice Web 的共同开发仓库。
请先阅读 AGENTS.md、README.md、CONTRIBUTING.md、docs/architecture.md 和 docs/data-contract.md。
检查当前分支和未提交修改；保留已有工作，从最新 main 建独立任务分支。
本任务目标是：[具体行为、涉及页面与验收标准]。
可以使用仓库完整公开题库和必要题图；学习状态只使用合成记录。
先简述拟改文件，然后实现，执行 README 检查，并说明浏览器实测范围。
保留 question_id、路由、owner 隔离、评分版本、提交与主动 Reveal 的边界。
交付 diff、验证结果、兼容影响和 PR 描述；请不要自动合并或部署生产。
```

后续任务给出 Issue / PR、目标和验收条件即可；有评分、迁移或云协议变更时，先补充兼容方案。
