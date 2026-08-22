from __future__ import annotations

import logging
import threading
import wave
from datetime import datetime
from pathlib import Path

import numpy as np
import sounddevice as sd


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def resolve_input_device(spec, logger: logging.Logger | None = None):
    """把 config 的 input_device 解析成 sounddevice 用的裝置。

    spec 可以是：
      None  -> 用系統預設輸入裝置（行為與未設定時完全相同）
      int   -> 直接當索引用（不建議：索引會隨裝置插拔位移）
      str   -> 名稱子字串比對，只在「有輸入聲道」的裝置裡找

    同一顆麥克風會在多個音訊介面(host API)下重複出現，這裡取第一個命中的，
    避免 sounddevice 內建的字串比對因為多重命中而直接丟例外。
    """
    log = logger or logging.getLogger(__name__)
    if spec is None or isinstance(spec, int):
        return spec

    needle = str(spec).strip().lower()
    if not needle:
        return None

    inputs = [(i, d) for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]

    for i, d in inputs:
        if d["name"].strip().lower() == needle:
            log.info("Input device %r matched exactly -> [%d] %s", spec, i, d["name"])
            return i

    for i, d in inputs:
        if needle in d["name"].lower():
            log.info("Input device %r matched -> [%d] %s", spec, i, d["name"])
            return i

    available = ", ".join(f"[{i}] {d['name']}" for i, d in inputs)
    raise ValueError(f"找不到名稱含「{spec}」的錄音裝置。目前可用：{available}")


class AudioRecorder:
    def __init__(
        self,
        temp_dir: str | Path,
        sample_rate: int = 16000,
        channels: int = 1,
        input_device=None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.temp_dir = Path(temp_dir)
        if not self.temp_dir.is_absolute():
            self.temp_dir = PROJECT_ROOT / self.temp_dir
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.sample_rate = sample_rate
        self.channels = channels
        self.logger = logger or logging.getLogger(__name__)
        self.input_device_spec = input_device
        self.input_device = resolve_input_device(input_device, self.logger)
        self._frames: list[bytes] = []
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()

    @property
    def is_recording(self) -> bool:
        return self._stream is not None

    def start(self) -> None:
        with self._lock:
            if self._stream is not None:
                return
            self._frames = []
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="int16",
                device=self.input_device,
                callback=self._callback,
            )
            self._stream.start()
            self.logger.info("Recording started")

    def stop(self) -> Path | None:
        with self._lock:
            stream = self._stream
            self._stream = None
        if stream is None:
            return None
        stream.stop()
        stream.close()
        self.logger.info("Recording stopped")
        return self._write_frames()

    def _callback(self, indata: np.ndarray, frames: int, time, status) -> None:
        if status:
            self.logger.warning("Recorder status: %s", status)
        self._frames.append(indata.copy().tobytes())

    def _write_frames(self) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        wav_path = self.temp_dir / f"dictation_{timestamp}.wav"
        with wave.open(str(wav_path), "wb") as wav:
            wav.setnchannels(self.channels)
            wav.setsampwidth(2)
            wav.setframerate(self.sample_rate)
            wav.writeframes(b"".join(self._frames))
        return wav_path


def write_silence_wav(
    target: str | Path,
    duration_seconds: float = 1.0,
    sample_rate: int = 16000,
    channels: int = 1,
) -> Path:
    target_path = Path(target)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    frame_count = max(1, int(duration_seconds * sample_rate))
    silence = np.zeros((frame_count, channels), dtype=np.int16)
    with wave.open(str(target_path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(silence.tobytes())
    return target_path


def remove_temp_audio(path: str | Path, logger: logging.Logger | None = None) -> None:
    audio_path = Path(path)
    try:
        if audio_path.exists():
            audio_path.unlink()
    except OSError as exc:
        (logger or logging.getLogger(__name__)).warning("Could not delete temp audio %s: %s", audio_path, exc)
