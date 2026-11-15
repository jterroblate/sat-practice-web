@AGENTS.md

# Claude 开发入口

`AGENTS.md` 是双方共同约定。开始任务时还需阅读 `README.md`、`CONTRIBUTING.md`、`docs/architecture.md` 和 `docs/data-contract.md`；涉及本地回流或发布时阅读 `docs/production-handoff.md`。

可直接使用本仓完整公开题库和必要图开发，在任务分支提交变更。需要小规模 fixture 时构建 `build/demo/`，完整站点构建到 `build/site/`。学习记录使用合成状态，不访问真实学生账号。

交付应包含修改文件、可观察行为、测试结果、兼容影响和 PR 描述。不要根据聊天里的旧信息推断已上线；以实际 commit、PR 和本轮发布凭据为准。生产发布由维护者另行执行。
