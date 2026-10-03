"""Album navigation and creation use only temporary directories."""
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from ai_photo_studio.library import Library


class LibraryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.photos = self.root / 'photos'
        self.photos.mkdir()
        self.library = Library(self.photos)

    def test_open_external_and_create_empty_album_then_refresh(self):
        outside = self.root / 'existing-album'
        outside.mkdir()
        image = outside / 'sample.jpg'
        Image.new('RGB', (20, 12), 'blue').save(image)
        source_bytes = image.read_bytes()
        registered = self.library.register(str(outside))
        self.assertEqual(len(self.library.open(registered['id']).catalog()), 1)
        self.assertEqual(self.library.register(outside)['id'], registered['id'])
        created = self.library.create(self.photos, '新相册')
        self.assertEqual(created['count'], 0)
        self.assertEqual(self.library.open(created['id']).catalog(), [])
        reloaded = Library(self.photos)
        self.assertEqual({a['id'] for a in reloaded.albums()}, {registered['id'], created['id']})
        self.assertIn(str(outside), [c['path'] for c in reloaded.browse(self.root)['children']])
        Image.new('RGB', (24, 16), 'green').save(Path(created['path']) / 'new.jpg')
        self.assertEqual(len(reloaded.open(created['id']).catalog()), 1)
        self.assertEqual(image.read_bytes(), source_bytes)

    def test_create_rejects_path_escape_and_existing_directory(self):
        with self.assertRaises(ValueError):
            self.library.create(self.photos, '../escape')
        self.assertFalse((self.root / 'escape').exists())
        existing = self.photos / 'existing'
        existing.mkdir()
        marker = existing / 'keep.txt'
        marker.write_text('preserve me')
        with self.assertRaisesRegex(ValueError, '已存在'):
            self.library.create(self.photos, 'existing')
        self.assertEqual(marker.read_text(), 'preserve me')


if __name__ == '__main__':
    unittest.main()
