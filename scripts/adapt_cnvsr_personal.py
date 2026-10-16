"""Run one bounded CNVSRC personal-adaptation experiment.

The 18 train samples update only the visual frontend and first Transformer
encoder block. The six held-out samples are decoded before and after training
and are never used for gradient updates. A candidate checkpoint is written
only when held-out CER improves over the unchanged base model.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np
import torch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VSR_ROOT = PROJECT_ROOT / "third_party" / "CNVSRC2025" / "VSR"


def edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


def normalize(text: str) -> str:
    return re.sub(r"[^\w]", "", text, flags=re.UNICODE)


def load_samples(session_dir: Path, phrase_set_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    phrase_data = json.loads(phrase_set_path.read_text(encoding="utf-8"))
    phrase_by_id = {int(item["id"]): item for item in phrase_data["phrases"]}
    manifest_path = session_dir / "manifest.jsonl"
    rows = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    by_id: dict[int, dict[str, Any]] = {}
    for row in rows:
        phrase_id = int(row["phrase_id"])
        if phrase_id in by_id:
            raise ValueError(f"phrase {phrase_id} has duplicate recordings; start a clean collection session")
        expected = phrase_by_id.get(phrase_id)
        if expected is None or row["text"] != expected["text"] or row["split"] != expected["split"]:
            raise ValueError(f"manifest label mismatch for phrase {phrase_id}")
        sample_path = (session_dir / "samples" / row["file"]).resolve()
        if session_dir.resolve() not in sample_path.parents or not sample_path.is_file():
            raise FileNotFoundError(f"missing or unsafe sample path for phrase {phrase_id}")
        with np.load(sample_path, allow_pickle=False) as data:
            lips = np.asarray(data["lips"])
            text = str(data["text"].item())
            split = str(data["split"].item())
            stored_id = int(data["phrase_id"].item())
        if lips.ndim != 4 or lips.shape[1:] != (96, 96, 3) or len(lips) < 12:
            raise ValueError(f"invalid mouth frames for phrase {phrase_id}: {lips.shape}")
        if (text, split, stored_id) != (expected["text"], expected["split"], phrase_id):
            raise ValueError(f"sample metadata mismatch for phrase {phrase_id}")
        row["sample_path"] = sample_path
        row["lips"] = lips
        by_id[phrase_id] = row
    if set(by_id) != set(range(1, 25)):
        missing = sorted(set(range(1, 25)) - set(by_id))
        extra = sorted(set(by_id) - set(range(1, 25)))
        raise ValueError(f"expected exactly 24 unique samples; missing={missing}, extra={extra}")
    train = [by_id[i] for i in range(1, 25) if by_id[i]["split"] == "train"]
    heldout = [by_id[i] for i in range(1, 25) if by_id[i]["split"] == "heldout"]
    if len(train) != 18 or len(heldout) != 6:
        raise ValueError(f"expected 18 train + 6 heldout; got {len(train)} + {len(heldout)}")
    return train, heldout


def evaluate(recognizer: Any, samples: list[dict[str, Any]]) -> dict[str, Any]:
    results = []
    total_distance = 0
    total_chars = 0
    exact = 0
    for row in samples:
        with torch.inference_mode():
            predicted, _latency, frame_count = recognizer.predict_prepared(row["lips"], greedy=False)
        reference = normalize(row["text"])
        output = normalize(predicted)
        distance = edit_distance(reference, output)
        total_distance += distance
        total_chars += len(reference)
        exact += int(reference == output)
        results.append(
            {
                "phrase_id": int(row["phrase_id"]),
                "reference": row["text"],
                "prediction": predicted,
                "edit_distance": distance,
                "reference_chars": len(reference),
                "model_frames": frame_count,
                "exact": reference == output,
            }
        )
    return {
        "cer": total_distance / max(1, total_chars),
        "exact_sentences": exact,
        "sentence_count": len(samples),
        "edit_distance": total_distance,
        "reference_chars": total_chars,
        "predictions": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-dir", required=True, type=Path)
    parser.add_argument("--phrase-set", type=Path, default=PROJECT_ROOT / "docs" / "personal-adaptation-phrases-v0.1.json")
    parser.add_argument("--vsr-root", type=Path, default=DEFAULT_VSR_ROOT)
    parser.add_argument("--checkpoint", type=Path, default=None)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--learning-rate", type=float, default=1e-5)
    parser.add_argument("--threads", type=int, default=max(1, min(4, os.cpu_count() or 1)))
    parser.add_argument("--seed", type=int, default=20261016)
    args = parser.parse_args()

    session_dir = args.session_dir.resolve()
    if not session_dir.is_dir():
        raise FileNotFoundError(f"session directory not found: {session_dir}")
    phrase_set = args.phrase_set.resolve()
    train_samples, heldout_samples = load_samples(session_dir, phrase_set)
    vsr_root = args.vsr_root.resolve()
    checkpoint = args.checkpoint or (vsr_root / "pretrained_models" / "model_avg_cncvs_2_3_cnvsrc.pth")
    checkpoint = checkpoint.resolve()
    if not checkpoint.is_file():
        raise FileNotFoundError(f"base checkpoint not found: {checkpoint}")
    if args.epochs < 1 or args.epochs > 5:
        raise ValueError("epochs must be between 1 and 5 for this bounded experiment")

    sys.path.insert(0, str(vsr_root))
    from datamodule.transforms import TextTransform, VideoTransform
    from lip_input_mvp import CNVSRCRecognizer

    torch.set_num_threads(max(1, args.threads))
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    random.seed(args.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    recognizer = CNVSRCRecognizer(vsr_root, checkpoint, device=device, beam_size=10)
    text_transform = TextTransform()
    train_transform = VideoTransform("train")

    for row in train_samples + heldout_samples:
        unknown_chars = [char for char in row["text"] if char not in text_transform.hashmap]
        if unknown_chars:
            raise ValueError(f"CNVSRC dictionary cannot represent phrase {row['phrase_id']}: {unknown_chars}")

    before = evaluate(recognizer, heldout_samples)
    before_path = session_dir / "heldout-before.json"
    before_path.write_text(json.dumps(before, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    model = recognizer.model
    model.requires_grad_(False)
    for module in (model.encoder.frontend, model.encoder.encoders[0]):
        module.requires_grad_(True)
    trainable = [parameter for parameter in model.parameters() if parameter.requires_grad]
    if not trainable:
        raise RuntimeError("no trainable parameters found in selected visual modules")
    optimizer = torch.optim.AdamW(trainable, lr=args.learning_rate, weight_decay=1e-4)

    started = time.time()
    epoch_losses: list[float] = []
    for epoch in range(args.epochs):
        model.eval()
        model.encoder.frontend.train()
        model.encoder.encoders[0].train()
        order = list(train_samples)
        random.shuffle(order)
        losses = []
        for index, row in enumerate(order, 1):
            video = torch.from_numpy(row["lips"]).permute(0, 3, 1, 2).contiguous()
            video = train_transform(video).unsqueeze(0).to(device)
            labels = text_transform.tokenize(row["text"]).view(1, -1, 1).to(device)
            lengths = torch.tensor([video.size(1)], dtype=torch.long, device=device)
            optimizer.zero_grad(set_to_none=True)
            loss, *_ = model(video, lengths, labels)
            if not torch.isfinite(loss):
                raise RuntimeError(f"non-finite training loss at epoch {epoch + 1}, sample {index}")
            loss.backward()
            torch.nn.utils.clip_grad_norm_(trainable, max_norm=5.0)
            optimizer.step()
            losses.append(float(loss.detach().cpu()))
            print(f"epoch {epoch + 1}/{args.epochs} sample {index}/{len(order)} loss={losses[-1]:.4f}", flush=True)
        epoch_losses.append(sum(losses) / max(1, len(losses)))

    model.eval()
    after = evaluate(recognizer, heldout_samples)
    improved = after["cer"] < before["cer"]
    candidate_path = session_dir / "cnvsrc-personal-candidate.pth"
    if improved:
        with tempfile.NamedTemporaryFile(dir=session_dir, suffix=".tmp", delete=False) as tmp:
            temporary_path = Path(tmp.name)
        try:
            torch.save(model.state_dict(), temporary_path)
            os.replace(temporary_path, candidate_path)
        finally:
            temporary_path.unlink(missing_ok=True)

    report = {
        "experiment": "cnvsrc-personal-adaptation-v0.1",
        "base_checkpoint": str(checkpoint),
        "candidate_checkpoint": str(candidate_path) if improved else None,
        "device": device,
        "epochs": args.epochs,
        "learning_rate": args.learning_rate,
        "train_samples": [int(row["phrase_id"]) for row in train_samples],
        "heldout_samples": [int(row["phrase_id"]) for row in heldout_samples],
        "trainable_scope": ["encoder.frontend", "encoder.encoders[0]"],
        "epoch_mean_losses": epoch_losses,
        "heldout_before": before,
        "heldout_after": after,
        "heldout_cer_improved": improved,
        "elapsed_seconds": round(time.time() - started, 1),
    }
    report_path = session_dir / "adaptation-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "device": device,
        "before_cer": before["cer"],
        "after_cer": after["cer"],
        "before_exact": before["exact_sentences"],
        "after_exact": after["exact_sentences"],
        "candidate_saved": improved,
        "report": str(report_path),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
