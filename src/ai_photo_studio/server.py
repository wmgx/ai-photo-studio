"""Local review UI and a small JSON API over the batch store."""
from __future__ import annotations

import json
import logging
import mimetypes
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit
import webbrowser

from PIL import Image, ImageOps

from .store import Store, _hash
from .library import Library
from .executor import Runner

CODE = Path(__file__).resolve().parent


def make_server(batch_path=None, port=0, library_path=None):
    if not library_path and not batch_path:
        raise ValueError('请指定相册根目录或批次目录')
    library = Library(library_path or Path(batch_path).expanduser().absolute())
    stores, runners = {}, {}
    album_lock = threading.RLock()
    token = secrets.token_urlsafe(32)
    if not library_path:
        stores['batch'] = Store(batch_path)
        runners['batch'] = Runner(stores['batch'])

    def albums():
        available = library.albums()
        if 'batch' in stores:
            store = stores['batch']
            available.insert(0, {'id': 'batch', 'name': store.project()['name'],
                                 'path': str(store.root), 'count': len(store.catalog())})
        return available

    def get_store(album_id=None, open_album=False):
        album_id = album_id or ('batch' if not library_path else None)
        with album_lock:
            if open_album and album_id != 'batch':
                stores[album_id] = library.open(album_id)
                if album_id not in runners:
                    runners[album_id] = Runner(stores[album_id])
            if album_id not in stores:
                raise ValueError('请先打开一个相册')
            return stores[album_id], runners[album_id]

    def catalog(store, runner):
        return {'items': store.catalog(), 'project': store.project(), 'token': token,
                'codex': runner.settings(), 'runs': runner.runs(),
                'capabilities': {'trash': True, 'multiPoint': True}}

    def version_record(store, version_id):
        for photo in store.catalog():
            for version in photo['versions']:
                if version['id'] == version_id:
                    if _hash(version['path']) != version['sha256']:
                        raise ValueError('版本源文件已被外部修改，请重新登记；历史记录不会自动替换')
                    return version
        raise ValueError('版本不存在')

    def preview(store, version, thumbnail=False):
        cache = store._managed(".review", "cache")
        if version['mediaType'] != 'image':
            raise ValueError('视频没有静态预览，请打开原视频')
        suffix = 'thumb' if thumbnail else 'preview'
        target = cache / f'{version["id"]}_{suffix}.jpg'
        if target.is_symlink() or not target.resolve().is_relative_to(cache.resolve()):
            raise ValueError('预览文件位置无效')
        if target.exists():
            return target
        with Image.open(version['path']) as source:
            rendered = ImageOps.exif_transpose(source).convert('RGB')
            rendered.thumbnail((720, 540) if thumbnail else (2400, 2400), Image.Resampling.LANCZOS)
            fd, staging = tempfile.mkstemp(prefix='.preview-', suffix='.jpg', dir=cache)
            os.close(fd)
            try:
                rendered.save(staging, quality=87 if thumbnail else 94, subsampling=0)
                os.replace(staging, target)
            finally:
                if os.path.exists(staging):
                    os.unlink(staging)
        return target

    def submit(store, runner, comment_id):
        comment = store.comment_submit(comment_id)
        if comment['status'] != 'open':
            return comment
        try:
            runner.start(comment['photoId'], comment['versionId'], [comment_id])
        except (ValueError, OSError) as error:
            store.comment_reply(comment_id, f'无法启动 Codex：{error}', status='failed')
        return next(c for c in store.comments() if c['id'] == comment_id)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            logging.info('%s %s', self.client_address[0], fmt % args)

        def local_host(self):
            return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}',
                                               f'localhost:{self.server.server_port}')

        def json_response(self, value, status=200):
            data = json.dumps(value, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def file_response(self, path):
            path = Path(path)
            if not path.is_file():
                return self.send_error(404)
            size = path.stat().st_size
            start, end, partial = 0, size - 1, False
            byte_range = self.headers.get('Range')
            if byte_range:
                try:
                    unit, span = byte_range.split('=', 1)
                    a, b = span.split('-', 1)
                    if unit != 'bytes' or ',' in span:
                        raise ValueError()
                    start = int(a) if a else max(0, size - int(b))
                    end = min(int(b), size - 1) if a and b else size - 1
                    if not 0 <= start <= end < size:
                        raise ValueError()
                    partial = True
                except ValueError:
                    return self.send_error(416)
            self.send_response(206 if partial else 200)
            self.send_header('Content-Type', mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
            self.send_header('Content-Length', str(end - start + 1))
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Cache-Control', 'no-cache')
            if partial:
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            self.end_headers()
            try:
                with path.open('rb') as source:
                    source.seek(start)
                    left = end - start + 1
                    while left:
                        chunk = source.read(min(left, 1024 * 1024))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            if not self.local_host():
                return self.send_error(403)
            path = unquote(urlsplit(self.path).path)
            try:
                if path == '/':
                    return self.file_response(CODE / 'web' / 'index.html')
                if path in ('/assets/app.js', '/assets/styles.css'):
                    return self.file_response(CODE / 'web' / path.rsplit('/', 1)[-1])
                if path == '/api/albums':
                    return self.json_response({'albums': albums(), 'token': token, 'libraryRoot': str(library.root)})
                query = parse_qs(urlsplit(self.path).query)
                if path == '/api/directories':
                    return self.json_response(library.browse(query.get('path', [None])[0]))
                store, runner = get_store(query.get('album', [None])[0])
                if path == '/api/catalog':
                    return self.json_response(catalog(store, runner))
                if path == '/api/comments':
                    return self.json_response(store.comments())
                if path == '/api/trash':
                    return self.json_response(store.trash())
                if path == '/api/codex/settings':
                    return self.json_response(runner.settings())
                if path == '/api/codex/runs':
                    return self.json_response(runner.runs())
                bits = path.strip('/').split('/')
                if len(bits) == 3 and bits[0] == 'media' and bits[1] in ('version', 'preview', 'thumb'):
                    version = version_record(store, bits[2])
                    source = version['path'] if bits[1] == 'version' else preview(store, version, bits[1] == 'thumb')
                    return self.file_response(source)
                return self.send_error(404)
            except (ValueError, KeyError) as error:
                return self.json_response({'error': str(error)}, 400)
            except (OSError, sqlite3.Error) as error:
                logging.exception('Read failed')
                return self.json_response({'error': f'本地读取失败：{error}'}, 500)

        def do_POST(self):
            if not self.local_host() or self.headers.get('X-Review-Token') != token:
                return self.json_response({'error': '请求验证失败，请刷新页面'}, 403)
            origin = self.headers.get('Origin')
            if origin and origin not in (f'http://127.0.0.1:{self.server.server_port}',
                                         f'http://localhost:{self.server.server_port}'):
                return self.send_error(403)
            try:
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length <= 32000:
                    raise ValueError('请求内容为空或过长')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError('请求必须为JSON对象')
                path = urlsplit(self.path).path
                if path == '/api/albums/register':
                    return self.json_response({'album': library.register(body['path'])})
                if path == '/api/albums/create':
                    return self.json_response({'album': library.create(body['parent'], body['name'])})
                if path == '/api/albums/open':
                    store, runner = get_store(body.get('albumId'), open_album=True)
                    return self.json_response(catalog(store, runner))
                store, runner = get_store(body.get('albumId'))
                if path == '/api/comments':
                    item = store.comment_add(body['photoId'], body['versionId'], body['text'],
                                             point=body.get('point'), submit=False, comment_id=body.get('id'))
                    if body.get('submit'):
                        item = submit(store, runner, item['id'])
                elif path == '/api/submit':
                    item = submit(store, runner, body['id'])
                elif path == '/api/select':
                    item = store.select(body['photoId'], body.get('versionId'))
                elif path == '/api/accept':
                    item = store.accept_version(body['photoId'], body['versionId'])
                elif path == '/api/trash':
                    item = runner.trash_item(body['photoId'], body.get('versionId'))
                elif path == '/api/restore':
                    item = store.restore_item(body['photoId'], body.get('versionId'))
                elif path == '/api/codex/settings':
                    item = runner.configure(body)
                elif path == '/api/codex/run-all':
                    item = runner.start_all()
                elif path == '/api/codex/run':
                    item = runner.start(body['photoId'], body['versionId'], body.get('commentIds', []))
                elif path == '/api/export':
                    item = store.export(body.get('directory') or None)
                else:
                    return self.send_error(404)
                return self.json_response(item)
            except (ValueError, TypeError, KeyError) as error:
                return self.json_response({'error': str(error)}, 400)
            except (OSError, sqlite3.Error) as error:
                logging.exception('Write failed')
                return self.json_response({'error': f'本地保存失败：{error}'}, 500)

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def serve(batch_path=None, port=0, library_path=None, open_browser=False):
    server = make_server(batch_path, port=port, library_path=library_path)
    root = Path(library_path or batch_path).expanduser().resolve()
    runtime = root / '.photo-review-server' if library_path else root / '.review'
    if runtime.is_symlink():
        raise ValueError('服务数据目录不能是软链接')
    logs = runtime / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    if not logs.resolve().is_relative_to(root) or (logs / 'server.log').is_symlink():
        raise ValueError('日志目录无效')
    logging.basicConfig(filename=logs / 'server.log', level=logging.INFO,
                        format='%(asctime)s %(message)s', encoding='utf-8')
    url = f'http://127.0.0.1:{server.server_port}'
    state = runtime / 'server.json'
    if state.is_symlink():
        raise ValueError('服务状态文件不能是软链接')
    state.write_text(json.dumps({'url': url, 'pid': os.getpid()}, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({'url': url, 'root': str(root)}, ensure_ascii=False), flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
