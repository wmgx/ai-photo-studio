#!/usr/bin/env python3
"""JSON command line interface for local AI photo editing and review."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .store import Store, init_batch


def _value(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc


def _parser():
    parser = argparse.ArgumentParser(prog="ai-photo-studio", description="Local AI photo editing and versioned review; all results are JSON.")
    parser.add_argument("--batch", help="Batch directory (also accepted after a command)")
    sub = parser.add_subparsers(dest="command", required=True)

    def command(name, help_text):
        item = sub.add_parser(name, help=help_text, description=help_text)
        item.add_argument("--batch", dest="command_batch", help="Batch directory")
        return item

    init = command("init", "Create a batch; source references and imports may be added later")
    init.add_argument("path", nargs="?", help="Batch directory; alternative to --batch")
    init.add_argument("--name")
    init.add_argument("--source", action="append", default=[], help="Read-only source reference; repeatable")
    init.add_argument("--preferences", default="{}", help="JSON object with editing preferences")
    init.add_argument("--preferences-file", help="UTF-8 JSON object with editing preferences")

    imp = command("import", "Import one JPEG/PNG/TIFF image or video as a fixed original version")
    imp.add_argument("path")
    imp.add_argument("--photo-id")
    imp.add_argument("--category", choices=("人物", "风景", "动态"), default="风景")
    imp.add_argument("--scene")
    imp.add_argument("--source", action="append", default=[], help="Read-only RAW/reference path; repeatable")

    command("catalog", "List photos and every permanent version")

    comments = command("comments", "List, add, submit or reply to version-bound comments")
    csub = comments.add_subparsers(dest="comment_command", required=True)
    clist = csub.add_parser("list", help="List comments")
    clist.add_argument("--photo-id")
    clist.add_argument("--version-id")
    clist.add_argument("--status")
    cadd = csub.add_parser("add", help="Add a saved or submitted comment")
    cadd.add_argument("--photo-id", required=True)
    cadd.add_argument("--version-id", required=True)
    cadd.add_argument("--text", required=True)
    cadd.add_argument("--point", help='Normalized JSON object {"x":0.4,"y":0.5} on this version')
    cadd.add_argument("--submit", action="store_true")
    cadd.add_argument("--comment-id")
    csubmit = csub.add_parser("submit", help="Queue a saved comment for work")
    csubmit.add_argument("comment_id")
    creply = csub.add_parser("reply", help="Reply to a comment")
    creply.add_argument("comment_id")
    creply.add_argument("--text", required=True)
    creply.add_argument("--status", choices=("ready", "needs_input", "failed"), default="ready")
    creply.add_argument("--result-version-id")

    work = command("work", "Claim comments for one photo and write AI task materials to .review/work/<jobId>")
    work.add_argument("--photo-id", required=True)
    work.add_argument("--version-id", help="Explicit base version; defaults to current")
    work.add_argument("--comment-id", action="append", dest="comment_ids")
    work.add_argument("--include-saved", action="store_true")

    publish = command("publish", "Copy a candidate into a permanent revision; requires explicit parent, expected current ID, and idempotency operation ID")
    publish.add_argument("--photo-id", required=True)
    publish.add_argument("--parent-id", required=True)
    publish.add_argument("--candidate", required=True)
    publish.add_argument("--label", required=True)
    publish.add_argument("--summary", default="")
    publish.add_argument("--crop-fraction", help="Normalized JSON [left,top,right,bottom]")
    publish.add_argument("--expected-current-id", required=True)
    publish.add_argument("--operation-id", required=True)
    publish.add_argument("--comment-id", action="append", dest="comment_ids")
    publish.add_argument("--job-id")

    select = command("select", "Set export selection independently of the current revision")
    select.add_argument("--photo-id", required=True)
    choice = select.add_mutually_exclusive_group(required=True)
    choice.add_argument("--version-id")
    choice.add_argument("--clear", action="store_true")

    accept = command("accept", "Approve one version and resolve ready comments linked to it")
    accept.add_argument("--photo-id", required=True)
    accept.add_argument("--version-id", required=True)

    export = command("export", "Copy exact selected versions to category folders with SHA-checked CSV manifest")
    export.add_argument("--directory", help="Existing parent directory; a new timestamp folder is created within it (default: batch/exports)")

    serve = command("serve", "Serve one batch or a directory-based album library")
    serve.add_argument("--library", help="Source directory; every media-containing folder is an album")
    serve.add_argument("--port", type=int, default=0)
    serve.add_argument("--open-browser", action="store_true")

    albums = command("albums", "List source directories that can be opened as albums")
    albums.add_argument("--library", required=True)
    return parser


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(argv)
    batch = args.command_batch or args.batch or getattr(args, "path", None)
    if args.command in ("serve", "albums") and args.library:
        if batch:
            parser.error("Use either --batch or --library")
        from .library import Library
        if args.command == "albums":
            result = Library(args.library).albums()
        else:
            from . import server
            result = server.serve(None, library_path=args.library, port=args.port, open_browser=args.open_browser)
    elif not batch:
        parser.error("--batch is required (or give a path to init)")
    elif args.command == "init":
        preferences = _value(Path(args.preferences_file).read_text(encoding="utf-8")) if args.preferences_file else _value(args.preferences)
        result = init_batch(batch, sources=args.source, name=args.name, preferences=preferences)
    else:
        store = Store(batch)
        if args.command == "import":
            result = store.add_photo(args.path, args.photo_id, args.category, args.scene, args.source)
        elif args.command == "catalog":
            result = store.catalog()
        elif args.command == "comments":
            if args.comment_command == "list":
                result = store.comments(args.photo_id, args.version_id, args.status)
            elif args.comment_command == "add":
                result = store.comment_add(args.photo_id, args.version_id, args.text,
                                           _value(args.point) if args.point else None, args.submit, args.comment_id)
            elif args.comment_command == "submit":
                result = store.comment_submit(args.comment_id)
            else:
                result = store.comment_reply(args.comment_id, args.text, args.status, args.result_version_id)
        elif args.command == "work":
            result = store.start_work(args.photo_id, args.version_id, args.comment_ids, args.include_saved)
        elif args.command == "publish":
            result = store.add_version(args.photo_id, args.parent_id, args.candidate, args.label,
                                       args.summary, _value(args.crop_fraction) if args.crop_fraction else None,
                                       args.expected_current_id, args.comment_ids, args.operation_id, args.job_id)
        elif args.command == "select":
            result = store.select(args.photo_id, args.version_id if not args.clear else None)
        elif args.command == "accept":
            result = store.accept_version(args.photo_id, args.version_id)
        elif args.command == "export":
            result = store.export(args.directory)
        elif args.command == "serve":
            from . import server
            result = server.serve(batch, port=args.port, open_browser=args.open_browser)
        else:
            parser.error("Unknown command")
    if result is not None:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def entrypoint():
    try:
        sys.exit(main())
    except (ValueError, FileNotFoundError, FileExistsError, PermissionError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    entrypoint()
