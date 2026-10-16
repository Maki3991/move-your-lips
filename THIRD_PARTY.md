# 第三方代码和模型

本仓库的 [MIT 许可证](LICENSE) 仅覆盖本项目自有的源码和文档。它不会改变下列上游项目的许可，也不授予模型权重或数据集的再分发权。

| 组件 | 用途 | 上游许可与说明 |
| --- | --- | --- |
| [CNVSRC2025 VSR](https://github.com/liu12366262626/CNVSRC2025/tree/main/VSR) | 默认视觉语音模型、人脸与嘴部预处理、个人适配所依赖的模型架构 | [README](https://github.com/liu12366262626/CNVSRC2025/blob/main/VSR/README.md) 写明非商业比较或基准测试用途；见其 [LICENSE](https://github.com/liu12366262626/CNVSRC2025/blob/main/VSR/LICENSE)。源码、子模块、检测权重和识别权重均需使用者自行获取。 |
| [Visual Speech Recognition for Multiple Languages](https://github.com/mpc001/Visual_Speech_Recognition_for_Multiple_Languages) | 可选的 CMLR 对比模型 | [README 的许可段落](https://github.com/mpc001/Visual_Speech_Recognition_for_Multiple_Languages#license) 限定非商业比较或基准测试用途；见其 [LICENSE](https://github.com/mpc001/Visual_Speech_Recognition_for_Multiple_Languages/blob/master/LICENSE)。本仓库不包含其源码或权重。 |

因此，公开本项目的 MIT 源码不等于一个可以任意商用或直接再分发全部模型资源的应用。准备其他用途时，请先核实每项上游资源的条款并取得所需授权。
