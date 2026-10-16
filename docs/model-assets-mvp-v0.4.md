# MVP v0.4 local model assets

Downloaded and stored under the `move-your-lips` project on the D: drive on 2026-10-16.

## Model A: CNVSRC2025 baseline

- Local copy: `models/cnvsrc2025/model_avg_cncvs_2_3_cnvsrc.pth`
- Copied byte-for-byte from `D:/Softwares/Programming Projects/_githubs/CNVSRC2025/VSR/pretrained_models/model_avg_cncvs_2_3_cnvsrc.pth`.
- SHA-256: `577CD9558EEA111683A406BC25D69C7161CDB79534C2273FC0D0F044C356231C`
- The original file remains in place.

## Model B: CMLR visual model and Chinese language model

- Upstream code and config: `third_party/Visual_Speech_Recognition_for_Multiple_Languages/`
- Upstream commit: `5e1405db0ae816509fb312f9a578724c2e0de0c7`
- Official config: `third_party/Visual_Speech_Recognition_for_Multiple_Languages/configs/CMLR_V_WER8.0.ini`
- Visual model: `third_party/Visual_Speech_Recognition_for_Multiple_Languages/benchmarks/CMLR/models/CMLR_V_WER8.0/model.pth`
- Visual model config: same folder, `model.json`
- Chinese LM: `third_party/Visual_Speech_Recognition_for_Multiple_Languages/benchmarks/CMLR/language_models/lm_zh/model.pth`
- Chinese LM config: same folder, `model.json`
- Official decoder settings: beam size 20, CTC weight 0.1, LM weight 0.3.

Retained source archives:

| Archive | Bytes | SHA-256 | Official file |
| --- | ---: | --- | --- |
| `models/cmlr/downloads/CMLR_V_WER8.0.zip` | 204050099 | `D4DD9974BE9F685E58E6A3649C350B15385F32D34050B3572E73726915CA9B7B` | [Google Drive](https://drive.google.com/file/d/1tN79KWj3Za8oS-7WWrcPMBCIx0RARfVD/view) |
| `models/cmlr/downloads/lm_zh.zip` | 195583605 | `7872B2EF0BD26CBF524579125793A5EDD9C2A9E4D2577B56B0242C0DB159BAF1` | [Google Drive](https://drive.google.com/file/d/1de4lqf_SkkjVn4t_NFs1dmRhAQpR1CfD/view) |

The upstream README describes the CMLR visual model as 8.0 CER on its benchmark; that is not a result for this project or its recordings. The upstream repository license restricts use to individual research/private study and comparative or benchmarking prototypes, separate from commercial product development. Preserve this limitation with any experiment report.

## Launching the CMLR test mode

From PowerShell, run:

```powershell
& 'D:\Softwares\Programming Projects\move-your-lips\scripts\run_lip_input_mvp.ps1' -Model cmlr
```

The default `-Model cnvsrc` path remains available. Both modes use the same camera capture and local CNVSRC2025 RetinaFace/FAN landmarks; CMLR then applies its own official 96×96 crop, 25 FPS sampling, grayscale normalization, and CMLR + `lm_zh` beam decoder. Each history record stores the model, checkpoint, config, language model, beam/CTC/LM weights, and crop path.

## Scope and status

Model files, official code/config, and a CMLR selection path are present. The model-loading self-test passed in `D:\cnvsrc-env` without opening the camera. No Python environment or dependencies were installed by this setup, and no real-video CMLR inference has been run yet. CMLR uses the upstream model architecture and preprocessing; the selection path is an experimental comparison path, not a replacement of the CNVSRC2025 default.
