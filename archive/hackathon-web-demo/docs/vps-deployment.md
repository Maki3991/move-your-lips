# VPS 网页与视频中转部署

> **已归档（2026-10-29）**：黑客松演示已结束，原 VPS 服务与部署已删除。以下保留历史设计和验证记录；涉及公网运行状态、服务器路径及部署操作均为当时情况，不代表当前服务可用。归档入口见 [README](../README.md)。

演示地址：**https://lips.maki3991.xyz**。部署日期：2026-10-20。

## 分工和启动

VPS 常驻运行网页、FastAPI、SQLite 和临时视频中转，不加载识别模型。笔记本 Worker 主动通过 HTTPS 拉取任务、下载视频并回传文字；不需要笔记本公网 IP 或入站端口。

在笔记本的项目根目录运行：

```powershell
.\archive\hackathon-web-demo\scripts\run_vps_worker.ps1
```

终端显示 `Model: online` 后，观众即可在网址中录制和提交。保持终端打开、电脑联网且不休眠。按 Ctrl+C 停止 Worker，VPS 网站继续运行，最后一次心跳约 20 秒后显示 Offline 并拒绝新任务。正在处理的任务不要中途关机；异常中断后会在处理超时后标记失败，不自动重跑模型。

本地测试仍使用 `scripts/run_web_demo.ps1`。两个入口共用同一个模型和临时目录，**不要同时运行**；先在原终端 Ctrl+C，再启动另一个入口。浏览器在 localhost 和正式域名下的记录相互独立。

私有连接文件为 `.web-demo/vps-worker.json`，包含 `url`、`workerSecret`。它已经被 Git 忽略；不要上传 GitHub、发给观众或写进网页代码。模型路径继续读取 `local.settings.json`。新电脑还需要原模型环境、代码与权重，只有这个 JSON 无法运行识别。

## 历史服务器配置（已删除）

| 项目 | 值 |
| --- | --- |
| 阿里云 ECS | `i-2zef1pphzxbat7xkfbxt`，`cn-beijing` |
| 公网 IP | `39.105.94.46` |
| 域名 | `lips.maki3991.xyz`，A 记录直连 VPS |
| API 监听 | `127.0.0.1:8010`，单进程，不直接暴露公网 |
| 服务 | `move-your-lips.service`，开机启动、异常退出自动恢复 |
| 服务用户 | `moveyourlips`，无交互 shell |
| 程序入口 | `/opt/move-your-lips/current`，指向 `releases/` 中的版本 |
| Python 环境 | `/opt/move-your-lips/venv`，Python 3.12 |
| 私有配置 | `/etc/move-your-lips/server.env`、`worker.json`，root 仅可读写 |
| 数据 | `/var/lib/move-your-lips/server`，SQLite 与临时视频 |
| 应用日志 | `/var/log/move-your-lips/server.log`，2 MB × 3 份 |
| Nginx 配置 | `/etc/nginx/sites-available/lips.maki3991.xyz` |
| TLS 证书 | Let's Encrypt，首次到期日 2027-01-18，Certbot 定时续期 |

systemd 限制服务最多使用 512 MB 内存、约一个 CPU 核心；部署时空闲常驻内存约 40 MB。其他网站沿用原配置，Nginx 使用 graceful reload 加载本站配置。

安装依赖锁定在 `requirements-server.lock.txt`，仅含服务运行依赖；完整本地测试环境另见 `requirements-web.lock.txt`。服务器实际安装快照为 `/etc/move-your-lips/installed-requirements.txt`。

## 名额、清理与隐私

- 同时上传 **2** 条；上传预留与排队合计最多 **10** 条；同时识别 **1** 条。
- 更新版新增每浏览器 **6 次/分钟**、同 IP **60 次/分钟**；同任务的认证恢复不重复扣次数。预计排队超过 **180 秒**时拒绝新任务，估算方法和无样本初值见 [web-demo.md](web-demo.md)。部署状态见文末更新记录。
- 单视频最多 **10 秒 / 10,000,000 字节**。临时视频预算 **200 MB**，磁盘余量不足时拒绝新任务。
- VPS 收到结果并持久化后删除视频；笔记本收到结果回传确认后删除本地副本。
- 网页把文字保存到 localStorage 后向 VPS 确认；确认后 VPS 清除文字与查询凭证。未确认结果最多保留一小时。
- 上传预留三分钟、排队三十分钟、处理五分钟超时；每十秒清理，启动时也清理异常遗留。
- 日常无需手动删除视频。勿把数据目录加入备份、公开静态目录或同步 Git。
- Nginx 关闭本站访问日志，上传流式转交 API，不额外缓冲保存完整视频。错误日志及应用日志不记录正文、认证头或识别文字。
- Nginx 覆盖来源 IP 头，API 只信任本机代理；手机与 Worker 都通过 HTTPS 访问。

完整数据生命周期和错误说明见 [web-demo.md](web-demo.md)。

## 历史维护与部署方式（未在归档后重新验证）

使用本机 `alibabacloud-workbench-cli` 技能，常用只读检查：

```powershell
workbench exec --instance-id i-2zef1pphzxbat7xkfbxt --command 'systemctl status move-your-lips --no-pager'
workbench exec --instance-id i-2zef1pphzxbat7xkfbxt --command 'curl -fsS http://127.0.0.1:8010/api/status'
workbench exec --instance-id i-2zef1pphzxbat7xkfbxt --command 'tail -50 /var/log/move-your-lips/server.log'
```

首次部署模板在 `scripts/deploy/`：

1. 在电脑构建 `web/`，将 `demo_server/`、`web/dist/`、`requirements-server.lock.txt`、`scripts/deploy/` 打成归档上传。
2. 解压到 `/opt/move-your-lips/releases/<版本>/`，运行该版本的 `scripts/deploy/provision.sh`。脚本只用于**首次安装**，遇到已有服务/配置会拒绝覆盖。
3. A 记录生效后运行同目录的 `enable-https.sh`。它保留本站原 HTTP 配置，签发证书、检查配置，再平滑加载 HTTPS。
4. 通过 Workbench 下载 `/etc/move-your-lips/worker.json` 至本机 `.web-demo/vps-worker.json`，启动笔记本入口。

后续更新应上传新的 release，在无处理中任务时切换 `current` 并重启本站服务；不要直接重新运行首次安装脚本，也不要修改其他网站配置。服务重启会结束当时的上传中/处理中任务，排队任务保留到过期。删除数据、停服或影响其他项目的操作应先确认范围。

## 验收边界

2026-10-20 已完成：

- 域名 DNS、可信 HTTPS、HTTP 跳转、网页/API 公网访问；未连接 Worker 时确实显示 Offline，模型准备好后显示 Online。
- 笔记本主动连接公网 API，用既有人脸视频作为 Chrome 虚拟摄像头输入，经 MediaRecorder 录制约 4 秒、真实上传与模型识别；任务 `8af4d493` 返回结果。没有用固定文字替代推理。
- 处理时刷新恢复、本机结果保存、复制、390px 布局、清空记录后刷新均通过；浏览器控制台没有错误。
- 服务端核对该任务为 `acked`，文字和查询凭证长度都为 0，中转视频目录为空；笔记本临时 `task-*` 目录也为空。
- 前端生产构建通过，后端 9 项自动化测试通过，VPS Python 依赖检查通过；服务没有异常重启。既有 `study.maki3991.xyz` 仍返回 HTTP 200。

截图保存于 Git 忽略的 `test-results/playwright/vps-desktop-result.png` 和 `vps-mobile-result.png`；应用事件日志在 `.web-demo/logs/worker.events.log`。浏览器测试产生的记录已从测试浏览器清空。

电脑浏览器通过 HTTPS 的验证不替代 iPhone Safari、Android 真机授权和移动网络测试；尚未完成 10 条连续任务、多设备容量与长时间稳定性验收。模型能完成识别也不代表输出准确率合格。

## 2026-10-20 限流与估时更新

新增浏览器限流、宽松 IP 总量限制、实测耗时统计、3 分钟预计排队准入，以及页面预计开始识别的提示。无需更换笔记本 Worker 或模型。`processing_samples` 表在服务启动时自动创建，保留最多 20 个纯数值耗时；首次上线从 90 秒/条的初始估算开始。

本地实现与验证完成后，更新包可解压至独立 release；切换 `current` 并重启服务后才在线上生效。发布前检查无上传中或处理中任务，保留旧 release 以便回滚。重启会短暂中断本站 API，并重置内存心跳和限流窗口；Worker 后续心跳会恢复在线状态。不得并行用新 `Store` 打开生产数据库进行验收，否则它会把旧进程的活动任务判定为重启遗留。

本次已在用户确认后完成上线：

- `current` 已从 `20261020-8845d90` 原子切换至 `20261020-e41a990`（代码提交 `e41a990`），并重启 `move-your-lips.service`；旧 release 保留用于回滚。切换前无上传、排队或处理中的任务。
- 公网 HTTPS 首页、`/api/status` 均返回 200；页面 JS 为 `index-BQYEFVul.js`，引用的静态资源与新 release 一致。接口返回预计排队上限 180 秒、初始处理估算 90 秒。
- 浏览器 Cookie 已核对 `Secure`、`HttpOnly`、`SameSite=Lax`、`Path=/api`；携带 Cookie 再次请求会保留同一浏览器身份。每浏览器 6 次/分钟、每 IP 60 次/分钟规则随新版生效，未对生产环境进行限流压测。
- SQLite 已创建 `processing_samples`，当前无耗时样本、活动任务或中转视频。服务为 `active/running`，无异常自动重启；`study.maki3991.xyz` 仍返回 200。
- 本次验收时笔记本 Worker 未连接，网站正确返回 Offline；在笔记本运行 `scripts/run_vps_worker.ps1` 后恢复模型服务。本次未进行多人真实模型压力测试。
