"""Child process entry point for sandbox.py: parse one file, print it as one JSON line.

    python -m app.ingest.parse_child <path> <torch device> <max pages>

Imports only the parser (no settings, database or network clients), so nothing secret is
loaded into the process that touches untrusted bytes.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path


def main() -> int:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    path, device, max_pages = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
    from . import parse

    parse.set_docling_device(device)
    parse.DOCLING_MAX_PAGES = max_pages
    try:
        document = parse.parse_file(path)
    except parse.ParseError as exc:
        print(json.dumps({"error": str(exc)}))
        return 0
    except MemoryError:
        print(json.dumps({"error": "parser ran out of memory (PARSE_MEMORY_MB)"}))
        return 0
    print(
        json.dumps(
            {
                "title": document.title,
                "mime_type": document.mime_type,
                "sections": [[s.heading, s.text] for s in document.sections],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
