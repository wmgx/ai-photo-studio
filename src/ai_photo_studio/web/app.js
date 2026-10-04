"use strict";
const $ = (id) => document.getElementById(id);
let items = [],
  comments = [],
  filtered = [],
  project = { name: "AI 修图工作台", root: "" },
  albums = [],
  albumId = "",
  libraryRoot = "",
  codex = { model: "", reasoning_effort: "", requirements: "" },
  runs = [],
  selectedAlbumJobId = "",
  albumGeneration = 0;
let albumLoading = false,
  albumDialogMode = "open",
  albumDialogSaving = false,
  directoryGeneration = 0;
let category = "全部",
  commentOnly = false,
  pendingOnly = false,
  galleryScope = "planned",
  selected = 0,
  current = null,
  activeVersionId = null,
  compareVersionId = null;
let visibleRevisionIds = [],
  comparisonCustomized = false;
let token = "",
  mode = "triple",
  zoomed = false,
  points = [],
  shownCommentId = null,
  requestId = null,
  writing = false,
  selectionSaving = false,
  exporting = false,
  submitting = false,
  accepting = false,
  trashBusy = false,
  trashAvailable = false,
  multiPointAvailable = false,
  settingsSaving = false,
  timer;
const statusNames = {
  saved: "已保存",
  open: "待处理",
  running: "正在处理",
  ready: "待审阅",
  needs_input: "待补充",
  resolved: "已处理",
  failed: "处理失败",
};
function el(tag, cls, value) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (value !== undefined) n.textContent = value;
  return n;
}
function toast(value) {
  $("toast").textContent = value;
  $("toast").hidden = false;
  clearTimeout(timer);
  timer = setTimeout(() => ($("toast").hidden = true), 3000);
}
async function api(path, data) {
  const global = [
      "/api/albums",
      "/api/albums/open",
      "/api/albums/register",
      "/api/albums/create",
      "/api/directories",
    ].includes(path.split("?")[0]),
    id = albumId,
    url =
      data === undefined && !global
        ? path +
          (path.includes("?") ? "&" : "?") +
          "album=" +
          encodeURIComponent(id)
        : path;
  const payload =
    data === undefined ? undefined : global ? data : { ...data, albumId: id };
  const response = await fetch(
    url,
    payload === undefined
      ? { cache: "no-store" }
      : {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Review-Token": token,
          },
          body: JSON.stringify(payload),
        },
  );
  let body;
  try {
    body = await response.json();
  } catch {
    throw Error("本地审片服务未响应");
  }
  if (!response.ok) throw Error(body.error || "请求失败");
  return body;
}
function original(p) {
  return p?.versions.find((v) => v.kind === "original") || p?.versions[0];
}
function version(p, id) {
  return p?.versions.find((v) => v.id === id);
}
function active() {
  return version(current, activeVersionId);
}
function currentVersion(p) {
  return version(p, p.currentVersionId) || p.versions.at(-1) || original(p);
}
function label(v) {
  return v?.label || (v?.kind === "original" ? "原图" : "修订版");
}
function dimensions(v) {
  return v?.mediaType === "video" &&
    v.dimensions?.[0] === 0 &&
    v.dimensions?.[1] === 0
    ? "原视频"
    : Array.isArray(v?.dimensions)
      ? v.dimensions.join(" × ")
      : "";
}
function mediaUrl(v, kind = "preview") {
  return `/media/${kind}/${encodeURIComponent(v.id)}?album=${encodeURIComponent(albumId)}`;
}
function setPreview(id, v) {
  const img = $(id);
  if (v?.mediaType === "video")
    img.removeAttribute("src");
  else img.src = mediaUrl(v);
}
const lightbox = {
  fit: 1,
  zoom: 1,
  x: 0,
  y: 0,
  drag: null,
  versionId: null,
  marking: false,
};
function normalizePoints(value) {
  return (Array.isArray(value) ? value : value ? [value] : [])
    .filter((p) => p && [p.x, p.y].every((n) => Number.isFinite(n) && n >= 0 && n <= 1))
    .map((p) => ({ x: p.x, y: p.y }));
}
function marksForVersion(id) {
  const comment = comments.find((c) => c.id === shownCommentId);
  if (comment) return comment.versionId === id ? normalizePoints(comment.point) : [];
  return id === activeVersionId ? points : [];
}
function paintImagePins(layer, image, versionId) {
  layer.replaceChildren();
  const marks = marksForVersion(versionId), rect = image.getBoundingClientRect();
  if (!marks.length || image.hidden || !image.naturalWidth || !rect.width || !rect.height) return;
  // Object-fit may leave margins inside the image element. Use its actual picture area.
  const scale = Math.min(rect.width / image.naturalWidth, rect.height / image.naturalHeight),
    width = image.naturalWidth * scale, height = image.naturalHeight * scale,
    origin = layer.getBoundingClientRect(),
    left = rect.left - origin.left + (rect.width - width) / 2,
    top = rect.top - origin.top + (rect.height - height) / 2;
  marks.forEach((mark, index) => {
    const pin = el("span", "pin", String(index + 1));
    pin.setAttribute("aria-label", `标记 ${index + 1}`);
    pin.style.left = `${left + mark.x * width}px`;
    pin.style.top = `${top + mark.y * height}px`;
    layer.append(pin);
  });
}
function drawPreviewPoints() {
  if (!current) return;
  document.querySelectorAll(".version-panel").forEach((panel) => {
    const image = panel.querySelector("img"), layer = panel.querySelector(".pins");
    if (image && layer) paintImagePins(layer, image, panel.dataset.versionId);
  });
  paintImagePins($("afterPins"), $("afterImage"), activeVersionId);
  paintImagePins($("beforePins"), $("beforeImage"), compareVersionId);
  paintImagePins($("originalPins"), $("originalImage"), original(current)?.id);
}
function drawLightboxPoint() {
  const sameVersion = lightbox.versionId === activeVersionId;
  $("lightboxClearPoint").disabled = !sameVersion || !points.length;
  $("lightboxUndoPoint").disabled = !sameVersion || !points.length;
  if ($("imageLightbox").open) {
    paintImagePins($("lightboxPins"), $("lightboxImage"), lightbox.versionId);
    if (lightbox.marking && !$("lightboxImage").hidden)
      $("lightboxStatus").textContent = points.length
        ? `已标记 ${points.length} 处，可继续添加 · 点“写修改意见”返回`
        : "点击添加编号标记 · 滚轮缩放 · 拖动画面定位";
  }
}
function setLightboxMarking(enabled) {
  if (enabled) {
    setActiveVersion(lightbox.versionId);
    shownCommentId = null;
    showPoint();
  }
  lightbox.marking = enabled;
  $("lightboxMark").setAttribute("aria-pressed", enabled);
  $("lightboxStage").classList.toggle("mark-mode", enabled);
  if (!$("lightboxImage").hidden)
    $("lightboxStatus").textContent = enabled
      ? "点击添加编号标记 · 滚轮缩放 · 拖动画面定位"
      : "滚轮缩放 · 拖动画面查看细节";
  drawLightboxPoint();
}
function lightboxBounds() {
  const stage = $("lightboxStage"),
    image = $("lightboxImage"),
    width = image.naturalWidth * lightbox.fit * lightbox.zoom,
    height = image.naturalHeight * lightbox.fit * lightbox.zoom,
    maxX = Math.max(stage.clientWidth * 0.15, (width - stage.clientWidth) / 2 + 40),
    maxY = Math.max(stage.clientHeight * 0.15, (height - stage.clientHeight) / 2 + 40);
  // Keep some room to reposition a fitted image and inspect its edges.
  lightbox.x = Math.max(-maxX, Math.min(maxX, lightbox.x));
  lightbox.y = Math.max(-maxY, Math.min(maxY, lightbox.y));
}
function drawLightbox() {
  const image = $("lightboxImage");
  if (!image.naturalWidth) return;
  lightboxBounds();
  image.style.width = `${image.naturalWidth * lightbox.fit}px`;
  image.style.height = `${image.naturalHeight * lightbox.fit}px`;
  image.style.transform = `translate(${lightbox.x}px, ${lightbox.y}px) scale(${lightbox.zoom})`;
  $("lightboxScale").textContent = `${Math.round(lightbox.fit * lightbox.zoom * 100)}%`;
  drawLightboxPoint();
}
function fitLightbox(reset = false) {
  const image = $("lightboxImage"), stage = $("lightboxStage");
  if (!image.naturalWidth || !stage.clientWidth || !stage.clientHeight) return;
  lightbox.fit = Math.min(stage.clientWidth * 0.82 / image.naturalWidth,
    stage.clientHeight * 0.82 / image.naturalHeight, 1);
  if (reset) {
    lightbox.zoom = 1;
    lightbox.x = lightbox.y = 0;
  }
  drawLightbox();
}
function zoomLightbox(next, clientX, clientY) {
  const image = $("lightboxImage"), stage = $("lightboxStage");
  if (image.hidden || !image.naturalWidth) return;
  const previous = lightbox.zoom,
    max = Math.max(16, 1 / lightbox.fit),
    zoom = Math.max(0.25, Math.min(max, next)),
    rect = stage.getBoundingClientRect(),
    x = clientX === undefined ? rect.width / 2 : clientX - rect.left,
    y = clientY === undefined ? rect.height / 2 : clientY - rect.top;
  lightbox.x = x - rect.width / 2 -
    (x - rect.width / 2 - lightbox.x) * (zoom / previous);
  lightbox.y = y - rect.height / 2 -
    (y - rect.height / 2 - lightbox.y) * (zoom / previous);
  lightbox.zoom = zoom;
  drawLightbox();
}
function openLightbox(item, annotate = false) {
  if (!item || item.mediaType === "video") return;
  const image = $("lightboxImage");
  lightbox.zoom = lightbox.fit = 1;
  lightbox.x = lightbox.y = 0;
  lightbox.drag = null;
  lightbox.versionId = item.id;
  $("lightboxStage").classList.remove("is-dragging");
  $("lightboxTitle").textContent = `${current?.scene || current?.id || "照片"} · ${label(item)}`;
  $("lightboxStatus").textContent = "正在载入完整图片…";
  $("lightboxScale").textContent = "—";
  image.hidden = true;
  $("lightboxMark").disabled = true;
  $("lightboxWrite").disabled = true;
  image.removeAttribute("src");
  image.style.transform = "";
  image.alt = `${label(item)}完整图片`;
  $("imageLightbox").showModal();
  setLightboxMarking(annotate);
  image.src = mediaUrl(item, "version");
  $("lightboxClose").focus({ preventScroll: true });
}
function closeLightbox() {
  lightbox.drag = null;
  lightbox.marking = false;
  $("lightboxStage").classList.remove("is-dragging");
  $("imageLightbox").close();
  $("lightboxImage").removeAttribute("src");
  $("fullSize").focus({ preventScroll: true });
}
function photoComments(id) {
  return comments.filter((c) => c.photoId === id);
}
function selectedPhotos() {
  return items.filter((p) => p.selectedVersionId);
}
function draftKey(photoId, versionId) {
  return `photo-review-draft:${project.root}:${photoId}:${versionId}`;
}
function storeDraft() {
  if (!current || !activeVersionId) return;
  try {
    localStorage.setItem(
      draftKey(current.id, activeVersionId),
      JSON.stringify({ text: $("commentText").value, points, requestId }),
    );
  } catch {}
}
function restoreDraft() {
  let draft = {};
  try {
    draft = JSON.parse(
      localStorage.getItem(draftKey(current.id, activeVersionId)) || "{}",
    );
  } catch {}
  $("commentText").value = draft.text || "";
  points = normalizePoints(draft.points ?? draft.point);
  if (points.length) shownCommentId = null;
  requestId = draft.requestId || null;
  showPoint();
}
function showPoint() {
  const has = points.length > 0;
  $("locationRow").hidden = !has;
  $("locationText").textContent = `已标记 ${points.length} 处，可在意见中引用编号`;
  $("pointList").replaceChildren();
  points.forEach((mark, index) => {
    const remove = el("button", null, `${index + 1} ×`);
    remove.type = "button";
    remove.setAttribute("aria-label", `删除标记 ${index + 1}`);
    remove.title = `横向 ${Math.round(mark.x * 100)}% · 纵向 ${Math.round(mark.y * 100)}%`;
    remove.onclick = () => {
      points.splice(index, 1);
      shownCommentId = requestId = null;
      showPoint();
      storeDraft();
    };
    $("pointList").append(remove);
  });
  const comment = comments.find((c) => c.id === shownCommentId),
    shown = comment ? normalizePoints(comment.point) : points,
    markedVersion = comment?.versionId || activeVersionId;
  $("markSummary").textContent = shown.length
    ? `${label(version(current, markedVersion))} · ${comment ? "评论" : "草稿"}标记 ${shown.length} 处` : "";
  drawPreviewPoints();
  drawLightboxPoint();
}
function updateExportButtons() {
  const count = selectedPhotos().length;
  document.querySelectorAll("[data-open-export]").forEach((b) => {
    b.textContent = `导出已选 · ${count}`;
    b.disabled = !count || selectionSaving || exporting;
  });
}
function renderFilters() {
  const names = [
    "全部",
    ...new Set(items.map((p) => p.category).filter(Boolean)),
  ];
  if (!names.includes(category)) category = "全部";
  const nav = $("filters") || document.querySelector(".filters");
  nav.replaceChildren();
  for (const name of names) {
    const b = el("button", null, name);
    b.dataset.category = name;
    b.setAttribute("aria-pressed", name === category ? "true" : "false");
    b.onclick = () => {
      category = name;
      selected = 0;
      renderFilters();
      renderGrid();
    };
    nav.append(b);
  }
}
function isPending(p) {
  const v = currentVersion(p);
  return v?.kind === "revision" && v.reviewStatus === "pending";
}
function photoJobState(photoId) {
  const jobs = runs.filter((run) => run.photoId === photoId);
  const run = jobs.find((item) => item.status === "running") ||
    jobs.find((item) => item.status === "queued") || jobs[0];
  const names = {
    queued: "待处理",
    running: "正在处理",
    ready: "处理完成",
    completed: "处理完成",
    needs_input: "待补充信息",
    failed: "处理失败",
  };
  return { label: names[run?.status] || "未提交", status: run?.status || "idle" };
}
function plannedPhotoIds() {
  const run = runs.find((item) => item.jobId === selectedAlbumJobId);
  const tasks = run?.plan?.tasks;
  return new Set(Array.isArray(tasks) ? tasks.map((task) => task.photoId)
    .filter((id) => items.some((photo) => photo.id === id)) : []);
}
function renderGalleryScope(ids) {
  const available = ids.size > 0;
  const showingPlanned = available && galleryScope === "planned";
  $("galleryScopeStatus").textContent = available
    ? `主 AI 选片 ${ids.size} / 全部 ${items.length} · 当前：${showingPlanned ? "主 AI 选片" : "全部"}`
    : `主 AI 尚未选片 · 全部 ${items.length}`;
  $("showPlanned").disabled = !available;
  $("showPlanned").setAttribute("aria-pressed", showingPlanned);
  $("showAll").setAttribute("aria-pressed", !showingPlanned);
}
function renderGrid(focus = false) {
  const old = filtered[selected]?.id,
    q = $("search").value.trim().toLowerCase(),
    planned = plannedPhotoIds(),
    showPlanned = planned.size > 0 && galleryScope === "planned";
  filtered = items.filter(
    (p) =>
      (!showPlanned || planned.has(p.id)) &&
      (category === "全部" || p.category === category) &&
      (!commentOnly || photoComments(p.id).length) &&
      (!pendingOnly || isPending(p)) &&
      (!q || (p.id + " " + p.scene).toLowerCase().includes(q)),
  );
  let next = filtered.findIndex((p) => p.id === old);
  selected = next < 0 ? 0 : next;
  renderGalleryScope(planned);
  if ($("viewer").open && current) {
    const currentIndex = filtered.findIndex((p) => p.id === current.id);
    $("position").textContent = currentIndex < 0
      ? "当前照片不在筛选结果中"
      : `${currentIndex + 1} / ${filtered.length}`;
    $("prev").disabled = currentIndex <= 0;
    $("next").disabled = currentIndex < 0 || currentIndex === filtered.length - 1;
    if (currentIndex >= 0) selected = currentIndex;
  }
  $("grid").replaceChildren();
  filtered.forEach((p, i) => {
    const v = currentVersion(p),
      isVideo = v?.mediaType === "video";
    const card = el("button", "photo-card");
    card.dataset.id = p.id;
    card.setAttribute("aria-label", `${p.id} ${p.scene}，打开审片`);
    card.setAttribute("aria-current", i === selected ? "true" : "false");
    card.tabIndex = i === selected ? 0 : -1;
    const media = el("div", "photo");
    if (isVideo) {
      const video = el("div", "video-card");
      video.append(el("b", null, "▷"), el("span", null, "动态素材"));
      media.append(video);
    } else if (v) {
      const img = el("img");
      img.src = mediaUrl(v, "thumb");
      img.alt = p.scene;
      img.loading = "lazy";
      media.append(img);
    }
    const meta = el("div", "photo-meta");
    meta.append(
      el("span", "photo-title", p.scene || p.id),
      el("span", "photo-id", `${p.id} · ${label(v)}`),
    );
    card.append(media, meta);
    if (p.selectedVersionId) {
      const chosen = version(p, p.selectedVersionId);
      card.append(el("span", "selection-badge", "已选 · " + label(chosen)));
    }
    const job = photoJobState(p.id);
    card.append(el("span", `job-status job-${job.status}`, job.label));
    if (isPending(p)) card.append(el("span", "review-badge", "待审阅"));
    const count = photoComments(p.id).length;
    if (count) card.append(el("span", "comment-count", count + " 条评论"));
    card.onclick = () => openPhoto(i);
    card.onfocus = () => selectCard(i, false);
    $("grid").append(card);
  });
  if (!filtered.length)
    $("grid").append(
      el(
        "p",
        "empty",
        !albums.length
          ? "点击“打开相册目录”选择已有照片文件夹，或点击“新建相册”。"
          : items.length
            ? "没有匹配的照片"
            : `这个相册还没有照片。请将照片放入 ${albums.find((a) => a.id === albumId)?.path || "相册目录"}，然后点击“刷新目录”。`,
      ),
    );
  if (focus) selectCard(selected, true);
  const videos = items.filter(
    (p) => currentVersion(p)?.mediaType === "video",
  ).length;
  $("stats").replaceChildren(
    el("strong", null, String(items.length)),
    document.createTextNode(` 项素材 · ${videos} 段动态`),
    el(
      "div",
      null,
      `${comments.length} 条修改意见 · 已选 ${selectedPhotos().length} 项`,
    ),
  );
  updateExportButtons();
}
function selectCard(i, focus) {
  if (!filtered.length) return;
  selected = Math.max(0, Math.min(i, filtered.length - 1));
  [...$("grid").children].forEach((c, k) => {
    c.tabIndex = k === selected ? 0 : -1;
    c.setAttribute("aria-current", k === selected ? "true" : "false");
  });
  if (focus) {
    const c = $("grid").children[selected];
    c.focus({ preventScroll: true });
    c.scrollIntoView({ block: "nearest" });
  }
}
function defaultCompare(p, v) {
  const base = original(p);
  return (
    (base?.id !== v.id ? base : null) ||
    (currentVersion(p)?.id !== v.id ? currentVersion(p) : null) ||
    p.versions.find((x) => x.id !== v.id)
  );
}
function revisions(p) {
  return p.versions.filter((item) => item.kind !== "original");
}
function changeDescription(v) {
  if (v.kind === "original") return "原始照片，未修改";
  return v.summary?.trim() || "未记录修改说明";
}
function shownRevisions() {
  const all = revisions(current),
    count = Math.min(2, all.length),
    allowed = new Set(all.map((item) => item.id));
  if (!comparisonCustomized) {
    visibleRevisionIds = count ? all.slice(-count).map((item) => item.id) : [];
  } else {
    visibleRevisionIds = visibleRevisionIds.filter((id) => allowed.has(id));
    for (const item of all.slice().reverse()) {
      if (visibleRevisionIds.length >= count) break;
      if (!visibleRevisionIds.includes(item.id))
        visibleRevisionIds.unshift(item.id);
    }
    visibleRevisionIds = visibleRevisionIds.slice(-count);
  }
  return visibleRevisionIds.map((id) => version(current, id)).filter(Boolean);
}
function showActiveInComparison() {
  const v = active();
  if (!v || v.kind === "original" || visibleRevisionIds.includes(v.id))
    return;
  if (visibleRevisionIds.length) {
    visibleRevisionIds[0] = v.id;
    visibleRevisionIds.sort(
      (left, right) =>
        current.versions.findIndex((item) => item.id === left) -
        current.versions.findIndex((item) => item.id === right),
    );
    comparisonCustomized = true;
  }
}
function renderComparisonPanels() {
  const base = original(current);
  shownRevisions();
  const displayed = [
      base,
      ...visibleRevisionIds.map((id) => version(current, id)),
    ].filter(Boolean),
    container = $("threeView");
  container.style.setProperty("--panel-count", displayed.length);
  container.style.minWidth =
    `${displayed.length * 280 + Math.max(0, displayed.length - 1) * 14 + 32}px`;
  container.replaceChildren();
  for (let index = 0; index < displayed.length; index++) {
    const item = displayed[index],
      panel = el("figure", "version-panel"),
      caption = el("figcaption"),
      media = el("div", "version-media"),
      note = el("p", "version-note", changeDescription(item));
    panel.dataset.versionId = item.id;
    panel.classList.toggle("active", item.id === activeVersionId);
    caption.append(el("span", "version-title", label(item)));
    if (item.id === activeVersionId)
      caption.append(el("span", "active-badge", "当前评论版本"));
    caption.append(el("small", null, dimensions(item)));
    if (item.kind !== "original") {
      const choice = el("select", "panel-version-select");
      choice.setAttribute("aria-label", `第 ${index + 1} 栏显示的修订版本`);
      for (const candidate of revisions(current)) {
        const option = el("option", null, label(candidate));
        option.value = candidate.id;
        choice.append(option);
      }
      choice.value = item.id;
      choice.onchange = () => {
        const next = choice.value,
          slot = visibleRevisionIds.indexOf(item.id),
          other = visibleRevisionIds.indexOf(next);
        if (other >= 0) visibleRevisionIds[other] = item.id;
        visibleRevisionIds[slot] = next;
        comparisonCustomized = true;
        renderComparisonPanels();
      };
      caption.append(choice);
    }
    if (item.mediaType === "video") {
      media.append(el("p", null, "动态素材，请切换到此版本播放"));
    } else {
      const img = el("img");
      img.src = mediaUrl(item);
      img.alt = `${label(item)}完整画面`;
      img.draggable = false;
      img.onload = drawPreviewPoints;
      media.append(img, el("div", "pins"));
      media.classList.add("enlargeable");
      media.tabIndex = 0;
      media.setAttribute("role", "button");
      media.setAttribute("aria-label", `全屏查看${label(item)}`);
      media.onclick = () => openLightbox(item);
      media.onkeydown = (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          openLightbox(item);
        }
      };
    }
    if (item.id !== activeVersionId) {
      const activate = el("button", "panel-activate", "在这版留意见");
      activate.type = "button";
      activate.onclick = () => item.mediaType === "video"
        ? setActiveVersion(item.id) : openLightbox(item, true);
      caption.append(activate);
    }
    panel.append(caption, media, note);
    container.append(panel);
  }
  requestAnimationFrame(drawPreviewPoints);
}
function populateVersions() {
  const v = active(),
    base = original(current),
    select = $("versionSelect");
  select.replaceChildren();
  for (const item of current.versions) {
    const opt = el(
      "option",
      null,
      label(item) + (item.id === current.currentVersionId ? " · 当前" : "") +
        (item.reviewStatus === "approved" ? " · 已通过" : ""),
    );
    opt.value = item.id;
    select.append(opt);
  }
  select.value = activeVersionId;
  const compare = $("sourceSelect");
  compare.replaceChildren();
  for (const item of current.versions) {
    if (item.id === activeVersionId) continue;
    const opt = el("option", null, "对比：" + label(item));
    opt.value = item.id;
    compare.append(opt);
  }
  if (
    !version(current, compareVersionId) ||
    compareVersionId === activeVersionId
  )
    compareVersionId = defaultCompare(current, v)?.id || null;
  if (compareVersionId) compare.value = compareVersionId;
  compare.hidden =
    !compare.options.length || v.mediaType === "video" || mode !== "compare";
  setPreview("originalImage", base);
  renderComparisonPanels();
  setPreview("afterImage", v);
  setPreview("beforeImage", version(current, compareVersionId) || base);
  $("beforeLabel").textContent = $("splitBeforeLabel").textContent = label(
    version(current, compareVersionId) || base,
  );
  $("afterLabel").textContent = $("splitAfterLabel").textContent = label(v);
  $("beforeNote").textContent =
    mode === "triple"
      ? `${label(base)} + ${visibleRevisionIds.length} 张修订`
      : `${label(version(current, compareVersionId) || base)} · ${label(v)}`;
  $("fullSize").href = mediaUrl(v, "version");
  $("fullSize").textContent = v.mediaType === "video"
    ? `全尺寸 · ${label(v)} ↗` : `全屏查看 · ${label(v)}`;
  const video = v.mediaType === "video";
  $("video").pause();
  $("video").removeAttribute("src");
  if (video) $("video").src = mediaUrl(v, "version");
  $("photoMeta").textContent =
    `${current.id} · ${label(v)} · ${current.category || ""} · ${dimensions(v)}${v.reviewStatus === "approved" ? " · 已通过" : v.kind === "revision" ? " · 待审阅" : ""}`;
  $("commentFormTitle").textContent = `${label(v)} · 修改意见`;
  renderVersionActions();
  $("sources").replaceChildren();
  for (const source of current.sources || [])
    $("sources").append(el("div", null, source));
  $("processing").textContent = v.summary || "";
  fitImage();
}
function renderSelection() {
  if (!current) return;
  const v = active(),
    selectedVersion = version(current, current.selectedVersionId);
  const box = $("selectionChoices");
  box.replaceChildren();
  const choose = el("button", null, "选为交付版本 · " + label(v));
  choose.type = "button";
  choose.disabled =
    selectionSaving || exporting || current.selectedVersionId === v.id;
  choose.onclick = () => saveSelection(v.id);
  box.append(choose);
  const clear = el("button", null, "取消选择");
  clear.type = "button";
  clear.disabled = selectionSaving || exporting || !current.selectedVersionId;
  clear.onclick = () => saveSelection(null);
  box.append(clear);
  $("selectionStatus").textContent = selectedVersion
    ? "已选择：" + label(selectedVersion)
    : "未选择，不会导出";
}
function renderRuns() {
  renderVersionActions();
  if (!current) {
    $("runStatus").textContent = "";
    $("photoJobStatus").textContent = "";
    return;
  }
  const job = photoJobState(current.id);
  $("photoJobStatus").textContent = job.label;
  $("photoJobStatus").className = `job-status job-${job.status}`;
  const mine = runs.filter((r) => r.photoId === current.id).slice(0, 3);
  const names = {
    queued: "待处理",
    running: "正在处理",
    ready: "处理完成，待审阅",
    needs_input: "需要补充信息",
    failed: "处理失败",
    completed: "处理完成",
  };
  $("runStatus").textContent = mine
    .map(
      (r) =>
        `${names[r.status] || r.status}${r.model ? " · " + r.model : ""}${r.error ? "：" + r.error : r.reply ? "：" + r.reply : ""}`,
    )
    .join("\n");
}
function albumRunName(run) {
  const time = run.startedAt ? new Date(run.startedAt) : null;
  const date = time && !Number.isNaN(time.getTime())
    ? time.toLocaleString("zh-CN", { hour12: false })
    : "整册任务";
  const stage = ({
    queued: "等待主 AI", running: run.phase === "processing" ? "子任务处理中" : "主 AI 判断中",
    ready: "已完成", needs_input: "待补充", failed: "处理失败",
  }[run.status] || run.status);
  return `${date} · ${stage}`;
}
function renderAlbumPlan() {
  const root = $("albumPlan");
  const history = runs.filter((run) => run.kind === "album");
  root.hidden = !albumId || !history.length;
  if (root.hidden) {
    $("albumPlanBody").replaceChildren();
    $("albumPlanStatus").textContent = "";
    renderGrid();
    return;
  }
  if (!history.some((run) => run.jobId === selectedAlbumJobId)) {
    selectedAlbumJobId = history[0].jobId;
    galleryScope = "planned";
  }
  renderGrid();
  const choices = $("albumPlanSelect");
  choices.replaceChildren();
  for (const run of history) {
    const option = el("option", null, albumRunName(run));
    option.value = run.jobId;
    choices.append(option);
  }
  choices.value = selectedAlbumJobId;
  const run = history.find((item) => item.jobId === selectedAlbumJobId);
  const body = $("albumPlanBody");
  body.replaceChildren();
  const tasks = Array.isArray(run.plan?.tasks) ? run.plan.tasks : null;
  const childRuns = runs.filter((item) =>
    item.albumJobId === run.jobId || (run.childJobIds || []).includes(item.jobId),
  );
  const finished = childRuns.filter((item) =>
    ["ready", "needs_input", "failed"].includes(item.status),
  ).length;
  const status = {
    queued: "等待主 AI 分析相册",
    running: run.phase === "processing" ? "子 AI 正在处理" : "主 AI 正在判断处理方式",
    ready: "整册任务已完成",
    needs_input: "需要补充信息",
    failed: "整册任务失败",
  }[run.status] || run.status;
  const progress = tasks
    ? ` · ${tasks.length} 个子任务` +
      (run.childJobIds?.length ? ` · 已处理 ${finished}/${run.childJobIds.length}` : "")
    : "";
  $("albumPlanStatus").textContent = status + progress;
  if (run.error || (run.reply && run.reply !== run.plan?.summary))
    body.append(el("p", run.error ? "error" : "quiet", run.error || run.reply));
  if (!run.plan) {
    if (["queued", "running"].includes(run.status))
      body.append(el("p", "quiet", "主 AI 的处理方案会在分析完成后显示。"));
    return;
  }
  if (run.plan.summary) {
    body.append(el("p", "album-plan-summary", run.plan.summary));
  }
  if (run.skipped?.length)
    body.append(el("p", "quiet", `未启动修图 ${run.skipped.length} 张：` +
      run.skipped.map((item) => `${item.photoId}（${item.reason}）`).join("、")));
}
$("albumPlanSelect").onchange = () => {
  selectedAlbumJobId = $("albumPlanSelect").value;
  galleryScope = "planned";
  renderAlbumPlan();
};
$("showPlanned").onclick = () => {
  galleryScope = "planned";
  renderGrid();
};
$("showAll").onclick = () => {
  galleryScope = "all";
  renderGrid();
};
async function saveSelection(versionId) {
  if (selectionSaving || exporting || !current) return;
  const photoId = current.id;
  selectionSaving = true;
  renderSelection();
  updateExportButtons();
  try {
    const updated = await api("/api/select", { photoId, versionId });
    const ix = items.findIndex((p) => p.id === photoId);
    if (ix >= 0) items[ix] = updated;
    if (current?.id === photoId) current = updated;
    renderGrid();
    renderSelection();
    toast(versionId ? "已保存交付版本" : "已取消选择");
  } catch (error) {
    toast(error.message);
  } finally {
    selectionSaving = false;
    renderSelection();
    updateExportButtons();
  }
}
function mainAiComments(photo) {
  const notes = [];
  for (const run of runs) {
    if (run.kind !== "album") continue;
    const task = run.plan?.tasks?.find((item) => item.photoId === photo.id);
    if (!task) continue;
    const child = runs.find((item) => item.photoId === photo.id &&
      (item.albumJobId === run.jobId || run.childJobIds?.includes(item.jobId)));
    const skipped = run.skipped?.find((item) => item.photoId === photo.id);
    notes.push({ run, task, child, skipped });
  }
  return notes;
}
function renderMainAiComment(note) {
  const { run, task, child, skipped } = note;
  const block = el("article", "comment-item main-ai-comment");
  block.append(el("strong", null, "主 AI 修图要求"));
  const state = skipped ? "未启动" : ({
    queued: "等待处理", running: "处理中", ready: "待审阅",
    needs_input: "待补充", failed: "处理失败",
  }[child?.status] || "待安排");
  block.append(el("span", "status", state));
  const base = version(current, child?.versionId);
  const result = version(current, child?.resultVersionId);
  const time = new Date(run.startedAt).toLocaleString("zh-CN", { hour12: false });
  block.append(el("p", "quiet", `${time}${base ? " · 对应版本：" + label(base) : " · 相册任务"}${result ? " → " + label(result) : ""}`));
  block.append(el("p", "comment-text", task.instructions || "未记录具体要求"));
  const references = (task.referenceIds || []).map((id) => {
    const photo = items.find((item) => item.id === id);
    return photo?.sources?.[0]?.split(/[\\/]/).at(-1) || id;
  });
  if (references.length)
    block.append(el("p", "comment-text", "参考素材：" + references.join("、")));
  if (base && base.id !== activeVersionId) {
    const button = el("button", null, "查看要求对应版本 · " + label(base));
    button.onclick = () => setActiveVersion(base.id);
    block.append(button);
  }
  if (skipped || child?.error)
    block.append(el("div", "reply", skipped?.reason || child.error));
  return block;
}
function renderComments() {
  if (!current) return;
  const rows = photoComments(current.id);
  const mainAiNotes = mainAiComments(current);
  $("commentHeading").textContent = `修图要求与评论 · ${rows.length + mainAiNotes.length}`;
  $("comments").replaceChildren();
  if (!rows.length && !mainAiNotes.length)
    $("comments").append(
      el("p", "quiet", "还没有评论。你的第一条意见会留在这里。"),
    );
  function addRow(c, group) {
    const block = el("article", "comment-item");
    block.dataset.commentId = c.id;
    const time = el(
      "time",
      null,
      new Date(c.createdAt).toLocaleString("zh-CN", {
        month: "numeric",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      }),
    );
    const commentRun = c.status === "running"
      ? runs.find((run) => run.commentIds?.includes(c.id)) : null;
    block.append(
      time,
      el(
        "span",
        "status",
        (group === "from"
          ? "本版处理意见 · "
          : group === "other"
            ? "其他版本 · "
            : "") + (commentRun?.status === "queued" ? "待处理" : statusNames[c.status] || c.status),
      ),
      el("p", "comment-text", c.text),
    );
    if (c.versionId !== activeVersionId) {
      const b = el(
        "button",
        null,
        "查看评论所在版本 · " + label(version(current, c.versionId)),
      );
      b.onclick = () => setActiveVersion(c.versionId);
      block.append(b);
    }
    const commentPoints = normalizePoints(c.point);
    if (commentPoints.length) {
      const b = el("button", null, `查看 ${commentPoints.length} 处标记`);
      b.onclick = () => {
        if (c.versionId !== activeVersionId) setActiveVersion(c.versionId);
        shownCommentId = c.id;
        shownRevisions();
        showActiveInComparison();
        renderComparisonPanels();
        setMode("triple");
        showPoint();
        toast("正在显示这条评论的标记");
      };
      block.append(b);
    }
    if (c.reply) block.append(el("div", "reply", "回复：" + c.reply));
    if (c.status === "saved" || c.status === "failed") {
      const b = el(
        "button",
        null,
        c.status === "failed" ? "重新提交修改意见" : "提交这条修改意见",
      );
      b.onclick = async () => {
        b.disabled = true;
        submitting = true;
        try {
          const updated = await api("/api/submit", { id: c.id });
          upsertComment(updated);
          renderComments();
          await refresh();
          toast(
            updated.status === "failed"
              ? updated.reply || "启动失败"
              : "已提交修改意见",
          );
        } catch (error) {
          toast(error.message);
          b.disabled = false;
        } finally {
          submitting = false;
        }
      };
      block.append(b);
    }
    $("comments").append(block);
  }
  const timeline = [
    ...mainAiNotes.map((note) => ({ time: note.run.startedAt, note })),
    ...rows.map((comment) => ({ time: comment.createdAt, comment })),
  ].sort((a, b) => (Date.parse(b.time) || 0) - (Date.parse(a.time) || 0));
  for (const entry of timeline) {
    if (entry.note) {
      $("comments").append(renderMainAiComment(entry.note));
    } else {
      const c = entry.comment;
      addRow(c, c.versionId === activeVersionId ? "local" :
        c.resultVersionId === activeVersionId ? "from" : "other");
    }
  }
}
function upsertComment(c) {
  const ix = comments.findIndex((x) => x.id === c.id);
  if (ix < 0) comments.push(c);
  else comments[ix] = c;
}
function setActiveVersion(id) {
  if (!current || !version(current, id) || id === activeVersionId) return;
  storeDraft();
  activeVersionId = id;
  compareVersionId = null;
  points = [];
  shownCommentId = photoComments(current.id).findLast((c) =>
    c.versionId === id && normalizePoints(c.point).length)?.id || null;
  requestId = null;
  $("formStatus").textContent = "";
  shownRevisions();
  showActiveInComparison();
  populateVersions();
  restoreDraft();
  renderComments();
  renderSelection();
  setMode(mode);
}
function openPhoto(index, refreshed = false) {
  const p = filtered[index];
  if (!p) return;
  if (current) storeDraft();
  const samePhoto = refreshed && current?.id === p.id;
  const retained = samePhoto ? activeVersionId : null;
  const oldCompare =
    samePhoto ? compareVersionId : null;
  if (!samePhoto) {
    visibleRevisionIds = [];
    comparisonCustomized = false;
    shownCommentId = null;
  }
  current = p;
  activeVersionId = version(p, retained) ? retained : currentVersion(p)?.id;
  compareVersionId = oldCompare;
  selected = index;
  zoomed = false;
  points = [];
  if (!samePhoto) shownCommentId = photoComments(p.id).findLast((c) =>
    c.versionId === activeVersionId && normalizePoints(c.point).length)?.id || null;
  requestId = null;
  $("zoom").textContent = "放大 2×";
  $("photoTitle").textContent = p.scene || p.id;
  $("position").textContent = `${index + 1} / ${filtered.length}`;
  $("prev").disabled = index === 0;
  $("next").disabled = index === filtered.length - 1;
  $("formStatus").textContent = "";
  if (!$("viewer").open) $("viewer").showModal();
  populateVersions();
  restoreDraft();
  renderComments();
  renderSelection();
  renderRuns();
  setMode(refreshed ? mode : "triple");
  $("split").value = 50;
  updateSplit();
  requestAnimationFrame(fitImage);
  if (!refreshed) $("closeViewer").focus({ preventScroll: true });
  selectCard(index, false);
}
function setMode(next) {
  if (!current) return;
  mode = next;
  const v = active(),
    isVideo = v.mediaType === "video",
    canCompare = !!compareVersionId && compareVersionId !== v.id;
  $("beforeLayer").hidden = !canCompare;
  $("threeView").hidden = isVideo || mode !== "triple";
  $("picture").hidden = isVideo || mode !== "compare";
  $("originalFrame").hidden = isVideo || mode !== "original";
  $("video").hidden = !isVideo;
  $("compareControls").hidden = isVideo;
  $("sourceSelect").hidden =
    isVideo || mode !== "compare" || !$("sourceSelect").options.length;
  $("split").hidden =
    $("splitAfterLabel").hidden =
    $("splitBeforeLabel").hidden =
      mode !== "compare" || !canCompare;
  $("splitHint").textContent =
    mode === "compare"
      ? "拖动查看完整画面"
      : mode === "triple"
        ? "各版本均保留完整画面与原始比例"
        : "查看原始完整画面";
  $("tripleMode").disabled = $("originalMode").disabled = isVideo;
  $("compareMode").disabled = isVideo || !canCompare;
  $("zoom").disabled = isVideo || mode !== "compare";
  $("mark").disabled = isVideo;
  $("tripleMode").setAttribute("aria-pressed", mode === "triple");
  $("compareMode").setAttribute("aria-pressed", mode === "compare");
  $("originalMode").setAttribute("aria-pressed", mode === "original");
  $("zoom").setAttribute("aria-pressed", zoomed);
  $("beforeNote").textContent =
    mode === "triple"
      ? `${label(original(current))} + ${visibleRevisionIds.length} 张修订`
      : `${label(version(current, compareVersionId) || original(current))} · ${label(v)}`;
  fitImage();
}
function fitImage() {
  if (!current || !$("viewer").open || active()?.mediaType === "video") return;
  const viewport = $("viewport"),
    [w, h] = active().dimensions || [1, 1],
    ratio =
      Math.min(
        (viewport.clientWidth - 34) / w,
        (viewport.clientHeight - 34) / h,
      ) * (zoomed ? 2 : 1);
  $("picture").style.width = Math.max(1, w * ratio) + "px";
  $("picture").style.height = Math.max(1, h * ratio) + "px";
  $("originalFrame").style.width =
    Math.max(1, viewport.clientWidth - 32) + "px";
  $("originalFrame").style.height =
    Math.max(1, viewport.clientHeight - 32) + "px";
  drawPreviewPoints();
}
function updateSplit() {
  const v = Number($("split").value);
  $("beforeLayer").style.clipPath = `inset(0 ${100 - v}% 0 0)`;
  $("divider").style.left = v + "%";
  $("beforeLabel").style.opacity = v < 12 ? "0" : "1";
  $("divider").hidden = v === 0 || v === 100 || !compareVersionId;
}
async function save() {
  if (writing || !current) return;
  if (points.length > 1 && !multiPointAvailable) {
    toast("多点标记需等待服务更新后提交，草稿已保留");
    storeDraft();
    return;
  }
  const value = $("commentText").value,
    text = value.trim();
  if (!text) {
    $("commentText").focus();
    return;
  }
  writing = true;
  $("submitComment").disabled = true;
  $("formStatus").className = "";
  $("formStatus").textContent = "正在提交修改…";
  const photoId = current.id,
    versionId = activeVersionId,
    commentPoint = points.length ? (multiPointAvailable ? points.map((p) => ({ ...p })) : points[0]) : null,
    id = requestId || crypto.randomUUID(),
    key = draftKey(photoId, versionId);
  requestId = id;
  storeDraft();
  try {
    const result = await api("/api/comments", {
      id,
      photoId,
      versionId,
      text,
      point: commentPoint,
      submit: true,
    });
    upsertComment(result);
    if (
      current?.id === photoId &&
      activeVersionId === versionId &&
      $("commentText").value === value
    ) {
      try {
        localStorage.removeItem(key);
      } catch {}
    }
    if (current?.id === photoId && activeVersionId === versionId) {
      if ($("commentText").value === value) {
        $("commentText").value = "";
        points = [];
        shownCommentId = result.id;
        requestId = null;
        showPoint();
      }
      $("formStatus").textContent = result.status === "failed"
        ? result.reply || "启动失败，可在评论中重试"
        : "已提交修改意见";
      $("formStatus").className = result.status === "failed" ? "error" : "";
      renderComments();
    }
    renderGrid();
    await refresh();
  } catch (error) {
    if (current?.id === photoId && activeVersionId === versionId) {
      $("formStatus").textContent = error.message + "，输入内容仍保留。";
      $("formStatus").className = "error";
      storeDraft();
    } else
      try {
        localStorage.setItem(
          key,
          JSON.stringify({ text: value, points: normalizePoints(commentPoint), requestId: id }),
        );
      } catch {}
  } finally {
    writing = false;
    $("submitComment").disabled = false;
  }
}
async function acceptCurrent() {
  if (
    !current || accepting || trashBusy ||
    active().reviewStatus === "approved"
  )
    return;
  const photoId = current.id,
    versionId = activeVersionId;
  accepting = true;
  $("acceptVersion").disabled = true;
  try {
    const approved = await api("/api/accept", { photoId, versionId });
    const p = items.find((x) => x.id === photoId),
      ix = p?.versions.findIndex((v) => v.id === versionId);
    if (ix >= 0) p.versions[ix] = approved;
    if (current?.id === photoId) {
      current = p;
      populateVersions();
      renderSelection();
    }
    renderGrid();
    await refresh();
    toast("已通过 " + label(approved));
  } catch (error) {
    toast(error.message);
    $("acceptVersion").disabled = false;
  } finally {
    accepting = false;
    renderVersionActions();
  }
}
function renderVersionActions() {
  const v = active();
  $("openTrash").disabled = !albumId || !trashAvailable || albumLoading || trashBusy;
  if (!v) return;
  const running = runs.some((run) => run.photoId === current.id &&
    ["queued", "running"].includes(run.status));
  const busy = trashBusy || writing || accepting || selectionSaving || exporting;
  $("acceptVersion").disabled = busy || v.reviewStatus === "approved";
  $("acceptVersion").textContent = v.reviewStatus === "approved" ? "此版本已通过" : "通过此版本 · " + label(v);
  $("deleteVersion").disabled = !trashAvailable || busy || running || v.kind === "original";
  $("deletePhoto").disabled = !trashAvailable || busy || running;
  $("versionActionHint").textContent = !trashAvailable
    ? "服务更新后可使用删除和回收站。"
    : running ? "这张照片正在排队或处理，完成后可删除。"
    : "删除后可从相册回收站恢复；原片不能单独删除。";
}
async function trashCurrent(wholePhoto) {
  if (!current || !trashAvailable || trashBusy || writing || accepting || selectionSaving || exporting) return;
  const photoId = current.id, v = active();
  const name = wholePhoto ? `整张照片“${current.scene || photoId}”及其全部版本` : `版本“${label(v)}”`;
  if (!confirm(`将${name}移入回收站？\n可以恢复；若已选为交付，将取消该选择。原始文件不会删除。`)) return;
  trashBusy = true;
  renderVersionActions();
  try {
    await api("/api/trash", { photoId, ...(wholePhoto ? {} : { versionId: v.id }) });
    if (wholePhoto && current?.id === photoId) closeViewer();
    else comparisonCustomized = false;
    await refresh();
    toast("已移入回收站");
  } catch (error) {
    toast(error.message);
  } finally {
    trashBusy = false;
    renderVersionActions();
  }
}
async function renderTrash() {
  const list = $("trashList");
  const entries = await api("/api/trash");
  list.replaceChildren();
  if (!entries.length) list.append(el("p", "quiet", "回收站为空"));
  for (const entry of entries) {
    const row = el("article", "trash-item"), detail = el("div");
    detail.append(el("strong", null, entry.scene || entry.photoId),
      el("p", "quiet", `${entry.kind === "photo" ? "整张照片及全部版本" : "修订版 · " + entry.label} · ${new Date(entry.deletedAt).toLocaleString("zh-CN")}`));
    const restore = el("button", null, entry.kind === "photo" ? "恢复照片" : "恢复版本");
    restore.onclick = async () => {
      if (trashBusy) return;
      trashBusy = true;
      list.querySelectorAll("button").forEach((button) => button.disabled = true);
      $("closeTrash").disabled = true;
      $("trashStatus").textContent = "正在恢复…";
      try {
        await api("/api/restore", { photoId: entry.photoId, ...(entry.versionId ? { versionId: entry.versionId } : {}) });
        await refresh();
        await renderTrash();
        $("trashStatus").textContent = "已恢复。交付选择保持当前设置，可自行重新选择。";
      } catch (error) {
        $("trashStatus").textContent = error.message;
      } finally {
        trashBusy = false;
        list.querySelectorAll("button").forEach((button) => button.disabled = false);
        $("closeTrash").disabled = false;
        renderVersionActions();
      }
    };
    row.append(detail, restore);
    list.append(row);
  }
}
$("deleteVersion").onclick = () => trashCurrent(false);
$("deletePhoto").onclick = () => trashCurrent(true);
$("openTrash").onclick = async () => {
  $("trashDialog").showModal();
  $("trashStatus").textContent = "正在读取…";
  $("trashList").replaceChildren();
  try {
    await renderTrash();
    $("trashStatus").textContent = "";
  } catch (error) {
    $("trashStatus").textContent = error.message;
  }
};
$("closeTrash").onclick = () => $("trashDialog").close();
$("trashDialog").addEventListener("cancel", (e) => {
  if (trashBusy) e.preventDefault();
});
function closeViewer() {
  storeDraft();
  $("video").pause();
  $("viewer").close();
  current = null;
  activeVersionId = null;
  selectCard(selected, true);
}
function openExport() {
  if (selectionSaving || exporting || !selectedPhotos().length) return;
  $("exportCount").textContent =
    `已选 ${selectedPhotos().length} 项。只导出这些已选版本。`;
  $("exportDirectory").value = project.root
    ? project.root.replace(/\/$/, "") + "/exports"
    : "";
  $("exportResult").textContent = "";
  $("exportResult").classList.remove("error");
  $("exportDialog").showModal();
  $("exportDirectory").focus();
}
document
  .querySelectorAll("[data-open-export]")
  .forEach((b) => (b.onclick = openExport));
$("closeExport").onclick = () => $("exportDialog").close();
$("exportDialog").addEventListener("cancel", (e) => {
  if (exporting) e.preventDefault();
});
$("exportForm").onsubmit = async (e) => {
  e.preventDefault();
  if (exporting || selectionSaving || !selectedPhotos().length) return;
  exporting = true;
  $("submitExport").disabled =
    $("closeExport").disabled =
    $("exportDirectory").disabled =
      true;
  renderSelection();
  updateExportButtons();
  $("exportResult").classList.remove("error");
  $("exportResult").textContent = "正在导出…";
  try {
    const result = await api("/api/export", {
      directory: $("exportDirectory").value.trim(),
    });
    $("exportResult").textContent =
      `已导出 ${result.count} 项至：\n${result.directory}\n清单：${result.manifestFile}`;
    toast(`已导出 ${result.count} 项`);
  } catch (error) {
    $("exportResult").classList.add("error");
    $("exportResult").textContent = error.message;
  } finally {
    exporting = false;
    $("submitExport").disabled =
      $("closeExport").disabled =
      $("exportDirectory").disabled =
        false;
    renderSelection();
    updateExportButtons();
  }
};
$("closeViewer").onclick = closeViewer;
$("fullSize").onclick = (e) => {
  if (active()?.mediaType === "video") return;
  e.preventDefault();
  openLightbox(active());
};
$("originalFrame").onclick = () => openLightbox(original(current));
$("originalFrame").onkeydown = (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    openLightbox(original(current));
  }
};
$("originalFrame").tabIndex = 0;
$("originalFrame").setAttribute("role", "button");
$("originalFrame").setAttribute("aria-label", "全屏查看原始构图");
$("lightboxImage").onload = () => {
  $("lightboxImage").hidden = false;
  $("lightboxMark").disabled = false;
  $("lightboxWrite").disabled = false;
  setLightboxMarking(lightbox.marking);
  fitLightbox(true);
};
$("lightboxImage").onerror = () => {
  $("lightboxImage").hidden = true;
  $("lightboxStatus").textContent = "完整图片载入失败";
};
$("lightboxClose").onclick = closeLightbox;
$("imageLightbox").addEventListener("cancel", (e) => {
  e.preventDefault();
  closeLightbox();
});
$("lightboxMinus").onclick = () => zoomLightbox(lightbox.zoom / 1.25);
$("lightboxPlus").onclick = () => zoomLightbox(lightbox.zoom * 1.25);
$("lightboxFit").onclick = () => fitLightbox(true);
$("lightboxActual").onclick = () => zoomLightbox(1 / lightbox.fit);
$("lightboxMark").onclick = () => setLightboxMarking(!lightbox.marking);
$("lightboxWrite").onclick = () => {
  setActiveVersion(lightbox.versionId);
  shownCommentId = null;
  showPoint();
  closeLightbox();
  $("commentText").focus();
};
$("lightboxClearPoint").onclick = () => {
  points = [];
  shownCommentId = requestId = null;
  showPoint();
  storeDraft();
};
$("lightboxUndoPoint").onclick = () => {
  points.pop();
  shownCommentId = requestId = null;
  showPoint();
  storeDraft();
};
$("lightboxStage").addEventListener("wheel", (e) => {
  if (!$("imageLightbox").open) return;
  e.preventDefault();
  const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? $("lightboxStage").clientHeight : 1,
    delta = Math.max(-100, Math.min(100, e.deltaY * unit));
  if (!delta) return;
  zoomLightbox(lightbox.zoom * Math.exp(-delta * (e.ctrlKey ? 0.01 : 0.0015)),
    e.clientX, e.clientY);
}, { passive: false });
$("lightboxStage").onpointerdown = (e) => {
  if (e.button !== 0 || !e.isPrimary || $("lightboxImage").hidden) return;
  e.preventDefault();
  lightbox.drag = { id: e.pointerId, x: e.clientX, y: e.clientY,
    startX: e.clientX, startY: e.clientY, moved: false };
  $("lightboxStage").classList.add("is-dragging");
  $("lightboxStage").setPointerCapture(e.pointerId);
};
$("lightboxStage").onpointermove = (e) => {
  if (lightbox.drag?.id !== e.pointerId) return;
  if (!lightbox.drag.moved && Math.hypot(e.clientX - lightbox.drag.startX,
    e.clientY - lightbox.drag.startY) < 6) return;
  lightbox.drag.moved = true;
  lightbox.x += e.clientX - lightbox.drag.x;
  lightbox.y += e.clientY - lightbox.drag.y;
  lightbox.drag.x = e.clientX;
  lightbox.drag.y = e.clientY;
  drawLightbox();
};
$("lightboxStage").onpointerup = $("lightboxStage").onpointercancel =
$("lightboxStage").onlostpointercapture = (e) => {
  if (lightbox.drag?.id !== e.pointerId) return;
  const mark = e.type === "pointerup" && !lightbox.drag.moved && lightbox.marking;
  lightbox.drag = null;
  $("lightboxStage").classList.remove("is-dragging");
  if ($("lightboxStage").hasPointerCapture(e.pointerId))
    $("lightboxStage").releasePointerCapture(e.pointerId);
  if (mark) {
    const rect = $("lightboxImage").getBoundingClientRect(),
      x = (e.clientX - rect.left) / rect.width,
      y = (e.clientY - rect.top) / rect.height;
    if (x < 0 || x > 1 || y < 0 || y > 1) return;
    points.push({ x, y });
    shownCommentId = requestId = null;
    showPoint();
    storeDraft();
  }
};
$("lightboxStage").ondblclick = (e) => {
  if (lightbox.marking) return;
  zoomLightbox(lightbox.zoom < 2 ? 2 : 1, e.clientX, e.clientY);
};
new ResizeObserver(() => {
  if ($("imageLightbox").open) fitLightbox();
}).observe($("lightboxStage"));
for (const id of ["afterImage", "beforeImage", "originalImage"])
  $(id).onload = drawPreviewPoints;
new ResizeObserver(drawPreviewPoints).observe($("viewport"));
$("viewer").addEventListener("cancel", (e) => {
  e.preventDefault();
  closeViewer();
});
$("prev").onclick = () => openPhoto(selected - 1);
$("next").onclick = () => openPhoto(selected + 1);
$("versionSelect").onchange = () => setActiveVersion($("versionSelect").value);
$("sourceSelect").onchange = () => {
  compareVersionId = $("sourceSelect").value;
  populateVersions();
  setMode(mode);
};
$("tripleMode").onclick = () => setMode("triple");
$("compareMode").onclick = () => {
  setActiveVersion(currentVersion(current).id);
  compareVersionId = defaultCompare(current, active())?.id || null;
  populateVersions();
  setMode("compare");
  $("split").value = 50;
  updateSplit();
};
$("originalMode").onclick = () => setMode("original");
$("zoom").onclick = () => {
  zoomed = !zoomed;
  $("zoom").setAttribute("aria-pressed", zoomed);
  $("zoom").textContent = zoomed ? "适合窗口" : "放大 2×";
  fitImage();
};
$("split").oninput = updateSplit;
$("mark").onclick = () => openLightbox(active(), true);
$("clearPoint").onclick = () => {
  points = [];
  shownCommentId = requestId = null;
  showPoint();
  storeDraft();
};
$("commentText").oninput = () => {
  requestId = null;
  storeDraft();
};
$("commentForm").onsubmit = (e) => {
  e.preventDefault();
  save();
};
$("acceptVersion").onclick = acceptCurrent;
let dragging = null;
$("picture").onpointerdown = (e) => {
  if (e.button !== 0) return;
  dragging = { startX: e.clientX, startY: e.clientY,
    split: Number($("split").value), moved: false };
  $("picture").setPointerCapture(e.pointerId);
};
$("picture").onpointermove = (e) => {
  if (!dragging) return;
  if (!dragging.moved && Math.hypot(e.clientX - dragging.startX,
    e.clientY - dragging.startY) < 6) return;
  dragging.moved = true;
  const r = $("picture").getBoundingClientRect();
  $("split").value = Math.max(
    0,
    Math.min(100, ((e.clientX - r.left) / r.width) * 100),
  );
  updateSplit();
};
$("picture").onpointerup = (e) => {
  if (dragging && !dragging.moved) {
    const r = $("picture").getBoundingClientRect(),
      x = ((e.clientX - r.left) / r.width) * 100,
      before = version(current, compareVersionId) || original(current);
    openLightbox(x <= dragging.split ? before : active());
  }
  dragging = null;
};
$("picture").onpointercancel = () => (dragging = null);
$("commentFilter").onclick = () => {
  commentOnly = !commentOnly;
  $("commentFilter").setAttribute("aria-pressed", commentOnly);
  renderGrid();
};
$("pendingFilter").onclick = () => {
  pendingOnly = !pendingOnly;
  $("pendingFilter").setAttribute("aria-pressed", pendingOnly);
  renderGrid();
};
$("search").oninput = () => renderGrid();
document.addEventListener("keydown", (e) => {
  if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey || e.shiftKey || e.isComposing) return;
  if (window.getSelection()?.isCollapsed === false) return;
  if ($("imageLightbox").open) return;
  if (
    albumLoading ||
    $("albumDialog").open ||
    $("exportDialog").open ||
    $("trashDialog").open ||
    $("settingsDialog").open
  ) return;
  if (e.target.matches("input,textarea,select,[contenteditable=true]")) return;
  if ($("viewer").open) {
    if (["ArrowLeft", "ArrowUp", "ArrowRight", "ArrowDown"].includes(e.key)) {
      e.preventDefault();
      if (!filtered.some((photo) => photo.id === current?.id)) return;
      const n = selected + (["ArrowLeft", "ArrowUp"].includes(e.key) ? -1 : 1);
      if (n >= 0 && n < filtered.length) openPhoto(n);
    }
    if (e.key.toLowerCase() === "c") {
      $("commentText").focus();
      e.preventDefault();
    }
    return;
  }
  const cols = getComputedStyle($("grid")).gridTemplateColumns.split(
      " ",
    ).length,
    deltas = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -cols, ArrowDown: cols };
  if (e.key in deltas) {
    e.preventDefault();
    selectCard(selected + deltas[e.key], true);
  }
  if (
    e.key === "Enter" &&
    filtered.length &&
    (e.target === $("grid") || e.target.closest?.(".photo-card"))
  ) {
    e.preventDefault();
    openPhoto(selected);
  }
});
window.addEventListener("resize", fitImage);
window.addEventListener("beforeunload", storeDraft);
$("search").value = new URLSearchParams(location.search).get("q") || "";
function renderAlbums() {
  const select = $("albumSelect");
  select.replaceChildren();
  for (const a of albums) {
    const count = a.id === albumId && !albumLoading ? items.length : (a.count ?? 0);
    const option = el("option", null, `${a.name} · ${count} 项 — ${a.path || ""}`);
    option.value = a.id;
    select.append(option);
  }
  if (albumId) select.value = albumId;
  select.disabled = !albums.length || albumLoading;
  $("openSettings").disabled = !albumId || albumLoading;
  renderVersionActions();
  $("albumPath").textContent = albums.find((a) => a.id === albumId)?.path ||
    (libraryRoot ? `照片根目录：${libraryRoot}` : "");
  if (!albums.length) {
    $("projectName").textContent = "暂无相册目录";
    $("stats").textContent = "";
    $("grid").replaceChildren(
      el(
        "p",
        "empty",
        "点击“打开相册目录”选择已有照片文件夹，或点击“新建相册”。",
      ),
    );
  }
}
async function listAlbums() {
  const data = await api("/api/albums");
  token = data.token || token;
  libraryRoot = data.libraryRoot || libraryRoot;
  albums = data.albums || [];
  renderAlbums();
  return albums;
}
async function openAlbum(id) {
  if (
    albumLoading ||
    writing ||
    selectionSaving ||
    exporting ||
    submitting ||
    accepting ||
    trashBusy ||
    settingsSaving
  ) {
    $("albumSelect").value = albumId;
    toast("请等待当前操作完成");
    return;
  }
  if (!albums.some((a) => a.id === id)) return;
  const previous = albumId,
    previousRuns = runs,
    previousPlanJobId = selectedAlbumJobId,
    previousGalleryScope = galleryScope,
    generation = ++albumGeneration;
  if (current) closeViewer();
  albumId = id;
  runs = [];
  selectedAlbumJobId = "";
  galleryScope = "planned";
  renderAlbumPlan();
  albumLoading = true;
  $("albumSelect").disabled = true;
  $("rescanAlbums").disabled = true;
  $("openAlbumDirectory").disabled = true;
  $("createAlbum").disabled = true;
  $("openSettings").disabled = true;
  $("grid").setAttribute("aria-busy", "true");
  $("loadError").textContent = "";
  try {
    const data = await api("/api/albums/open", { albumId: id });
    if (generation !== albumGeneration) return;
    token = data.token || token;
    project = data.project || project;
    items = data.items || [];
    comments = await api("/api/comments");
    runs = data.runs || [];
    codex = data.codex || { model: "", reasoning_effort: "", requirements: "" };
    trashAvailable = !!data.capabilities?.trash;
    multiPointAvailable = !!data.capabilities?.multiPoint;
    if (generation !== albumGeneration) return;
    category = "全部";
    commentOnly = pendingOnly = false;
    selected = 0;
    $("search").value = "";
    $("commentFilter").setAttribute("aria-pressed", "false");
    $("pendingFilter").setAttribute("aria-pressed", "false");
    $("projectName").textContent = project.name || "AI 修图工作台";
    document.title =
      (project.name ? project.name + " · " : "") + "AI 修图工作台";
    history.replaceState(
      null,
      "",
      location.pathname + "?album=" + encodeURIComponent(id),
    );
    renderFilters();
    renderGrid();
    renderAlbumPlan();
  } catch (error) {
    if (generation === albumGeneration) {
      albumId = previous;
      runs = previousRuns;
      selectedAlbumJobId = previousPlanJobId;
      galleryScope = previousGalleryScope;
      renderAlbumPlan();
      $("loadError").textContent = error.message;
      toast(error.message);
    }
  } finally {
    if (generation === albumGeneration) {
      albumLoading = false;
      $("grid").removeAttribute("aria-busy");
      $("rescanAlbums").disabled = false;
      $("openAlbumDirectory").disabled = false;
      $("createAlbum").disabled = false;
      renderAlbums();
    }
  }
}
async function refresh() {
  if (!albumId || albumLoading) return;
  const generation = albumGeneration;
  try {
    const [data, fresh, newRuns] = await Promise.all([
      api("/api/catalog"),
      api("/api/comments"),
      api("/api/codex/runs"),
    ]);
    if (generation !== albumGeneration) return;
    token = data.token || token;
    project = data.project || project;
    codex = data.codex || codex;
    trashAvailable = !!data.capabilities?.trash;
    multiPointAvailable = !!data.capabilities?.multiPoint;
    renderVersionActions();
    const changed = JSON.stringify(data.items) !== JSON.stringify(items),
      commentsChanged = JSON.stringify(fresh) !== JSON.stringify(comments),
      runsChanged = JSON.stringify(newRuns) !== JSON.stringify(runs);
    if (changed) {
      const openId = current?.id;
      items = data.items;
      renderAlbums();
      renderFilters();
      renderGrid();
      renderAlbumPlan();
      if (openId) {
        const i = filtered.findIndex((p) => p.id === openId);
        if (!items.some((p) => p.id === openId)) closeViewer();
        else if (i >= 0) openPhoto(i, true);
        else {
          current = items.find((p) => p.id === openId) || current;
          if (!version(current, activeVersionId)) {
            activeVersionId = currentVersion(current)?.id;
            restoreDraft();
          }
          populateVersions();
          renderSelection();
        }
      }
    }
    if (commentsChanged) {
      comments = fresh;
      renderGrid();
      renderComments();
      showPoint();
    }
    if (runsChanged) {
      runs = newRuns;
      renderRuns();
      renderAlbumPlan();
      renderComments();
    }
  } catch {}
}
function albumActionsBusy() {
  return (
    albumLoading ||
    albumDialogSaving ||
    writing ||
    selectionSaving ||
    exporting ||
    submitting ||
    accepting ||
    trashBusy ||
    settingsSaving
  );
}
async function browseDirectory(path) {
  const generation = ++directoryGeneration;
  const result = $("albumDialogResult");
  result.textContent = "正在读取目录…";
  result.classList.remove("error");
  try {
    const data = await api(
      "/api/directories?path=" + encodeURIComponent(path || ""),
    );
    if (generation !== directoryGeneration || !$("albumDialog").open) return;
    $("albumDirectory").value = data.path;
    $("directoryLocation").textContent = data.path;
    $("directoryParent").dataset.parent = data.parent || "";
    $("directoryParent").disabled = !data.parent;
    $("directoryParent").onclick = () => browseDirectory(data.parent);
    const list = $("directoryChildren");
    list.replaceChildren();
    for (const child of data.children || []) {
      const button = el("button", "directory-child", child.name);
      button.type = "button";
      button.title = child.path;
      button.onclick = () => browseDirectory(child.path);
      list.append(button);
    }
    if (!list.childElementCount)
      list.append(el("p", "quiet", "这里没有子目录。可以选择当前目录。"));
    result.textContent = "";
  } catch (error) {
    if (generation !== directoryGeneration || !$("albumDialog").open) return;
    result.textContent = error.message;
    result.classList.add("error");
  }
}
function showAlbumDialog(mode) {
  if (albumActionsBusy()) {
    toast("请等待当前操作完成");
    return;
  }
  albumDialogMode = mode;
  const creating = mode === "create";
  $("albumDialogTitle").textContent = creating ? "新建相册" : "打开相册目录";
  $("albumDialogHint").textContent = creating
    ? "选择新相册所在的上级目录，填写名称后创建。"
    : "选择或输入已有照片文件夹。照片会留在原处。";
  $("albumNameGroup").hidden = !creating;
  $("albumName").required = creating;
  $("albumName").value = "";
  $("confirmAlbumDialog").textContent = creating ? "创建并打开" : "打开相册";
  $("albumDialogResult").textContent = "";
  $("albumDialogResult").classList.remove("error");
  $("directoryChildren").replaceChildren();
  $("albumDirectory").value = creating
    ? libraryRoot
    : albums.find((a) => a.id === albumId)?.path || libraryRoot;
  $("albumDialog").showModal();
  browseDirectory($("albumDirectory").value);
  $(creating ? "albumName" : "albumDirectory").focus();
}
$("openAlbumDirectory").onclick = () => showAlbumDialog("open");
$("createAlbum").onclick = () => showAlbumDialog("create");
$("cancelAlbumDialog").onclick = () => $("albumDialog").close();
$("albumDialog").addEventListener("cancel", (event) => {
  if (albumDialogSaving) event.preventDefault();
});
$("albumDialog").addEventListener("close", () => ++directoryGeneration);
$("albumDirectory").oninput = () => {
  ++directoryGeneration;
  $("albumDialogResult").textContent = "";
};
$("browseDirectory").onclick = () =>
  browseDirectory($("albumDirectory").value.trim());
$("albumForm").onsubmit = async (event) => {
  event.preventDefault();
  if (albumActionsBusy()) return;
  const path = $("albumDirectory").value.trim();
  const name = $("albumName").value.trim();
  const creating = albumDialogMode === "create";
  if (!path.startsWith("/")) {
    $("albumDialogResult").textContent = "请输入以 / 开头的绝对路径";
    $("albumDialogResult").classList.add("error");
    return;
  }
  if (creating && !name) {
    $("albumDialogResult").textContent = "请填写相册名称";
    $("albumDialogResult").classList.add("error");
    return;
  }
  albumDialogSaving = true;
  ++directoryGeneration;
  for (const control of $("albumForm").querySelectorAll("button,input"))
    control.disabled = true;
  $("albumDialogResult").classList.remove("error");
  $("albumDialogResult").textContent = creating ? "正在创建相册…" : "正在打开相册…";
  try {
    const response = await api(
      creating ? "/api/albums/create" : "/api/albums/register",
      creating ? { parent: path, name } : { path },
    );
    await listAlbums();
    if (!albums.some((album) => album.id === response.album?.id))
      throw Error("相册已登记，但列表尚未更新；请点击“刷新目录”");
    $("albumDialog").close();
    albumDialogSaving = false;
    await openAlbum(response.album.id);
    if (albumId === response.album.id)
      toast(creating ? "相册已创建" : "相册已打开");
  } catch (error) {
    $("albumDialogResult").textContent = error.message;
    $("albumDialogResult").classList.add("error");
  } finally {
    albumDialogSaving = false;
    for (const control of $("albumForm").querySelectorAll("button,input"))
      control.disabled = false;
    $("directoryParent").disabled = !$("directoryParent").dataset.parent;
  }
};
$("albumSelect").onchange = () => openAlbum($("albumSelect").value);
$("rescanAlbums").onclick = async () => {
  if (
    albumLoading ||
    writing ||
    selectionSaving ||
    exporting ||
    submitting ||
    accepting ||
    settingsSaving
  )
    return;
  try {
    const previous = albumId;
    await listAlbums();
    if (previous && albums.some((a) => a.id === previous))
      await openAlbum(previous);
    else if (albums.length) await openAlbum(albums[0].id);
    else {
      albumId = "";
      items = [];
      comments = [];
      runs = [];
      selectedAlbumJobId = "";
      project = { name: "AI 修图工作台", root: "" };
      renderGrid();
      renderAlbumPlan();
    }
    toast("目录已刷新");
  } catch (error) {
    toast(error.message);
  }
};
$("openSettings").onclick = () => {
  $("modelInput").value = codex.model || "";
  $("effortSelect").value = codex.reasoning_effort || "";
  $("requirementsInput").value = codex.requirements || "";
  const concurrencyAvailable = Number.isInteger(codex.max_concurrency);
  $("concurrencyInput").disabled = !concurrencyAvailable;
  $("concurrencyInput").value = concurrencyAvailable ? codex.max_concurrency : 1;
  $("concurrencyHint").textContent = concurrencyAvailable
    ? "本相册默认同时运行 1 个 AI 任务，可设为 1–8。调低后，已开始的任务会继续完成；等待中的任务按新上限启动。"
    : "当前修图结束后更新服务即可设置";
  $("settingsResult").textContent = "";
  $("settingsResult").classList.remove("error");
  $("runAll").disabled = !$("requirementsInput").value.trim() || !items.length;
  $("settingsDialog").showModal();
  $("requirementsInput").focus();
};
$("closeSettings").onclick = () => $("settingsDialog").close();
$("requirementsInput").oninput = () => {
  $("runAll").disabled = settingsSaving || !$("requirementsInput").value.trim() || !items.length;
};
async function saveAlbumSettings(runAll) {
  if (settingsSaving) return;
  const settings = {
    model: $("modelInput").value.trim(),
    reasoning_effort: $("effortSelect").value,
    requirements: $("requirementsInput").value.trim(),
  };
  if (!$("concurrencyInput").disabled) {
    const value = $("concurrencyInput").value.trim();
    const count = Number(value);
    if (!value || !Number.isSafeInteger(count) || count < 1 || count > 8) {
      $("settingsResult").textContent = "最大并发任务数须为 1–8 的整数。";
      $("settingsResult").classList.add("error");
      $("concurrencyInput").focus();
      return;
    }
    settings.max_concurrency = count;
  }
  settingsSaving = true;
  let settingsSaved = false;
  $("saveSettings").disabled = $("runAll").disabled = true;
  $("settingsResult").textContent = "正在保存…";
  $("settingsResult").classList.remove("error");
  try {
    codex = await api("/api/codex/settings", settings);
    settingsSaved = true;
    if (runAll) {
      $("settingsResult").textContent = "正在安排整册分析…";
      const result = await api("/api/codex/run-all", {});
      selectedAlbumJobId = result.jobId;
      galleryScope = "planned";
      runs = [result, ...runs.filter((item) => item.jobId !== result.jobId)];
      renderAlbumPlan();
      $("settingsResult").textContent = "整册任务已安排。主 AI 的处理方案和子任务进度会显示在相册上方。";
      toast("已交给主 AI 处理");
      refresh();
    } else {
      $("settingsResult").textContent =
        settings.max_concurrency !== undefined
          ? "已保存。并发数立即作用于等待队列；本相册后续任务将使用新的要求与模型设置。"
          : "已保存。本相册后续任务将使用新的要求与模型设置。";
      toast("相册设置已保存");
    }
  } catch (error) {
    $("settingsResult").textContent =
      settingsSaved && runAll
        ? `相册设置已保存；整册任务启动失败：${error.message}`
        : error.message;
    $("settingsResult").classList.add("error");
  } finally {
    settingsSaving = false;
    $("saveSettings").disabled = false;
    $("runAll").disabled = !$("requirementsInput").value.trim() || !items.length;
  }
}
$("settingsForm").onsubmit = (e) => {
  e.preventDefault();
  saveAlbumSettings(false);
};
$("runAll").onclick = () => saveAlbumSettings(true);
(async () => {
  try {
    await listAlbums();
    if (albums.length) {
      const preferred = new URLSearchParams(location.search).get("album"),
        id = albums.some((a) => a.id === preferred) ? preferred : albums[0].id;
      await openAlbum(id);
    }
    setInterval(refresh, 5000);
  } catch (error) {
    $("loadError").textContent = error.message;
  }
})();
