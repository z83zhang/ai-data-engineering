# CLAUDE.md — Project Context for Claude Code

## What this project is

A multimodal semantic search pipeline over a single AMI Meeting Corpus session (ES2008a). It transcribes audio with Whisper, extracts slide text with Tesseract OCR, embeds both into a shared vector space, and lets a user query across both modalities at once.

Deliverables: `notebook.ipynb`, `download_data.py`, `requirements.txt`, `README.md`, `.env.example`, `prompt.md`.

---

## File structure

```
multimodal-search-pipeline/
├── notebook.ipynb          ← 25-cell pipeline notebook
├── download_data.py        ← Downloads audio + transcript only
├── requirements.txt
├── .env.example
├── README.md
├── CLAUDE.md               ← this file
├── prompt.md               ← original project spec (kept for reference)
│
├── data/raw/
│   ├── audio/              ← ES2008a.Mix-Headset.wav  (downloaded)
│   ├── slides/             ← slide JPGs               (committed to repo)
│   └── transcripts/        ← ES2008a.transcript.txt   (downloaded)
│
└── chroma_db/              ← ChromaDB persistence (auto-created by notebook)
```

---

## Tech stack

| Layer | Choice | Notes |
|---|---|---|
| ASR | `openai-whisper` `whisper-base` | Default; tiny/small also run in WER comparison cell |
| OCR | `pytesseract` `--oem 3 --psm 1` | Full layout mode; Tesseract must be installed system-wide |
| Embeddings | `sentence-transformers` `all-MiniLM-L6-v2` | 384-d, fast, good general quality |
| Vector store | `chromadb` cosine similarity | Two collections: `transcript_chunks`, `slides_ocr` |
| WER | `jiwer` | Compared against reference transcript |
| Data | `pandas`, `numpy` | DataFrames for transcription and OCR results |

---

## Architecture

```
ES2008a.Mix-Headset.wav  →  Whisper ASR  →  transcript text
                                                    ↓
                                           chunk (500 chars, 50 overlap)
                                                    ↓
slide JPGs  →  Tesseract OCR  →  slide text         ↓
                                           sentence-transformers embed
                                                    ↓
                                    ChromaDB  ┌──────────────────┐
                                              │ transcript_chunks │
                                              │ slides_ocr        │
                                              └──────────────────┘
                                                    ↓
                                         cross-modal search & ranking
```

---

## Key decisions

### Data download
- **Audio**: downloaded from `https://groups.inf.ed.ac.uk/ami/AMICorpusMirror/amicorpus/ES2008a/audio/ES2008a.Mix-Headset.wav`. The `download_file()` helper tries `requests` first and falls back to `urllib` because the Edinburgh server occasionally closes the connection mid-stream on large files.
- **Slides**: committed directly to the repo. The AMI mirror's `slides/` directory returns an HTML frameset with no accessible JPG listing; sequential filename probing also yielded nothing. Slides live in `data/raw/slides/`.
- **Transcript**: the AMI server no longer serves individual `*.words.xml` files at flat URLs. They are bundled inside `ami_public_manual_1.6.2.zip` (~22 MB) at `https://groups.inf.ed.ac.uk/ami/AMICorpusAnnotations/`. `download_data.py` streams the zip into memory, extracts all `ES2008a.*.words.xml` entries, parses word start-times, sorts chronologically, and writes a single plain-text file.
- **No synthetic fallbacks**: `download_data.py` never generates placeholder data. If a real download fails it prints the exact URL, HTTP status, and aborts.

### Chunking
- `CHUNK_SIZE = 500`, `OVERLAP = 50` characters. Chunk IDs use format `{meeting_id}_{part}_{index}` (e.g. `ES2008_a_14`).
- The notebook also runs a chunk-size ablation (250 / 500 / 1000) in the evaluation section.

### Embeddings & vector store
- `all-MiniLM-L6-v2` chosen for speed and 384-d size; `all-mpnet-base-v2` is the noted drop-in upgrade for production quality.
- Two separate ChromaDB collections so audio and slide modalities can be queried independently or merged.

### Evaluation
- **WER**: `jiwer` against the AMI human transcript. `parse_ami_reference()` in the notebook handles both the downloaded `.transcript.txt` fast path and raw NXT `.words.xml` files.
- **Precision@5**: placeholder `test_set` list with empty queries/relevant-chunks — user fills in after running the pipeline.
- **Timing**: `step_times` dict populated with `time.time()` deltas around each pipeline step.

### Windows-specific notes
- Tesseract path override: set `TESSERACT_CMD` in `.env` if the executable is not on PATH.
- The Edinburgh server requires `Connection: close` in request headers; keep-alive causes aborted connections on large file GETs.
