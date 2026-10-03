import csv
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from ai_photo_studio.store import Store, init_batch


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.original = self.root / "original.jpg"
        Image.new("RGB", (20, 12), "red").save(self.original)
        init_batch(self.root / "batch", preferences={"tone": "natural"})
        self.store = Store(self.root / "batch")

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


if __name__ == "__main__":
    unittest.main()
