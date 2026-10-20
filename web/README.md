# Move Your Lips Web

手机单列、电脑双列，共用同一套 Figma 组件风格。采用 Vite + TypeScript + 原生浏览器 MediaRecorder，不引入第二套 UI 框架。

从项目根目录运行 `scripts/run_web_demo.ps1`，默认打开 `http://127.0.0.1:8000`。首次安装运行 `scripts/setup_web_demo.ps1`。

完整流程、参数、数据清理、API/Worker 分工与验证边界见 [运行说明](../docs/web-demo.md)。

`public/assets` 中的唇形标识、摄像头和复制图标来自当前 Figma 导出；运行时不依赖临时 Figma URL。页面真实摄像头画面取代设计稿中的示意头像。
