# voice-dictation

按住熱鍵講話，放開後在本機用 faster-whisper 轉寫，選配本機 LLM 清稿，再貼進你游標所在的視窗。全程不出本機。

## 為什麼

系統內建的語音輸入夠用，直到你需要它拼對你自己的詞彙——專案名、產品名、技術名詞——或者你不希望自己的聲音被上傳到任何地方。這支把整條路徑留在本機：錄音、轉寫、清稿、插入。

1. **錄音。** 全域熱鍵切換錄音，`sounddevice` 把 16 kHz 單聲道 WAV 寫進暫存資料夾。
2. **轉寫。** `faster-whisper` 在 CUDA 可用時走 GPU，不可用時自己退回 CPU——不用改設定，兩邊都不丟例外。你維護的詞庫會當作提示餵進去，讓專有名詞出得來。
3. **清稿。** 預設走規則式：修標點與空白、簡轉繁，再套你的校正表。偵測得到本機 Ollama 時可以改由它清稿。
4. **插入。** 文字送進當前焦點視窗，方式是剪貼簿 + Ctrl+V，或逐字送 Unicode。

托盤圖示可切換清稿、重載詞庫、開 log、退出。

### 模型怎麼選

`plan_device()` 啟動時探一次 GPU 然後決定：

| 條件 | 裝置 | 模型 | compute type |
|---|---|---|---|
| 有 CUDA、支援 `int8_float16`、可用 VRAM ≥ 2500 MB | `cuda` | `large-v3-turbo` | `int8_float16` |
| 有 CUDA 但可用 VRAM 低於門檻 | `cuda` | `medium` | `int8_float16` |
| 沒有可用的 CUDA | `cpu` | `small` | `int8` |

表裡每個值都來自 `config.json`，都能改。探測永遠不丟例外——沒有 GPU 的機器就落在最後一列。

## 環境需求

- **Windows。** 全域熱鍵、剪貼簿來回、文字注入走的都是 Windows 專有路徑。
- Python 3.10 以上。
- `app/requirements.txt` 內已鎖版本的套件。
- 選配：支援 CUDA 的 NVIDIA GPU，用來跑大模型。沒有也能跑，只是慢一點、模型小一點。
- 選配：本機 [Ollama](https://ollama.com) 用來清稿。沒有就走規則式。

模型第一次用到時下載到 `app/.cache/faster-whisper`（已 gitignore）。`large-v3-turbo` 大約 1.6 GB，第一次跑會慢。

## 安裝

```powershell
git clone <repo-url> voice-dictation
cd voice-dictation
python -m venv app\.venv
app\.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r app\requirements.txt
```

`start.ps1` 預期虛擬環境在 `app\.venv`，並把所有快取環境變數（`PIP_CACHE_DIR`、`HF_HOME`、`TRANSFORMERS_CACHE`、`XDG_CACHE_HOME`、`TEMP`）指到 repo 裡面，所以不會寫進你的使用者設定檔資料夾。

## 用法

```powershell
.\start.cmd
```

`start.cmd` 只是轉呼叫 `start.ps1`，後者設好快取路徑再啟動 `app\main.py`。虛擬環境不在就直接報錯。

常駐後按熱鍵開始錄音，再按一次停止。轉寫、清稿後插到游標位置。托盤圖示顯示目前狀態。

要開機自啟：按 `Win + R` 輸入 `shell:startup`，在開啟的資料夾內建立 `start.cmd` 的捷徑。

## 設定

全部在 `app/config.json`。

| 鍵 | 預設 | 意思 |
|---|---|---|
| `hotkey` | `right windows+alt` | 任何 `keyboard` 認得的鍵或組合。組合鍵用 `add_hotkey` 註冊、按下即切換；單鍵是放開時切換。 |
| `hotkey_label` | `右Win+Alt` | 托盤提示上顯示的字。 |
| `hotkey_suppress` | `true` | 吃掉該鍵，避免 Windows 同時反應（例如叫出開始選單）。 |
| `sample_rate`／`channels` | `16000`／`1` | 錄音格式。faster-whisper 要 16 kHz 單聲道。 |
| `input_device` | `null` | `null` 代表系統預設錄音裝置。 |
| `language` | `auto` | 或給語言碼跳過偵測。 |
| `stt.*` | 見上表 | 模型、裝置、compute type、VRAM 門檻、beam size、VAD 過濾、下載位置。 |
| `polish.enabled` | `true` | 整個清稿關掉。 |
| `polish.engine` | `rules` | `rules`、`ollama`，或 `auto`（有模型就用 Ollama，沒有就規則式）。 |
| `polish.ollama_url`／`ollama_model` | `http://127.0.0.1:11434`／`qwen2.5:1.5b` | Ollama 在哪、要哪顆模型。 |
| `inject.method` | `type` | `paste` 走剪貼簿加 Ctrl+V，最快，適合一般輸入框。`type` 逐字送 Unicode，慢一點但終端機、Electron 視窗這類吃不到程式化貼上的地方也能進。 |
| `paths.lexicon` | `app/lexicon.txt` | 一行一個專有名詞，當提示餵給 Whisper。 |
| `paths.corrections` | `app/corrections.txt` | 一行一條 `錯誤詞=正確詞`，轉寫後替換。 |
| `paths.temp_dir`／`log_dir` | `_temp`／`app/logs` | 暫存音檔與 log。 |

兩份詞表都是純 UTF-8 文字，下一次轉寫時重新讀取——存檔即生效，不用重開。附的兩個檔是範例，換成你自己的詞彙。

## 產出

- 文字直接插進焦點視窗，預設不另存。
- `app/logs/typeless.log` — 執行紀錄。
- `_temp/` — 正在轉寫的 WAV，轉完就刪。
- `app/.cache/` — 下載的模型。

四個都已 gitignore。

## 冒煙測試

```powershell
app\.venv\Scripts\python.exe app\smoke_test.py
```

它會檢查 import、載入規劃出來的模型、把一段靜音 WAV 推過整條路徑、用空字串試一次注入，最後印出 JSON 摘要。第一次跑會下載模型。

## 已知限制

- **只能在 Windows 跑。** `keyboard`、`pyperclip` 與注入路徑都不可攜；而且全域熱鍵要求本程式的權限層級至少等同你要打字的那個視窗。在提權的程式裡插字安靜失敗，原因就是這個。
- **介面與 log 訊息是繁體中文**，規則式清稿（標點、空白、簡轉繁）也是為中文寫的。英文轉寫走的清稿路徑薄很多。
- **`keyboard` 需要裝低階鉤子。** 有些防護軟體會擋，有些全螢幕遊戲會吃掉它。
- **第一次執行要下載約 1.6 GB 的模型**，很慢，而且沒有進度條。
- **VRAM 只在啟動時探一次。** 之後若有別的程式吃掉 GPU，轉寫可能直接失敗而不是退回 CPU。
- **沒有串流。** 你是停止錄音後才拿到逐字稿，不是邊講邊出。
- **Ollama 清稿是讓語言模型改寫你的話**，它可能改掉語意。`polish.engine: "rules"` 是保守設定，也是預設值。
- **`paste` 注入方式會覆寫你的剪貼簿**，隔一小段時間才還原。你若剛好在那個空檔複製東西，會被還原蓋掉。
- **除了冒煙測試沒有其他測試**，而冒煙測試要有真的模型才有意義。

## 授權

MIT，見 [LICENSE](LICENSE)。

English version: [README.md](README.md)
