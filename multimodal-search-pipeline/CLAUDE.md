# CLAUDE.md — Project Context for Claude Code

## What this project is

A multimodal semantic search pipeline over a single AMI Meeting Corpus session (ES2008a). It transcribes audio with Whisper, extracts slide text with Tesseract OCR, embeds both into a shared vector space, and lets a user query across both modalities at once.

Deliverables: `notebook.ipynb`, `download_data.py`, `requirements.txt`, `README.md`, `.env.example`, `prompt.md`.

---

## File structure

```
multimodal-search-pipeline/
├── notebook.ipynb          ← 35-cell pipeline notebook (Steps 1–9 + Extension 1)
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
│   ├── transcripts/        ← ES2008a.transcript.txt   (downloaded)
│   ├── words/              ← ES2008a.A/B/C/D.words.xml  (AMI NXT annotations)
│   └── video/              ← ES2008a_video_analysis.json  (auto-created by notebook)
│
└── chroma_db/              ← ChromaDB persistence (auto-created by notebook)
                               Collections: transcript_chunks, slides_ocr, video_segments
```

---

## Tech stack

| Layer | Choice | Notes |
|---|---|---|
| ASR | `openai-whisper` `whisper-base` | Default; tiny/small also run in WER comparison cell |
| OCR | `pytesseract` `--oem 3 --psm 1` | Full layout mode; Tesseract must be installed system-wide |
| Embeddings | `sentence-transformers` `all-MiniLM-L6-v2` | 384-d, fast, good general quality |
| Vector store | `chromadb` cosine similarity | Three collections: `transcript_chunks`, `slides_ocr`, `video_segments` |
| Segment summarisation | `anthropic` `claude-haiku-4-5-20251001` | Generates 2-3 sentence descriptions for video segments; requires `ANTHROPIC_API_KEY` |
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
*.words.xml →  ET.parse  →  timestamped segments    ↓
(4 speakers)   cue detection                        ↓
                                    ChromaDB  ┌──────────────────┐
                                              │ transcript_chunks │
                                              │ slides_ocr        │
                                              │ video_segments    │
                                              └──────────────────┘
                                                    ↓
                              cross-modal search · time-filtered search
                                         · topic-time aggregation
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
- Three ChromaDB collections (`transcript_chunks`, `slides_ocr`, `video_segments`) so each modality can be queried independently or merged.

### Evaluation
- **WER** (Part A): `jiwer` against the AMI human transcript. `parse_ami_reference()` handles both the downloaded `ES2008a.transcript.txt` fast path and raw NXT `.words.xml` files. Compares whisper-tiny, base, small.
- **Precision@5** (Part B): `test_set` has five labelled queries with ground-truth chunk IDs. Mean P@5 = 0.48 on the current run.
- **Precision@1** (Part B2): slide-specific evaluation using three queries against `slides_ocr`. Relevant answers are slide IDs (filename stems without `.jpg`). Mean P@1 = 1.00 on the current run.
- **Timing** (Part C): `step_times` dict populated with `time.time()` deltas. Transcription dominates at ~70 s; search is <15 ms.
- **Chunk size ablation** (Part D): reruns chunking and search at 250 / 500 / 1000 chars using ephemeral ChromaDB. 500 chars confirmed optimal.
- **Step 9**: prose evaluation summary with interpretation of all results, metric limitations, and suggested improvements (MRR, hybrid BM25+semantic search).

### Extension 1 — Video Analysis
- **Source data**: `data/raw/words/ES2008a.A/B/C/D.words.xml` — four AMI NXT speaker files. The notebook also accepts the path `data/raw/amicorpus/ES2008a/words/` and tries both with a fallback.
- **XML parsing**: `xml.etree.ElementTree`; strips `nite:` namespace prefix; skips `punc="true"` elements and vocalsound/nonvocalsound tokens. Extracts `starttime` and `endtime` floats (seconds) from each `<w>` element.
- **Segment detection**: Merges all four speakers sorted by `starttime`. Joins words into `full_text`. Applies `BOUNDARY_RE` regex (conversational cues: "moving on", "first of all", "Okay so", "our agenda", etc.) to find boundary positions. A `min_gap` of 5% total text length prevents over-segmentation. Merges down to ≤10 segments using smallest-gap rule.
- **Timestamps**: A `bisect`-based character-position-to-timestamp index (`_word_char_starts` / `_word_start_times` / `_word_end_times`) maps each segment boundary to the exact `starttime`/`endtime` of the word at that character position — no proportional estimation.
- **Segment descriptions**: `make_description(text)` calls `claude-haiku-4-5-20251001` via the `anthropic` SDK. The prompt instructs the model to write exactly 2-3 sentences covering (1) the main topic and (2) the activity type (brainstorming, Q&A, decision-making, etc.). The first 2000 characters of `seg_text` are passed as context; `max_tokens=120` caps output length. An `_aclient = _anthropic.Anthropic()` instance is created once per cell run and reads `ANTHROPIC_API_KEY` from the environment.
- **Output**: `video_df` DataFrame + `data/raw/video/ES2008a_video_analysis.json` (columns: `meeting_id`, `meeting_part`, `start_time`, `end_time`, `description`).
- **ChromaDB**: `video_segments` collection; per-document metadata includes `start_sec`, `end_sec`, `duration_sec` as floats for numeric filtering.
- **Time + Semantic Search**: `time_filtered_semantic_search()` — similarity ≥ 0.3 threshold (permissive; time window already narrows candidates).
- **Time-Based Aggregation**: `aggregate_topic_time()` — similarity ≥ 0.7 threshold (strict; counts only predominantly on-topic segments). Returns `total_minutes` and `segment_count`.
- **Production design** (in markdown cell): 0.25 FPS, ~375 frames, structured JSON VLM prompt, 4-GPU container, `/dev/shm` inter-process communication.

### API key usage policy
- **Do not use `ANTHROPIC_API_KEY` or call the Anthropic API unless the user explicitly asks.** Extension 1 cells are the only place in the notebook that call Claude; all other steps run fully locally.
- When a user asks for help with Extension 1, remind them the key is required and confirm they want to proceed before running any cells that invoke the API.
- Never log, print, or expose the key value in output.

### Windows-specific notes
- Tesseract path override: set `TESSERACT_CMD` in `.env` if the executable is not on PATH.
- The Edinburgh server requires `Connection: close` in request headers; keep-alive causes aborted connections on large file GETs.
- Extension 1 requires `ANTHROPIC_API_KEY` in the environment (or `.env`). Without it the `_anthropic.Anthropic()` constructor raises `AuthenticationError` before any segment is processed.
