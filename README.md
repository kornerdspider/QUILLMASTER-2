# Quill Master 2 — Build 1 (alpha)

This is the first working piece of Quill Master 2: **Full Manuscript mode and the Story Bible**.
It reads every book of the trilogy one scene at a time, builds the Story Bible, finds contradictions,
and asks the writer about them at the Reference Desk.

## What works in this build

- **The Catalog** – add Book 1 and Book 2 (`.docx`, `.odt`, `.md`, `.txt`), then click **Read Full Manuscript**.
  Books are read in series order. Reading can be paused and picks up where it stopped.
  After you edit a book, click **Re-import**: only the scenes that changed are read again.
- **The Archive** – the Story Bible. Every fact shows its status (canon, found, conflict, planned, retired)
  and the exact sentence it came from. Edit facts, make them canon, add planned facts for Book 3.
  Export the whole Bible as `.md` or `.docx`.
- **The Reference Desk** – questions about contradictions ("Tavin is 19 in Ch 1 and 17 in Ch 3"),
  storylines that never resolve, and possible duplicate characters. Every answer shows what will change
  before it's saved. Lines that now disagree with canon go on the **To fix** list in the Catalog.
- **The Index** – outline, chapter and book summaries, synopsis, character summaries, storylines and
  the subplot matrix.
- **Trilogy rules** – one Bible for all books. Ages, titles and similar facts may change between books
  without counting as a mistake. Eye color and other fixed facts must match across all books.

## Not in this build yet (next steps on the roadmap)

- Chapter mode (the Reading Room), the 11 passes, and applying edits to the manuscript.
- The desktop window and installers. For now it opens in your web browser. Once the Quill Master 1
  project folder arrives, this gets moved into that desktop app and its Mac/Windows/Linux installers.
- Encrypting the database file. Right now it's a normal file in your data folder (below).

---

## How to start it

You need two things on the computer first:

1. **Python 3** – from https://www.python.org/downloads/ (On Windows, tick **"Add python.exe to PATH"**
   on the first installer screen.)
2. **Ollama** with a model – the same one Quill Master 1 uses. If Ollama is already set up for
   Quill Master 1, you're done.

### Windows

1. Unzip `QuillMaster2.zip`. Right-click it, click **Extract All…**, then **Extract**.
   You get a folder like `C:\Users\YourName\Downloads\QuillMaster2`.
2. Open that folder and double-click **`Start-QuillMaster2-Windows.bat`**.
   - If a blue "Windows protected your PC" box appears, click **More info**, then **Run anyway**.
3. The first time, a black window installs what it needs (a minute or two). Then your web browser opens
   Quill Master 2 at `http://127.0.0.1:8765`.
4. Keep the black window open while you work. Close it to stop Quill Master 2.

### Mac

1. Double-click `QuillMaster2.zip` in your **Downloads** folder. A `QuillMaster2` folder appears next to it
   (`/Users/YourName/Downloads/QuillMaster2`).
2. Open the folder. **Two-finger click** (right-click) **`Start-QuillMaster2-Mac.command`** and choose **Open**.
   Click **Open** again in the warning box. (You only need to do this the first time; after that a
   double-click works.)
3. A Terminal window installs what it needs the first time, then your browser opens Quill Master 2.
4. Keep the Terminal window open while you work.

### Linux (Mint)

1. Right-click `QuillMaster2.zip` → **Extract Here**.
2. Open the folder, right-click an empty spot → **Open in Terminal**, and type:
   `./start-quillmaster2-linux.sh`
   If it says venv is missing, run `sudo apt install python3-venv` first.

---

## First time in the app

1. Click the **⚙ (Settings)** button at the bottom left. In **Model name**, type the exact model name
   Quill Master 1 uses on this computer. (To see your installed models, open Terminal or Command Prompt
   and type `ollama list`.) Click **Save**. The pill at the top right turns green when the model is ready.
2. Go to **Catalog**. Under **Add a book**: type the title, set **Place in the series** to 1,
   choose the Book 1 file, and click **Add to shelf**. Do the same for Book 2 with place 2.
3. Click **Read Full Manuscript**. A progress bar shows at the top. A 130,000-word book is a long read
   on a laptop, so let it run; you can click **Pause** and come back later.
4. When it finishes, open the **Reference Desk** and answer the questions. Then look through the
   **Archive** and the **Index**.

### Want to look around first? Try demo mode

In **Settings**, set **Reader** to **Demo** and click **Save**. Then add the two sample books from the
`samples` folder (`Book1_The_Ember_Road.md`, `Book2_The_Ash_Sea.txt`) and click **Read Full Manuscript**.
Demo mode is a simple pattern reader, not an AI, but it shows how the Bible, conflicts and questions work.
The sample books contain planted mistakes (Tavin's age, Mara's eye color, a box that never gets opened).
Switch **Reader** back to **Ollama** before using real books.

## Where your work is saved

- Windows: `C:\Users\YourName\QuillMaster2Data`
- Mac: `/Users/YourName/QuillMaster2Data`
- Linux: `/home/yourname/QuillMaster2Data`

Inside: `quillmaster2.db` (books and the Story Bible), `config.json` (settings) and `uploads`
(copies of the files you added). Your original manuscript files are never changed.

## For developers

- `quillmaster2/importer.py` – reads files, splits chapters and scenes, fingerprints scenes
- `quillmaster2/archivist.py` – scene reading, Bible merging, conflicts, Q&A answers, documents
- `quillmaster2/llm.py` – Ollama client (structured JSON output) and the demo reader
- `quillmaster2/server.py` – the `/v2` API; `quillmaster2/static/` – the screens
- Tests: `python -m pytest tests`
