from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class GpuInfo:
    name: str
    total_mb: int
    free_mb: int
    driver_version: str
    cuda_device_count: int
    supported_compute_types: tuple[str, ...]


@dataclass(frozen=True)
class ModelSelection:
    model_name: str
    reason: str
    gpu_info: GpuInfo


@dataclass(frozen=True)
class DevicePlan:
    device: str          # "cuda" 或 "cpu"
    compute_type: str    # "int8_float16"（GPU）或 "int8"（CPU）
    model_name: str
    reason: str
    gpu_info: "GpuInfo | None" = None


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(config_path) if config_path else PROJECT_ROOT / "app" / "config.json"
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _add_cuda_dll_dirs() -> None:
    """pip 安裝的 nvidia cuBLAS/cuDNN DLL 不在系統 PATH，須手動掛給 ctranslate2。"""
    import site

    candidates = list(site.getsitepackages())
    candidates.append(str(Path(__file__).resolve().parent / ".venv" / "Lib" / "site-packages"))
    for sp in candidates:
        nvidia_root = Path(sp) / "nvidia"
        if not nvidia_root.exists():
            continue
        for bin_dir in nvidia_root.glob("*/bin"):
            try:
                os.add_dll_directory(str(bin_dir))
            except OSError:
                continue
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")


def configure_local_cache(config: dict[str, Any]) -> None:
    _add_cuda_dll_dirs()
    cache_root = PROJECT_ROOT / "app" / ".cache"
    temp_root = PROJECT_ROOT / "_temp"
    hf_root = cache_root / "huggingface"
    os.environ["TEMP"] = str(temp_root)
    os.environ["TMP"] = str(temp_root)
    os.environ["PIP_CACHE_DIR"] = str(cache_root / "pip")
    os.environ["HF_HOME"] = str(hf_root)
    os.environ["TRANSFORMERS_CACHE"] = str(hf_root / "transformers")
    os.environ["XDG_CACHE_HOME"] = str(cache_root)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    for key in ("TEMP", "TMP", "PIP_CACHE_DIR", "HF_HOME", "TRANSFORMERS_CACHE", "XDG_CACHE_HOME"):
        Path(os.environ[key]).mkdir(parents=True, exist_ok=True)
    download_root = config.get("stt", {}).get("download_root")
    if download_root:
        (PROJECT_ROOT / download_root).mkdir(parents=True, exist_ok=True)


def resolve_model_reference(model_ref: str | Path) -> str:
    """Resolve shipped relative model folders against the install/project root."""
    raw = str(model_ref)
    expanded = Path(os.path.expandvars(os.path.expanduser(raw)))
    candidates = [expanded] if expanded.is_absolute() else [PROJECT_ROOT / expanded]
    for candidate in candidates:
        if candidate.is_dir():
            return str(candidate.resolve())
    return raw


def probe_gpu() -> GpuInfo | None:
    """回傳 GPU 資訊；沒有可用 CUDA（無卡／驅動缺／ctranslate2 無 cuda）時回 None，不丟例外。"""
    try:
        cmd = [
            "nvidia-smi",
            "--query-gpu=name,memory.total,memory.free,driver_version",
            "--format=csv,noheader,nounits",
        ]
        completed = subprocess.run(cmd, check=True, capture_output=True, text=True)
        line = completed.stdout.strip().splitlines()[0]
        name, total, free, driver = [part.strip() for part in line.split(",")]
    except Exception:
        return None
    try:
        import ctranslate2

        cuda_count = ctranslate2.get_cuda_device_count()
        supported = tuple(sorted(ctranslate2.get_supported_compute_types("cuda"))) if cuda_count else tuple()
    except Exception:
        return None
    if cuda_count < 1:
        return None
    return GpuInfo(
        name=name,
        total_mb=int(total),
        free_mb=int(free),
        driver_version=driver,
        cuda_device_count=cuda_count,
        supported_compute_types=supported,
    )


def plan_device(config: dict[str, Any]) -> DevicePlan:
    """依實機自動決定用 GPU 或 CPU、配哪顆模型。無 CUDA 時自動退 CPU＋小模型，永不丟例外。"""
    stt_cfg = config.get("stt", {})
    gpu = probe_gpu()
    if gpu is not None and "int8_float16" in gpu.supported_compute_types:
        preferred = stt_cfg.get("preferred_model", "large-v3-turbo")
        fallback = stt_cfg.get("fallback_model", "medium")
        min_free = int(stt_cfg.get("min_free_vram_mb_for_large", 2500))
        if gpu.free_mb < min_free:
            return DevicePlan("cuda", "int8_float16", fallback,
                              f"CUDA ok but free VRAM {gpu.free_mb}MB < {min_free}MB, use {fallback}", gpu)
        return DevicePlan("cuda", "int8_float16", preferred,
                          f"CUDA ok, free VRAM {gpu.free_mb}MB, use {preferred}", gpu)
    cpu_model = stt_cfg.get("cpu_model", "small")
    cpu_compute = stt_cfg.get("cpu_compute_type", "int8")
    return DevicePlan("cpu", cpu_compute, cpu_model, "No usable CUDA; falling back to CPU", None)


def read_lexicon(path: str | Path) -> list[str]:
    lexicon_path = Path(path)
    if not lexicon_path.is_absolute():
        lexicon_path = PROJECT_ROOT / lexicon_path
    if not lexicon_path.exists():
        return []
    terms: list[str] = []
    with lexicon_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            term = line.strip()
            if term and not term.startswith("#"):
                terms.append(term)
    return terms


# Whisper 中文模型在靜音/雜訊上常吐的固定幻覺句（多為 YouTube 字幕殘留）
_HALLUCINATION_PATTERNS = (
    "字幕提供",
    "字幕by",
    "字幕組",
    "明鏡與點點欄目",
    "點贊訂閱",
    "訂閱轉發打賞",
    "請不吝",
    "謝謝觀看",
    "謝謝大家收看",
    "下次再見",
    "多謝收睇",
)


def _is_hallucination(text: str) -> bool:
    compact = re.sub(r"[\s，。！？、,.!?]", "", text)
    if len(compact) <= 12:
        for pat in _HALLUCINATION_PATTERNS:
            if pat in compact:
                return True
    return False


def build_initial_prompt(terms: list[str], language: str) -> str:
    # 自動偵測語言時不強壓中文，避免把英文硬掰成中文；中文專有名詞改靠校正表補救。
    if language in ("auto", "", None):
        return ""
    if not terms:
        return "請以繁體中文轉寫。"
    joined = "、".join(terms)
    return f"請以繁體中文轉寫。專有名詞優先使用：{joined}。"


class LocalWhisperTranscriber:
    def __init__(self, config: dict[str, Any], plan: DevicePlan | None = None,
                 logger: logging.Logger | None = None) -> None:
        self.config = config
        self.logger = logger or logging.getLogger(__name__)
        configure_local_cache(config)
        self.plan = plan or plan_device(config)
        self._model = None

    @property
    def model_name(self) -> str:
        return self.plan.model_name

    def load(self) -> None:
        if self._model is not None:
            return
        from faster_whisper import WhisperModel

        stt_cfg = self.config.get("stt", {})
        download_root = PROJECT_ROOT / stt_cfg.get("download_root", "app/.cache/faster-whisper")
        model_ref = resolve_model_reference(self.plan.model_name)
        self.logger.info("Loading faster-whisper model %s on %s (%s)",
                         model_ref, self.plan.device, self.plan.compute_type)
        self._model = WhisperModel(
            model_ref,
            device=self.plan.device,
            compute_type=self.plan.compute_type,
            download_root=str(download_root),
        )

    def transcribe(self, audio_path: str | Path) -> str:
        self.load()
        assert self._model is not None
        stt_cfg = self.config.get("stt", {})
        lexicon_path = self.config.get("paths", {}).get("lexicon", "app/lexicon.txt")
        lang = self.config.get("language", "auto")
        whisper_lang = None if lang in ("auto", "", None) else lang
        prompt = build_initial_prompt(read_lexicon(lexicon_path), lang)
        segments, info = self._model.transcribe(
            str(audio_path),
            language=whisper_lang,
            task="transcribe",
            beam_size=int(stt_cfg.get("beam_size", 5)),
            vad_filter=bool(stt_cfg.get("vad_filter", True)),
            initial_prompt=prompt,
            condition_on_previous_text=False,
        )
        seg_list = list(segments)
        text = "".join(segment.text for segment in seg_list)
        text = re.sub(r"\s+", " ", text).strip()

        # 防呆：錄音過短（多半是誤觸點按），直接丟棄
        min_speech = float(stt_cfg.get("min_speech_seconds", 0.6))
        speech_dur = sum((s.end - s.start) for s in seg_list) if seg_list else 0.0
        if getattr(info, "duration", 0.0) < min_speech or speech_dur < 0.3:
            self.logger.info("Discarded too-short audio (%.2fs speech); likely a tap", speech_dur)
            return ""

        # 防呆：過濾 Whisper 中文常見幻覺（YouTube 字幕殘留），整句命中才丟
        if text and _is_hallucination(text):
            self.logger.info("Discarded hallucination text: %s", text)
            return ""

        self.logger.info(
            "Transcribed %s with model=%s device=%s language=%s duration=%.2fs",
            audio_path,
            self.plan.model_name,
            self.plan.device,
            getattr(info, "language", "unknown"),
            getattr(info, "duration", 0.0),
        )
        return text
