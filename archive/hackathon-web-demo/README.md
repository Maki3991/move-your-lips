# 黑客松网页演示归档

这版为黑客松现场展示制作，已于 2026-10-29 归档，不是桌面程序的默认入口。原 VPS 上的服务、部署目录、站点配置和专用证书已删除；原域名不再提供该服务。目录中的部署脚本和文档保留作历史参考，重新部署需要重新评估和验证。

观众用手机网页录制视频并提交，VPS 负责接收视频、排队和中转，笔记本 Worker 主动领取任务，调用本机模型识别，再经 VPS 将结果返回提交视频的浏览器。模型不在 VPS 上运行。

| 内容 | 作用 |
| --- | --- |
| `web/` | 浏览器录制、提交和本机结果记录 |
| `demo_server/` | FastAPI、任务队列、视频中转及清理 |
| `laptop_worker/` | 笔记本任务领取与模型识别 |
| `scripts/` | 本地启动和历史 VPS 部署脚本 |
| `tests/web_demo/` | 网页服务自动化测试 |
| `docs/` | 需求、设计、运行方式和历史部署记录 |

Worker 复用仓库根目录 `scripts/lip_input_mvp.py`、模型环境和模型选择，不是自包含模型。`local.settings.json`、`.venv-web/`、`.web-demo/`、`outputs/` 和 `third_party/` 仍位于仓库根目录；其中私有配置、运行数据和模型资产不随源码归档，也不提交 Git。旧 `.web-demo/vps-worker.json` 属于已删除的部署，不能直接用于恢复公网服务。

在 Windows 项目根目录，可通过以下命令重建前端依赖并启动本地离线界面。安装依赖需要联网；启动完整 Worker 还需要已配置的桌面模型环境和权重。

```powershell
.\archive\hackathon-web-demo\scripts\setup_web_demo.ps1
.\archive\hackathon-web-demo\scripts\run_web_demo.ps1 -NoWorker
```

完整运行及设计细节见 [网页演示说明](docs/web-demo.md)，历史部署和验收范围见 [VPS 部署记录](docs/vps-deployment.md)。此前公网技术闭环曾用电脑浏览器虚拟摄像头验证；这不代表真实手机、多设备压力或识别准确率已验收。
