> "Build a multimodal search pipeline project in this folder from scratch. The pipeline processes meeting recordings and slide images to enable semantic search across both modalities. Use the AMI Meeting Corpus (meeting ES2008, part a only) as sample data to keep processing time manageable.
>
> **Pipeline steps:**
>
> 1. **Audio Transcription** — Transcribe ES2008a WAV file using Whisper. Store meeting_id, meeting_part, audio_file_path, transcript_text, and audio_duration in a pandas DataFrame. Preview the first 100 characters of the transcript. Use `whisper-base` as the default model.
>
> 2. **Slide OCR** — Extract text from ES2008a slide JPG images using Tesseract OCR in LAYOUT mode. Store meeting_id, meeting_part, slide_filename, slide_file_path, and extracted_text. Preview first 100 characters per slide.
>
> 3. **Chunking** — Split the transcript into overlapping chunks with `CHUNK_SIZE = 500` characters and `OVERLAP = 50` characters. Assign each chunk a unique ID formatted as `{meeting_id}_{part}_{chunk_index}`. Show chunk statistics: count and average chunk length. Print the full text of 10 random chunks with their chunk_id.
>
> 4. **Embeddings** — Generate 384-dimensional vector embeddings using `sentence-transformers` with model `all-MiniLM-L6-v2`. Embed both transcript chunks and slide OCR text. Store in ChromaDB in two separate collections: `transcript_chunks` and `slides_ocr`. Add a markdown cell explaining why `all-MiniLM-L6-v2` was chosen (speed, 384 dimensions, good general quality) and note that `all-mpnet-base-v2` is a drop-in upgrade for better accuracy in production — the only change needed is the model name string.
>
> 5. **Semantic Search: Audio** — Search transcript chunks by meaning using cosine similarity. Use a placeholder test query `"REPLACE WITH YOUR QUERY"`. Return top 5 results with chunk_id, meeting_part, similarity score, and 500-character preview.
>
> 6. **Semantic Search: Slides** — Search slide OCR text by meaning. Use a placeholder test query `"REPLACE WITH YOUR QUERY"`. Return top 5 results with slide_filename, meeting_part, similarity score, and 500-character preview.
>
> 7. **Cross-Modal Search** — Search across both audio chunks and slides simultaneously with a single query. Use a placeholder test query `"REPLACE WITH YOUR QUERY"`. Label each result with content_type (AUDIO or SLIDE), and rank all results together by similarity score.
>
> 8. **Evaluation** — Add a comprehensive evaluation section structured as follows:
>
>    **Part A — Transcription Quality (WER)**
>    - Download the AMI reference transcript for ES2008a and compare Whisper output against it using Word Error Rate via the `jiwer` library
>    - Run and compare `whisper-tiny`, `whisper-base`, and `whisper-small`
>    - Present a summary table: Model | WER | Transcription Time
>
>    **Part B — Search Quality (Precision@5)**
>    - Set up the evaluation framework with a placeholder `test_set` list structured like this:
>      ```python
>      test_set = [
>          {"query": "", "relevant_chunks": []},
>          {"query": "", "relevant_chunks": []},
>          {"query": "", "relevant_chunks": []},
>          {"query": "", "relevant_chunks": []},
>          {"query": "", "relevant_chunks": []},
>      ]
>      ```
>    - Implement the `precision_at_5` function:
>      ```python
>      def precision_at_5(retrieved_chunks, relevant_chunks):
>          retrieved_top5 = [r["chunk_id"] for r in retrieved_chunks[:5]]
>          hits = len(set(retrieved_top5) & set(relevant_chunks))
>          return hits / 5
>      ```
>    - Calculate and display Precision@5 for each query once the test set is filled in
>
>    **Part C — Pipeline Timing**
>    - Time each pipeline step (transcription, OCR, embedding, search) using Python's `time` module
>    - Display a summary table: Step | Time (seconds)
>
>    **Part D — Chunk Size Experiment**
>    - Re-run chunking and search with chunk sizes 250, 500, and 1000 characters
>    - For each chunk size, measure search latency and Precision@5
>    - Display a summary table: Chunk Size | Precision@5 | Search Latency (ms)
>
> **Deliverables:**
>
> - `notebook.ipynb` — well-documented Jupyter notebook with descriptive markdown cells explaining the *why* behind each step, written in first person as my own original project
>
> - `download_data.py` — Downloads AMI Meeting Corpus ES2008a files into `data/raw/` using `requests` and `tqdm`. Creates all destination folders automatically. **Never generates synthetic data** — if any download fails, the script exits immediately with the exact URL and HTTP error.
>   - **Audio:** `https://groups.inf.ed.ac.uk/ami/AMICorpusMirror/amicorpus/ES2008a/audio/ES2008a.Mix-Headset.wav` → `data/raw/audio/ES2008a.Mix-Headset.wav`. Uses a `urllib` fallback if `requests` drops the connection on large files.
>   - **Slides:** The slides directory returns an HTML frameset, not a plain listing. The script uses a two-strategy approach: (1) fetch `index.html` then `slides.html` inside `https://groups.inf.ed.ac.uk/ami/AMICorpusMirror/amicorpus/ES2008a/slides/` and parse all `.jpg`/`.JPG` hrefs; (2) if no index page yields links, probe sequential filenames `ES2008a.slide-0001.jpg`, `ES2008a.slide-0002.jpg`, … with HEAD requests until a 404. Downloads all found JPGs to `data/raw/slides/`. If neither strategy finds any slides, prints the exact URLs tried and aborts — **never generates synthetic slides**.
>   - **Reference transcript:** Downloads `https://groups.inf.ed.ac.uk/ami/AMICorpusAnnotations/ami_public_manual_1.6.2.zip` (~22 MB), extracts the per-speaker `ES2008a.*.words.xml` files in-memory, parses word start-times, sorts chronologically, and writes a single plain-text file to `data/raw/transcripts/ES2008a.transcript.txt`.
>
> - `requirements.txt` — all dependencies including `jiwer`, `requests`, and `tqdm`
>
> - `README.md` — professional portfolio README with project overview, architecture diagram (ASCII is fine), tech stack, setup instructions including a step to run `python download_data.py` before running the notebook, and example search results including WER comparison table
>
> - `.env.example` — template for any environment variables
>
> **Tech stack:** Python, Whisper, Tesseract, sentence-transformers (all-MiniLM-L6-v2), ChromaDB, pandas, jiwer
>
> Add Extension 1 — Video Analysis to the notebook after the evaluation summary. Do not modify any existing cells.
>
> 1. Add a markdown cell that clearly distinguishes two things:
>
>    **Production VLM Design Specification** — document what a real implementation would look like:
>    - FPS=0.25 (1 frame every 4 seconds) generating ~375 frames for a 25-minute meeting, balancing coverage of meaningful visual changes against redundancy of near-identical consecutive frames
>    - Prompt engineering for structured JSON output: 'Analyze this meeting video and identify 5-10 major segments or phases. For each segment, describe what is happening visually and what topic is being discussed. Provide 2-3 sentences of detail per segment. Return as a JSON array with objects containing start_time (hh:mm:ss), end_time (hh:mm:ss), and description fields'
>    - Similarity threshold of 0.3 for time-filtered search and 0.7 for time-based aggregation, with explanation of why these thresholds differ
>    - Production deployment: 4 GPU container job, shared memory volume for GPU inter-process communication, video read from cloud storage, results written to database table
>
>    **What this notebook actually does** — be transparent:
>    - Instead of running a VLM, topic segmentation is derived from AMI word-level timestamp annotations (ES2008a.A/B/C/D.words.xml files in NXT XML format) combined with the transcript text
>    - The XML files provide accurate word-level start and end times in seconds for all four speakers, enabling precise segment timestamps rather than approximations
>    - Topic boundaries are detected by combining timestamp data with conversational cue detection in the transcript
>    - Frame it as: 'In production this would be implemented as a VLM container job. For this portfolio demonstration, I derive accurate topic segments from AMI word-level timestamp annotations as a transparent and realistic substitute'
>
> 2. Parse all four AMI words XML files from `data/raw/amicorpus/ES2008a/words/` (ES2008a.A.words.xml, ES2008a.B.words.xml, ES2008a.C.words.xml, ES2008a.D.words.xml) using Python's `xml.etree.ElementTree`. Extract all `<w>` elements with their `starttime`, `endtime`, and text content. Merge words from all four speakers sorted by starttime to reconstruct the full timestamped transcript.
>
> 3. Detect 5-10 topic boundaries using conversational cues in the merged transcript (e.g. 'moving on', 'okay', 'first of all', 'next'). Use the word timestamps to assign accurate start_time and end_time in hh:mm:ss format to each segment. Write a 2-3 sentence description for each segment summarizing the main topic and activity. Store in a DataFrame with columns: meeting_id, meeting_part, start_time, end_time, description.
>
> 4. Add segment duration analysis showing: segment count, average segment duration in seconds, and total duration covered. Display description previews at 100 characters.
>
> 5. Save to `data/raw/video/ES2008a_video_analysis.json`
>
> 6. Embed descriptions using `all-MiniLM-L6-v2` and store in ChromaDB as a third collection `video_segments`
>
> 7. Add Time + Semantic Search: filter video segments by timestamp range AND semantic similarity threshold of 0.3. Example query: 'find segments about project goals in the first 5 minutes'
>
> 8. Add Time-Based Aggregation: sum total duration of segments above similarity threshold 0.7 for a given query. Return total minutes spent on that topic and segment count.
>
> After implementing, review and update README and CLAUDE.md accordingly.