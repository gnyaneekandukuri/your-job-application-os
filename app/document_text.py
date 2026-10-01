"""Local, bounded text extraction. Never execute macros, links, or PDF scripts."""
import io
import json
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from .retrieval import RetrievalError

MAX_FILE_BYTES = 2_000_000
EXTRACTOR_VERSION = "document-text-v1"


def extract_document(filename: str, raw: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in (".docx", ".pdf", ".txt", ".md"):
        raise RetrievalError("Use a Word .docx, text-based PDF, UTF-8 .txt or .md file.")
    if not raw or len(raw) > MAX_FILE_BYTES:
        raise RetrievalError("Upload a nonempty file no larger than 2 MB.")
    try:
        if suffix in (".txt", ".md"):
            text = raw.decode("utf-8-sig")
        elif suffix == ".docx":
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                infos = archive.infolist()
                if len(infos) > 1000 or sum(i.file_size for i in infos) > 8_000_000:
                    raise RetrievalError("Word document expands beyond the safe size limit.")
                xml = archive.read("word/document.xml")
                if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
                    raise RetrievalError("Unsupported XML declarations in Word document.")
                document = ET.fromstring(xml)
                ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                # Reject ambiguous revisions instead of accepting them silently.
                if document.findall(".//w:ins", ns) or document.findall(".//w:del", ns):
                    raise RetrievalError("Resolve tracked changes in Word before uploading.")
                paragraphs = []
                for paragraph in document.findall(".//w:body//w:p", ns):
                    parts = []
                    for node in paragraph.iter():
                        if node.tag == "{" + ns["w"] + "}t":
                            parts.append(node.text or "")
                        elif node.tag == "{" + ns["w"] + "}tab":
                            parts.append("\t")
                        elif node.tag == "{" + ns["w"] + "}br":
                            parts.append("\n")
                    line = "".join(parts)
                    style = paragraph.find("w:pPr/w:pStyle", ns)
                    if style is not None and "heading" in style.get("{" + ns["w"] + "}val", "").lower():
                        line = "## " + line
                    if line.strip():
                        paragraphs.append(line)
                text = "\n\n".join(paragraphs)
        else:
            # Bound parser runtime; use stdin, not user-controlled file paths/commands.
            result = subprocess.run([sys.executable, "-m", "app.pdf_worker"], input=raw,
                                    capture_output=True, timeout=25, cwd=Path(__file__).resolve().parents[1],
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode:
                raise RetrievalError("PDF could not be read. Use an unlocked, text-based PDF (maximum 40 pages).")
            value = json.loads(result.stdout)
            if value.get("error"):
                raise RetrievalError(value["error"])
            text = value["text"]
    except RetrievalError:
        raise
    except (ValueError, KeyError, OSError, ET.ParseError, zipfile.BadZipFile, RuntimeError, subprocess.TimeoutExpired):
        raise RetrievalError("Document extraction failed. Export a clean text-based document and try again.") from None
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(text) < 20 or "\x00" in text:
        raise RetrievalError("Not enough readable text. Scanned/image-only files need OCR first.")
    if len(text.encode("utf-8")) > 64000:
        raise RetrievalError("Extracted text exceeds 64 KB. Split the document before uploading.")
    return text
