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
from datetime import datetime, timezone
from pathlib import Path


EFFORTS = {"", "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
MODEL = re.compile(r"^[A-Za-z0-9_./:+-]{0,100}$")
REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["revised", "needs_input", "failed"]},
        "candidatePath": {"type": ["string", "null"]},
        "label": {"type": ["string", "null"]},
        "summary": {"type": ["string", "null"]},
        "cropFraction": {"type": ["array", "null"], "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
        "reply": {"type": "string"},
    },
    "required": ["status", "candidatePath", "label", "summary", "cropFraction", "reply"],
    "additionalProperties": False,
}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
        self._threads = {}

    def settings(self):
        current = self.store.project().get("codex") or {}
        return {"model": current.get("model", ""),
                "reasoning_effort": current.get("reasoning_effort", ""),
                "available": bool(self.executable and shutil.which(self.executable))}

    def configure(self, values):
        if not isinstance(values, dict) or not set(values).issubset({"model", "reasoning_effort", "albumId"}):
            raise ValueError("Only model and reasoning_effort can be configured")
        with self._lock:
            old = self.settings()
            model = values.get("model", old["model"])
            effort = values.get("reasoning_effort", old["reasoning_effort"])
            if not isinstance(model, str) or not MODEL.fullmatch(model):
                raise ValueError("Invalid model name")
            if not isinstance(effort, str) or effort not in EFFORTS:
                raise ValueError("Invalid reasoning_effort")
            path = self.store._managed("project.json")
            project = json.loads(path.read_text(encoding="utf-8"))
            project["codex"] = {"model": model, "reasoning_effort": effort}
            _write_json(path, project)
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
                if run.get("status") == "running" and not _alive(run.get("pid")):
                    self._fail(run, "本机 Codex 任务中断，候选文件仍保留在工作目录。")
            return sorted(result, key=lambda item: item.get("startedAt", ""), reverse=True)

    def start(self, photo_id, version_id=None, comment_ids=None):
        if not self.settings()["available"]:
            raise ValueError("Codex CLI is not installed or cannot be run")
        with self._lock:
            for run in self.runs():
                if run.get("photoId") == photo_id and run.get("status") == "running":
                    same_comments = (run.get("commentIds") == comment_ids) if comment_ids is not None else not any(
                        item["status"] == "open" for item in self.store.comments(photo_id=photo_id,
                                                                                   version_id=run["versionId"]))
                    if run.get("versionId") == (version_id or run.get("versionId")) and same_comments:
                        return run
                    raise ValueError("This photo already has a Codex edit running")
            work = self.store.start_work(photo_id, version_id, comment_ids)
            settings = self.settings()
            run = {"jobId": work["jobId"], "photoId": photo_id,
                   "versionId": work["baseVersionId"],
                   "commentIds": [item["id"] for item in work["comments"]],
                   "status": "running", "model": settings["model"],
                   "reasoning_effort": settings["reasoning_effort"],
                   "startedAt": _now(), "updatedAt": _now(), "pid": os.getpid(),
                   "workDir": work["workDir"], "resultVersionId": None,
                   "reply": None, "error": None}
            self._save(run)
            worker = threading.Thread(target=self._execute, args=(work, run), daemon=True,
                                      name=f"ai-photo-studio-{run['jobId']}")
            self._threads[run["jobId"]] = worker
            worker.start()
            return dict(run)

    def _prompt(self, work):
        comments = work["comments"]
        return ("你在本地处理照片审片任务。只在当前工作目录内写候选图、蒙版、脚本和中间文件。"
                "可以只读查看给定的原片、RAW、参考图和历史版本；不得修改或删除它们，也不得改项目配置、数据库、正式版本或评论。"
                "不要调用外部图像生成、上传或远程修图服务；使用本机工具处理素材。"
                "保持人物身份、真实身形、自然皮肤、服饰、文字和现场地貌天气。检查最终图的人脸、手、眼镜、动物、建筑及文字。"
                "原片预览不裁切；成片若裁切，在 cropFraction 中准确提供原图归一化范围。"
                "如果无法可靠完成，返回 needs_input 或 failed，并在 reply 用中文说明。"
                "只输出符合给定 JSON schema 的结果。修订时 candidatePath 必须指向当前工作目录内的真实图像文件，"
                "label、summary、reply 均用中文；未修订时 candidatePath 与 cropFraction 填 null。"
                "不要执行 ai-photo-studio 的 publish、select、accept 或 comments reply；本程序会在校验后登记新版本，交由用户审阅。\n\n"
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
            if not isinstance(label, str) or not label.strip() or not isinstance(summary, str):
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
            _write_json(schema, REPORT_SCHEMA)
            prompt = self._prompt(work)
            (work_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
            with (work_dir / "events.jsonl").open("w", encoding="utf-8") as output, \
                    (work_dir / "stderr.log").open("w", encoding="utf-8") as errors:
                completed = subprocess.run(self._command(run, work, schema, result),
                                           cwd=work_dir, input=prompt, text=True,
                                           stdout=output, stderr=errors, timeout=3600, check=False)
            if completed.returncode:
                raise ValueError(f"Codex exited with status {completed.returncode}; see stderr.log")
            report = json.loads(result.read_text(encoding="utf-8"))
            self._finish(work, run, report)
        except Exception as exc:
            self._fail(run, f"本机 Codex 处理未完成：{exc}")
        finally:
            with self._lock:
                self._threads.pop(run["jobId"], None)
