#!/usr/bin/env python3
"""Split a narration text at paragraph boundaries, synthesize each part with
tts.py (OpenRouter, Fish Audio S2.1, Japanese female narrator by default),
and concatenate the parts into one MP3 with ffmpeg.

Usage:
    python make_audio.py --tts /path/to/tts.py --text report_narration.txt \
        --out ../audio/report.mp3 [--max-chars 1500]

Requires OPENROUTER_API_KEY in the environment (tts.py reads it) and ffmpeg.
Parts that already exist are reused, so a failed run can be resumed.
"""

import argparse
import subprocess
import sys
from pathlib import Path


def split_paragraphs(text: str, max_chars: int) -> list[str]:
    parts, current = [], ""
    for para in [p.strip() for p in text.split("\n\n") if p.strip()]:
        if len(para) > max_chars:
            # a single long paragraph: split at sentence ends
            sentences = [s + "。" for s in para.split("。") if s.strip()]
        else:
            sentences = [para]
        for chunk in sentences:
            if current and len(current) + len(chunk) + 2 > max_chars:
                parts.append(current)
                current = ""
            current = f"{current}\n\n{chunk}" if current else chunk
    if current:
        parts.append(current)
    return parts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tts", required=True, help="path to tts.py")
    ap.add_argument("--text", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-chars", type=int, default=1500)
    args = ap.parse_args()

    text = Path(args.text).read_text(encoding="utf-8")
    out = Path(args.out)
    parts_dir = out.parent / f"{out.stem}_parts"
    parts_dir.mkdir(parents=True, exist_ok=True)

    parts = split_paragraphs(text, args.max_chars)
    print(f"{len(parts)} parts from {len(text)} chars")
    files = []
    for i, part in enumerate(parts, 1):
        txt = parts_dir / f"part{i:02d}.txt"
        mp3 = parts_dir / f"part{i:02d}.mp3"
        txt.write_text(part, encoding="utf-8")
        if not (mp3.exists() and mp3.stat().st_size > 0):
            print(f"[{i}/{len(parts)}] {len(part)} chars")
            rc = subprocess.call([sys.executable, args.tts, "--file", str(txt), "-o", str(mp3)])
            if rc != 0:
                print(f"part {i} failed (exit {rc}); rerun to resume", file=sys.stderr)
                return rc
        files.append(mp3)

    listing = parts_dir / "concat.txt"
    listing.write_text("".join(f"file '{f.resolve()}'\n" for f in files), encoding="utf-8")
    # re-encode so part boundaries join cleanly
    subprocess.check_call(["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                           "-i", str(listing), "-c:a", "libmp3lame", "-b:a", "128k", str(out)])
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
