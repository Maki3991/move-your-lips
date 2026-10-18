"""Compare base and personal CNVSRC checkpoints on the same ten saved clips."""

from __future__ import annotations

import argparse
import gc
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import torch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from compare_lossless_input import read_mp4  # noqa: E402
from lip_input_mvp import CNVSRCRecognizer, DEFAULT_VSR_ROOT  # noqa: E402


def parse_phrases(path: Path) -> list[dict[str, str | int]]:
    phrases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\| (\d+) \| ([^|]+) \| ([^|]+) \| ([^|]+) \| ([^|]+) \|", line)
        if not match:
            continue
        number, reference, record_id, status, live_output = match.groups()
        phrases.append(
            {
                "number": int(number),
                "reference": reference.strip(),
                "record_id": record_id.strip(),
                "status": status.strip(),
                "live_candidate_output": live_output.strip(),
            }
        )
    if len(phrases) != 10 or [item["number"] for item in phrases] != list(range(1, 11)):
        raise ValueError("expected the ordered 10-sentence table")
    return phrases


def normalize(text: str) -> str:
    return re.sub(r"[，。！？、；：,.!?;:\s]", "", text)


def edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for index, char in enumerate(left, 1):
        current = [index]
        for column, other in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[column] + 1, previous[column - 1] + (char != other)))
        previous = current
    return previous[-1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phrases", type=Path, default=PROJECT_ROOT / "docs" / "archived-experiments" / "unseen-sentence-check-v0.1.md")
    parser.add_argument("--history", type=Path, default=PROJECT_ROOT / "outputs" / "mvp" / "recognitions.jsonl")
    parser.add_argument("--recordings", type=Path, default=PROJECT_ROOT / "outputs" / "mvp" / "recordings")
    parser.add_argument("--vsr-root", type=Path, default=DEFAULT_VSR_ROOT)
    parser.add_argument("--base-checkpoint", type=Path, default=None)
    parser.add_argument("--candidate-checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "outputs" / "mvp" / "offline_diagnostic" / "unseen-sentence-check-v0.1.json")
    args = parser.parse_args()

    phrases = parse_phrases(args.phrases)
    history = [json.loads(line) for line in args.history.read_text(encoding="utf-8").splitlines() if line.strip()]
    first_id = phrases[0]["record_id"]
    first_index = next((i for i, item in enumerate(history) if item.get("id") == first_id), None)
    if first_index is None:
        raise ValueError(f"first record not found: {first_id}")
    matching = [
        item for item in history[first_index:]
        if item.get("status") == "success"
        and Path(str(item.get("checkpoint") or "")).name == args.candidate_checkpoint.name
    ]
    if len(matching) < 10:
        raise ValueError(f"expected at least 10 candidate recordings after {first_id}; found {len(matching)}")
    matching = matching[:10]

    records = []
    for phrase, history_item in zip(phrases, matching, strict=True):
        record_id = history_item["id"]
        if phrase["record_id"] not in ("待填", record_id):
            raise ValueError(f"record ID mismatch for sentence {phrase['number']}")
        if phrase["live_candidate_output"] not in ("待填", history_item["text"]):
            raise ValueError(f"live output mismatch for sentence {phrase['number']}")
        if history_item.get("media_status") != "saved":
            raise ValueError(f"media not saved for {record_id}")
        media_path = (args.recordings / str(history_item["media_file"])).resolve()
        if media_path.parent != args.recordings.resolve() or not media_path.is_file():
            raise FileNotFoundError(f"missing or unsafe media path: {media_path}")
        records.append(
            {
                "number": phrase["number"],
                "reference": phrase["reference"],
                "record_id": record_id,
                "recorded_at": history_item["at"],
                "media_file": str(media_path),
                "live_candidate_output": history_item["text"],
                "live_model_frames": history_item.get("model_frames"),
            }
        )

    base_checkpoint = (args.base_checkpoint or args.vsr_root / "pretrained_models" / "model_avg_cncvs_2_3_cnvsrc.pth").resolve()
    candidate_checkpoint = args.candidate_checkpoint.resolve()
    for checkpoint in (base_checkpoint, candidate_checkpoint):
        if not checkpoint.is_file():
            raise FileNotFoundError(checkpoint)
    torch.set_num_threads(min(4, max(1, torch.get_num_threads())))

    for model_name, checkpoint in (("base", base_checkpoint), ("candidate", candidate_checkpoint)):
        print(f"loading {model_name}: {checkpoint.name}", flush=True)
        recognizer = CNVSRCRecognizer(args.vsr_root, checkpoint, device="cpu", beam_size=10)
        recognizer.warmup()
        for record in records:
            video = read_mp4(Path(record["media_file"]))
            output, latency, frame_count = recognizer.predict_prepared(video)
            reference = normalize(str(record["reference"]))
            normalized_output = normalize(output)
            record[model_name] = {
                "text": output,
                "edit_distance": edit_distance(reference, normalized_output),
                "reference_chars": len(reference),
                "exact": reference == normalized_output,
                "video_frames": frame_count,
                "latency_seconds": round(latency, 3),
            }
            print(f"{model_name} {record['number']}/10: {output}", flush=True)
        del recognizer
        gc.collect()

    summary = {}
    for model_name in ("base", "candidate"):
        distance = sum(int(record[model_name]["edit_distance"]) for record in records)
        chars = sum(int(record[model_name]["reference_chars"]) for record in records)
        summary[model_name] = {
            "exact": sum(bool(record[model_name]["exact"]) for record in records),
            "edit_distance": distance,
            "reference_chars": chars,
            "cer": distance / chars,
        }
    report = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "input_type": "saved_mouth_mp4",
        "device": "cpu",
        "beam_size": 10,
        "base_checkpoint": str(base_checkpoint),
        "candidate_checkpoint": str(candidate_checkpoint),
        "records": records,
        "summary": summary,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"report: {args.output}", flush=True)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
