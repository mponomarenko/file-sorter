#!/usr/bin/env python3
import os
import sys
import argparse
import json
from pathlib import Path

# Add parent directory to path for importing app modules
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import AppConfig
from app.media import MediaHelper, detect_mime, peek_text

def extract_metadata(path: str, media: MediaHelper) -> dict:
    """Extract all available metadata for a file."""
    mime = detect_mime(path)
    result = {
        "path": path,
        "basic": {
            "name": os.path.basename(path),
            "size": os.path.getsize(path),
            "modified": os.path.getmtime(path),
            "mime": mime
        },
        "media": {},
        "content_preview": None
    }
    
    # Extract media-specific metadata
    try:
        # Future: add media-specific extraction here
        
        # Add content preview if available
        preview = peek_text(path, mime, 1000)
        if preview:
            result["content_preview"] = preview
    except Exception as e:
        result["errors"] = str(e)
    
    return result

def write_metadata_json(metadata: dict, destination: str) -> None:
    target = Path(destination).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def format_output(metadata: dict) -> None:
    """Format and print metadata information."""

    print(f"\nFile: {metadata['path']}")
    print("\nBasic Information:")
    for key, value in metadata["basic"].items():
        print(f"  {key}: {value}")
    
    if metadata.get("media"):
        print("\nMedia Metadata:")
        for key, value in metadata["media"].items():
            print(f"\n  {key}:")
            if isinstance(value, dict):
                for k, v in value.items():
                    print(f"    {k}: {v}")
            else:
                print(f"    {value}")
    
    if metadata.get("content_preview"):
        print("\nContent Preview:")
        print("-" * 80)
        print(metadata["content_preview"])
        print("-" * 80)
    
    if metadata.get("errors"):
        print("\nErrors:")
        print(f"  {metadata['errors']}")

def main():
    parser = argparse.ArgumentParser(description="Extract and display file metadata")
    parser.add_argument("path", help="File to analyze")
    parser.add_argument("--output-json", help="Write metadata payload to a JSON file")
    args = parser.parse_args()

    cfg = AppConfig.from_env()
    media = MediaHelper(cfg)
    
    path = os.path.abspath(args.path)
    if not os.path.isfile(path):
        print(f"Error: {path} is not a file", file=sys.stderr)
        sys.exit(1)
    
    metadata = extract_metadata(path, media)
    if args.output_json:
        write_metadata_json(metadata, args.output_json)
    format_output(metadata)

if __name__ == "__main__":
    main()
