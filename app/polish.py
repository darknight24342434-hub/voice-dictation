from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from opencc import OpenCC


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class PolishStatus:
    engine: str
    model: str | None
    detail: str


class Polisher:
    def __init__(self, config: dict[str, Any], logger: logging.Logger | None = None) -> None:
        self.config = config
        self.logger = logger or logging.getLogger(__name__)
        self.polish_cfg = config.get("polish", {})
        self.converter = OpenCC("s2twp")
        self.status = self._detect_engine()

    def _detect_engine(self) -> PolishStatus:
        if not self.polish_cfg.get("enabled", True):
            return PolishStatus("off", None, "polish disabled in config")
        engine = self.polish_cfg.get("engine", "auto")
        if engine == "rules":
            return PolishStatus("rules", None, "rules engine forced by config")
        if engine not in ("auto", "ollama"):
            return PolishStatus("rules", None, f"unknown engine {engine}; rules fallback")
        model = self.polish_cfg.get("ollama_model") or self._pick_ollama_model()
        if model:
            return PolishStatus("ollama", model, "local Ollama model detected")
        if engine == "ollama" and not self.polish_cfg.get("rules_fallback", True):
            return PolishStatus("unavailable", None, "Ollama forced but no local model detected")
        return PolishStatus("rules", None, "no local Ollama model detected; rules fallback")

    def _pick_ollama_model(self) -> str | None:
        base_url = self.polish_cfg.get("ollama_url", "http://127.0.0.1:11434").rstrip("/")
        try:
            response = requests.get(f"{base_url}/api/tags", timeout=1.0)
            response.raise_for_status()
            models = response.json().get("models", [])
        except requests.RequestException as exc:
            self.logger.info("Ollama not available: %s", exc)
            return None
        if not models:
            return None
        preferred = []
        fallback = []
        for item in models:
            name = item.get("name") or item.get("model")
            if not name:
                continue
            size = int(item.get("size") or 0)
            row = (size, name)
            lowered = name.lower()
            if any(token in lowered for token in ("instruct", "qwen", "llama", "gemma", "phi")):
                preferred.append(row)
            else:
                fallback.append(row)
        candidates = preferred or fallback
        candidates.sort(key=lambda row: (row[0] == 0, row[0], row[1]))
        return candidates[0][1] if candidates else None

    def polish(self, text: str) -> str:
        raw = (text or "").strip()
        if not raw:
            return ""
        # 英文一律走規則式：LLM 的中文清稿提示會把英文搞壞
        if _is_mostly_latin(raw):
            return self._apply_corrections(_english_polish(raw))
        if self.status.engine == "ollama" and self.status.model:
            try:
                return self._apply_corrections(self._to_traditional(self._ollama_polish(raw)))
            except Exception as exc:
                self.logger.warning("Ollama polish failed, falling back to rules: %s", exc)
                if not self.polish_cfg.get("rules_fallback", True):
                    raise
        return self._apply_corrections(self._to_traditional(rule_polish(raw)))

    def _apply_corrections(self, text: str) -> str:
        """套用個人校正表（預設 app/corrections.txt，格式：錯誤詞=正確詞），檔案小、每次即讀即用。
        路徑可用 config.json 的 paths.corrections 覆寫。"""
        configured = (self.config.get("paths", {}) or {}).get("corrections", "app/corrections.txt")
        table_path = Path(configured)
        if not table_path.is_absolute():
            table_path = PROJECT_ROOT / table_path
        if not table_path.exists():
            return text
        try:
            for line in table_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                wrong, right = line.split("=", 1)
                if wrong:
                    text = text.replace(wrong.strip(), right.strip())
        except OSError as exc:
            self.logger.warning("Could not read corrections table: %s", exc)
        return text

    def _ollama_polish(self, text: str) -> str:
        base_url = self.polish_cfg.get("ollama_url", "http://127.0.0.1:11434").rstrip("/")
        timeout = float(self.polish_cfg.get("timeout_seconds", 20))
        prompt = (
            "你是本機語音聽寫清稿器。請只輸出清稿後文字，不要解釋。\n"
            "規則：去除嗯、呃、那個、就是說等贅字；講錯改口時保留改後版本；補上自然標點；"
            "不可新增或刪除原本語意；輸出繁體中文。\n\n"
            f"原文：{text}"
        )
        options = {"temperature": 0.1}
        # num_gpu=0 → 強制 LLM 跑 CPU，不與語音模型搶顯卡（防卡死）
        num_gpu = self.polish_cfg.get("ollama_num_gpu")
        if num_gpu is not None:
            options["num_gpu"] = int(num_gpu)
        payload = {
            "model": self.status.model,
            "prompt": prompt,
            "stream": False,
            "options": options,
            # 保溫：模型在此時間內常駐，避免每次冷啟動重載（閒置後首次會慢）
            "keep_alive": self.polish_cfg.get("ollama_keep_alive", "30m"),
        }
        response = requests.post(f"{base_url}/api/generate", data=json.dumps(payload), timeout=timeout)
        response.raise_for_status()
        result = response.json().get("response", "").strip()
        return result or rule_polish(text)

    def _to_traditional(self, text: str) -> str:
        return self.converter.convert(text).strip()


FILLERS = (
    "嗯",
    "呃",
    "啊",
    "那個",
    "就是說",
    "對啊就是",
    "對阿就是",
    "就是",
)


def _is_mostly_latin(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    latin = sum(1 for c in letters if ord(c) < 128)
    return latin / len(letters) > 0.6


def _english_polish(text: str) -> str:
    # 英文：保留空格與半形標點，只做去多餘空白、句尾補句點、首字母大寫
    t = re.sub(r"\s+", " ", text).strip()
    t = re.sub(r"\s+([,.!?;:])", r"\1", t)
    if not t:
        return ""
    if t[-1] not in ".!?":
        t += "."
    return t[0].upper() + t[1:]


def rule_polish(text: str) -> str:
    if _is_mostly_latin(text):
        return _english_polish(text)
    cleaned = _normalize_space(text)
    cleaned = _remove_fillers(cleaned)
    cleaned = _apply_time_corrections(cleaned)
    cleaned = _apply_general_corrections(cleaned)
    cleaned = _normalize_space(cleaned)
    cleaned = _apply_basic_punctuation(cleaned)
    return cleaned


def _normalize_space(text: str) -> str:
    text = re.sub(r"[ \t\r\n]+", " ", text)
    text = re.sub(r"\s*([，。！？；：、,.!?;:])\s*", r"\1", text)
    return text.strip(" ，,。")


def _remove_fillers(text: str) -> str:
    filler_pattern = "|".join(re.escape(item) for item in FILLERS)
    text = re.sub(rf"(^|[，,。！？\s])(?:{filler_pattern})(?=([，,。！？\s]|$))", r"\1", text)
    text = re.sub(rf"^(?:{filler_pattern})[，,、\s]*", "", text)
    return _normalize_space(text)


def _apply_time_corrections(text: str) -> str:
    date_words = r"(?:今天|明天|後天|昨天|本週|這週|下週|下禮拜|禮拜[一二三四五六日天])?"
    part_words = r"(?:凌晨|早上|上午|中午|下午|傍晚|晚上)?"
    time_words = r"[零一二三四五六七八九十兩\d]+點(?:半|多|[零一二三四五六七八九十兩\d]+分)?"
    pattern = re.compile(
        rf"(?P<prefix>{date_words}\s*{part_words})\s*{time_words}"
        rf"\s*(?:，|,|。|\s)*(?:不對|不是|錯了)"
        rf"\s*(?:，|,|。|\s)*(?:我是說|應該是|是)?\s*"
        rf"(?P<new>{part_words}\s*{time_words})"
    )

    def replace(match: re.Match[str]) -> str:
        prefix = _normalize_space(match.group("prefix"))
        new_time = _normalize_space(match.group("new"))
        has_part = re.match(r"^(凌晨|早上|上午|中午|下午|傍晚|晚上)", new_time)
        if prefix and not has_part:
            return f"{prefix}{new_time}"
        date_only = re.sub(r"(凌晨|早上|上午|中午|下午|傍晚|晚上)$", "", prefix)
        return f"{date_only}{new_time}"

    return pattern.sub(replace, text)


def _apply_general_corrections(text: str) -> str:
    markers = ("不對我是說", "不對應該是", "不對是", "不對", "不是", "錯了")
    compact = text
    for marker in markers:
        if marker in compact:
            before, after = compact.rsplit(marker, 1)
            if len(after.strip()) >= 2 and len(before.strip()) <= 10:
                compact = after.strip()
    return compact


def _apply_basic_punctuation(text: str) -> str:
    if not text:
        return ""
    text = text.replace(",", "，").replace("?", "？").replace("!", "！")
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"([，。！？]){2,}", r"\1", text)
    if text[-1] not in "。！？":
        text += "。"
    return text
