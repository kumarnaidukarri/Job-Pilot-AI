"""
Store the resume in MongoDB so GitHub Actions can use it without the PDF (which contains
phone/email) ever being committed to the repo.

  python tools/upload_resume.py [path/to/resume.pdf]     (default: config.yaml resume.path)
"""
import os
import sys

import pdfplumber
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from storage.database import get_resume, save_resume  # noqa: E402


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else yaml.safe_load(open("config.yaml"))["resume"]["path"]
    with open(path, "rb") as f:
        data = f.read()
    with pdfplumber.open(path) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    save_resume(data, text, os.path.basename(path))
    stored = get_resume()
    print(f"Uploaded {os.path.basename(path)}: {len(data)} bytes, {len(text)} chars of text "
          f"(stored text length check: {len(stored.get('text', ''))})")


if __name__ == "__main__":
    main()
