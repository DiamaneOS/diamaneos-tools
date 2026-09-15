#!/usr/bin/env python3
"""Serve the design references locally with explicit UTF-8 text encoding."""

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class DesignHandler(SimpleHTTPRequestHandler):
    def send_head(self):
        # A cached response from an older preview server may omit the charset.
        # Return fresh headers and bytes even when the browser revalidates it.
        for header in ("If-Modified-Since", "If-None-Match"):
            if header in self.headers:
                del self.headers[header]
        return super().send_head()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def guess_type(self, path):
        content_type = (
            "text/markdown"
            if Path(path).suffix.lower() == ".md"
            else super().guess_type(path)
        )
        if content_type.startswith("text/") or content_type in {
            "application/javascript",
            "application/json",
            "image/svg+xml",
        }:
            return f"{content_type}; charset=utf-8"
        return content_type


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    repository_root = Path(__file__).resolve().parent.parent
    handler = partial(DesignHandler, directory=str(repository_root))
    with ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
        print(f"Design preview: http://127.0.0.1:{args.port}/design/branding/lift/preview.html", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
