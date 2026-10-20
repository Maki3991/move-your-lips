# 项目目录说明

桌面程序和网页 Demo 共用现有模型能力。按运行职责保留源码目录；依赖、个人数据和测试产物由 Git 忽略。

## 源码与文档

```text
move-your-lips/
├── web/                      网页源码和静态图标
├── demo_server/              上传、队列、结果交接与清理；将来部署到 VPS
├── laptop_worker/            笔记本模型 Worker；主动领取任务
├── scripts/                  桌面程序、训练、实验及启动脚本
│   ├── ui/                   桌面界面
│   └── diagnostics/          模型输入、人脸关键点诊断脚本
├── tests/
│   └── web_demo/             网页任务服务的自动化测试代码
├── docs/                     需求、设计、实验记录与运行说明
├── requirements-ui.txt       桌面界面依赖
├── requirements-web.txt      API 依赖范围
└── requirements-web.lock.txt 已验证的 API 依赖版本快照
```

以上文件需要提交 Git。`web/package-lock.json` 也应提交；`web/node_modules/` 和 `web/dist/` 是生成内容，不提交。

`.vscode/settings.json` 当前只有使用工作区相对路径的解释器配置，可以提交。个人绝对路径写入已忽略的 `local.settings.json`。

## 本地环境、运行数据与测试材料

| 目录或文件 | 内容 | Git |
| --- | --- | --- |
| `.venv/`、`.venv-web/` | 模型/桌面环境与轻量 API 环境；两套依赖分开安装 | 忽略 |
| `.web-demo/` | 网页任务密钥、SQLite、临时视频、Worker 工作目录与日志 | 忽略 |
| `outputs/` | 原桌面程序的识别素材、个人适配样本、训练结果及权重选择 | 忽略 |
| `test-videos/` | 本地测试视频素材 | 忽略 |
| `test-results/` | 测试与诊断报告、截图等输出 | 忽略 |
| `test-results/playwright/` | 浏览器自动化测试截图和日志 | 忽略 |
| `models/` | 本地下载的模型资产 | 忽略 |
| `third_party/` 下已配置的上游仓库 | 本地下载的 CNVSRC2025 和可选 CMLR 上游代码 | 忽略 |
| `.codex-temp/` | 开发辅助脚本与临时材料，不作为正式入口 | 忽略 |
| `.cache/`、`.pytest_cache/`、`.ruff_cache/`、`__pycache__/` | 安装、测试、检查工具和 Python 缓存 | 忽略 |
| `local.settings.json` | 本机 Python 和上游仓库路径 | 忽略 |

`tests/` 保存可以重复执行的测试代码；`test-results/` 保存执行产生的材料。测试结论、必要参数和证据位置写入 `docs/`，随代码提交。

`.web-demo/` 与 `outputs/` 分开：前者包含需自动清理的网页临时数据，后者可能包含需要长期保留的个人训练样本和模型。不要把它们当作同一种缓存统一删除。

## 本次目录调整（2026-10-20）

- 原 `output/playwright/` 已合并到 `test-results/playwright/`，后续浏览器截图沿用新位置。
- 原 `tools/diagnose_face_keypoints.py` 和 `tools/diagnose_model_input.py` 已移入 `scripts/diagnostics/`，脚本内容保持原样。
- 根目录不再保留空的 `output/` 和 `tools/`。
- `web/`、`demo_server/`、`laptop_worker/` 按运行和部署职责分别保留。
- `.gitignore` 按环境、模型资产、运行数据、生成内容和缓存归组，移除了重复及旧目录规则。

网页启动、资源限制和清理细节见 [网页 Demo 运行说明](web-demo.md)。
