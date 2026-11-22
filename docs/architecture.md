# 架构与兼容约束

本仓库包含完整公开题库与开发核心。Python 标准库负责静态构建，HTML 模板与原生 JavaScript 负责练习、复习、搜索和本地学习记录。完整生产账号系统、云函数、发布器和来源审查档案由维护者另行保管。

## 内容到页面

```text
sat-content-registry.json + content/canonical/ + migrations + 必要图
                              │
                scripts/build-site.py
                              │
     build_learning_experience.py + templates + learning_storage.js
                              │
       build/site/index.html、48 routes、manifest、静态资源
```

`registry` 的 `dataPath` 指向 37 份 set / collection canonical；11 个题型页从这些内容派生。构建按发布状态读取内容，按稳定 ID 合并跨材料题目，并按注册表中的优先级选择展示版本。题型路由不是新题源，生成 HTML 和 manifest 也不是编辑源。

`build-site.py` 在 `build/site/` 准备独立内容工作树，再调用构建器；不把生成物写回仓库输入。`build-demo.py` 在 `build/demo/` 组合 11 道原创 fixture 和独立 registry，构建 1 个 set、1 个 collection、11 个 category。两类输出分开，可以并存。

生成器按脚本位置读取模板和内置 SVG，按内容工作树读取 registry、canonical、迁移及图像。开发环境不依赖某台机器的绝对目录、账号配置或私有 skill。

## 代码修改位置

| 修改目标 | 编辑位置 |
| --- | --- |
| 内容装配、路由、公共 DTO、资源规则 | `skills/skill-sat-rw-classification/scripts/build_learning_experience.py` |
| 练习交互、提交 / Reveal、同步后的重绘 | `skills/skill-sat-rw-classification/assets/templates/html_template_v1.1.html` |
| 首页、搜索、复习入口与统计展示 | `skills/skill-sat-rw-classification/assets/templates/learning_index_v2.html` |
| 身份归属、存储与队列共享约定 | `skills/skill-sat-rw-classification/assets/templates/learning_storage.js` |
| 题文、答案、解析、分类 | `content/canonical/`，同时查看 [数据契约](data-contract.md) |
| 分类和分类报告辅助工具 | `skills/skill-sat-rw-classification/scripts/` 中对应分类脚本 |

分类工具输出需要人工 review；工具支持的 schema 不自动代表当前 canonical 或学生 DTO。不要为适配一次导入而扩大公开运行时允许的字段或 HTML 标签。

## 公开题库与生产包装

公开 canonical 保留教学内容与必要素材，移除了私有来源、审查与模型运行元数据。导出说明见 [content-export.json](content-export.json)；导入过程与后续内容修改规则见 [data-contract.md](data-contract.md)。

正式站额外提供登录保护、宿主身份和云同步。开发页面通过 `satCloudbaseSession`、`sat-cloudbase-session-ready` 及相应宿主接口接入这些能力；本地预览采用隔离记录。不得把可匿名预览的构建直接当作带认证的生产发布包。

新增云功能先约定 DTO、身份和错误处理，再以显式 mock 开发。修改前后分别测试匿名本地与带宿主身份场景；真实账号和跨设备验收仍需独立环境与授权。

## 必须保留的行为

1. `question_id` 是永久公共身份；`contentId` 与路由定位材料。路由 / `pageId`、题目顺序和迁移 aliases 都可能参与历史记录恢复，不能仅为美观改名。
2. `gradingRevision` 由构建器计算，涉及题目身份、题文、选项、答案、解析。修改这些字段会影响旧成绩解释，不能靠固定旧 hash 掩盖变化。
3. owner 隔离采用 `sat-owner:v1` 命名空间及身份 fence。未知归属的旧记录不自动认领；迟到请求不能落入新账号。
4. 选择和改选后，提交按屏幕对应的有效记录判分；用户主动 Reveal 才展开答案与解析。云端回包、多标签与刷新不能令按钮写入失效对象。
5. 重置与空白重做有各自语义，不能为修 UI 静默删除历史作答、收藏或笔记。
6. `examEligible` 只由 `scripts/exam_modules.py` 写入（见 [content-db.md](content-db.md)），提供的是读写固定两模块模考，结果只有原始正确率和错题题号；模板保留模考状态机不代表可以手工开启其他模考；Partial 云端编号等未决兼容问题不能靠猜测迁移。

## 验证分层

仓库检查覆盖构建、数据契约、公共标记与 JavaScript 语法等。浏览器开发回归使用本地页面和合成状态。两者均不证明题源身份、全部题目教学正确性、生产认证或真实跨设备同步。

交互改动需要实际操作覆盖选择 / 改选 / 提交、主动 Reveal、刷新、重置 / 重做、账号隔离和迟到同步。生产候选还须加入认证包装后重新验收，不能借用旧 run 的 PASS。
