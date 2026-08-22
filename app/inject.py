from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import keyboard
import pyperclip


@dataclass(frozen=True)
class InjectResult:
    method: str
    restored_clipboard: bool


class TextInjector:
    """把文字送進當前游標視窗。

    method 可設：
      - "paste"（預設）：走剪貼簿 + Ctrl+V，最快，適合記事本/Word/一般輸入框。
      - "type"：用 SendInput 逐字打 Unicode，最相容，適合終端機、Electron/瀏覽器
                對話框等吃不到程式化 Ctrl+V 的地方（速度略慢）。
    """

    def __init__(self, config: dict[str, Any], logger: logging.Logger | None = None) -> None:
        self.config = config
        self.inject_cfg = config.get("inject", {})
        self.logger = logger or logging.getLogger(__name__)

    def inject(self, text: str) -> InjectResult:
        if not text:
            return InjectResult("skipped-empty", True)
        method = str(self.inject_cfg.get("method", "paste")).lower()
        if method == "type":
            return self._type(text)
        return self._paste(text)

    def _type(self, text: str) -> InjectResult:
        interval = float(self.inject_cfg.get("type_interval_seconds", 0.005))
        try:
            keyboard.write(text, delay=interval)
            return InjectResult("keyboard-type", True)
        except Exception as exc:
            self.logger.warning("keyboard.write failed, fallback to paste: %s", exc)
            return self._paste(text)

    def _paste(self, text: str) -> InjectResult:
        try:
            previous = pyperclip.paste()
        except Exception:
            previous = ""
        method = "clipboard-paste"
        settle = float(self.inject_cfg.get("copy_settle_seconds", 0.12))
        restore = float(self.inject_cfg.get("restore_delay_seconds", 0.7))
        try:
            pyperclip.copy(text)
            time.sleep(settle)  # 等剪貼簿內容真正就緒再貼
            keyboard.press_and_release(self.inject_cfg.get("paste_hotkey", "ctrl+v"))
            time.sleep(restore)  # 等目標視窗讀完剪貼簿再還原，避免貼到舊內容
        except Exception as exc:
            self.logger.warning("Clipboard paste failed, fallback to keyboard.write: %s", exc)
            interval = float(self.inject_cfg.get("type_interval_seconds", 0.005))
            keyboard.write(text, delay=interval)
            method = "keyboard-type"
        try:
            pyperclip.copy(previous)
            return InjectResult(method, True)
        except Exception as exc:
            self.logger.warning("Clipboard restore failed: %s", exc)
            return InjectResult(method, False)
