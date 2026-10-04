"""Album-to-Codex publishing checks using synthetic files and a fake CLI."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

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
if work.get('kind') == 'album':
    chosen = next(p['photoId'] for p in work['photos'] if p['currentVersion']['mediaType'] == 'image')
    tasks = [{'photoId': chosen,
              'referenceIds': [p['photoId'] for p in work['photos'] if p['photoId'] != chosen],
              'instructions': '自然暖金秋色，参考其他素材调整构图'}]
    if '未知素材' in work['preferences']['global_requirements']:
        tasks[0]['referenceIds'].append('not-in-this-album')
    if '只提建议' in work['preferences']['global_requirements']:
        tasks = []
    result = {'status': 'planned', 'summary': '主 AI 已判断处理方式', 'tasks': tasks}

else:
    shutil.copyfile(work['basePath'], work['candidatePath'])
    result = {'status':'revised','candidatePath':work['candidatePath'],
              'label':'示例修订','summary':'合成验证','cropFraction':None,'reply':'请审阅新版本'}
    if work['comments'] and '越界' in work['comments'][0]['text']:
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
        self.assertEqual(self.runner.settings()['max_concurrency'], 1)
        with self.assertRaises(ValueError):
            self.runner.configure({'max_concurrency': 0})
        self.runner.configure({'model':'test-model','reasoning_effort':'high','albumId':self.album_id,
                               'max_concurrency': 2,
                               'requirements':'秋景暖金色，保留自然肤色'})
        self.runner.configure({'reasoning_effort':'high'})
        self.assertEqual(Runner(self.store, executable=self.cli).settings()['max_concurrency'], 2)
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


    def wait_album(self, job_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = next(r for r in self.runner.runs() if r['jobId'] == job_id)
            if result['status'] not in ('queued', 'running'):
                return result
            time.sleep(0.02)
        self.fail('Fake album analysis did not finish')

    def test_lead_ai_dispatches_explicit_tasks_and_can_return_advice_only(self):
        extra = self.album / 'second.jpg'
        Image.new('RGB', (24, 16), 'blue').save(extra)
        video = self.album / 'clip.mov'
        video.write_bytes(b'synthetic motion reference')
        self.library.open(self.album_id)
        self.runner.configure({'model': 'planning-model', 'reasoning_effort': 'high',
                               'requirements': '先按场景连拍分组，每组选最佳几张，统一秋景暖色'})
        comment = self.store.comment_add(self.photo['id'], self.photo['currentVersionId'], '保留衣服文字', submit=True)
        # Keep planning queued while testing repeated clicks and preference changes.
        with self.runner._lock:
            run = self.runner.start_all()
            duplicate = self.runner.start_all()
            self.assertEqual(len(self.runner.runs()), 1)
            self.assertTrue(all(len(p['versions']) == 1 for p in self.store.catalog()))
            later_comment = self.store.comment_add(self.photo['id'], self.photo['currentVersionId'], '稍后处理的意见', submit=True)
            self.runner.configure({'model': 'next-model', 'requirements': '下一批的新要求'})
        self.assertEqual(duplicate['jobId'], run['jobId'])
        result = self.wait_album(run['jobId'])
        self.assertEqual(result['status'], 'ready', result)
        self.assertEqual(result['phase'], 'completed')
        self.assertEqual(len(result['childJobIds']), 1)
        self.assertEqual(len(result['plan']['tasks']), 1)
        task = result['plan']['tasks'][0]
        chosen = task['photoId']
        child = next(r for r in self.runner.runs() if r['jobId'] in result['childJobIds'])
        inputs = json.loads((Path(child['workDir']) / 'inputs.json').read_text())
        self.assertEqual(inputs['preferences']['global_requirements'], '先按场景连拍分组，每组选最佳几张，统一秋景暖色')
        self.assertEqual({p['photoId'] for p in inputs['albumTask']['references']}, set(task['referenceIds']))
        self.assertEqual(inputs['albumTask']['instructions'], task['instructions'])
        self.assertEqual([c['id'] for c in inputs['comments']], [comment['id']])
        self.assertEqual(next(c for c in self.store.comments() if c['id'] == later_comment['id'])['status'], 'open')
        self.assertEqual(child['model'], 'planning-model')
        overview = json.loads((Path(run['workDir']) / 'inputs.json').read_text())
        self.assertTrue(Path(overview['contactSheets'][0]).is_file())
        self.assertEqual({p['overviewIndex'] for p in overview['photos']}, {1, 2, 3})
        self.assertTrue(all(len(p['versions']) == (2 if p['id'] == chosen else 1) for p in self.store.catalog()))
        self.assertTrue(all(p['selectedVersionId'] is None for p in self.store.catalog()))
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertEqual(video.read_bytes(), b'synthetic motion reference')
        before = self.store.catalog()
        self.runner.configure({'requirements': '只提建议，先不用修图'})
        advice = self.wait_album(self.runner.start_all()['jobId'])
        self.assertEqual(advice['status'], 'ready', advice)
        self.assertEqual(advice['childJobIds'], [])
        self.assertEqual(advice['plan']['tasks'], [])
        self.assertEqual(self.store.catalog(), before)

    def test_unknown_task_reference_does_not_dispatch_edits(self):
        Image.new('RGB', (24, 16), 'blue').save(self.album / 'second.jpg')
        self.library.open(self.album_id)
        self.runner.configure({'requirements': '模拟未知素材的方案'})
        run = self.runner.start_all()
        result = self.wait_album(run['jobId'])
        self.assertEqual(result['status'], 'failed')
        self.assertIn('未知参考素材', result['error'])
        self.assertEqual(result['childJobIds'], [])
        self.assertEqual(len(self.runner.runs()), 1)
        self.assertTrue(all(len(p['versions']) == 1 for p in self.store.catalog()))

    def test_followup_keeps_photo_history_across_runner_restarts(self):
        first = self.execute('先提亮脸部，保持肤色')
        self.assertEqual(first['status'], 'ready')
        self.photo = self.store.catalog()[0]
        first_version = self.photo['currentVersionId']
        first_comment = self.store.comments()[0]
        Image.new('RGB', (24, 16), 'blue').save(self.album / 'other.jpg')
        other = next(p for p in self.library.open(self.album_id).catalog() if p['id'] != self.photo['id'])
        self.store.comment_add(other['id'], other['currentVersionId'], '另一张照片的意见', submit=True)
        self.runner = Runner(self.store, executable=self.cli)
        second = self.execute('保留上次的亮度，只整理帽檐')
        self.assertEqual(second['status'], 'ready')
        work = json.loads((Path(second['workDir']) / 'inputs.json').read_text())
        history = work['history']
        self.assertEqual(work['baseVersionId'], first_version)
        self.assertEqual([v['id'] for v in history['versions']], [v['id'] for v in self.photo['versions']])
        self.assertEqual(history['versions'][-1]['summary'], '合成验证')
        self.assertEqual(history['comments'], [first_comment])
        self.assertEqual([j['jobId'] for j in history['jobs']], [first['jobId']])
        previous_inputs = json.loads(Path(history['jobs'][0]['inputsPath']).read_text())
        self.assertEqual(previous_inputs['comments'][0]['text'], '先提亮脸部，保持肤色')
        self.assertEqual([c['text'] for c in work['comments']], ['保留上次的亮度，只整理帽檐'])
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_concurrency_changes_fill_slots_and_drain_without_cancelling(self):
        for index in range(3):
            Image.new('RGB', (24, 16), 'blue').save(self.album / f'extra{index}.jpg')
        photos = self.library.open(self.album_id).catalog()
        self.runner.configure({'max_concurrency': 2})
        entered = threading.Condition()
        gates = {}
        draining = False
        from ai_photo_studio.executor import subprocess
        real_run = subprocess.run

        def gated_run(*args, **kwargs):
            gate = threading.Event()
            with entered:
                gates[Path(kwargs['cwd']).name] = gate
                if draining:
                    gate.set()
                entered.notify_all()
            if not gate.wait(5):
                raise TimeoutError('Test did not release fake Codex')
            return real_run(*args, **kwargs)

        with patch('ai_photo_studio.executor.subprocess.run', side_effect=gated_run):
            jobs = [self.runner.start(p['id']) for p in photos]
            try:
                with entered:
                    self.assertTrue(entered.wait_for(lambda: len(gates) == 2, timeout=5))
                self.assertEqual(self.runner.start(photos[0]['id'])['jobId'], jobs[0]['jobId'])
                self.runner.configure({'max_concurrency': 3})
                with entered:
                    self.assertTrue(entered.wait_for(lambda: len(gates) == 3, timeout=5))
                self.runner.configure({'max_concurrency': 1})
                for job in jobs[:2]:
                    gates[job['jobId']].set()
                    self.assertEqual(self.wait_album(job['jobId'])['status'], 'ready')
                self.assertEqual(next(r for r in self.runner.runs() if r['jobId'] == jobs[3]['jobId'])['status'], 'queued')
                gates[jobs[2]['jobId']].set()
                with entered:
                    self.assertTrue(entered.wait_for(lambda: len(gates) == 4, timeout=5))
                gates[jobs[3]['jobId']].set()
                self.assertTrue(all(self.wait_album(j['jobId'])['status'] == 'ready' for j in jobs))
                self.assertTrue(all(len(p['versions']) == 2 for p in self.store.catalog()))
            finally:
                with entered:
                    draining = True
                    for gate in gates.values():
                        gate.set()
                for job in jobs:
                    self.wait_album(job['jobId'])

    def test_outside_candidate_is_rejected(self):
        run = self.execute('越界候选')
        self.assertEqual(run['status'], 'failed')
        self.assertEqual(len(self.store.catalog()[0]['versions']), 1)
        self.assertEqual(self.store.comments()[0]['status'], 'failed')
        self.assertEqual(self.source.read_bytes(), self.original)


if __name__ == '__main__':
    unittest.main()
