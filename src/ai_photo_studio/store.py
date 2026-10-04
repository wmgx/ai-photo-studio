"""Durable, append-only local photo review storage."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageOps


CATEGORIES = ("人物", "风景", "动态")
IMAGE_FORMATS = {"JPEG": ".jpg", "MPO": ".jpg", "PNG": ".png", "TIFF": ".tif"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi", ".webm"}
COMMENT_STATUSES = {"saved", "open", "running", "ready", "needs_input", "resolved", "failed"}
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _uuid():
    return str(uuid.uuid4())


def _hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _id(value, name="id"):
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise ValueError(f"Invalid {name}: use letters, numbers, underscore or hyphen")
    return value


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _media(path):
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"Not a file: {path}")
    if path.suffix.lower() in VIDEO_SUFFIXES:
        return path, "video", [0, 0], path.suffix.lower(), _hash(path)
    try:
        with Image.open(path) as image:
            if image.format not in IMAGE_FORMATS:
                raise ValueError("Only JPEG, PNG, TIFF images and supported video files can be imported")
            image.verify()
        with Image.open(path) as image:
            width, height = ImageOps.exif_transpose(image).size
            suffix = IMAGE_FORMATS[image.format]
    except (OSError, SyntaxError) as exc:
        raise ValueError(f"Invalid image: {path}") from exc
    return path, "image", [width, height], suffix, _hash(path)


def _crop(value):
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("crop_fraction must be [left, top, right, bottom]")
    try:
        left, top, right, bottom = map(float, value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid crop_fraction") from exc
    if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
        raise ValueError("crop_fraction must describe a rectangle inside the image")
    return [left, top, right, bottom]


def _relative_external(path):
    return str(Path(path).expanduser().resolve(strict=True))


def init_batch(path, sources=None, name=None, preferences=None):
    """Create a batch with optional source references; no imports are required."""
    root = Path(path).expanduser().absolute()
    if root.is_symlink():
        raise ValueError("Batch root cannot be a symlink")
    root.mkdir(parents=True, exist_ok=True)
    project_path = root / "project.json"
    if project_path.exists() or project_path.is_symlink():
        raise FileExistsError(f"Batch already initialized: {root}")
    state_path = root / ".review" / "state.sqlite3"
    if (root / ".review").is_symlink() or state_path.exists() or state_path.is_symlink():
        raise FileExistsError(f"Batch database path already exists: {state_path}")
    if sources is None:
        sources = []
    if not isinstance(sources, list) or not all(isinstance(x, str) for x in sources):
        raise ValueError("sources must be a list of paths")
    if preferences is None:
        preferences = {}
    if not isinstance(preferences, dict):
        raise ValueError("preferences must be an object")
    project = {"id": _uuid(), "name": name or root.name, "sources": [_relative_external(x) for x in sources],
               "preferences": preferences, "createdAt": _now()}
    for relative in ("versions", "exports", ".review", ".review/cache", ".review/work", ".review/logs"):
        target = root / relative
        if target.is_symlink():
            raise ValueError(f"Managed path cannot be a symlink: {target}")
        target.mkdir(exist_ok=True)
    with project_path.open("x", encoding="utf-8") as stream:
        json.dump(project, stream, ensure_ascii=False, indent=2)
    sqlite3.connect(state_path).close()
    Store(root)._initialize_db()
    return project


class Store:
    def __init__(self, batch_path):
        self.root = Path(batch_path).expanduser().absolute()
        if self.root.is_symlink() or not self.root.is_dir():
            raise ValueError("Batch directory is missing or is a symlink")
        self._managed("project.json").resolve(strict=True)
        if not (self.root / "project.json").is_file():
            raise ValueError("Batch is not initialized")
        self.db_path = self._managed(".review", "state.sqlite3")
        if not self.db_path.is_file():
            raise ValueError("Batch database is missing")
        # Existing albums predate the recycle bin; create only its metadata table.
        with self._db(True) as db:
            db.execute("CREATE TABLE IF NOT EXISTS tombstones(photo_id TEXT NOT NULL, "
                       "version_id TEXT NOT NULL DEFAULT '', deleted_at TEXT NOT NULL, "
                       "PRIMARY KEY(photo_id, version_id))")

    def _managed(self, *parts):
        target = self.root
        for part in parts:
            if not isinstance(part, str) or part in ("", ".", "..") or Path(part).name != part or "/" in part or "\\" in part:
                raise ValueError("Invalid managed path component")
            target = target / part
            if target.is_symlink():
                raise ValueError(f"Managed symlink rejected: {target}")
        if not target.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("Managed path escapes batch")
        return target

    def _version_path(self, row):
        if row["kind"] == "original":
            return Path(row["path"])
        path = self._managed("versions", row["photo_id"], row["id"], row["file_name"])
        if str(path) != row["path"]:
            raise ValueError("Version path in database is invalid")
        return path

    def _initialize_db(self):
        with sqlite3.connect(self.db_path) as db:
            db.executescript("""
                PRAGMA foreign_keys=ON;
                CREATE TABLE photos(id TEXT PRIMARY KEY, category TEXT NOT NULL, scene TEXT,
                    sources TEXT NOT NULL, current_id TEXT NOT NULL, selected_id TEXT);
                CREATE TABLE versions(id TEXT PRIMARY KEY, photo_id TEXT NOT NULL REFERENCES photos(id),
                    parent_id TEXT, label TEXT NOT NULL, path TEXT NOT NULL, file_name TEXT,
                    sha256 TEXT NOT NULL, dimensions TEXT NOT NULL, crop_fraction TEXT,
                    created_at TEXT NOT NULL, summary TEXT NOT NULL, kind TEXT NOT NULL,
                    media_type TEXT NOT NULL, review_status TEXT NOT NULL DEFAULT 'pending',
                    operation_id TEXT UNIQUE, operation_args TEXT);
                CREATE TABLE comments(id TEXT PRIMARY KEY, photo_id TEXT NOT NULL REFERENCES photos(id),
                    version_id TEXT NOT NULL REFERENCES versions(id), text TEXT NOT NULL,
                    point TEXT, status TEXT NOT NULL, reply TEXT, result_version_id TEXT,
                    created_at TEXT NOT NULL);
                CREATE TABLE jobs(id TEXT PRIMARY KEY, photo_id TEXT NOT NULL, version_id TEXT NOT NULL,
                    comment_ids TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE INDEX comments_for_version ON comments(version_id, status);
                CREATE TABLE IF NOT EXISTS tombstones(photo_id TEXT NOT NULL, version_id TEXT NOT NULL DEFAULT '',
                    deleted_at TEXT NOT NULL, PRIMARY KEY(photo_id, version_id));
            """)

    @contextmanager
    def _db(self, write=False):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def project(self):
        project = json.loads(self._managed("project.json").read_text(encoding="utf-8"))
        project["root"] = str(self.root.resolve())
        return project

    def _version(self, row):
        return {"id": row["id"], "photoId": row["photo_id"], "parentId": row["parent_id"],
                "label": row["label"], "path": str(self._version_path(row)), "sha256": row["sha256"],
                "dimensions": json.loads(row["dimensions"]),
                "cropFraction": json.loads(row["crop_fraction"]) if row["crop_fraction"] else None,
                "createdAt": row["created_at"], "summary": row["summary"], "kind": row["kind"],
                "mediaType": row["media_type"], "reviewStatus": row["review_status"]}

    def _photo(self, db, row, include_deleted=False):
        sql = "SELECT * FROM versions WHERE photo_id=?"
        if not include_deleted:
            sql += " AND id NOT IN (SELECT version_id FROM tombstones WHERE photo_id=? AND version_id!='')"
        args = (row["id"], row["id"]) if not include_deleted else (row["id"],)
        versions = db.execute(sql + " ORDER BY created_at, rowid", args).fetchall()
        return {"id": row["id"], "scene": row["scene"], "category": row["category"],
                "sources": json.loads(row["sources"]), "currentVersionId": row["current_id"],
                "selectedVersionId": row["selected_id"], "versions": [self._version(v) for v in versions]}

    def catalog(self, include_deleted=False):
        with self._db() as db:
            sql = "SELECT * FROM photos"
            if not include_deleted:
                sql += " WHERE id NOT IN (SELECT photo_id FROM tombstones WHERE version_id='')"
            return [self._photo(db, r, include_deleted) for r in db.execute(sql + " ORDER BY rowid")]

    def trash(self):
        with self._db() as db:
            rows = db.execute("SELECT t.photo_id,t.version_id,t.deleted_at,p.scene,v.label "
                              "FROM tombstones t JOIN photos p ON p.id=t.photo_id "
                              "LEFT JOIN versions v ON v.id=t.version_id "
                              "WHERE t.version_id='' OR NOT EXISTS "
                              "(SELECT 1 FROM tombstones whole WHERE whole.photo_id=t.photo_id AND whole.version_id='') "
                              "ORDER BY t.deleted_at DESC,t.rowid DESC").fetchall()
            return [{"kind": "version" if row["version_id"] else "photo",
                     "photoId": row["photo_id"], "versionId": row["version_id"] or None,
                     "scene": row["scene"], "label": row["label"] or row["scene"],
                     "deletedAt": row["deleted_at"]} for row in rows]

    def trash_item(self, photo_id, version_id=None):
        with self._db(True) as db:
            photo = self._require_photo(db, photo_id)
            if db.execute("SELECT 1 FROM comments WHERE photo_id=? AND status='running' LIMIT 1", (photo_id,)).fetchone():
                raise ValueError("这张照片有正在处理的评论，请等待任务结束后再删除")
            key = _id(version_id, "version_id") if version_id is not None else ""
            if version_id is not None:
                version = self._require_version(db, photo_id, version_id)
                if version["kind"] == "original":
                    raise ValueError("原片版本不能单独删除；可以删除整张照片")
            if db.execute("SELECT 1 FROM tombstones WHERE photo_id=? AND version_id=?", (photo_id, key)).fetchone():
                raise ValueError("已在回收站中")
            db.execute("INSERT INTO tombstones VALUES(?,?,?)", (photo_id, key, _now()))
            if version_id is None:
                db.execute("UPDATE photos SET selected_id=NULL WHERE id=?", (photo_id,))
            else:
                if photo["selected_id"] == version_id:
                    db.execute("UPDATE photos SET selected_id=NULL WHERE id=?", (photo_id,))
                if photo["current_id"] == version_id:
                    fallback = db.execute("SELECT id FROM versions WHERE photo_id=? AND id NOT IN "
                                          "(SELECT version_id FROM tombstones WHERE photo_id=?) "
                                          "ORDER BY created_at DESC,rowid DESC LIMIT 1", (photo_id, photo_id)).fetchone()
                    db.execute("UPDATE photos SET current_id=? WHERE id=?", (fallback["id"], photo_id))
        return {"ok": True}

    def restore_item(self, photo_id, version_id=None):
        key = _id(version_id, "version_id") if version_id is not None else ""
        with self._db(True) as db:
            self._require_photo(db, photo_id, include_deleted=version_id is None)
            if version_id is not None:
                self._require_version(db, photo_id, version_id, include_deleted=True)
            cursor = db.execute("DELETE FROM tombstones WHERE photo_id=? AND version_id=?", (photo_id, key))
            if cursor.rowcount != 1:
                raise ValueError("该项目不在回收站中")
        return {"ok": True}

    @staticmethod
    def _require_photo(db, photo_id, include_deleted=False):
        row = db.execute("SELECT * FROM photos WHERE id=?", (_id(photo_id, "photo_id"),)).fetchone()
        if row is None:
            raise ValueError(f"Unknown photo: {photo_id}")
        if not include_deleted and db.execute("SELECT 1 FROM tombstones WHERE photo_id=? AND version_id=''", (photo_id,)).fetchone():
            raise ValueError("照片已移入回收站，请先恢复")
        return row

    @staticmethod
    def _require_version(db, photo_id, version_id, include_deleted=False):
        row = db.execute("SELECT * FROM versions WHERE photo_id=? AND id=?", (photo_id, _id(version_id, "version_id"))).fetchone()
        if row is None:
            raise ValueError(f"Unknown version for photo {photo_id}: {version_id}")
        if not include_deleted:
            Store._require_photo(db, photo_id)
            if db.execute("SELECT 1 FROM tombstones WHERE photo_id=? AND version_id=?", (photo_id, version_id)).fetchone():
                raise ValueError("版本已移入回收站，请先恢复")
        return row

    def add_photo(self, path, photo_id=None, category="风景", scene=None, source_paths=None):
        if category not in CATEGORIES:
            raise ValueError("category must be 人物, 风景 or 动态")
        photo_id = _id(photo_id or _uuid(), "photo_id")
        source, media_type, dimensions, _, digest = _media(path)
        sources = list(dict.fromkeys([str(source), *[_relative_external(x) for x in (source_paths or [])]]))
        scene = scene if scene is not None else source.stem
        version_id = _uuid()
        with self._db(True) as db:
            db.execute("INSERT INTO photos VALUES(?,?,?,?,?,NULL)", (photo_id, category, scene, _json(sources), version_id))
            db.execute("INSERT INTO versions(id,photo_id,parent_id,label,path,file_name,sha256,dimensions,crop_fraction,created_at,summary,kind,media_type,review_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (version_id, photo_id, None, "原片", str(source), None, digest, _json(dimensions), None, _now(), "", "original", media_type, "pending"))
            return self._photo(db, self._require_photo(db, photo_id))

    def add_version(self, photo_id, parent_id, candidate_path, label, summary="", crop_fraction=None,
                    expected_current_id=None, comment_ids=None, operation_id=None, job_id=None):
        photo_id = _id(photo_id, "photo_id")
        parent_id = _id(parent_id, "parent_id")
        expected_current_id = _id(expected_current_id, "expected_current_id")
        if not isinstance(label, str) or not label.strip():
            raise ValueError("label is required")
        if operation_id is not None:
            _id(operation_id, "operation_id")
        if job_id is not None:
            _id(job_id, "job_id")
        comment_ids = comment_ids or []
        if not isinstance(comment_ids, list) or len(comment_ids) != len(set(comment_ids)):
            raise ValueError("comment_ids must be a unique list")
        comment_ids = [_id(x, "comment_id") for x in comment_ids]
        crop = _crop(crop_fraction)
        if job_id is not None:
            work_dir = self._managed(".review", "work", job_id)
            candidate = Path(candidate_path).expanduser().absolute()
            try:
                relative = candidate.relative_to(work_dir)
            except ValueError as exc:
                raise ValueError("Candidate for job_id must be inside its work directory") from exc
            if not relative.parts or any(part in ("", ".", "..") for part in relative.parts):
                raise ValueError("Invalid candidate path")
            current = work_dir
            for part in relative.parts:
                current = current / part
                if current.is_symlink():
                    raise ValueError("Candidate path cannot use symlinks")
        source, media_type, dimensions, suffix, digest = _media(candidate_path)
        args = _json({"photoId": photo_id, "parentId": parent_id, "candidatePath": str(source),
                      "candidateSha256": digest, "label": label, "summary": summary,
                      "cropFraction": crop, "expectedCurrentId": expected_current_id,
                      "commentIds": comment_ids, "jobId": job_id})
        with self._db(True) as db:
            if operation_id is not None:
                previous = db.execute("SELECT * FROM versions WHERE operation_id=?", (operation_id,)).fetchone()
                if previous is not None:
                    if previous["operation_args"] != args:
                        raise ValueError("operation_id was already used with different parameters")
                    self._require_version(db, photo_id, previous["id"])
                    return self._version(previous)
            if not isinstance(summary, str) or not summary.strip():
                raise ValueError("A non-empty change summary is required")
            photo = self._require_photo(db, photo_id)
            parent = self._require_version(db, photo_id, parent_id)
            if photo["current_id"] != expected_current_id:
                raise ValueError("Current version changed; refresh before publishing")
            if media_type != parent["media_type"]:
                raise ValueError("Candidate media type differs from parent")
            if job_id is not None:
                job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
                if job is None or job["photo_id"] != photo_id or job["version_id"] != parent_id:
                    raise ValueError("job_id does not match this photo and parent version")
                if not set(comment_ids).issubset(set(json.loads(job["comment_ids"]))):
                    raise ValueError("comment_ids were not claimed by this job")
            for comment_id in comment_ids:
                comment = db.execute("SELECT * FROM comments WHERE id=?", (comment_id,)).fetchone()
                if comment is None or comment["photo_id"] != photo_id or comment["version_id"] != parent_id:
                    raise ValueError(f"Comment is not on the parent version: {comment_id}")
                if comment["status"] not in ("running", "open"):
                    raise ValueError(f"Comment cannot be completed: {comment_id}")
            version_id = _uuid()
            filename = "image" + suffix if media_type == "image" else "video" + suffix
            dest_dir = self._managed("versions", photo_id, version_id)
            dest_dir.mkdir(parents=True, exist_ok=False)
            dest = self._managed("versions", photo_id, version_id, filename)
            with source.open("rb") as src, dest.open("xb") as out:
                shutil.copyfileobj(src, out)
            if _hash(dest) != digest:
                raise ValueError("Candidate changed while copying")
            db.execute("INSERT INTO versions(id,photo_id,parent_id,label,path,file_name,sha256,dimensions,crop_fraction,created_at,summary,kind,media_type,review_status,operation_id,operation_args) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (version_id, photo_id, parent_id, label, str(dest), filename, digest, _json(dimensions), _json(crop) if crop else None, _now(), summary, "revision", media_type, "pending", operation_id, args))
            db.execute("UPDATE photos SET current_id=? WHERE id=?", (version_id, photo_id))
            for comment_id in comment_ids:
                db.execute("UPDATE comments SET status='ready', result_version_id=? WHERE id=?", (version_id, comment_id))
            return self._version(self._require_version(db, photo_id, version_id))

    @staticmethod
    def _comment(row):
        return {"id": row["id"], "photoId": row["photo_id"], "versionId": row["version_id"],
                "text": row["text"], "point": json.loads(row["point"]) if row["point"] else None,
                "status": row["status"], "reply": row["reply"], "resultVersionId": row["result_version_id"],
                "createdAt": row["created_at"]}

    def comments(self, photo_id=None, version_id=None, status=None):
        sql, args = ("SELECT c.* FROM comments c WHERE c.photo_id NOT IN "
                     "(SELECT photo_id FROM tombstones WHERE version_id='') "
                     "AND NOT EXISTS (SELECT 1 FROM tombstones t WHERE t.photo_id=c.photo_id "
                     "AND t.version_id=c.version_id)"), []
        for column, value in (("photo_id", photo_id), ("version_id", version_id), ("status", status)):
            if value is not None:
                sql += f" AND c.{column}=?"
                args.append(value)
        with self._db() as db:
            return [self._comment(r) for r in db.execute(sql + " ORDER BY c.created_at, c.rowid", args)]

    def comment_add(self, photo_id, version_id, text, point=None, submit=False, comment_id=None):
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Comment text is required")
        points = point if isinstance(point, list) else ([] if point is None else [point])
        if points == []:
            point = None
        if any(not isinstance(item, dict) or set(item) != {"x", "y"} or
               not all(isinstance(item[axis], (int, float)) and not isinstance(item[axis], bool) and
                       0 <= item[axis] <= 1 and math.isfinite(item[axis]) for axis in ("x", "y"))
               for item in points):
            raise ValueError("point must be normalized {x,y} or a list of normalized {x,y} points")
        comment_id = _id(comment_id or _uuid(), "comment_id")
        with self._db(True) as db:
            previous = db.execute("SELECT * FROM comments WHERE id=?", (comment_id,)).fetchone()
            if previous is not None:
                if (previous["photo_id"], previous["version_id"], previous["text"], previous["point"]) != (photo_id, version_id, text, _json(point) if point else None):
                    raise ValueError("comment_id was already used with different parameters")
                self._require_version(db, photo_id, version_id)
                return self._comment(previous)
            self._require_version(db, photo_id, version_id)
            db.execute("INSERT INTO comments VALUES(?,?,?,?,?,?,?,?,?)",
                       (comment_id, photo_id, version_id, text, _json(point) if point else None,
                        "open" if submit else "saved", None, None, _now()))
            return self._comment(db.execute("SELECT * FROM comments WHERE id=?", (comment_id,)).fetchone())

    def comment_submit(self, comment_id):
        with self._db(True) as db:
            row = db.execute("SELECT * FROM comments WHERE id=?", (_id(comment_id),)).fetchone()
            if row is None or row["status"] not in ("saved", "open", "failed"):
                raise ValueError("Only saved, open or failed comments can be submitted")
            self._require_version(db, row["photo_id"], row["version_id"])
            db.execute("UPDATE comments SET status='open' WHERE id=?", (comment_id,))
            return self._comment(db.execute("SELECT * FROM comments WHERE id=?", (comment_id,)).fetchone())

    def comment_reply(self, comment_id, text, status="ready", result_version_id=None):
        if status not in {"ready", "needs_input", "failed"}:
            raise ValueError("Reply status must be ready, needs_input or failed")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Reply text is required")
        with self._db(True) as db:
            row = db.execute("SELECT * FROM comments WHERE id=?", (_id(comment_id),)).fetchone()
            if row is None:
                raise ValueError("Unknown comment")
            self._require_version(db, row["photo_id"], row["version_id"])
            if row["status"] == "resolved":
                raise ValueError("Resolved comments cannot be changed")
            if result_version_id is not None:
                self._require_version(db, row["photo_id"], result_version_id)
            else:
                result_version_id = row["result_version_id"]
                if result_version_id is not None and db.execute(
                    "SELECT 1 FROM tombstones WHERE photo_id=? AND version_id=?",
                    (row["photo_id"], result_version_id)).fetchone():
                    result_version_id = None
            db.execute("UPDATE comments SET reply=?,status=?,result_version_id=? WHERE id=?",
                       (text, status, result_version_id, comment_id))
            return self._comment(db.execute("SELECT * FROM comments WHERE id=?", (comment_id,)).fetchone())

    def select(self, photo_id, version_id_or_none):
        with self._db(True) as db:
            self._require_photo(db, photo_id)
            if version_id_or_none is not None:
                self._require_version(db, photo_id, version_id_or_none)
            db.execute("UPDATE photos SET selected_id=? WHERE id=?", (version_id_or_none, photo_id))
            return self._photo(db, self._require_photo(db, photo_id))

    def accept_version(self, photo_id, version_id):
        with self._db(True) as db:
            self._require_version(db, photo_id, version_id)
            db.execute("UPDATE versions SET review_status='approved' WHERE id=?", (version_id,))
            db.execute("UPDATE comments SET status='resolved' WHERE result_version_id=? AND status='ready'", (version_id,))
            return self._version(self._require_version(db, photo_id, version_id))

    def start_work(self, photo_id, version_id=None, comment_ids=None, include_saved=False, expected_current_id=None):
        with self._db(True) as db:
            photo = self._require_photo(db, photo_id)
            if expected_current_id is not None and photo["current_id"] != expected_current_id:
                raise ValueError("Current version changed since album analysis; review the latest version first")
            version_id = version_id or photo["current_id"]
            version = self._require_version(db, photo_id, version_id)
            original = db.execute("SELECT * FROM versions WHERE photo_id=? AND kind='original'", (photo_id,)).fetchone()
            for item in (version, original):
                path = self._version_path(item)
                if not path.is_file() or _hash(path) != item["sha256"]:
                    raise ValueError(f"Work source changed or disappeared: {path}")
            eligible = ("open", "saved") if include_saved else ("open",)
            if comment_ids is None:
                placeholders = ",".join("?" for _ in eligible)
                rows = db.execute(f"SELECT * FROM comments WHERE photo_id=? AND version_id=? AND status IN ({placeholders}) ORDER BY created_at,rowid",
                                  (photo_id, version_id, *eligible)).fetchall()
            else:
                if not isinstance(comment_ids, list) or len(comment_ids) != len(set(comment_ids)):
                    raise ValueError("comment_ids must be a unique list")
                rows = []
                for cid in comment_ids:
                    row = db.execute("SELECT * FROM comments WHERE id=?", (_id(cid),)).fetchone()
                    if row is None or row["photo_id"] != photo_id or row["version_id"] != version_id or row["status"] not in eligible:
                        raise ValueError(f"Comment unavailable for work: {cid}")
                    rows.append(row)
            if comment_ids is None and not rows:
                running = db.execute("SELECT 1 FROM comments WHERE photo_id=? AND version_id=? AND status='running' LIMIT 1", (photo_id, version_id)).fetchone()
                if running:
                    raise ValueError("Comments for this version are already claimed")
            job_id = _uuid()
            work_dir = self._managed(".review", "work", job_id)
            work_dir.mkdir(parents=True, exist_ok=False)
            ids = [row["id"] for row in rows]
            db.execute("INSERT INTO jobs VALUES(?,?,?,?,?)", (job_id, photo_id, version_id, _json(ids), _now()))
            for cid in ids:
                db.execute("UPDATE comments SET status='running' WHERE id=?", (cid,))
            project = self.project()
            base_path = self._version_path(version)
            history_versions = db.execute(
                "SELECT * FROM versions WHERE photo_id=? ORDER BY created_at,rowid", (photo_id,)).fetchall()
            history_comments = db.execute(
                "SELECT * FROM comments WHERE photo_id=? AND status!='saved' ORDER BY created_at,rowid",
                (photo_id,)).fetchall()
            history_jobs = db.execute(
                "SELECT * FROM jobs WHERE photo_id=? AND id!=? ORDER BY created_at,rowid",
                (photo_id, job_id)).fetchall()
            deleted_versions = {row["version_id"]: row["deleted_at"] for row in db.execute(
                "SELECT version_id,deleted_at FROM tombstones WHERE photo_id=? AND version_id!=''", (photo_id,))}
            payload = {"workDir": str(work_dir), "jobId": job_id, "photoId": photo_id,
                       "baseVersionId": version_id, "expectedCurrentVersionId": photo["current_id"],
                       "baseVersion": self._version(version), "originalVersion": self._version(original),
                       "basePath": str(base_path), "baseSha256": version["sha256"],
                       "originalPath": str(self._version_path(original)), "originalSha256": original["sha256"],
                       "sources": json.loads(photo["sources"]), "projectSources": project.get("sources", []),
                       "preferences": project.get("preferences", {}), "comments": [self._comment(r) for r in rows],
                       "history": {
                           "versions": [{**self._version(r), **({"deletedAt": deleted_versions[r["id"]]}
                                                                if r["id"] in deleted_versions else {})}
                                        for r in history_versions],
                           "comments": [{**self._comment(r), **({"deletedAt": deleted_versions[r["version_id"]]}
                                                                if r["version_id"] in deleted_versions else {})}
                                        for r in history_comments if r["id"] not in ids],
                           "jobs": [{"jobId": r["id"], "baseVersionId": r["version_id"],
                                     "createdAt": r["created_at"],
                                     "workDir": str(self._managed(".review", "work", r["id"])),
                                     "inputsPath": str(self._managed(".review", "work", r["id"], "inputs.json"))}
                                    for r in history_jobs],
                       },
                       "candidatePath": str(self._managed(".review", "work", job_id, "candidate" + base_path.suffix.lower()))}
            self._managed(".review", "work", job_id, "inputs.json").write_text(_json(payload), encoding="utf-8")
            return payload

    def export(self, directory=None):
        with self._db() as db:
            rows = db.execute("SELECT v.*,p.category,p.scene FROM photos p JOIN versions v ON v.id=p.selected_id "
                              "WHERE p.id NOT IN (SELECT photo_id FROM tombstones WHERE version_id='') "
                              "AND NOT EXISTS (SELECT 1 FROM tombstones t WHERE t.photo_id=p.id AND t.version_id=v.id) "
                              "ORDER BY p.rowid").fetchall()
            selected = [(row, self._version_path(row)) for row in rows]
            if not selected:
                raise ValueError("Select at least one version before export")
            for row, source in selected:
                if not source.is_file() or _hash(source) != row["sha256"]:
                    raise ValueError(f"Selected version changed or disappeared: {row['id']}")
        parent = self._managed("exports") if directory is None else Path(directory).expanduser().absolute()
        if parent.is_symlink() or not parent.is_dir():
            raise ValueError("Export parent must be an existing, non-symlink directory")
        if parent.resolve().is_relative_to(self.root.resolve()):
            if not parent.resolve().is_relative_to(self._managed("exports").resolve()):
                raise ValueError("Export inside batch must be under exports")
            anchor = next((root for root in (self.root, self.root.resolve()) if parent.is_relative_to(root)), None)
            if anchor is None:
                raise ValueError("Managed export path uses an unexpected alias")
            current = parent
            while current != anchor:
                if current.is_symlink():
                    raise ValueError("Managed export path cannot use symlinks")
                current = current.parent
        target = parent / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        target.mkdir(parents=True, exist_ok=False)
        manifest = target / "导出清单.csv"
        records = []
        for row, source in selected:
            category = row["category"]
            if category not in CATEGORIES:
                raise ValueError("Invalid category")
            folder = target / category
            folder.mkdir(exist_ok=True)
            output = folder / f"{row['photo_id']}_{row['id']}{source.suffix.lower()}"
            with source.open("rb") as src, output.open("xb") as dst:
                shutil.copyfileobj(src, dst)
            if _hash(output) != row["sha256"]:
                raise ValueError(f"Export hash mismatch: {output}")
            records.append((row["photo_id"], row["scene"] or "", row["id"], row["label"], str(source), str(output.relative_to(target)), row["sha256"]))
        with manifest.open("x", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["photoId", "scene", "versionId", "label", "source", "output", "sha256"])
            writer.writerows(records)
        return {"directory": str(target), "count": len(records), "manifestFile": str(manifest)}
