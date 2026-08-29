# voice-dictation

Hold a hotkey, talk, release: the audio is transcribed locally with faster-whisper, optionally cleaned up by a local LLM, and typed straight into whatever window your cursor is in. Nothing leaves the machine.

## What it does / why

Dictation built into an operating system is fine until you need it to spell your own vocabulary — project names, product names, technical terms — or until you would rather your voice not be uploaded anywhere. This runs the whole path locally: capture, transcription, clean-up, insertion.

1. **Capture.** A global hotkey toggles recording. `sounddevice` writes 16 kHz mono WAV to a scratch folder.
2. **Transcribe.** `faster-whisper` runs on the GPU when CUDA is usable, and falls back to CPU on its own if not — no configuration change and no exception either way. A word list you maintain is fed in as a prompt so proper nouns come out right.
3. **Clean up.** By default a rules pass fixes punctuation and spacing, converts Simplified to Traditional Chinese, and applies your correction table. If a local Ollama instance is reachable it can do the clean-up instead.
4. **Insert.** The text goes into the focused window, either by clipboard + Ctrl+V or by typing Unicode character by character.

A tray icon toggles clean-up, reloads the word lists, opens the log, and quits.

### Model selection

`plan_device()` probes the GPU once at startup and decides:

| Condition | Device | Model | Compute type |
| --- | --- | --- | --- |
| CUDA present, `int8_float16` supported, free VRAM ≥ 2500 MB | `cuda` | `large-v3-turbo` | `int8_float16` |
| CUDA present but free VRAM below that threshold | `cuda` | `medium` | `int8_float16` |
| No usable CUDA | `cpu` | `small` | `int8` |

Every value in that table comes from `config.json` and can be changed. The probe never raises — a machine with no GPU simply lands on the last row.

## Requirements

- **Windows.** The global hotkey, the clipboard round-trip and the text injection all use Windows-only paths.
- Python 3.10 or newer.
- The pinned packages in `app/requirements.txt`.
- Optional: an NVIDIA GPU with CUDA for the large model. Without one it still works, just slower and on a smaller model.
- Optional: a local [Ollama](https://ollama.com) instance for LLM clean-up. Without it, the rules engine handles it.

Models are downloaded on first use into `app/.cache/faster-whisper`, which is gitignored. `large-v3-turbo` is roughly 1.6 GB; expect the first run to be slow.

## Install

```powershell
git clone <repo-url> voice-dictation
cd voice-dictation
python -m venv app\.venv
app\.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r app\requirements.txt
```

`start.ps1` expects the virtual environment at `app\.venv` and points every cache environment variable (`PIP_CACHE_DIR`, `HF_HOME`, `TRANSFORMERS_CACHE`, `XDG_CACHE_HOME`, `TEMP`) inside the repository, so nothing is written to your profile.

## Usage

```powershell
.\start.cmd
```

`start.cmd` just calls `start.ps1`, which sets the cache paths and launches `app\main.py`. It errors out if the virtual environment is missing.

Once resident, press the hotkey to start recording and press it again to stop. The transcript is cleaned up and inserted at the cursor. The tray icon shows the current state.

To start it with Windows, press `Win + R`, enter `shell:startup`, and put a shortcut to `start.cmd` in the folder that opens.

## Configuration

Everything lives in `app/config.json`.

| Key | Default | Meaning |
| --- | --- | --- |
| `hotkey` | `right windows+alt` | Any key or combination `keyboard` understands. A combination is registered with `add_hotkey` and toggles on press; a single key toggles on release. |
| `hotkey_label` | `右Win+Alt` | What the tray tooltip shows. |
| `hotkey_suppress` | `true` | Swallow the key so Windows does not also act on it (opening the Start menu, for instance). |
| `sample_rate` / `channels` | `16000` / `1` | Capture format. faster-whisper wants 16 kHz mono. |
| `input_device` | `null` | `null` means the system default recording device. |
| `language` | `auto` | Or a language code to skip detection. |
| `stt.*` | see the table above | Model, device, compute type, VRAM threshold, beam size, VAD filter, download root. |
| `polish.enabled` | `true` | Turn clean-up off entirely. |
| `polish.engine` | `rules` | `rules`, `ollama`, or `auto` (use Ollama if a model is there, else rules). |
| `polish.ollama_url` / `ollama_model` | `http://127.0.0.1:11434` / `qwen2.5:1.5b` | Where to find Ollama and which model to ask for. |
| `inject.method` | `type` | `paste` uses the clipboard and Ctrl+V — fastest, right for ordinary text boxes. `type` sends Unicode character by character — slower but works in terminals and Electron windows that ignore a programmatic paste. |
| `paths.lexicon` | `app/lexicon.txt` | One proper noun per line, fed to Whisper as a prompt. |
| `paths.corrections` | `app/corrections.txt` | One `misheard=correct` rule per line, applied after transcription. |
| `paths.temp_dir` / `log_dir` | `_temp` / `app/logs` | Scratch audio and the log file. |

Both word lists are plain UTF-8 text, re-read on the next transcription — edit and save, no restart. The two shipped files are examples; replace them with your own vocabulary.

## Output

- Text is inserted into the focused window. Nothing is saved by default.
- `app/logs/typeless.log` — the runtime log.
- `_temp/` — the WAV being transcribed, deleted once transcription finishes.
- `app/.cache/` — downloaded models.

All of these are gitignored.

## Smoke test

There is no pytest suite. The check below is a script you run by hand; `pytest.ini` keeps
pytest from trying to import it, because doing so would pull in the whole runtime.

```powershell
app\.venv\Scripts\python.exe app\smoke_test.py
```

It checks imports, loads the planned model, pushes a silent WAV through the whole path, and exercises the injector with empty text. It prints a JSON summary. The first run downloads a model.

## Limitations

- **Windows only.** `keyboard`, `pyperclip` and the injection path are not portable, and the global hotkey needs the process to be running as at least the same privilege level as the window you are typing into. If insertion silently fails in an elevated application, that is why.
- **The interface and log messages are in Traditional Chinese**, and the rules-based clean-up (punctuation, spacing, Simplified→Traditional conversion) is written for Chinese. English transcripts go through a much thinner clean-up path.
- **`keyboard` needs to install a low-level hook.** Some security software blocks that, and some full-screen games swallow it.
- **First run downloads a model** of about 1.6 GB and is slow. There is no progress bar for that.
- **VRAM is probed once at startup.** If another process claims the GPU afterwards, transcription can fail rather than falling back to CPU.
- **No streaming.** You get the transcript after you stop recording, not while speaking.
- **Ollama clean-up is a language model rewriting your words.** It can change meaning. `polish.engine: "rules"` is the conservative setting and is the default.
- **The `paste` injection method overwrites your clipboard** and restores it after a configurable delay. If you copy something during that window, the restore wins.
- **No tests beyond the smoke test**, which needs a real model to be meaningful.

## License

MIT. See [LICENSE](LICENSE).

A Traditional Chinese version of this document is in [README.zh-TW.md](README.zh-TW.md).
