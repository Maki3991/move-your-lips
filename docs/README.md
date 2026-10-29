# 文档目录

第一次使用应用，请看项目根目录的 [README](../README.md)。想核对项目做到哪一步，先看 [Evidence](evidence.md)；需要逐句识别结果，再进入 `archived-experiments/`。旧需求中的“当前”指写作当时，不代表程序现在的状态。

## 项目记录

| 文件 | 用途 |
| --- | --- |
| [evidence.md](evidence.md) | 按日期保存项目决策、实验结论、证据位置和贡献边界。结果摘要在这里，逐条数据在实验文件里。 |
| [project-structure.md](project-structure.md) | 项目目录职责、运行数据和测试产物的位置，以及 Git 提交边界。 |
| [vps-deployment.md](../archive/hackathon-web-demo/docs/vps-deployment.md) | 已归档：历史 VPS 配置、部署记录及验收边界；服务已停止并删除。 |
| [personal-adaptation-phrases-v0.1.json](personal-adaptation-phrases-v0.1.json) | 应用和训练脚本读取的 24 句原文及训练／留出划分。程序依赖此路径，暂留在 `docs/` 根目录。 |

## `product/`：问题与版本需求

| 文件 | 用途 |
| --- | --- |
| [design-thinking.md](product/design-thinking.md) | 从个人使用场景到 Prototype、MVP 的问题定义和选择记录；停留在 2026-10-12 的阶段。 |
| [mvp-prd.md](product/mvp-prd.md) | v0.2 和早期 v0.3 的需求草案；用来查当时的设想，不作为当前功能说明。 |
| [prd-mvp-v0.3.md](product/prd-mvp-v0.3.md) | v0.3 的 Home、HUD、历史记录、快捷键和素材管理需求。 |
| [prd-mvp-v0.4.md](product/prd-mvp-v0.4.md) | v0.4 纯视觉模型对比的范围、测试方法和验收规则。 |
| [prd-web-demo.md](../archive/hackathon-web-demo/docs/product/prd-web-demo.md) | 已归档：黑客松手机网页 Demo、VPS 与笔记本 Worker 的用户流程、容量、数据生命周期和待验证项。 |
| [web-demo-figma.md](../archive/hackathon-web-demo/docs/product/web-demo-figma.md) | 已归档：手机网页 Figma 设计、可点击原型入口、页面状态、组件与实现注意。 |
| [网页 Demo 运行说明](../archive/hackathon-web-demo/docs/web-demo.md) | 已归档：实现目标、电脑本地启动、API/Worker 分工、资源参数、清理规则和已验证边界。 |

## `archived-experiments/`：已完成的模型与测试记录

| 文件 | 用途 |
| --- | --- |
| [research-mvp-v0.4.md](archived-experiments/research-mvp-v0.4.md) | CNVSRC、CMLR 和个人适配的研究计划、模型选择、诊断与结论；前半保留实验前模板。 |
| [v0.3-baseline-test.md](archived-experiments/v0.3-baseline-test.md) | 10 句各读 3 次的原模型基线、CMLR 复测和个人模型复测，含逐次输出与 CER。 |
| [v0.4-input-diagnostic.md](archived-experiments/v0.4-input-diagnostic.md) | 同一条录制的实时输入、无损嘴部帧和 MP4 回放对照。 |
| [personal-adaptation-v0.1.md](archived-experiments/personal-adaptation-v0.1.md) | 首次 24 句个人适配实验的录制、训练和 6 句留出结果。 |
| [unseen-sentence-check-v0.1.md](archived-experiments/unseen-sentence-check-v0.1.md) | 另外 10 句新句子的原文、记录 ID 和两模型同素材对照。比较脚本读取此文件。 |
| [model-assets-mvp-v0.4.md](archived-experiments/model-assets-mvp-v0.4.md) | 两个模型的来源、路径、校验值和下载当天的状态；末尾状态不是最终测试结论。 |
| [news-anchor-five-sentences.md](archived-experiments/news-anchor-five-sentences.md) | 同一位新闻播音员的 5 段视频原句，以及这次实验为何影响项目方向。逐段结果另见 `test-results/cnvsrc2025/news-reference/`。 |

## 旧笔记

| 文件 | 用途 |
| --- | --- |
| [open.md](archive/open.md) | 原作者的本机启动命令备忘，含个人电脑路径；不作为安装教程。若以后不需要保留这份笔记，可以从仓库移除。 |

`evidence.md` 保留结果摘要，`archived-experiments/` 保留原句、输出和参数。两者有少量重复，方便先看结论再查原始记录；目前不合并测试表，也不删历史需求。
