import csv
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from ai_photo_studio.store import Store, init_batch
from ai_photo_studio.executor import Runner


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.original = self.root / "original.jpg"
        Image.new("RGB", (20, 12), "red").save(self.original)
        init_batch(self.root / "batch", preferences={"tone": "natural"})
        self.store = Store(self.root / "batch")

    def test_comment_points_keep_order_in_work_materials(self):
        photo = self.store.add_photo(self.original, "p1")
        base = photo["currentVersionId"]
        points = [{"x": 0.2, "y": 0.7}, {"x": 0.8, "y": 0.3}]
        multiple = self.store.comment_add("p1", base, "1 帽檐；2 手指", points, submit=True)
        single = self.store.comment_add("p1", base, "眼神", {"x": 0.5, "y": 0.5}, submit=True)
        empty = self.store.comment_add("p1", base, "整体颜色", [], submit=True)
        self.assertEqual(multiple["point"], points)
        self.assertEqual(single["point"], {"x": 0.5, "y": 0.5})
        self.assertIsNone(empty["point"])
        work = self.store.start_work("p1")
        saved = json.loads((Path(work["workDir"]) / "inputs.json").read_text(encoding="utf-8"))
        self.assertEqual([item["point"] for item in saved["comments"]],
                         [points, {"x": 0.5, "y": 0.5}, None])
        with self.assertRaisesRegex(ValueError, "normalized"):
            self.store.comment_add("p1", base, "无效", [{"x": 0.1, "y": 0.2}, {"x": True, "y": 0.3}])

    def test_version_comment_selection_and_export(self):
        self.assertEqual(self.store.project()["root"], str((self.root / "batch").resolve()))
        photo = self.store.add_photo(self.original, "p1", "人物", source_paths=[str(self.original)])
        self.assertEqual(photo["scene"], "original")
        self.assertEqual(photo["sources"], [str(self.original.resolve())])
        original_id = photo["currentVersionId"]
        point = {"x": 0.4, "y": 0.5}
        comment = self.store.comment_add("p1", original_id, "亮一点", point, submit=True, comment_id="comment-1")
        self.assertEqual(self.store.comment_add("p1", original_id, "亮一点", point, comment_id="comment-1"), comment)
        with self.assertRaisesRegex(ValueError, "different parameters"):
            self.store.comment_add("p1", original_id, "暗一点", point, comment_id="comment-1")
        job = self.store.start_work("p1")
        self.assertEqual(job["comments"][0]["id"], comment["id"])
        self.assertEqual(job["preferences"], {"tone": "natural"})
        with self.assertRaisesRegex(ValueError, "already claimed"):
            self.store.start_work("p1")
        Image.new("RGB", (16, 12), "blue").save(job["candidatePath"])
        revision = self.store.add_version("p1", original_id, job["candidatePath"], "明亮版", summary="提亮主体并收紧构图",
                                          expected_current_id=original_id, comment_ids=[comment["id"]],
                                          operation_id="edit-1", job_id=job["jobId"])
        self.assertEqual(revision["summary"], "提亮主体并收紧构图")
        self.assertEqual(revision["parentId"], original_id)
        self.assertEqual(revision["dimensions"], [16, 12])
        self.assertTrue(Path(revision["path"]).is_file())
        self.assertEqual(self.store.comments(version_id=original_id)[0]["resultVersionId"], revision["id"])
        replied = self.store.comment_reply(comment["id"], "已调整")
        self.assertEqual(replied["resultVersionId"], revision["id"])
        self.store.accept_version("p1", revision["id"])
        self.assertEqual(self.store.comments(version_id=original_id)[0]["status"], "resolved")
        with self.assertRaisesRegex(ValueError, "Resolved"):
            self.store.comment_reply(comment["id"], "旧任务回填", status="failed")
        self.store.select("p1", revision["id"])
        result = self.store.export(self.root)
        self.assertEqual(Path(result["directory"]).parent, self.root)
        self.assertEqual(result["count"], 1)
        with Path(result["manifestFile"]).open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(rows[0]["sha256"], revision["sha256"])
        self.assertTrue((Path(result["directory"]) / rows[0]["output"]).is_file())

    def test_publish_rejects_stale_current_and_changed_operation(self):
        photo = self.store.add_photo(self.original, "p1")
        base = photo["currentVersionId"]
        with self.assertRaisesRegex(ValueError, "Select at least one"):
            self.store.export()
        failed = self.store.comment_add("p1", base, "重做", submit=True)
        self.store.comment_reply(failed["id"], "候选损坏", status="failed")
        self.assertEqual(self.store.comment_submit(failed["id"])["status"], "open")
        job = self.store.start_work("p1")
        candidate = self.root / "candidate.png"
        Image.new("RGB", (10, 10), "green").save(candidate)
        with self.assertRaisesRegex(ValueError, "inside its work directory"):
            self.store.add_version("p1", base, candidate, "A", summary="调整色调", expected_current_id=base,
                                   operation_id="job-op", job_id=job["jobId"])
        with self.assertRaisesRegex(ValueError, "change summary"):
            self.store.add_version("p1", base, candidate, "缺少说明", summary="  ", expected_current_id=base,
                                   operation_id="empty-summary")
        first = self.store.add_version("p1", base, candidate, "A", summary="调整色调", expected_current_id=base,
                                       operation_id="op-1")
        with self.assertRaisesRegex(ValueError, "Current version changed since album analysis"):
            self.store.start_work("p1", base, expected_current_id=base)
        retry = self.store.add_version("p1", base, candidate, "A", summary="调整色调", expected_current_id=base,
                                       operation_id="op-1")
        self.assertEqual(first["id"], retry["id"])
        with self.assertRaisesRegex(ValueError, "different parameters"):
            self.store.add_version("p1", base, candidate, "B", summary="调整色调", expected_current_id=base,
                                   operation_id="op-1")
        with self.assertRaisesRegex(ValueError, "Current version changed"):
            self.store.add_version("p1", base, candidate, "B", summary="调整色调", expected_current_id=base,
                                   operation_id="op-2")
        self.assertEqual(len(self.store.catalog()[0]["versions"]), 2)

    def test_recycle_version_and_photo_preserves_files_and_restores_history(self):
        # Opening an older album installs the small metadata table in place.
        with sqlite3.connect(self.store.db_path) as db:
            db.execute("DROP TABLE tombstones")
        self.store = Store(self.root / "batch")
        photo = self.store.add_photo(self.original, "p1")
        original_id = photo["currentVersionId"]
        candidate = self.root / "revision.jpg"
        Image.new("RGB", (20, 12), "blue").save(candidate)
        revision = self.store.add_version("p1", original_id, candidate, "秋色版", summary="调整秋色",
                                          expected_current_id=original_id)
        comment = self.store.comment_add("p1", revision["id"], "保留暖色")
        self.store.select("p1", revision["id"])

        self.store.trash_item("p1", revision["id"])
        self.assertEqual(self.store.catalog()[0]["currentVersionId"], original_id)
        self.assertEqual(self.store.catalog()[0]["selectedVersionId"], None)
        self.assertEqual(len(self.store.catalog()[0]["versions"]), 1)
        self.assertEqual(self.store.comments(), [])
        self.assertTrue(Path(revision["path"]).is_file())
        with self.assertRaisesRegex(ValueError, "回收站"):
            self.store.select("p1", revision["id"])
        with self.assertRaisesRegex(ValueError, "回收站"):
            self.store.comment_submit(comment["id"])
        with self.assertRaisesRegex(ValueError, "回收站"):
            self.store.comment_reply(comment["id"], "旧版回复")
        with self.assertRaisesRegex(ValueError, "原片版本"):
            self.store.trash_item("p1", original_id)
        with self.assertRaisesRegex(ValueError, "Select at least one"):
            self.store.export()

        self.store.restore_item("p1", revision["id"])
        self.assertEqual(len(self.store.catalog()[0]["versions"]), 2)
        self.assertEqual(self.store.catalog()[0]["currentVersionId"], original_id)
        self.assertEqual(self.store.comments()[0]["id"], comment["id"])
        self.store.select("p1", revision["id"])
        self.store.trash_item("p1", revision["id"])
        self.store.trash_item("p1")
        self.assertEqual(self.store.catalog(), [])
        self.assertEqual([entry["kind"] for entry in self.store.trash()], ["photo"])
        with self.assertRaisesRegex(ValueError, "照片已移入回收站"):
            self.store.restore_item("p1", revision["id"])
        with self.assertRaisesRegex(ValueError, "回收站"):
            self.store.start_work("p1")
        self.store.restore_item("p1")
        self.assertEqual(self.store.catalog()[0]["selectedVersionId"], None)
        self.assertEqual(len(self.store.catalog()[0]["versions"]), 1)
        self.store.restore_item("p1", revision["id"])
        self.assertTrue(self.original.is_file())
        self.assertTrue(Path(revision["path"]).is_file())

    def test_recycle_refuses_photo_with_active_job(self):
        photo = self.store.add_photo(self.original, "p1")
        runner = Runner(self.store)
        work_dir = self.store._managed(".review", "work", "queued-test")
        work_dir.mkdir()
        run = {"jobId": "queued-test", "photoId": "p1", "versionId": photo["currentVersionId"],
               "status": "queued", "pid": os.getpid(), "startedAt": "2026-01-01", "workDir": str(work_dir)}
        runner._save(run)
        with self.assertRaisesRegex(ValueError, "待处理或正在处理"):
            runner.trash_item("p1")
        self.assertEqual(self.store.trash(), [])
        run["status"] = "ready"
        runner._save(run)
        runner.trash_item("p1")
        self.assertEqual(self.store.trash()[0]["photoId"], "p1")


if __name__ == "__main__":
    unittest.main()
