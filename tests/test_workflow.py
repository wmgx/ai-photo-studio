"""Album-to-Codex publishing checks using synthetic files and a fake CLI."""
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

from PIL import Image

from ai_photo_studio.executor import Runner
from ai_photo_studio.library import Library


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.album = self.root / 'photos' / 'trip'
        self.album.mkdir(parents=True)
        self.source = self.album / 'sample.JPG'
        Image.new('RGB', (24, 16), 'green').save(self.source)
        (self.album / 'sample.nef').write_bytes(b'synthetic raw reference')
        self.original = self.source.read_bytes()
        self.library = Library(self.root / 'photos')
        self.album_id = self.library.albums()[0]['id']
        self.store = self.library.open(self.album_id)
        self.photo = self.store.catalog()[0]
        self.cli = self.root / 'fake-codex'
        self.cli.write_text('#!' + sys.executable + '\n' + '''import json, pathlib, shutil, sys
work = json.loads(pathlib.Path('inputs.json').read_text())
pathlib.Path('arguments.json').write_text(json.dumps(sys.argv[1:]))
shutil.copyfile(work['basePath'], work['candidatePath'])
result = {'status':'revised','candidatePath':work['candidatePath'],
          'label':'示例修订','summary':'合成验证','cropFraction':None,'reply':'请审阅新版本'}
if '越界' in work['comments'][0]['text']:
    result['candidatePath'] = work['originalPath']
pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text(json.dumps(result))
''')
        self.cli.chmod(0o700)
        self.runner = Runner(self.store, executable=self.cli)

    def execute(self, text):
        comment = self.store.comment_add(self.photo['id'], self.photo['currentVersionId'], text, submit=True)
        run = self.runner.start(self.photo['id'], self.photo['currentVersionId'], [comment['id']])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = next(r for r in self.runner.runs() if r['jobId'] == run['jobId'])
            if result['status'] not in ('queued', 'running'):
                return result
            time.sleep(0.02)
        self.fail('Fake Codex did not finish')

    def test_album_edit_preserves_versions_and_model_settings(self):
        self.assertEqual(len(self.library.open(self.album_id).catalog()), 1)
        self.assertEqual(len(self.photo['sources']), 2)
        self.runner.configure({'model':'test-model','reasoning_effort':'high','albumId':self.album_id,
                               'requirements':'秋景暖金色，保留自然肤色'})
        self.runner.configure({'reasoning_effort':'high'})
        self.assertEqual(self.runner.settings()['requirements'], '秋景暖金色，保留自然肤色')
        run = self.execute('调整明暗')
        self.assertEqual(run['status'], 'ready', run)
        self.runner.configure({'requirements':'柔和低饱和度'})
        work = json.loads((Path(run['workDir']) / 'inputs.json').read_text())
        self.assertEqual(work['preferences']['global_requirements'], '秋景暖金色，保留自然肤色')
        self.assertIn('秋景暖金色，保留自然肤色', (Path(run['workDir']) / 'prompt.txt').read_text())
        self.assertEqual(self.store.catalog()[0]['versions'][-1]['summary'], '合成验证')
        self.assertEqual(self.source.read_bytes(), self.original)
        photo = self.store.catalog()[0]
        self.assertEqual(len(photo['versions']), 2)
        self.assertIsNone(photo['selectedVersionId'])
        self.assertEqual(self.store.comments()[0]['resultVersionId'], photo['currentVersionId'])
        args = json.loads((Path(run['workDir']) / 'arguments.json').read_text())
        self.assertEqual(args[args.index('--model')+1], 'test-model')
        self.assertIn('model_reasoning_effort="high"', args)
        self.assertIn('sandbox_workspace_write.writable_roots=[]', args)
        self.store.select(photo['id'], self.photo['currentVersionId'])
        output = self.store.export()
        self.assertEqual(next(Path(output['directory']).rglob('*.jpg')).read_bytes(), self.original)


    def test_album_requirements_queue_all_images(self):
        extra = self.album / 'second.jpg'
        Image.new('RGB', (24, 16), 'blue').save(extra)
        self.library.open(self.album_id)
        self.runner.configure({'requirements': '统一秋景暖色，文字不变'})
        # Hold the worker until the second request has checked duplicate jobs.
        with self.runner._lock:
            result = self.runner.start_all()
            duplicate = self.runner.start_all()
            self.runner.configure({'requirements': '下一批的新要求'})
        self.assertEqual(len(result['started']), 2)
        self.assertEqual(len(duplicate['started']), 0)
        self.assertEqual(len(duplicate['skipped']), 2)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            runs = self.runner.runs()
            if all(r['status'] not in ('queued', 'running') for r in runs):
                break
            time.sleep(0.02)
        self.assertEqual([r['status'] for r in runs], ['ready', 'ready'])
        for run in runs:
            work = json.loads((Path(run['workDir']) / 'inputs.json').read_text())
            self.assertEqual(work['preferences']['global_requirements'], '统一秋景暖色，文字不变')
        self.assertTrue(all(len(p['versions']) == 2 for p in self.store.catalog()))
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_outside_candidate_is_rejected(self):
        run = self.execute('越界候选')
        self.assertEqual(run['status'], 'failed')
        self.assertEqual(len(self.store.catalog()[0]['versions']), 1)
        self.assertEqual(self.store.comments()[0]['status'], 'failed')
        self.assertEqual(self.source.read_bytes(), self.original)


if __name__ == '__main__':
    unittest.main()
