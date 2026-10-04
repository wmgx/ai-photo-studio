"""Read-only album materials and a small task contract for the lead AI."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from .store import _hash


PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["planned", "needs_input", "failed"]},
        "summary": {"type": "string"},
        "tasks": {
            "type": "array", "items": {
                "type": "object",
                "properties": {
                    "photoId": {"type": "string"},
                    "referenceIds": {"type": "array", "items": {"type": "string"}},
                    "instructions": {"type": "string"},
                },
                "required": ["photoId", "referenceIds", "instructions"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["status", "summary", "tasks"],
    "additionalProperties": False,
}


def inventory(catalog):
    return [{"photoId": photo["id"], "scene": photo["scene"], "sources": photo["sources"],
             "originalVersion": next(v for v in photo["versions"] if v["kind"] == "original"),
             "currentVersion": next(v for v in photo["versions"] if v["id"] == photo["currentVersionId"])}
            for photo in catalog]


def verify_materials(photos):
    checked = set()
    for photo in photos:
        for version in (photo["originalVersion"], photo["currentVersion"]):
            if version["id"] not in checked:
                if _hash(version["path"]) != version["sha256"]:
                    raise ValueError(f"素材已变化，请刷新后重新分析：{photo['photoId']}")
                checked.add(version["id"])


def prepare_overview(work):
    """Write numbered, uncropped original contact sheets only inside this job."""
    photos = work["photos"]
    verify_materials(photos)
    for photo in photos:
        photo["capturedAt"] = None
        if photo["originalVersion"]["mediaType"] == "image":
            with Image.open(photo["originalVersion"]["path"]) as source:
                exif = source.getexif()
                details = exif.get_ifd(34665)
                photo["capturedAt"] = str(details.get(36867) or exif.get(306) or "") or None
    photos.sort(key=lambda p: (p["capturedAt"] or "~", Path(p["originalVersion"]["path"]).name))
    work["contactSheets"] = []
    for start in range(0, len(photos), 12):
        sheet = Image.new("RGB", (1280, 810), "#202124")
        draw = ImageDraw.Draw(sheet)
        for offset, photo in enumerate(photos[start:start + 12]):
            photo["overviewIndex"] = start + offset + 1
            x, y = (offset % 4) * 320, (offset // 4) * 270
            version = photo["originalVersion"]
            if version["mediaType"] == "image":
                with Image.open(version["path"]) as source:
                    preview = ImageOps.exif_transpose(source).convert("RGB")
                    preview.thumbnail((312, 240), Image.Resampling.LANCZOS)
                    sheet.paste(preview, (x + (320 - preview.width) // 2, y + (240 - preview.height) // 2))
            label = f"#{photo['overviewIndex']}  {version['mediaType']}"
            draw.text((x + 8, y + 248), label, fill="white")
        path = Path(work["workDir"]) / f"overview-{start // 12 + 1:03d}.jpg"
        sheet.save(path, quality=88)
        work["contactSheets"].append(str(path))


def validate_plan(report, photos):
    """Validate dispatch targets, not the AI's editing/selection strategy."""
    if not isinstance(report, dict) or report.get("status") not in {"planned", "needs_input", "failed"}:
        raise ValueError("主 AI 返回的状态无效")
    if not isinstance(report.get("summary"), str) or not report["summary"].strip():
        raise ValueError("主 AI 缺少处理说明")
    if report["status"] != "planned":
        return
    available = {p["photoId"]: p for p in photos}
    seen = set()
    if not isinstance(report.get("tasks"), list):
        raise ValueError("主 AI 缺少子任务列表，可用空列表表示无需修图")
    for task in report["tasks"]:
        if not isinstance(task, dict) or not isinstance(task.get("photoId"), str):
            raise ValueError("子任务主图无效")
        pid = task["photoId"]
        if pid not in available or pid in seen:
            raise ValueError("子任务包含未知或重复主图")
        if not isinstance(task.get("instructions"), str) or not task["instructions"].strip():
            raise ValueError("子任务缺少具体指令")
        refs = task.get("referenceIds")
        if not isinstance(refs, list) or not all(isinstance(ref, str) and ref in available for ref in refs):
            raise ValueError("子任务包含未知参考素材")
        if available[pid]["currentVersion"]["mediaType"] != "image":
            raise ValueError("目前自动编辑子任务仅支持静态照片，动态素材保留原文件")
        seen.add(pid)


def planning_prompt():
    return (
        "你是这个相册的主 AI。用户写给整个相册的要求就是给你的任务。"
        "读取当前目录 inputs.json 中的 preferences.global_requirements、其他偏好、素材版本和已提交意见，"
        "理解用户要得到的结果，自行决定怎样查看素材、是否分组或选片、处理哪些照片以及怎样分工。"
        "不要把分组、选片或逐张修图当作每次必走流程。用户要求全部调色就安排全部适合处理的照片；"
        "要求挑最佳几张才比较挑选；只要建议时可以直接说明，tasks 为空。"
        "contactSheets 是可用的原片总览，overviewIndex 对应编号；按判断需要打开原片、最新版本及局部，"
        "不能仅凭文件名或拍摄时间编造视觉结论。需要跨素材比较时实际查看相关素材。"
        "用 summary 向用户说明你的判断、处理方案和未处理部分。"
        "需要修图时输出具体 tasks，每个任务指定一张主图 photoId、任意相关参考图 referenceIds 和中文 instructions。"
        "每个任务会交给独立的子 AI 执行；把期望效果、参考用途、风格和保护要求写清楚，使其无需重新猜整册要求。"
        "同一主图只安排一次；拼接或表情参考可以指定多张参考图，输出挂在主图的新版本下。"
        "只用本机工具查看素材，不调用外部图像处理或上传服务。原片、RAW、历史版本只读；"
        "本阶段只判断和派任务，不修图，不发布版本，不替用户选择导出版本。只在当前工作目录写分析材料。"
        "当前子任务接口只支持静态照片修订；动态素材保持原文件。请求超出此能力时说明限制，不能声称完成。"
        "信息不足或无法可靠判断时返回 needs_input 并说明需要什么；无法执行时返回 failed。"
        "输出符合给定 schema 的结果，status=planned 才执行 tasks，空 tasks 表示无需启动修图。"
        "本次输入以 inputs.json 快照为准。"
    )
