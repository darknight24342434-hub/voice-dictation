from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

from inject import TextInjector
from polish import Polisher, rule_polish
from recorder import remove_temp_audio, write_silence_wav
from stt import LocalWhisperTranscriber, configure_local_cache, load_config, plan_device


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config(PROJECT_ROOT / "app" / "config.json")
    configure_local_cache(config)
    started = time.perf_counter()
    summary: dict[str, object] = {
        "ok": False,
        "imports": "ok",
        "model_load": "not-run",
        "silence_flow": "not-run",
        "inject_empty": "not-run",
        "errors": [],
    }
    try:
        plan = plan_device(config)
        summary["device_plan"] = {
            "device": plan.device,
            "compute_type": plan.compute_type,
            "model": plan.model_name,
            "reason": plan.reason,
        }
        if plan.gpu_info is not None:
            summary["gpu"] = {
                "name": plan.gpu_info.name,
                "total_mb": plan.gpu_info.total_mb,
                "free_mb": plan.gpu_info.free_mb,
                "driver_version": plan.gpu_info.driver_version,
                "cuda_device_count": plan.gpu_info.cuda_device_count,
                "supported_compute_types": list(plan.gpu_info.supported_compute_types),
            }
        else:
            summary["gpu"] = None

        polisher = Polisher(config)
        summary["polish"] = {
            "engine": polisher.status.engine,
            "model": polisher.status.model,
            "detail": polisher.status.detail,
            "rules_example": rule_polish("嗯 今天下午三點 不對 是四點 開會"),
        }

        transcriber = LocalWhisperTranscriber(config)
        transcriber.load()
        summary["model_load"] = "ok"

        silence_path = PROJECT_ROOT / "_temp" / "smoke_silence.wav"
        write_silence_wav(silence_path, duration_seconds=1.0)
        try:
            raw_text = transcriber.transcribe(silence_path)
            final_text = polisher.polish(raw_text)
            summary["silence_flow"] = {
                "raw_text": raw_text,
                "final_text": final_text,
                "final_length": len(final_text),
            }
        finally:
            remove_temp_audio(silence_path)

        injector = TextInjector(config)
        inject_result = injector.inject("")
        summary["inject_empty"] = {
            "method": inject_result.method,
            "restored_clipboard": inject_result.restored_clipboard,
        }
        summary["ok"] = True
    except Exception as exc:
        summary["errors"].append(f"{type(exc).__name__}: {exc}")
    summary["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
