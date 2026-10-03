"""Discover photo directories and remember albums opened from the local UI."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from pathlib import Path

from .store import Store, VIDEO_SUFFIXES, init_batch


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".jpe", ".mpo", ".png", ".tif", ".tiff"}
RAW_SUFFIXES = {".nef", ".nrw", ".cr2", ".cr3", ".arw", ".dng", ".raf",
                ".orf", ".rw2", ".pef", ".srw"}
SKIP_DIRS = {"versions", "exports", "__pycache__"}
MANAGED_DIRS = SKIP_DIRS | {".photo-review", ".review", ".photo-review-server"}


class Library:
    """Each album owns its data; the server root remembers external albums."""

    def __init__(self, root):
        self.root = self._directory(Path(root).expanduser().absolute())
        self._lock = threading.RLock()

    @staticmethod
    def _directory(value):
        if not isinstance(value, (str, Path)) or not str(value).strip():
            raise ValueError("请填写目录的绝对路径")
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError("请使用绝对路径")
        if path.is_symlink() or not path.is_dir():
            raise ValueError("目录不存在或是软链接")
        return path.resolve(strict=True)

    def _identity(self, directory):
        if directory.is_relative_to(self.root):
            key = directory.relative_to(self.root).as_posix()
            name = self.root.name if key == "." else key
        else:
            key = str(directory)
            name = directory.name
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24], name

    @staticmethod
    def _media_files(directory):
        return sorted((path for path in directory.iterdir()
                       if not path.name.startswith(".") and path.is_file() and not path.is_symlink()
                       and path.suffix.lower() in IMAGE_SUFFIXES | VIDEO_SUFFIXES),
                      key=lambda path: path.name.casefold())

    def _registry_path(self):
        runtime = self.root / ".photo-review-server"
        path = runtime / "albums.json"
        if runtime.is_symlink() or path.is_symlink():
            raise ValueError("相册列表不能存放在软链接中")
        return path

    def _registered(self):
        path = self._registry_path()
        if not path.exists():
            return []
        paths = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(paths, list) or not all(isinstance(item, str) for item in paths):
            raise ValueError("相册列表格式无效")
        return paths

    def _discover(self):
        found = {}
        for parent, dirs, _ in os.walk(self.root, followlinks=False):
            dirs[:] = sorted(name for name in dirs if not name.startswith(".")
                             and name.lower() not in SKIP_DIRS
                             and not (Path(parent) / name).is_symlink())
            directory = Path(parent)
            media = self._media_files(directory)
            if media or (directory / ".photo-review" / "project.json").is_file():
                album_id, name = self._identity(directory)
                found[album_id] = (directory, media, name)
        for value in self._registered():
            directory = Path(value)
            if directory.is_symlink() or not directory.is_dir():
                continue
            album_id, name = self._identity(directory)
            if album_id not in found:
                found[album_id] = (directory, self._media_files(directory), name)
        return found

    def albums(self):
        return [{"id": album_id, "name": name, "path": str(directory), "count": len(media)}
                for album_id, (directory, media, name) in
                sorted(self._discover().items(), key=lambda item: item[1][2].casefold())]

    def browse(self, path=None):
        directory = self._directory(path or self.root)
        children = [{"name": child.name, "path": str(child)}
                    for child in sorted(directory.iterdir(), key=lambda item: item.name.casefold())
                    if child.is_dir() and not child.is_symlink() and not child.name.startswith(".")
                    and child.name.lower() not in SKIP_DIRS]
        return {"path": str(directory), "parent": str(directory.parent) if directory != directory.parent else None,
                "children": children}

    def register(self, path):
        directory = self._directory(path)
        if any(part.lower() in MANAGED_DIRS for part in directory.parts):
            raise ValueError("请选择原片目录，不要选择版本、导出或程序数据目录")
        with self._lock:
            paths = self._registered()
            if str(directory) not in paths:
                paths.append(str(directory))
                target = self._registry_path()
                target.parent.mkdir(exist_ok=True)
                fd, temporary = tempfile.mkstemp(prefix=".albums-", suffix=".json", dir=target.parent)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as stream:
                        json.dump(paths, stream, ensure_ascii=False, indent=2)
                    os.replace(temporary, target)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
            album_id, name = self._identity(directory)
            return {"id": album_id, "name": name, "path": str(directory),
                    "count": len(self._media_files(directory))}

    def create(self, parent, name):
        directory = self._directory(parent)
        if not isinstance(name, str) or not name.strip() or name != name.strip() or name.startswith(".") or any(c in name for c in ("/", "\\", "\0")) or name.lower() in MANAGED_DIRS:
            raise ValueError("请填写单个相册名称，不要包含路径或使用程序数据目录名")
        if any(part.lower() in MANAGED_DIRS for part in directory.parts):
            raise ValueError("请在原片目录或其他普通目录中新建相册")
        with self._lock:
            target = directory / name
            if target.exists() or target.is_symlink():
                raise ValueError("该目录已存在，请使用“打开相册目录”")
            target.mkdir()
            return self.register(target)

    def open(self, album_id):
        if not isinstance(album_id, str) or len(album_id) != 24 or any(c not in "0123456789abcdef" for c in album_id):
            raise ValueError("Invalid album ID")
        with self._lock:
            found = self._discover()
            if album_id not in found:
                raise ValueError("Album not found")
            directory, media, _ = found[album_id]
            managed = directory / ".photo-review"
            if managed.is_symlink():
                raise ValueError("Album data directory cannot be a symlink")
            if not managed.exists():
                init_batch(managed, name=directory.name)
            store = Store(managed)
            imported = {photo["sources"][0] for photo in store.catalog()}
            raw_by_stem = {}
            for path in directory.iterdir():
                if path.is_file() and not path.is_symlink() and path.suffix.lower() in RAW_SUFFIXES:
                    raw_by_stem.setdefault(path.stem.casefold(), []).append(str(path))
            for path in media:
                source = str(path)
                if source in imported:
                    continue
                photo_id = "p" + hashlib.sha256(path.name.encode("utf-8")).hexdigest()[:24]
                refs = sorted(raw_by_stem.get(path.stem.casefold(), []))
                category = "动态" if path.suffix.lower() in VIDEO_SUFFIXES else "风景"
                store.add_photo(source, photo_id=photo_id, category=category,
                                scene=path.stem, source_paths=refs)
                imported.add(source)
            return store
