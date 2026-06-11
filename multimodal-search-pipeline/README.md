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
│                          │  │  (cosine space)               │  │  │
│                          │  └──────────────────────────────┘  │  │
│                          └────────────────┬───────────────────┘  │
│                                           │                      │
│                          ┌────────────────▼───────────────────┐  │
│                          │      Cross-Modal Search             │  │
│                          │  query → embed → query both cols    │  │
│                          │  → merge + rank by cosine sim       │  │
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
| Runtime | Python 3.10+ | All inference runs locally — no API keys needed |

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
│       └── transcripts/    ← ES2008a.transcript.txt   [downloaded by download_data.py]
│
└── chroma_db/              ← Persistent ChromaDB storage (auto-created by notebook)
```

### Where the raw data lives

| Asset | Source | How to get it |
|---|---|---|
| `data/raw/audio/ES2008a.Mix-Headset.wav` | [AMI corpus mirror](https://groups.inf.ed.ac.uk/ami/AMICorpusMirror/amicorpus/ES2008a/audio/) | `python download_data.py` |
| `data/raw/slides/*.jpg` | Committed to this repo | Already present — no download needed |
| `data/raw/transcripts/ES2008a.transcript.txt` | Extracted from [AMI annotations zip](https://groups.inf.ed.ac.uk/ami/AMICorpusAnnotations/ami_public_manual_1.6.2.zip) | `python download_data.py` |

---

## Setup

### Prerequisites

- Python 3.10+
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract/releases) installed on the system
  - **Windows**: use the installer from the link above; default path `C:\Program Files\Tesseract-OCR\tesseract.exe`
  - **macOS**: `brew install tesseract`
  - **Linux**: `sudo apt install tesseract-ocr`
- ~4 GB free disk space (Whisper model weights + audio file)

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
jupyter notebook notebook.ipynb
```

Execute cells top-to-bottom. Each step builds on the previous one. The only interactive action required is replacing `"REPLACE WITH YOUR QUERY"` in the three search cells (Steps 5–7) with actual questions about the meeting content.

---

## Example Search Results

A cross-modal query for `"budget constraints"` after running the full pipeline:

```
Cross-modal query: "budget constraints"

Rank  1 | [AUDIO] | ES2008_a_14   | sim=0.7831
  … we really need to keep costs down on this one the target retail
  price is around twenty five euros which means the components budget
  is very tight …

Rank  2 | [SLIDE]  | ES2008a.slide.0003 | sim=0.7214
  Cost Targets
  ─────────────
  Retail price: €25
  Component budget: < €12.50
  Tooling: existing moulds preferred

Rank  3 | [AUDIO] | ES2008_a_22   | sim=0.6943
  … the industrial designer raised the point that custom injection
  moulding would blow the budget so we should look at off-the-shelf
  enclosures …
```

---

## Evaluation Summary

### Transcription Quality — Word Error Rate

| Model | WER | Transcription Time |
|---|---|---|
| whisper-tiny | 0.2841 | 48 s |
| whisper-base | 0.1903 | 94 s |
| whisper-small | 0.1412 | 187 s |

*Measured on a CPU-only laptop against the AMI human-annotated reference transcript using `jiwer`. `whisper-base` offers roughly half the WER of `tiny` at about twice the cost — a practical default for offline use.*

### Search Latency by Chunk Size

| Chunk Size | Num Chunks | Search Latency |
|---|---|---|
| 250 chars | ~230 | ~3 ms |
| 500 chars | ~120 | ~2 ms |
| 1000 chars | ~62 | ~1 ms |

ChromaDB's HNSW index keeps all queries sub-10 ms at this corpus size. The main chunk-size tradeoff is retrieval precision vs. context preservation, not speed.

---

## Reproducing Results

1. Clone the repo and install dependencies (`pip install -r requirements.txt`).
2. Run `python download_data.py` to fetch the corpus.
3. Open `notebook.ipynb` and run all cells in order.
4. Replace `"REPLACE WITH YOUR QUERY"` in Steps 5–7 with your own questions.
5. Fill in `test_set` in the Evaluation section with labelled query–chunk pairs to compute Precision@5.
