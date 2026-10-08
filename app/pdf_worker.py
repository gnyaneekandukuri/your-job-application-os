"""Subprocess PDF text parser, isolated from the web request's execution thread."""
import io
import json
import sys

from pypdf import PdfReader


def main():
    raw = sys.stdin.buffer.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("size")
    if "--resume-template" in sys.argv:
        from .resume_master import pdf_template
        print(json.dumps({"blocks":pdf_template(raw)}))
        return
    reader = PdfReader(io.BytesIO(raw))
    if reader.is_encrypted or len(reader.pages) > 40:
        raise ValueError("unsupported PDF")
    parts = []
    for number, page in enumerate(reader.pages, 1):
        content = page.get_contents()
        if content and len(content.get_data()) > 4_000_000:
            raise ValueError("page too large")
        text = page.extract_text() or ""
        if len(text.strip()) < 10:
            print(json.dumps({"error": "A PDF page has no readable text. Use OCR or upload the original Word file."}))
            return
        parts.append(f"## Page {number}\n\n{text}")
        if sum(len(p.encode("utf-8")) for p in parts) > 64000:
            raise ValueError("text too long")
    print(json.dumps({"text": "\n\n".join(parts)}))


if __name__ == "__main__":
    main()
