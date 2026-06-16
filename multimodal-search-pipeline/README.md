# Multimodal Search Pipeline

Semantic search across meeting audio and presentation slides — powered by local speech recognition, OCR, and vector embeddings. No cloud APIs required.

The pipeline transcribes a meeting recording, extracts text from slide images, embeds both into a shared 384-dimensional vector space, and exposes a unified search interface that retrieves relevant content from either modality in response to a natural-language query.

Sample data: meeting **ES2008a** from the [AMI Meeting Corpus](https://groups.inf.ed.ac.uk/ami/corpus/) — a 30-minute scenario session on product design, chosen for its manageable size and publicly available reference annotations.

---

## Architecture

```
┌───────────────────────────────────────────────────────────────────┐
│                     Multimodal Search Pipeline                    │
│                                                                   │
│  ┌──────────────┐   Whisper (ASR)    ┌─────────────────────────┐ │
│  │ ES2008a.wav  │ ─────────────────► │   transcript_text       │ │
│  └──────────────┘   whisper-base     │   (raw string)          │ │
│                                      └────────────┬────────────┘ │
│  ┌──────────────┐   Tesseract OCR                 │ chunk_text() │
│  │ *.slide.jpg  │ ─────────────────► ┌────────────▼────────────┐ │
│  │  (N slides)  │   --psm 1 layout   │   slides_df  +          │ │
│  └──────────────┘                    │   chunks_df             │ │
│                                      └────────────┬────────────┘ │
│  ┌──────────────┐   NXT XML parse                 │              │
│  │ *.words.xml  │ ─────────────────► ┌────────────▼────────────┐ │
│  │  (4 speakers)│   cue detection    │   video_df  (segments)  │ │
│  └──────────────┘                    └────────────┬────────────┘ │
│                                                   │              │
│                              sentence-transformers│              │
│                              all-MiniLM-L6-v2     │              │
│                              (384-d embeddings)   ▼              │
│                          ┌────────────────────────────────────┐  │
│                          │         ChromaDB (local)           │  │
│                          │  ┌──────────────────────────────┐  │  │
│                          │  │  collection: transcript_      │  │  │
│                          │  │  chunks  (cosine space)       │  │  │
│                          │  ├──────────────────────────────┤  │  │
│                          │  │  collection: slides_ocr       │  │  │
│                          │  ├──────────────────────────────┤  │  │
│                          │  │  collection: video_segments   │  │  │
│                          │  └──────────────────────────────┘  │  │
│                          └────────────────┬───────────────────┘  │
│                                           │                      │
│                          ┌────────────────▼───────────────────┐  │
│                          │   Cross-Modal + Video Search        │  │
│                          │  time-filter · semantic similarity  │  │
│                          │  topic-time aggregation             │  │
│                          └────────────────┬───────────────────┘  │
│                                           │                      │
│                          ┌────────────────▼───────────────────┐  │
│                          │   Multimodal RAG  (Extension 2)     │  │
│                          │  top-3 per collection, 2,000-token  │  │
│                          │  budget · Claude Haiku generates    │  │
│                          │  grounded answers + citations       │  │
│                          └────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────┘
```

---

## Tech Stack

| Component | Library / Tool | Role |
|---|---|---|
| Speech recognition | [OpenAI Whisper](https://github.com/openai/whisper) | Offline ASR — `whisper-base` default |
| OCR | [Tesseract](https://github.com/tesseract-ocr/tesseract) + pytesseract | Slide text extraction (`--psm 1` layout mode) |
| Embeddings | [sentence-transformers](https://www.sbert.net/) `all-MiniLM-L6-v2` | 384-d semantic vectors |
| Vector store | [ChromaDB](https://www.trychroma.com/) | Persistent local collections with cosine ANN |
| WER evaluation | [jiwer](https://github.com/jitsi/jiwer) | Word Error Rate vs. AMI reference transcript |
| Data wrangling | pandas, numpy | DataFrames for transcription and OCR results |
| Runtime | Python 3.10+ | Core pipeline runs locally — no API keys needed for Steps 1–9 |
| Topic segmentation | `xml.etree.ElementTree` + regex | AMI NXT words XML → timestamped topic segments (Extension 1) |
| Segment summarisation | [Anthropic](https://www.anthropic.com/) `claude-haiku-4-5-20251001` | 2-3 sentence descriptions for video segments — **Extension 1 only**, requires `ANTHROPIC_API_KEY` |
| Multimodal RAG | [Anthropic](https://www.anthropic.com/) `claude-haiku-4-5-20251001` | Grounded answer generation with inline source citations — **Extension 2 only**, requires `ANTHROPIC_API_KEY` |

---

## Project Structure

```
multimodal-search-pipeline/
├── notebook.ipynb          ← Main pipeline notebook (run top-to-bottom)
├── download_data.py        ← Downloads audio + transcript from AMI server
├── requirements.txt        ← Python dependencies
├── .env.example            ← Environment variable template
├── README.md
│
├── data/
│   └── raw/
│       ├── audio/          ← ES2008a.Mix-Headset.wav  [downloaded by download_data.py]
│       ├── slides/         ← ES2008a slide JPGs       [committed to repo]
│       ├── transcripts/    ← ES2008a.transcript.txt   [downloaded by download_data.py]
│       ├── words/          ← ES2008a.A/B/C/D.words.xml  [committed to repo]
│       └── video/          ← ES2008a_video_analysis.json  [auto-created by notebook]
│
└── chroma_db/              ← Persistent ChromaDB storage (auto-created by notebook)
                               Three collections: transcript_chunks · slides_ocr · video_segments
```

### Where the raw data lives

| Asset | Source | How to get it |
|---|---|---|
| `data/raw/audio/ES2008a.Mix-Headset.wav` | [AMI corpus mirror](https://groups.inf.ed.ac.uk/ami/AMICorpusMirror/amicorpus/ES2008a/audio/) | `python download_data.py` |
| `data/raw/slides/*.jpg` | Committed to this repo | Already present — no download needed |
| `data/raw/transcripts/ES2008a.transcript.txt` | Extracted from [AMI annotations zip](https://groups.inf.ed.ac.uk/ami/AMICorpusAnnotations/ami_public_manual_1.6.2.zip) | `python download_data.py` |
| `data/raw/words/ES2008a.{A,B,C,D}.words.xml` | Committed to this repo | Already present — no download needed |

---

## Setup

- Clone the repo
- Create a virtual environment:
```bash
   python -m venv venv
   source venv/Scripts/activate  # Windows Git Bash
   # or
   venv\Scripts\activate  # Windows PowerShell
   # or
   source venv/bin/activate  # Mac/Linux
```

### Prerequisites

- Python 3.10+
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract/releases) installed on the system
  - **Windows**: use the installer from the link above; default path `C:\Program Files\Tesseract-OCR\tesseract.exe`
  - **macOS**: `brew install tesseract`
  - **Linux**: `sudo apt install tesseract-ocr`
- ~4 GB free disk space (Whisper model weights + audio file)

### System Dependencies
- **ffmpeg** — required for audio processing by Whisper
  - **Windows (recommended):** `choco install ffmpeg` (automatically added to PATH)
    - Install Chocolatey first: [chocolatey.org/install](https://chocolatey.org/install)
  - **Windows (Anaconda):** open Anaconda Prompt and run `conda install -c conda-forge ffmpeg`
  - **Mac:** `brew install ffmpeg`
  - **Linux:** `sudo apt install ffmpeg`
- **Tesseract OCR** — required for slide text extraction
  - **Windows:** Download installer from [github.com/UB-Mannheim/tesseract/wiki](https://github.com/UB-Mannheim/tesseract/wiki)
  - **Mac:** `brew install tesseract`
  - **Linux:** `sudo apt install tesseract-ocr`

### Install Python dependencies

```bash
pip install -r requirements.txt
```

### Download the AMI corpus data

```bash
python download_data.py
```

This downloads two things from the Edinburgh AMI server:

- **Audio** — `ES2008a.Mix-Headset.wav` (~32 MB) from the AMI corpus mirror
- **Transcript** — word-level annotations extracted from `ami_public_manual_1.6.2.zip` (~22 MB), saved as a plain-text file

Slide JPGs are already committed to the repo under `data/raw/slides/` and require no download.

### Run the notebook
```bash
source venv/Scripts/activate  # Windows Git Bash
jupyter notebook notebook.ipynb
```

Execute cells top-to-bottom. Each step builds on the previous one. The only interactive action required is replacing `"REPLACE WITH YOUR QUERY"` in the three search cells (Steps 5–7) with actual questions about the meeting content.

---

## Example Search Results

A cross-modal query for `"what does the remote control need to look like"` after running the full pipeline:

```
Cross-modal query: 'what does the remote control need to look like'

Rank  1 | [SLIDE] | ES2008a.82.68__96.34    | sim=0.5576
  Project Aim
  • New remote control — Original — Trendy — User friendly

Rank  2 | [AUDIO] | ES2008_a_15             | sim=0.5085
  … If you're going to make a remote control, it should actually work
  for what it's doing. We could use a lithium battery — that would
  last a lot longer than double A's …

Rank  3 | [AUDIO] | ES2008_a_14             | sim=0.5082
  … What's important for me is that it's easy to use. Not too many
  buttons, not too small. I need to know what you're doing …

Rank  4 | [SLIDE] | ES2008a.509.35__907.72  | sim=0.4977
  Discussion — Experience with remote control — First ideas new remote
```

---

## Evaluation Summary

### Transcription Quality — Word Error Rate

| Model | WER | Transcription Time |
|---|---|---|
| whisper-tiny | 0.5557 | 35 s |
| whisper-base | 0.5256 | 68 s |
| whisper-small | 0.4917 | 189 s |

*Measured on CPU against the AMI NXT reference transcript using `jiwer`. WER is high across all models because ES2008a is spontaneous multi-speaker speech — this reflects corpus difficulty, not model failure. Spontaneous meetings are considered well-transcribed at 20–30% WER. `whisper-base` is the default: it cuts `tiny`'s error by 3 points at under twice the runtime.*

### Chunk Size Ablation

| Chunk Size | Num Chunks | Precision@5 | Search Latency |
|---|---|---|---|
| 250 chars | 52 | 0.00 | 1.2 ms |
| 500 chars | 26 | 0.48 | 1.3 ms |
| 1000 chars | 13 | 0.00 | 1.3 ms |

Search latency is flat across all sizes — the corpus is too small for chunk count to affect ChromaDB's HNSW index. The chunk size tradeoff is entirely about retrieval quality: 250 chars fragments context, 1000 chars dilutes topic specificity, 500 chars is the confirmed optimum for this meeting.

---

## Reproducing Results

1. Clone the repo and install dependencies (`pip install -r requirements.txt`).
2. Run `python download_data.py` to fetch the corpus.
3. Open `notebook.ipynb` and run all cells in order.
4. Replace `"REPLACE WITH YOUR QUERY"` in Steps 5–7 with your own questions.
5. The `test_set` in Part B is pre-filled with five labelled queries. Add your own to expand coverage. Part B2 evaluates slide retrieval using Precision@1 with three slide-specific queries.

---

## Extension 1 — Video Analysis

Extension 1 adds a third ChromaDB collection (`video_segments`) and two new search modes.

### How it works

The four AMI NXT words XML files (`ES2008a.A/B/C/D.words.xml`) provide word-level `starttime` and `endtime` in seconds for all speakers. The notebook:

1. Parses all four files with `xml.etree.ElementTree`, skips punctuation and non-lexical tokens, and sorts all words chronologically into a single merged transcript.
2. Searches the joined text for conversational cue phrases ("moving on", "first of all", "Okay so", etc.) to detect 5–10 topic boundaries.
3. Maps each boundary character position back to its word's exact timestamp using a binary-search index — no proportional estimation.
4. Builds a `video_df` DataFrame and saves `data/raw/video/ES2008a_video_analysis.json`.
5. Embeds segment descriptions with `all-MiniLM-L6-v2` into the `video_segments` ChromaDB collection.

### New search capabilities

| Mode | Description | Threshold |
|---|---|---|
| **Time + Semantic Search** | Segments overlapping a timestamp window **and** meeting a similarity floor | 0.3 |
| **Time-Based Aggregation** | Sum total duration of segments above a strict similarity threshold | 0.7 |

The lower threshold (0.3) for time-filtered search is intentional: the timestamp window already narrows candidates, so a permissive floor avoids missing loosely relevant segments. The higher threshold (0.7) for aggregation counts only time spent *predominantly* on a topic.

### API key requirement

Extension 1 calls the Anthropic API to generate segment descriptions. Add your key to a `.env` file (never commit it):

```
ANTHROPIC_API_KEY=sk-ant-...
```

The key is only read in the Extension 1 cells. Steps 1–9 of the main pipeline run entirely locally and do not require it.

### Production design (documented in notebook)

A production implementation would sample the meeting video at **0.25 FPS** (~375 frames) and submit each frame to a VLM with a structured JSON prompt. The job runs as a **4-GPU container**, reading video from cloud storage and writing to a database table.

---

## Extension 2 — Multimodal RAG

Extension 2 adds retrieval-augmented generation on top of the three existing ChromaDB collections, producing grounded natural-language answers with traceable source citations. It builds directly on the collections populated in Steps 4–5 and Extension 1 — no re-indexing required.

### How it works

`multimodal_rag()` (cells 37–38) runs in three stages:

1. **Retrieve** — queries `transcript_chunks`, `slides_ocr`, and `video_segments` for the top 3 results each, giving up to 9 candidates per call.
2. **Rank and budget** — sorts all candidates by cosine similarity and fills a context block greedily until the 2,000-token ceiling (~8,000 characters) is reached. Each snippet is labelled with its type, ChromaDB ID, and similarity score. The last snippet is trimmed to fit rather than dropped, so the budget is never wasted.
3. **Generate** — calls `claude-haiku-4-5-20251001` with a system prompt that restricts the model to the retrieved context only and requires inline citations matching ChromaDB IDs.

### Citation format

Every claim in the generated answer is attributed to a specific retrieved document:

```
[TRANSCRIPT | ES2008_a_14]   ← transcript chunk
[SLIDE | ES2008a.82.68__96.34]   ← slide OCR result
[VIDEO | ES2008a_vseg_02]   ← video segment
```

### API key requirement

Extension 2 uses the same `ANTHROPIC_API_KEY` as Extension 1. Add it to `.env` — it is read automatically. Steps 1–9 and the base search cells run entirely without it.

### Limitations

| Limitation | Detail |
|---|---|
| **Temporal comparison queries** | Transcript chunks carry no timestamps (stripped during chunking); the LLM cannot reliably calculate durations from 3 retrieved video segments; and technical vs. aesthetic topics are interleaved throughout the meeting rather than in distinct blocks. For *how long* queries, run `aggregate_topic_time()` per topic first (Extension 1), then pass the structured `{topic, total_minutes}` results as context into `multimodal_rag()` for synthesis. |
| **Single-turn only** | Each `multimodal_rag()` call is stateless — no conversation history. Follow-up questions cannot refer to a previous answer. |
| **No re-ranking** | Candidates are ranked by bi-encoder cosine similarity only. A cross-encoder re-ranker (e.g. `ms-marco-MiniLM-L-6-v2`) would improve precision on longer or more complex queries. |
| **Small corpus** | One 25-minute session limits contrastive retrieval — every query returns something semantically close simply because there is little else in the index. |
