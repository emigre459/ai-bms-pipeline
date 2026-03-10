#!/usr/bin/env python3
"""Convert a PDF in references/ to a Markdown file in the same directory."""

import sys
from pathlib import Path

import pymupdf4llm

DEFAULT_PDF = (
    Path(__file__).resolve().parent.parent
    / "references"
    / "AI Engineer Take-Home Exercise.pdf"
)


def main() -> None:
    pdf_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PDF
    if not pdf_path.is_file():
        print(f"Error: not a file: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    md_path = pdf_path.with_suffix(".md")
    md_text = pymupdf4llm.to_markdown(str(pdf_path))
    md_path.write_text(md_text, encoding="utf-8")
    print(f"Wrote {md_path}")


if __name__ == "__main__":
    main()
