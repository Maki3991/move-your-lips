# MVP v0.4 模型素材记录

这份文件记录 2026 年 10 月 16 日取得模型时的来源、版本、存放位置和校验值，用于复查当时测试的是哪一份模型。它不是当前安装教程，也不代表最终识别效果。后续实测和比较见 `docs/archived-experiments/research-mvp-v0.4.md`、`docs/archived-experiments/v0.3-baseline-test.md`。

## 模型 A：CNVSRC2025 原模型

- 当时的本地副本：`models/cnvsrc2025/model_avg_cncvs_2_3_cnvsrc.pth`。
- 来源：本机另一个 CNVSRC2025 仓库的 `VSR/pretrained_models/model_avg_cncvs_2_3_cnvsrc.pth`；当时逐字节复制，原文件仍留在原处。
- SHA-256：`577CD9558EEA111683A406BC25D69C7161CDB79534C2273FC0D0F044C356231C`。

## 模型 B：CMLR 视觉模型与中文语言模型

- 上游代码和配置：`third_party/Visual_Speech_Recognition_for_Multiple_Languages/`。
- 当时使用的上游提交：`5e1405db0ae816509fb312f9a578724c2e0de0c7`。
- 模型配置：`third_party/Visual_Speech_Recognition_for_Multiple_Languages/configs/CMLR_V_WER8.0.ini`。
- 视觉模型：`third_party/Visual_Speech_Recognition_for_Multiple_Languages/benchmarks/CMLR/models/CMLR_V_WER8.0/model.pth`；同目录的 `model.json` 是对应配置。
- 中文语言模型：`third_party/Visual_Speech_Recognition_for_Multiple_Languages/benchmarks/CMLR/language_models/lm_zh/model.pth`；同目录的 `model.json` 是对应配置。
- 当时采用的解码参数：beam size 20、CTC weight 0.1、LM weight 0.3。

当时保留的下载压缩包：

| 文件 | 字节数 | SHA-256 | 下载来源 |
| --- | ---: | --- | --- |
| `models/cmlr/downloads/CMLR_V_WER8.0.zip` | 204050099 | `D4DD9974BE9F685E58E6A3649C350B15385F32D34050B3572E73726915CA9B7B` | [Google Drive](https://drive.google.com/file/d/1tN79KWj3Za8oS-7WWrcPMBCIx0RARfVD/view) |
| `models/cmlr/downloads/lm_zh.zip` | 195583605 | `7872B2EF0BD26CBF524579125793A5EDD9C2A9E4D2577B56B0242C0DB159BAF1` | [Google Drive](https://drive.google.com/file/d/1de4lqf_SkkjVn4t_NFs1dmRhAQpR1CfD/view) |

上游 README 报告的 CMLR 指标为 8.0 CER，来自其测试集，不能当作本项目录制素材的识别结果。模型和上游代码的使用条件也不同于本项目自有代码的 MIT 许可；具体限制见上游许可及根目录 `THIRD_PARTY.md`。

## 当时的 CMLR 测试入口

在项目根目录打开 PowerShell，运行：

```powershell
.\scripts\run_lip_input_mvp.ps1 -Model cmlr
```

默认的 `-Model cnvsrc` 路径仍可使用。两种模式共用摄像头采集及本地 CNVSRC2025 的 RetinaFace/FAN 人脸与关键点检测。CMLR 随后使用对应的 96×96 嘴部画面处理、25 FPS 采样、灰度归一化，以及 CMLR 视觉模型、`lm_zh` 中文语言模型和 beam 解码器。识别历史会记录所用模型、权重、配置、语言模型、解码参数和裁剪路径。

## 2026 年 10 月 16 日的阶段状态

当日已取得模型文件、上游代码和配置，并接通 CMLR 测试入口；在当时的 Python 环境中，仅完成了不打开摄像头的模型加载自检。**这只是模型准备当天的记录**：之后已经进行真人录制与模型比较，结果请看本文件开头列出的实验记录。CMLR 是对比实验方案，CNVSRC2025 仍是默认模型。
