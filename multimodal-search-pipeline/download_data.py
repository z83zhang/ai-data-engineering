"""
download_data.py
Downloads AMI Meeting Corpus ES2008a files into data/raw/.

Sources
-------
  Audio      : https://groups.inf.ed.ac.uk/ami/AMICorpusMirror/amicorpus/ES2008a/audio/
  Slides     : included in the repo under data/raw/slides/ (no download needed)
  Transcript : https://groups.inf.ed.ac.uk/ami/AMICorpusAnnotations/ami_public_manual_1.6.2.zip

Files downloaded
----------------
  Audio      : amicorpus/ES2008a/audio/ES2008a.Mix-Headset.wav
  Transcript : ES2008a *.words.xml extracted from annotations zip

Usage
-----
    python download_data.py
"""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import requests
from tqdm import tqdm

BASE_URL = "https://groups.inf.ed.ac.uk/ami/AMICorpusMirror"
ANN_BASE = "https://groups.inf.ed.ac.uk/ami/AMICorpusAnnotations"
MEETING  = "ES2008a"

DATA_DIR       = Path("data/raw")
AUDIO_DIR      = DATA_DIR / "audio"
SLIDES_DIR     = DATA_DIR / "slides"
TRANSCRIPT_DIR = DATA_DIR / "transcripts"

for _d in [AUDIO_DIR, SLIDES_DIR, TRANSCRIPT_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; multimodal-search-pipeline/1.0)",
    "Accept": "*/*",
    "Connection": "close",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def download_file(url: str, dest: Path) -> bool:
    """Stream-download url to dest with a tqdm progress bar.
    Falls back to urllib if requests drops the connection."""
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  [skip] {dest.name}")
        return True

    # --- attempt 1: requests ---
    try:
        with requests.Session() as s:
            s.headers.update(HEADERS)
            r = s.get(url, stream=True, timeout=120)
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            with open(dest, "wb") as fh, tqdm(
                total=total, unit="B", unit_scale=True,
                desc=f"  {dest.name}", leave=False,
            ) as bar:
                for chunk in r.iter_content(65_536):
                    fh.write(chunk)
                    bar.update(len(chunk))
        size_mb = dest.stat().st_size / 1_048_576
        print(f"  [ok]   {dest.name}  ({size_mb:.1f} MB)")
        return True
    except Exception as e:
        print(f"  [warn] requests failed ({e}), retrying with urllib …")
        if dest.exists():
            dest.unlink()

    # --- attempt 2: urllib (handles RemoteDisconnected better) ---
    try:
        import urllib.request
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=300) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            with open(dest, "wb") as fh, tqdm(
                total=total, unit="B", unit_scale=True,
                desc=f"  {dest.name}", leave=False,
            ) as bar:
                while True:
                    chunk = resp.read(65_536)
                    if not chunk:
                        break
                    fh.write(chunk)
                    bar.update(len(chunk))
        size_mb = dest.stat().st_size / 1_048_576
        print(f"  [ok]   {dest.name}  ({size_mb:.1f} MB)")
        return True
    except Exception as e:
        print(f"  [err]  {url}  ->  {e}")
        if dest.exists():
            dest.unlink()
        return False


# ── Audio ─────────────────────────────────────────────────────────────────────

def download_audio() -> bool:
    print("\n=== Audio ===")
    url  = f"{BASE_URL}/amicorpus/{MEETING}/audio/{MEETING}.Mix-Headset.wav"
    dest = AUDIO_DIR / f"{MEETING}.Mix-Headset.wav"
    return download_file(url, dest)


# ── Slides ────────────────────────────────────────────────────────────────────

def check_slides() -> int:
    print("\n=== Slides ===")
    existing = list(SLIDES_DIR.glob("*.jpg"))
    print(f"  Slides are included in the repo under data/raw/slides/ — no download needed.")
    print(f"  Found {len(existing)} slide(s) on disk.")
    return len(existing)


# ── Transcript ────────────────────────────────────────────────────────────────

def download_transcript() -> bool:
    """
    Download ami_public_manual_1.6.2.zip from AMICorpusAnnotations,
    extract ES2008a *.words.xml files, combine into a single plain-text
    file sorted by word start-time.
    """
    import zipfile
    import io

    print("\n=== Reference Transcript ===")
    dest = TRANSCRIPT_DIR / f"{MEETING}.transcript.txt"
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  [skip] {dest.name}")
        return True

    zip_url = f"{ANN_BASE}/ami_public_manual_1.6.2.zip"
    print(f"  Downloading annotations zip (~22 MB) …")

    try:
        with requests.Session() as s:
            s.headers.update(HEADERS)
            r = s.get(zip_url, stream=True, timeout=120)
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            buf = bytearray()
            with tqdm(total=total, unit="B", unit_scale=True,
                      desc="  annotations.zip", leave=False) as bar:
                for chunk in r.iter_content(65_536):
                    buf.extend(chunk)
                    bar.update(len(chunk))
    except Exception as e:
        print(f"  [err] Could not download annotations zip: {e}")
        return False

    timed_words: list[tuple[float, str]] = []
    try:
        with zipfile.ZipFile(io.BytesIO(bytes(buf))) as zf:
            words_entries = [
                n for n in zf.namelist()
                if MEETING in n and n.endswith(".words.xml")
            ]
            if not words_entries:
                print(f"  [err] No {MEETING} words.xml found inside zip")
                return False
            for entry in words_entries:
                with zf.open(entry) as fh:
                    try:
                        tree = ET.parse(fh)
                        for elem in tree.iter():
                            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                            if tag == "w" and elem.text:
                                word = elem.text.strip()
                                if word and word not in {
                                    "<vocalsound>", "<nonvocalsound>",
                                    "IGNORE_TIME_SEGMENT_IN_SCORING",
                                }:
                                    t = float(elem.get("starttime", 0))
                                    timed_words.append((t, word))
                    except ET.ParseError as exc:
                        print(f"  [warn] XML parse error in {entry}: {exc}")
    except zipfile.BadZipFile as e:
        print(f"  [err] Bad zip file: {e}")
        return False

    if not timed_words:
        print("  [err] No transcript words extracted.")
        return False

    timed_words.sort(key=lambda x: x[0])
    text = " ".join(w for _, w in timed_words)
    dest.write_text(text, encoding="utf-8")
    print(f"  [ok]   {dest.name}  ({len(text.split()):,} words)")
    return True


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"AMI Meeting Corpus -- {MEETING} Download")
    print("=" * 50)

    audio_ok = download_audio()
    n_slides = check_slides()
    trans_ok = download_transcript()

    print("\n=== Summary ===")
    print(f"  Audio            : {len(list(AUDIO_DIR.glob('*.wav')))}")
    print(f"  Slides           : {len(list(SLIDES_DIR.glob('*.jpg')))}")
    print(f"  Transcript files : {len(list(TRANSCRIPT_DIR.glob('*.txt')))}")

    if audio_ok and n_slides > 0 and trans_ok:
        print("\nData ready.  Run:  jupyter notebook notebook.ipynb")
    else:
        print("\nOne or more files missing -- check errors above.")
        sys.exit(1)
