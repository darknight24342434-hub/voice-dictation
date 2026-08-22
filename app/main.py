from __future__ import annotations

import os
import sys

# pythonw.exe 無主控台，sys.stdout/stderr 為 None；任何函式庫寫 stdout 就會崩。
# 在載入其他模組前先導到空裝置，避免啟動途中無聲崩潰（例如卡在載入模型/OpenCC 前）。
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

import json
import logging
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from typing import Any

import keyboard

from inject import TextInjector
from polish import Polisher
from recorder import AudioRecorder, remove_temp_audio
from stt import LocalWhisperTranscriber, configure_local_cache, plan_device


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = PROJECT_ROOT / "app"
CONFIG_PATH = APP_DIR / "config.json"

# 狀態顏色（底色, 文字模板；{hk} 由熱鍵標籤填入）
STATE_STYLE = {
    "idle": ("#2b2f36", "#8a929c", "● 待命　按 {hk} 開始"),
    "recording": ("#c0392b", "#ffffff", "🔴 錄音中　按 {hk} 結束"),
    "working": ("#b9770e", "#ffffff", "⏳ 辨識中…"),
}


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def setup_logging(config: dict[str, Any]) -> logging.Logger:
    log_dir = PROJECT_ROOT / config.get("paths", {}).get("log_dir", "app/logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "typeless.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8")],
    )
    return logging.getLogger("typeless")


class TypelessApp:
    def __init__(self) -> None:
        self.config = load_config()
        configure_local_cache(self.config)
        self.logger = setup_logging(self.config)
        self.plan = plan_device(self.config)
        self.logger.info("Device plan: %s on %s (%s)", self.plan.model_name, self.plan.device, self.plan.reason)

        paths = self.config.get("paths", {})
        self.recorder = AudioRecorder(
            PROJECT_ROOT / paths.get("temp_dir", "_temp"),
            sample_rate=int(self.config.get("sample_rate", 16000)),
            channels=int(self.config.get("channels", 1)),
            input_device=self.config.get("input_device"),
            logger=self.logger,
        )
        self.transcriber: LocalWhisperTranscriber | None = None
        self.logger.info("CKPT recorder-ok")
        self.polisher = Polisher(self.config, logger=self.logger)
        self.logger.info("CKPT polisher-ok engine=%s", self.polisher.status.engine)
        self.injector = TextInjector(self.config, logger=self.logger)
        self.logger.info("CKPT injector-ok")

        self.jobs: queue.Queue[Path] = queue.Queue()
        self.running = True
        self.polish_enabled = bool(self.config.get("polish", {}).get("enabled", True))
        self.status = "idle"
        self._last_toggle = 0.0
        self.hotkey = self.config.get("hotkey", "f8")
        self.hotkey_label = self.config.get("hotkey_label") or self.hotkey.upper()

        self.worker = threading.Thread(target=self._worker_loop, daemon=True)

        # ---- tkinter 浮動狀態框（主執行緒）----
        self.logger.info("CKPT before-tk")
        self.root = tk.Tk()
        self.logger.info("CKPT tk-created")
        self.root.title("語音聽寫")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        try:
            self.root.attributes("-alpha", 0.94)
        except tk.TclError:
            pass
        self.label = tk.Label(
            self.root,
            text=STATE_STYLE["idle"][2],
            font=("Microsoft JhengHei", 11, "bold"),
            padx=16,
            pady=8,
        )
        self.label.pack()
        self._place_bottom_right()
        self._build_menu()
        self._bind_drag()
        self._apply_style()

    # ---------- UI ----------
    def _place_bottom_right(self) -> None:
        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"+{sw - w - 40}+{sh - h - 80}")

    def _build_menu(self) -> None:
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="潤稿開/關", command=self._toggle_polish)
        self.menu.add_command(label="重載詞庫/校正表", command=lambda: self.logger.info("Lexicon reloaded on next run"))
        self.menu.add_command(label="開 log", command=self._open_log)
        self.menu.add_separator()
        self.menu.add_command(label="退出", command=self._quit)

    def _bind_drag(self) -> None:
        self._drag = {"x": 0, "y": 0}
        for w in (self.root, self.label):
            w.bind("<Button-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<Button-3>", self._popup_menu)

    def _drag_start(self, e: tk.Event) -> None:
        self._drag = {"x": e.x, "y": e.y}

    def _drag_move(self, e: tk.Event) -> None:
        x = self.root.winfo_x() + (e.x - self._drag["x"])
        y = self.root.winfo_y() + (e.y - self._drag["y"])
        self.root.geometry(f"+{x}+{y}")

    def _popup_menu(self, e: tk.Event) -> None:
        self.menu.tk_popup(e.x_root, e.y_root)

    def _apply_style(self) -> None:
        bg, fg, template = STATE_STYLE.get(self.status, STATE_STYLE["idle"])
        text = template.format(hk=self.hotkey_label)
        if self.status == "idle" and not self.polish_enabled:
            text = f"● 待命（潤稿關）　按 {self.hotkey_label} 開始"
        self.root.configure(bg=bg)
        self.label.configure(bg=bg, fg=fg, text=text)

    def _ui_tick(self) -> None:
        if not self.running:
            return
        self._apply_style()
        self.root.after(80, self._ui_tick)

    # ---------- 熱鍵：F8 切換錄音 ----------
    def _toggle(self) -> None:
        now = time.monotonic()
        if now - self._last_toggle < 0.35:  # 去彈跳，避免快速雙擊誤觸
            return
        self._last_toggle = now
        if not self.recorder.is_recording:
            self._start()
        else:
            self._stop()

    def _start(self) -> None:
        try:
            self.recorder.start()
            self.status = "recording"
            self._beep(880)
        except Exception:
            self.logger.exception("Could not start recording")

    def _stop(self) -> None:
        try:
            wav_path = self.recorder.stop()
            self._beep(523)
            self.status = "working"
            if wav_path:
                self.jobs.put(wav_path)
            else:
                self.status = "idle"
        except Exception:
            self.logger.exception("Could not stop recording")
            self.status = "idle"

    # ---------- 背景：辨識 + 貼字 ----------
    def _worker_loop(self) -> None:
        while self.running:
            try:
                wav_path = self.jobs.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._process_audio(wav_path)
            finally:
                self.jobs.task_done()
                self.status = "idle"

    def _process_audio(self, wav_path: Path) -> None:
        try:
            if self.transcriber is None:
                self.transcriber = LocalWhisperTranscriber(self.config, plan=self.plan, logger=self.logger)
            raw_text = self.transcriber.transcribe(wav_path)
            final_text = self.polisher.polish(raw_text) if self.polish_enabled else raw_text
            if final_text:
                self.injector.inject(final_text)
                self.logger.info("Injected %d characters", len(final_text))
            else:
                self.logger.info("Nothing to inject (empty/filtered)")
        except Exception:
            self.logger.exception("Audio processing failed")
        finally:
            remove_temp_audio(wav_path, logger=self.logger)

    # ---------- 雜項 ----------
    def _beep(self, freq: int) -> None:
        if not self.config.get("beep", True):
            return
        try:
            import winsound

            threading.Thread(target=winsound.Beep, args=(freq, 60), daemon=True).start()
        except Exception:
            pass

    def _toggle_polish(self) -> None:
        self.polish_enabled = not self.polish_enabled
        self.logger.info("Polish enabled=%s", self.polish_enabled)

    def _open_log(self) -> None:
        log_path = PROJECT_ROOT / self.config.get("paths", {}).get("log_dir", "app/logs") / "typeless.log"
        if log_path.exists():
            os.startfile(str(log_path))

    def _quit(self) -> None:
        self.running = False
        try:
            keyboard.unhook_all()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self) -> None:
        self.worker.start()
        hotkey = self.hotkey
        suppress = bool(self.config.get("hotkey_suppress", True))
        if "+" in hotkey:
            # 組合鍵（例：right windows+alt）→ add_hotkey，按下即切換；suppress 擋掉系統反應（如開始選單）
            keyboard.add_hotkey(hotkey, self._toggle, suppress=suppress, trigger_on_release=False)
        else:
            # 單鍵 → 放開即切換
            keyboard.on_release_key(hotkey, lambda _: self._toggle(), suppress=False)
        self.logger.info("Typeless local dictation started. Toggle key = %s (label=%s).", hotkey, self.hotkey_label)
        self._ui_tick()
        self.root.mainloop()


def main() -> int:
    try:
        app = TypelessApp()
        app.run()
        return 0
    except Exception as exc:
        print(f"Startup failed: {exc}", file=sys.stderr)
        logging.getLogger("typeless").exception("Startup failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
