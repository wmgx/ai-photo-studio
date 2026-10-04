"""Run one local Codex edit at a time per photo and publish validated results."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from .planning import PLAN_SCHEMA, inventory, prepare_overview, planning_prompt, validate_plan, verify_materials


EFFORTS = {"", "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
MODEL = re.compile(r"^[A-Za-z0-9_./:+-]{0,100}$")
REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["revised", "needs_input", "failed"]},
        "candidatePath": {"type": ["string", "null"]},
        "label": {"type": ["string", "null"]},
        "summary": {"type": ["string", "null"], "description": "修订时必填：逐项说明相对父版本实际修改的内容，不得编造"},
        "cropFraction": {"type": ["array", "null"], "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
        "reply": {"type": "string"},
    },
    "required": ["status", "candidatePath", "label", "summary", "cropFraction", "reply"],
    "additionalProperties": False,
}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _write_json(path, data):
    path = Path(path)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix="." + path.name + "-",
                                     suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    os.replace(temporary, path)


def _alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class Runner:
    """Background Codex jobs for one initialized Store; no photo data is sent to a service by this code."""

    def __init__(self, store, executable=None):
        self.store = store
        self.executable = str(executable) if executable is not None else shutil.which("codex")
        self._lock = threading.RLock()
        self._pending = deque()
        self._active = 0

    def settings(self):
        project = self.store.project()
        current = project.get("codex") or {}
        return {"model": current.get("model", ""),
                "reasoning_effort": current.get("reasoning_effort", ""),
                "max_concurrency": current.get("max_concurrency", 1),
                "requirements": project.get("preferences", {}).get("global_requirements", ""),
                "available": bool(self.executable and shutil.which(self.executable))}

    def configure(self, values):
        if not isinstance(values, dict) or not set(values).issubset({"model", "reasoning_effort", "max_concurrency", "requirements", "albumId"}):
            raise ValueError("Only model, reasoning_effort, max_concurrency and requirements can be configured")
        with self._lock:
            old = self.settings()
            model = values.get("model", old["model"])
            effort = values.get("reasoning_effort", old["reasoning_effort"])
            concurrency = values.get("max_concurrency", old["max_concurrency"])
            requirements = values.get("requirements", old["requirements"])
            if type(concurrency) is not int or not 1 <= concurrency <= 8:
                raise ValueError("最大并发任务数必须是 1–8 的整数")
            if not isinstance(requirements, str) or len(requirements) > 5000:
                raise ValueError("相册统一要求必须是文字，最多 5000 字")
            if not isinstance(model, str) or not MODEL.fullmatch(model):
                raise ValueError("Invalid model name")
            if not isinstance(effort, str) or effort not in EFFORTS:
                raise ValueError("Invalid reasoning_effort")
            path = self.store._managed("project.json")
            project = json.loads(path.read_text(encoding="utf-8"))
            project["codex"] = {"model": model, "reasoning_effort": effort, "max_concurrency": concurrency}
            project.setdefault("preferences", {})["global_requirements"] = requirements.strip()
            _write_json(path, project)
            self._schedule()
            return self.settings()

    def _run_path(self, job_id):
        return self.store._managed(".review", "work", job_id, "run.json")

    def _save(self, run):
        run["updatedAt"] = _now()
        _write_json(self._run_path(run["jobId"]), run)

    def _all_runs(self):
        work = self.store._managed(".review", "work")
        result = []
        for directory in work.iterdir():
            if directory.is_symlink() or not directory.is_dir():
                continue
            path = directory / "run.json"
            if path.is_symlink() or not path.is_file():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("jobId") == directory.name:
                result.append(data)
        return result

    def runs(self):
        with self._lock:
            result = self._all_runs()
            for run in result:
                if run.get("status") in ("queued", "running") and not _alive(run.get("pid")):
                    self._fail(run, "本机 Codex 任务中断，候选文件仍保留在工作目录。")
            return sorted(result, key=lambda item: item.get("startedAt", ""), reverse=True)

    def trash_item(self, photo_id, version_id=None):
        with self._lock:
            if any(run.get("photoId") == photo_id and run.get("status") in ("queued", "running")
                   for run in self.runs()):
                raise ValueError("这张照片有待处理或正在处理的任务，请等待完成后再删除")
            return self.store.trash_item(photo_id, version_id)

    def start(self, photo_id, version_id=None, comment_ids=None, *, album_context=None):
        if not self.settings()["available"]:
            raise ValueError("Codex CLI is not installed or cannot be run")
        with self._lock:
            for run in self.runs():
                if run.get("photoId") == photo_id and run.get("status") in ("queued", "running"):
                    same_comments = (run.get("commentIds") == comment_ids) if comment_ids is not None else not any(
                        item["status"] == "open" for item in self.store.comments(photo_id=photo_id,
                                                                                   version_id=run["versionId"]))
                    if album_context is None and run.get("versionId") == (version_id or run.get("versionId")) and same_comments:
                        return run
                    raise ValueError("This photo already has a Codex edit running")
            expected = version_id if album_context else None
            work = self.store.start_work(photo_id, version_id, comment_ids, expected_current_id=expected)
            settings = album_context["settings"] if album_context else self.settings()
            if album_context:
                work["preferences"] = album_context["preferences"]
                work["albumTask"] = album_context["task"]
                _write_json(Path(work["workDir"]) / "inputs.json", work)
            run = {"jobId": work["jobId"], "photoId": photo_id,
                   "versionId": work["baseVersionId"],
                   "commentIds": [item["id"] for item in work["comments"]],
                   "status": "queued", "model": settings["model"],
                   "reasoning_effort": settings["reasoning_effort"],
                   "startedAt": _now(), "updatedAt": _now(), "pid": os.getpid(),
                   "workDir": work["workDir"], "resultVersionId": None,
                   "reply": None, "error": None}
            if album_context:
                run["albumJobId"] = album_context["task"]["jobId"]
            self._enqueue(work, run)
            return dict(run)

    def _enqueue(self, work, run):
        self._save(run)
        self._pending.append((work, run))
        self._schedule()

    def _schedule(self):
        # Called under _lock; count reserved slots before any worker starts.
        limit = self.settings()["max_concurrency"]
        while self._pending and self._active < limit:
            work, run = self._pending.popleft()
            self._active += 1
            threading.Thread(target=self._run_job, args=(work, run), daemon=True,
                             name="ai-photo-studio-edit").start()

    def _run_job(self, work, run):
        try:
            self._execute(work, run)
        finally:
            with self._lock:
                self._active -= 1
                self._schedule()

    def start_all(self):
        """Give the album request to one lead AI, then dispatch its concrete tasks."""
        with self._lock:
            settings = self.settings()
            if not settings["requirements"].strip():
                raise ValueError("请先填写并保存相册统一要求")
            if not settings["available"]:
                raise ValueError("Codex CLI is not installed or cannot be run")
            for run in self.runs():
                if run.get("kind") == "album" and run["status"] in ("queued", "running"):
                    return run
            photos = inventory(self.store.catalog())
            if not photos:
                raise ValueError("相册还没有素材，请先加入照片")
            comments = self.store.comments(status="open")
            for photo in photos:
                photo["comments"] = [c for c in comments if c["photoId"] == photo["photoId"]
                                     and c["versionId"] == photo["currentVersion"]["id"]]
            job_id = str(uuid.uuid4())
            work_dir = self.store._managed(".review", "work", job_id)
            work_dir.mkdir(parents=True, exist_ok=False)
            work = {"kind": "album", "jobId": job_id, "workDir": str(work_dir),
                    "preferences": self.store.project().get("preferences", {}),
                    "photos": photos}
            _write_json(work_dir / "inputs.json", work)
            run = {"jobId": job_id, "kind": "album", "photoId": None, "commentIds": [],
                   "status": "queued", "phase": "analyzing", "plan": None, "childJobIds": [], "skipped": [],
                   "model": settings["model"], "reasoning_effort": settings["reasoning_effort"],
                   "startedAt": _now(), "updatedAt": _now(), "pid": os.getpid(),
                   "workDir": str(work_dir), "reply": None, "error": None}
            self._enqueue(work, run)
            return dict(run)

    def _finish_album(self, work, run, report):
        validate_plan(report, work["photos"])
        if report["status"] != "planned":
            run.update(status=report["status"], reply=report["summary"], phase="completed")
            self._save(run)
            return
        run.update(plan={"summary": report["summary"], "tasks": report["tasks"]},
                   reply=report["summary"], phase="processing")
        # Save the full plan before claiming edits, and preserve each dispatch as it happens.
        self._save(run)
        photos = {p["photoId"]: p for p in work["photos"]}
        with self._lock:
            for task in report["tasks"]:
                photo = photos[task["photoId"]]
                context = {"settings": run, "preferences": work["preferences"],
                           "task": {"jobId": run["jobId"], "summary": report["summary"],
                                    "instructions": task["instructions"],
                                    "references": [photos[pid] for pid in task["referenceIds"]]}}
                try:
                    child = self.start(photo["photoId"], photo["currentVersion"]["id"],
                                       [c["id"] for c in photo["comments"]], album_context=context)
                    run["childJobIds"].append(child["jobId"])
                except (ValueError, OSError, sqlite3.Error) as error:
                    run["skipped"].append({"photoId": photo["photoId"], "reason": str(error)})
                self._save(run)
            self._update_album(run["jobId"])

    def _update_album(self, job_id):
        with self._lock:
            run = json.loads(self._run_path(job_id).read_text(encoding="utf-8"))
            if run["status"] != "running" or run["phase"] != "processing":
                return
            children = [json.loads(self._run_path(cid).read_text(encoding="utf-8"))
                        for cid in run["childJobIds"]]
            if any(child["status"] in ("queued", "running") for child in children):
                return
            if any(child["status"] == "failed" for child in children):
                status = "failed"
            elif run["skipped"] or any(child["status"] == "needs_input" for child in children):
                status = "needs_input"
            else:
                status = "ready"
            run.update(status=status, phase="completed")
            self._save(run)

    def _prompt(self, work):
        comments = work["comments"]
        requirements = work.get("preferences", {}).get("global_requirements", "")
        return ("你在本地处理照片审片任务。只在当前工作目录内写候选图、蒙版、脚本和中间文件。"
                "可以只读查看给定的原片、RAW、参考图和历史版本；不得修改或删除它们，也不得改项目配置、数据库、正式版本或评论。"
                "不要调用外部图像生成、上传或远程修图服务；使用本机工具处理素材。"
                "保持人物身份、真实身形、自然皮肤、服饰、文字和现场地貌天气。检查最终图的人脸、手、眼镜、动物、建筑及文字。"
                "原片预览不裁切；成片若裁切，在 cropFraction 中准确提供原图归一化范围。"
                "如果无法可靠完成，返回 needs_input 或 failed，并在 reply 用中文说明。"
                "只输出符合给定 JSON schema 的结果。修订时 candidatePath 必须指向当前工作目录内的真实图像文件，"
                "label、summary、reply 均用中文。summary 不得为空，逐项写明相对父版本实际修改的内容，"
                "包括实际改动的曝光、颜色、构图或人物细节；可用换行列表，不得声称未执行的改动。"
                "未修订时 candidatePath 与 cropFraction 填 null。"
                "若任务材料含 albumTask，你是主 AI 委派的独立编辑子 AI。按其 instructions 修本张主图，"
                "实际查看 references 中相关素材；相册原始要求提供风格和保护约束，任务范围由主 AI 的指令确定，"
                "不要重新规划整册或另行输出参考照片。summary 说明实际使用的参考照片文件名及用途。"
                "单图意见有明确不同要求时以单图意见为准，并保留人物身份与真实场景。"
                "history 是本张照片的连续修改上下文：先结合各版本的 parentId、summary、历史评论和回复，"
                "理解之前做过的修改、用户反馈及应保留的效果，再基于本次 baseVersion 修改。"
                "history 中带 deletedAt 的版本或评论已被用户移入回收站，不代表当前认可的效果；不要据此恢复已删除版本。"
                "history.jobs 的 inputsPath 可只读查看此前主 AI 指令、参考素材和工作目录，"
                "需要复用脚本或蒙版时复制到本次工作目录再修改，绝不改写历史任务文件。"
                "历史上下文仅供参考；本次 comments 和明确的新要求优先，其他版本或未领取的意见不要当作本次任务，"
                "评论的 point 可为单点或多点数组；多点按数组顺序对应用户文字中的编号 1、2、3。"
                "坐标绑定该评论的 versionId，须查看对应版本理解标记，不要自行映射到其他版本。"
                "已被用户否定或撤回的调整不要重复，历史工作文件缺失时仍可根据版本与评论继续。"
                "不要执行 ai-photo-studio 的 publish、select、accept 或 comments reply；本程序会在校验后登记新版本，交由用户审阅。\n\n"
                f"相册统一要求：{requirements or '未设置'}\n\n"
                f"任务材料：{json.dumps(work, ensure_ascii=False, indent=2)}\n\n"
                f"本次要处理的意见：{json.dumps(comments, ensure_ascii=False, indent=2)}\n")

    def _command(self, run, work, schema, result):
        command = [self.executable, "--ask-for-approval", "never", "exec",
                   "--sandbox", "workspace-write", "--cd", work["workDir"],
                   "--skip-git-repo-check", "--ephemeral", "--json",
                   "--config", "sandbox_workspace_write.writable_roots=[]",
                   "--config", "sandbox_workspace_write.network_access=false",
                   "--output-schema", str(schema), "--output-last-message", str(result)]
        if run["model"]:
            command += ["--model", run["model"]]
        if run["reasoning_effort"]:
            command += ["--config", f'model_reasoning_effort="{run["reasoning_effort"]}"']
        return command + ["-"]

    @staticmethod
    def _candidate(work, name):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Codex did not return a candidate file")
        work_dir = Path(work["workDir"])
        path = Path(name)
        path = path if path.is_absolute() else work_dir / path
        try:
            relative = path.relative_to(work_dir)
        except ValueError as exc:
            raise ValueError("Codex candidate is outside the work directory") from exc
        if not relative.parts or any(part in ("", ".", "..") for part in relative.parts):
            raise ValueError("Invalid candidate path")
        current = work_dir
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                raise ValueError("Candidate path contains a symlink")
        if not path.is_file():
            raise ValueError("Codex candidate does not exist")
        return path

    def _finish(self, work, run, report):
        if not isinstance(report, dict) or report.get("status") not in {"revised", "needs_input", "failed"}:
            raise ValueError("Codex returned an invalid result status")
        reply = report.get("reply")
        if not isinstance(reply, str) or not reply.strip():
            raise ValueError("Codex result has no reply")
        if report["status"] == "revised":
            candidate = self._candidate(work, report.get("candidatePath"))
            label = report.get("label")
            summary = report.get("summary")
            if not isinstance(label, str) or not label.strip() or not isinstance(summary, str) or not summary.strip():
                raise ValueError("Codex result needs a label and summary")
            version = self.store.add_version(
                work["photoId"], work["baseVersionId"], candidate, label,
                summary=summary, crop_fraction=report.get("cropFraction"),
                expected_current_id=work["expectedCurrentVersionId"],
                comment_ids=run["commentIds"], operation_id=work["jobId"], job_id=work["jobId"])
            for comment_id in run["commentIds"]:
                self.store.comment_reply(comment_id, reply, "ready", version["id"])
            run.update(status="ready", resultVersionId=version["id"], reply=reply)
        else:
            status = report["status"]
            for comment_id in run["commentIds"]:
                self.store.comment_reply(comment_id, reply, status)
            run.update(status=status, reply=reply)
        self._save(run)

    def _fail(self, run, error):
        run.update(status="failed", error=str(error), reply=str(error))
        for comment_id in run.get("commentIds", []):
            try:
                self.store.comment_reply(comment_id, str(error), "failed")
            except (ValueError, OSError, sqlite3.Error):
                pass
        self._save(run)

    def _execute(self, work, run):
        work_dir = Path(work["workDir"])
        schema = work_dir / "report_schema.json"
        result = work_dir / "result.json"
        try:
            with self._lock:
                run["status"] = "running"
                self._save(run)
            is_album = run.get("kind") == "album"
            if is_album:
                prepare_overview(work)
                _write_json(work_dir / "inputs.json", work)
            elif work.get("albumTask"):
                verify_materials(work["albumTask"]["references"])
            _write_json(schema, PLAN_SCHEMA if is_album else REPORT_SCHEMA)
            prompt = planning_prompt() if is_album else self._prompt(work)
            (work_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
            with (work_dir / "events.jsonl").open("w", encoding="utf-8") as output, \
                    (work_dir / "stderr.log").open("w", encoding="utf-8") as errors:
                completed = subprocess.run(self._command(run, work, schema, result),
                                           cwd=work_dir, input=prompt, text=True,
                                           stdout=output, stderr=errors, timeout=3600, check=False)
            if completed.returncode:
                raise ValueError(f"Codex exited with status {completed.returncode}; see stderr.log")
            report = json.loads(result.read_text(encoding="utf-8"))
            if is_album:
                self._finish_album(work, run, report)
            else:
                self._finish(work, run, report)
        except Exception as exc:
            self._fail(run, f"本机 Codex 处理未完成：{exc}")
        finally:
            if run.get("albumJobId"):
                self._update_album(run["albumJobId"])
