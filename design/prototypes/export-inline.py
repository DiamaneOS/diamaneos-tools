#!/usr/bin/env python3
"""Extract the host-ready study from the standalone browser document."""

import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Destination HTML fragment")
    args = parser.parse_args()
    source = Path(__file__).with_name("diamaneos-ui.html")
    if args.output.resolve() == source.resolve():
        parser.error("The output must differ from the editable source")
    document = source.read_text(encoding="utf-8")
    start, end = "<!-- FACET-STUDY-START -->\n", "<!-- FACET-STUDY-END -->"
    if document.count(start) != 1 or document.count(end) != 1:
        raise ValueError("Expected one study boundary pair")
    fragment = document.split(start, 1)[1].split(end, 1)[0]
    args.output.write_text(fragment, encoding="utf-8")


if __name__ == "__main__":
    main()
