# Move Your Lips

打开应用，对着摄像头无声地说话，程序会截取嘴部画面，交给 [CNVSRC2025](https://github.com/liu12366262626/CNVSRC2025) 识别，再把文字复制到剪贴板、存入识别记录。你也可以录制 24 句规定句子，训练一个适合自己口型的候选模型。

这是研究版。测试中常有整句话认错的情况，个人训练后也未必变准。我们还没有在其他人的电脑上验证安装流程。仓库不包含模型权重。

## 安装与启动

安装前准备 Windows、摄像头、Git、Git LFS 和 Python 3.10。你还需要按 [CNVSRC2025 VSR 的说明](https://github.com/liu12366262626/CNVSRC2025/blob/main/VSR/README.md) 安装 PyTorch、`fairseq`、人脸检测和关键点模块。本项目的 `requirements-ui.txt` 负责界面等额外依赖；上游依赖需要你手动配置。

在项目根目录打开 PowerShell：

```powershell

New-Item -ItemType Directory -Force .\third_party | Out-Null

git clone --recurse-submodules https://github.com/liu12366262626/CNVSRC2025.git .\third_party\CNVSRC2025

py -3.10 -m venv .venv

```

进入 `third_party/CNVSRC2025/VSR/tools/face_detection` 执行 `git lfs pull`，按上游说明安装 Python 依赖。然后从上游 README 的 **Model zoo** 下载 `model_avg_cncvs_2_3_cnvsrc.pth`，放进 `third_party/CNVSRC2025/VSR/pretrained_models/`。回到项目根目录，安装界面依赖并启动：

```powershell

.\.venv\Scripts\python.exe -m pip install -r requirements-ui.txt

.\scripts\run_lip_input_mvp.ps1

```

启动脚本会查找你下载的权重，不会替你下载。第一次启动使用 CNVSRC 原模型。如果你在个人适配页面选用了个人模型，程序会把选择记在 `outputs/mvp/active-model.json`，下次启动继续加载它。你可以在页面中切回原模型，也可以通过 `-Checkpoint <权重路径>` 指定这一次启动使用的权重。

如果 Python 环境或上游代码不在上述目录，用启动参数 `-Python`、`-VsrRoot` 指定路径，或在项目根目录创建 `local.settings.json`：

```json

{"python":"D:\\path\\to\\python.exe","vsrRoot":"D:\\path\\to\\CNVSRC2025\\VSR"}

```

程序把录制和训练结果放在 `outputs/mvp/`。Git 会忽略 `.venv/`、`third_party/`、`outputs/` 和 `local.settings.json`。如需把 pip 缓存也放在项目里，安装前运行 `$env:PIP_CACHE_DIR = "$PWD\.cache\pip"`。Python 启动器和系统临时文件的位置取决于你的 Windows 配置。

## 普通识别

等底部的模型加载提示消失后，按一次 右 Ctrl 开始录制。面对摄像头无声地说话，再按一次右 Ctrl 结束。录制到约 20 秒时，程序会自行结束。程序随后识别嘴型，把结果复制到剪贴板，并放进 Home 的「识别记录」。

每次识别的嘴部画面会保存为 MP4，放在 `outputs/mvp/recordings/`，方便回放或重新转写，不会保存完整人脸视频或音频。

| 操作              | 作用                   |
| --------------- | -------------------- |
| 右 Ctrl          | 开始或结束当前录制            |
| Esc             | 取消正在录制、识别或重新转写的这一次操作 |
| Ctrl+O          | 打开本地嘴部视频文件夹          |
| Ctrl+R          | 手动刷新识别记录             |
| Ctrl++ / Ctrl+- | 放大或缩小主窗口             |
| Ctrl+0          | 恢复主窗口默认大小            |

只进入个人适配页面时，右 Ctrl 仍用于普通识别。你点击「开始录制」后，它才改为采集规定句；离开该页面，右 Ctrl 又用于普通识别。
 
## 个人适配

1. 点击侧边栏麦克风图标。模型就绪后，点「开始录制」，照着页面上的句子读。
2. 程序存好一句后会显示下一句。刚才没读好，就点「重录上一句」。离开页面或关闭程序后，进度仍在本机；回来点「继续采集」。
3. 录完第 24 句，程序会直接开始训练。其中 18 句用于调整模型，另外 6 句留给训练前后比较。训练失败时，素材仍在，你可以点「重试训练」。
4. 训练结束，页面会列出两个模型在 6 句留出句上的字符错误率（CER），并加载本轮个人模型。如果个人模型的 CER 更高，请点「原模型」切回。模型切换完成后立即生效，程序也会记住你的选择；重启后不用再录 24 句。
5. 如果想重做，点「重新训练」。程序会先请你确认，再清空本轮的 24 句样本和个人模型，从第 1 句开始。普通识别历史会保留。

程序把样本、报告、权重和训练日志放在 `outputs/mvp/personal_adaptation/`。你训练出的模型只用到了你自己的这轮样本。个人适配页面目前只支持 CNVSRC 模型。

## 怎么看识别效果

CER 是字符错误率。

计算时，先从原句和识别结果中去掉标点、空格，再数一数需要插入、删除或替换多少个字，才能把结果改成原句。用这个数量除以原句字数，就得到 CER。数值越低，文字越接近原句；模型多输出很多字时，CER 也可能超过 100%。从 50% 升到 58%，表示错误率增加了 8 个百分点。看测试结果时，也请留意有几句完全正确、有几句能看懂，以及采集失败了几次。

我们用一位使用者的录制做了两组对照。其他人可能得到不同结果。

| 对照                           |        原模型 CER |       个人模型 CER | 整句完全正确   | 如何理解             |
| ---------------------------- | -------------: | -------------: | -------- | ---------------- |
| 24 句中的 6 句留出句，两模型识别同一批无损嘴部帧  |  46.55%（27/58） |  41.38%（24/58） | 两者均 0/6  | 页面训练报告显示的结果      |
| 另外录的 10 句新句子，两模型识别同一批嘴部 MP 4 | 55.30%（73/132） | 45.45%（60/132） | 两者均 0/10 | 认对的字多了，仍没有一句完全正确 |

我们还让同一人用原模型读了 10 句、每句 3 次：CER 为 60.45%，30 次中有 1 次整句正确。训练后，他在另一天重读这些句子，CER 为 51.13%，整句正确 3/30。两天的机位和光线没有固定，所以这组现场数据不能单独证明训练带来了多少改善。每句原文、输出和计算口径见[基线与复测](docs/archived-experiments/v0.3-baseline-test.md)、[个人适配实验](docs/archived-experiments/personal-adaptation-v0.1.md)、[新句子对照](docs/archived-experiments/unseen-sentence-check-v0.1.md)。

### 测自己的模型

训练页面会显示 6 句留出句的 CER。如果你还想知道模型遇到新句子时怎么样，先写下至少 10 句未用于训练的句子，每句录一次。认错了也留下原结果，别挑最好的一次。然后让原模型和个人模型读取**同一份**嘴部视频，并保持预处理、解码参数一致。分别统计 CER、整句正确数和失败次数。这样，两模型不会因为你读了两遍、换了光线而拿到不同输入。

你可以参考我们的 [10 句对照记录](docs/archived-experiments/unseen-sentence-check-v0.1.md) 和 `scripts/compare_unseen_models.py`。这份脚本只处理该记录的格式，没有通用测评界面。保存的 MP 4 经过压缩，离线结果也不能直接代替实时识别结果。

6 句留出句一共只有 58 个字。多错几个字，就会让 CER 变动好几个百分点；如果留出 CER 变差，建议再看新句子的对照。要知道其他用户会得到什么结果，还需要请不同的人各自录制、测试，并记下录制条件。

### 识别为什么还不准

我们曾把一条录制的实时输入和无损嘴部帧分别送进模型，得到相同结果；抽查画面和代码时，也没看到嘴部裁剪、尺寸或帧率有明显错误。你可以在[输入诊断](docs/archived-experiments/v0.4-input-diagnostic.md)里看到那次的画面和输出。但我们只细查了这一条，不能据此认定整条输入流程都没有问题。

上游模型用自己的数据训练、评测；普通电脑摄像头拍下的口型可能与训练画面不同。我们只用 18 句调整模型参数，6 句检查结果，样本也少。程序的采集、预处理和解码方式同样可能影响识别。现有测试还无法判断各环节分别造成了多少错误。[上游 README 的指标](https://github.com/liu12366262626/CNVSRC2025/blob/main/VSR/README.md)来自它的评测集；你可以用自己新录的句子判断是否保留个人模型。

## 文档与许可

- [文档目录](docs/README.md)：按用途查找需求、实验和发布记录。
- [Evidence](docs/evidence.md)：项目决策、测试结果与未验证项。
- [v0.4 研究记录](docs/archived-experiments/research-mvp-v0.4.md)：模型选择与输入诊断。

我们自写的代码和文档采用 [MIT 许可证](LICENSE)。默认使用的 [CNVSRC2025 VSR](https://github.com/liu12366262626/CNVSRC2025/blob/main/VSR/README.md) 限定代码用于非商业比较或基准测试，使用前还请阅读它的 [LICENSE](https://github.com/liu12366262626/CNVSRC2025/blob/main/VSR/LICENSE)。可选的 [CMLR 对比模型](https://github.com/mpc001/Visual_Speech_Recognition_for_Multiple_Languages#license) 也有自己的使用条件。本仓库不包含这些项目的代码或权重；如果要获取、使用或再分发，请先查看各自的条款。组件清单见 [THIRD_PARTY.md](THIRD_PARTY.md)。
