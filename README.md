# Gmail 自動讀寫程式

依課程作業一自行撰寫的 Python 命令列程式，使用標準函式庫，無須安裝額外套件。專案包含 F1–F7 功能、Word 報告、多份歷史測試紀錄與截圖。執行紀錄有不同程式版本，應依各紀錄與報告日期解讀；自動測試使用模擬伺服器，不等同真實 Gmail 實測。

新視窗請使用 [逐項實測清單](逐項實測清單.md)，逐一測試 F1–F7，並區分舊版證據與修正後的新結果。

## Python 環境

本作業專用 `.venv` 已以 Python 3.14.6 建立，不包含系統或使用者的 site 套件；程式與測試只使用標準函式庫，無須安裝第三方套件。以下命令均直接使用 `.venv/bin/python`，不必啟動環境，也不會誤用 shell 選到的其他 Python。可用下列指令確認：

```sh
.venv/bin/python --version
.venv/bin/python -c 'import sys; print(sys.executable); print(sys.prefix != sys.base_prefix)'
```

若在另一台電腦或新的資料夾使用，先安裝 Python 3.14.6，再於本目錄執行 `python3.14 -m venv .venv`。虛擬環境依賴建立時的 Python 與本機路徑，不能保證跨機器或系統升級後仍可用；失效時請重新建立。`.venv` 不納入 Git 或作業 ZIP，並非 Python 版本鎖定或可攜式執行檔。

## 準備

1. 使用自己的測試 Gmail，於 Google 帳戶自行開啟兩步驟驗證並建立應用程式密碼；依題目保留遮蔽密碼的設定過程截圖。帳號可能因組織政策或帳戶設定而不提供應用程式密碼；遇此情況請依 Google 介面與課堂要求處理。不要把密碼、驗證碼或畫面交給 AI，也不要將密碼寫入檔案、報告、截圖或版本控制。
2. 在終端機切換至 `作業一_Gmail` 目錄，以自己的測試地址設定環境變數（下列地址僅為格式示例，需自行替換）：

   ```sh
   export GMAIL_ADDRESS='your-test-account@gmail.com'
   ```

3. 每次執行時在終端機的隱藏輸入提示輸入應用程式密碼。程式接受 Google 顯示的分組空格。自動化環境亦可透過 `GMAIL_APP_PASSWORD` 設定，但避免寫進 shell 歷史、設定檔或日誌；完成後清除環境變數。不要將密碼輸入命令列參數。

## 操作與真實證據

```sh
.venv/bin/python src/gmail_app.py list -n 5 --save-result
.venv/bin/python src/gmail_app.py verify --subject 'Gmail 作業自寄測試' --body '這是一封測試信。' --save-result
```

`list` 印出總信數、讀取數，以及由新到舊的日期、寄件者和主旨。`verify` 寄給登入帳號本身，依本次產生的 Message-ID 搜尋收件匣，找到才表示收件成功並顯示耗時。預設等待 120 秒、每 5 秒查詢，可加 `--timeout 180 --interval 5`；逾時是從寄信開始到取得查詢結果的期限；找到信件的結果若超過期限仍算逾時。網路操作各有逾時，無法立即中斷進行中的連線，因此實際執行時間可能超過設定秒數。若等待逾時或連線中斷，先檢查信箱，避免重複寄送；逾時不表示信件永遠不會送達。

另可寄給自己或同組同學：

```sh
.venv/bin/python src/gmail_app.py send --to 'your-test-account@gmail.com' --subject '測試' --body '你好' --save-result
```

SMTP 接受不等於收件；請至收件端實際確認。帳號與收件者限常見 ASCII 單一郵件地址格式，格式正確不代表信箱存在。指令參數可能留在 shell 歷史中，請只放非敏感的測試文字，不要輸入密碼或個資。

選用 `--save-result` 時，成功操作在 `results/` 產生 Markdown 與 JSON 紀錄，包含時間、操作、結果及計數或耗時，不寫入信箱、信件標頭、主旨、內文或密碼。紀錄不等於認證設定、讀信清單、收件畫面的完整證據：需另外截圖或人工整理，先遮蔽密碼與不必要個資，保留日期、主旨、部分遮蔽寄件者與收件者等可核對資訊。程式的螢幕輸出包含真實信件資料，分享或截圖前務必檢查遮蔽。本公開版的 `results/` 僅收錄經個資遮蔽的歷史紀錄；日後新增資料前仍應逐一檢查。

依題目也需記錄真實錯誤及處理：若未遇錯，可在隱藏密碼提示輸入錯誤值執行 `list`，保留不含輸入值的錯誤畫面；不要反覆嘗試。報告模板列出其他必要項目。結束後依 Google 帳戶介面撤銷此次應用程式密碼。

## 自動測試

```sh
.venv/bin/python -m unittest discover -s tests -v
```

測試使用模擬伺服器，不連線 Gmail、不寄信；可驗證主要邏輯及錯誤分支，不能取代真實收發驗證。

## 信件篩選與附件（加分功能）

`list` 可用 `--unread`、`--since YYYY-MM-DD`（含該日）及 `--subject-keyword 文字`，條件可組合。主旨比對不區分大小寫；先篩選再取最新 N 封，不將信件標記為已讀。主旨條件需逐封讀取候選標頭，候選信件多時可能較慢。例如：

```sh
.venv/bin/python src/gmail_app.py list -n 5 --unread --since 2026-10-29 --subject-keyword 'Gmail 作業自寄測試' --save-result
```

`send` 與 `verify` 可各自加上一個或多個 `--attach`；附件無法讀取時會先停止，不建立 SMTP 連線。提供不含個資的測試檔 `testdata/附件測試.txt`：

```sh
.venv/bin/python src/gmail_app.py send --to "$GMAIL_ADDRESS" --subject 'Gmail 附件測試' --body '這是一封附件測試信。' --attach 'testdata/附件測試.txt' --save-result
.venv/bin/python src/gmail_app.py verify --subject 'Gmail 附件驗證' --body '這是一封附件驗證信。' --attach 'testdata/附件測試.txt' --save-result
```

`send` 只證明伺服器接受；請查看 Gmail 收件端確認附件及內容。`verify` 會查詢本次信件的 Message-ID，證明信件到達，但也必須在收件端確認附件內容。自動紀錄保存附件數量、啟用的篩選種類及結果數，不保存檔名、篩選字詞與信件資料。

## 信件存檔及單次自動回覆

2026-11-10 新增功能；既有附件等測試紀錄與新增功能的紀錄可能對應不同的程式版本，請依紀錄內容判讀。下列範例僅用自己的 F3 測試信，帳密仍由環境變數和隱藏提示提供。

```sh
.venv/bin/python src/gmail_app.py export --subject 'F3 寄後驗證 20261110' --out 'results/F3測試信.eml' --save-result
```

程式唯讀查詢收件匣，完整主旨必須恰好對應一封信；結果以原始 `.eml` 格式存入指定的新檔案，不覆寫既有檔案。檔案以擁有者可讀寫的權限建立；真實信件可能包含個資或附件，勿直接公開或放入作業繳交檔。真實 `.eml` 不應納入 Git；本庫僅收錄一份已匿名化的歷史範例。若同主旨有多封信，先用唯一主旨的新測試信，勿以刪除真實郵件的方式消除歧義。

```sh
.venv/bin/python src/gmail_app.py auto-reply --subject 'F3 寄後驗證 20261110' --body '已收到測試信，這是自動回覆。' --save-result
```

每次執行只檢查一次，僅回覆自己寄來、未讀且主旨完全相同的唯一信件，收件者固定為自己。程式拒絕自動信及缺少可用 Message-ID 的信；SMTP 接受後仍須至 Gmail 確認收到。查詢本身不改已讀狀態，不會持續在背景執行。

為避免重複寄送，執行時在 `results/.auto_reply_guard/` 建立防重複紀錄；此紀錄不含帳密或信件內容。即使連線失敗、寄送結果不明，紀錄也會保留，請先查看 Gmail 收件匣和寄件備份，不要刪除紀錄或盲目重試；需再做測試時可寄一封具有新主旨的本人測試信。兩種功能都需讀取候選信件標頭，信件較多時可能稍慢。

## 公開版與檔案說明

- `src/`、`tests/`：程式與自動測試；程式僅使用 Python 標準函式庫。
- `report/`：報告各版本、架構圖與草稿；`results/`：歷次執行結果及測試截圖。
- 公開副本中的 Word、截圖、文字紀錄與 EML 已遮蔽或替換帳號、電腦識別資訊及認證資訊；原始檔仍保留於本機。公開版截圖檔名以「公開遮蔽截圖」標示。
- EML 範例經匿名化，並非原始信件的逐位元副本，檔案大小可能不同。
- 歷史的 F1–F6 和附件實測紀錄與後來加入 EML 匯出、自動回覆的程式版本並不完全相同；請勿將早期紀錄視為目前所有程式碼均已重新實測的證據。
- `.venv`、密碼、設定檔、快取和自動回覆防重複狀態不隨公開版上傳。
