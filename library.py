"""Discover source directories as local albums; keep review data beside each one."""

from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

from store import Store, VIDEO_SUFFIXES, init_batch


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".jpe", ".mpo", ".png", ".tif", ".tiff"}
RAW_SUFFIXES = {".nef", ".nrw", ".cr2", ".cr3", ".arw", ".dng", ".raf",
                ".orf", ".rw2", ".pef", ".srw"}
SKIP_DIRS = {"versions", "exports", "__pycache__"}


class Library:
    """One directory containing displayable source files is one album."""

    def __init__(self, root):
        self.root = Path(root).expanduser().absolute()
        if self.root.is_symlink() or not self.root.is_dir():
            raise ValueError("Library directory is missing or is a symlink")
        self.root = self.root.resolve(strict=True)
        self._lock = threading.Lock()

    def _discover(self):
        found = {}
        for parent, dirs, files in os.walk(self.root, followlinks=False):
            dirs[:] = sorted(name for name in dirs if not name.startswith(".")
                             and name.lower() not in SKIP_DIRS
                             and not (Path(parent) / name).is_symlink())
            directory = Path(parent)
            media = sorted((directory / name for name in files
                            if not name.startswith(".")
                            and (directory / name).is_file()
                            and not (directory / name).is_symlink()
                            and (directory / name).suffix.lower() in IMAGE_SUFFIXES | VIDEO_SUFFIXES),
                           key=lambda path: path.name.casefold())
            if not media:
                continue
            relative = directory.relative_to(self.root).as_posix()
            album_id = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:24]
            found[album_id] = (directory, media, relative)
        return found

    def albums(self):
        found = self._discover()
        return [{"id": album_id, "name": self.root.name if relative == "." else relative,
                 "path": str(directory), "count": len(media)}
                for album_id, (directory, media, relative) in sorted(found.items(), key=lambda item: item[1][2].casefold())]

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
