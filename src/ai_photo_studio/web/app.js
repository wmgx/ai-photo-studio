"use strict";
const $ = (id) => document.getElementById(id);
let items = [],
  comments = [],
  filtered = [],
  project = { name: "AI 修图工作台", root: "" },
  albums = [],
  albumId = "",
  codex = { model: "", reasoning_effort: "", requirements: "" },
  runs = [],
  albumGeneration = 0;
let category = "全部",
  commentOnly = false,
  pendingOnly = false,
  selected = 0,
  current = null,
  activeVersionId = null,
  compareVersionId = null;
const revisionCountKey = "ai-photo-studio:latest-revision-count";
let requestedRevisionCount = 2,
  visibleRevisionIds = [],
  comparisonCustomized = false;
try {
  const stored = Number(localStorage.getItem(revisionCountKey));
  if (Number.isSafeInteger(stored) && stored > 0)
    requestedRevisionCount = stored;
} catch {}
let token = "",
  mode = "triple",
  zoomed = false,
  marking = false,
  point = null,
  requestId = null,
  writing = false,
  selectionSaving = false,
  exporting = false,
  submitting = false,
  accepting = false,
  settingsSaving = false,
  timer;
const statusNames = {
  saved: "已保存",
  open: "待处理",
  running: "处理中",
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
  const global = path === "/api/albums" || path === "/api/albums/open",
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
      JSON.stringify({ text: $("commentText").value, point, requestId }),
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
  point = draft.point || null;
  requestId = draft.requestId || null;
  showPoint();
}
function showPoint() {
  const has = !!point;
  $("pin").hidden = !has;
  $("locationRow").hidden = !has;
  if (has) {
    $("pin").style.left = point.x * 100 + "%";
    $("pin").style.top = point.y * 100 + "%";
    $("locationText").textContent =
      `已标记：横向 ${Math.round(point.x * 100)}% · 纵向 ${Math.round(point.y * 100)}%`;
  }
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
function renderGrid(focus = false) {
  const old = filtered[selected]?.id,
    q = $("search").value.trim().toLowerCase();
  filtered = items.filter(
    (p) =>
      (category === "全部" || p.category === category) &&
      (!commentOnly || photoComments(p.id).length) &&
      (!pendingOnly || isPending(p)) &&
      (!q || (p.id + " " + p.scene).toLowerCase().includes(q)),
  );
  let next = filtered.findIndex((p) => p.id === old);
  selected = next < 0 ? 0 : next;
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
          ? "请在指定照片根目录下放入照片文件夹，然后点击“刷新目录”。"
          : items.length
            ? "没有匹配的照片"
            : "这个相册还没有可审阅的照片",
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
    version(p, v.parentId) ||
    (base?.id !== v.id ? base : null) ||
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
    count = Math.min(requestedRevisionCount, all.length),
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
  const base = original(current),
    shown = shownRevisions();
  const displayed = [
      base,
      ...visibleRevisionIds.map((id) => version(current, id)),
    ].filter(Boolean),
    container = $("threeView");
  container.style.setProperty("--panel-count", displayed.length);
  container.style.minWidth =
    `${displayed.length * 280 + Math.max(0, displayed.length - 1) * 14 + 32}px`;
  container.replaceChildren();
  $("revisionCount").value = String(requestedRevisionCount);
  $("revisionCountHint").textContent =
    `原图 + ${shown.length}/${requestedRevisionCount} 张修订` +
    (active()?.kind !== "original" &&
    !visibleRevisionIds.includes(activeVersionId)
      ? ` · 评论版本：${label(active())}`
      : "");
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
      media.append(img);
    }
    if (item.id !== activeVersionId) {
      const activate = el("button", "panel-activate", "在这版留意见");
      activate.type = "button";
      activate.onclick = () => setActiveVersion(item.id);
      caption.append(activate);
    }
    panel.append(caption, media, note);
    container.append(panel);
  }
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
      label(item) + (item.id === current.currentVersionId ? " · 当前" : ""),
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
  $("fullSize").textContent = `全尺寸 · ${label(v)} ↗`;
  const video = v.mediaType === "video";
  $("video").pause();
  $("video").removeAttribute("src");
  if (video) $("video").src = mediaUrl(v, "version");
  $("photoMeta").textContent =
    `${current.id} · ${label(v)} · ${current.category || ""} · ${dimensions(v)}${v.reviewStatus === "approved" ? " · 已通过" : v.kind === "revision" ? " · 待审阅" : ""}`;
  $("commentFormTitle").textContent = `${label(v)} · 修改意见`;
  $("acceptVersion").hidden =
    v.kind !== "revision" || v.reviewStatus === "approved";
  $("acceptVersion").disabled = false;
  $("acceptVersion").textContent = "通过当前版本";
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
  const choose = el("button", null, "选择当前版本 · " + label(v));
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
  if (!current) {
    $("runStatus").textContent = "";
    return;
  }
  const mine = runs.filter((r) => r.photoId === current.id).slice(0, 3);
  const names = {
    queued: "排队中",
    running: "Codex 正在处理",
    ready: "已生成新版本，待审阅",
    needs_input: "需要补充信息",
    failed: "处理失败",
    completed: "已完成",
  };
  $("runStatus").textContent = mine
    .map(
      (r) =>
        `${names[r.status] || r.status}${r.model ? " · " + r.model : ""}${r.error ? "：" + r.error : r.reply ? "：" + r.reply : ""}`,
    )
    .join("\n");
}
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
function renderComments() {
  if (!current) return;
  const rows = photoComments(current.id),
    local = rows.filter((c) => c.versionId === activeVersionId),
    from = rows.filter(
      (c) =>
        c.resultVersionId === activeVersionId &&
        c.versionId !== activeVersionId,
    ),
    other = rows.filter(
      (c) =>
        c.versionId !== activeVersionId &&
        c.resultVersionId !== activeVersionId,
    );
  $("commentHeading").textContent = `本版评论 · ${local.length}`;
  $("comments").replaceChildren();
  if (!rows.length)
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
    block.append(
      time,
      el(
        "span",
        "status",
        (group === "from"
          ? "本版处理意见 · "
          : group === "other"
            ? "其他版本 · "
            : "") + (statusNames[c.status] || c.status),
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
    if (c.point) {
      const b = el("button", null, "查看标记位置");
      b.onclick = () => {
        if (c.versionId !== activeVersionId) setActiveVersion(c.versionId);
        point = { x: c.point.x, y: c.point.y };
        setMode("compare");
        $("split").value = 0;
        updateSplit();
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
  local.forEach((c) => addRow(c, "local"));
  if (from.length) {
    $("comments").append(el("h2", null, "本版处理意见"));
    from.forEach((c) => addRow(c, "from"));
  }
  if (other.length) {
    $("comments").append(el("h2", null, "其他版本评论"));
    other.forEach((c) => addRow(c, "other"));
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
  point = null;
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
  }
  current = p;
  activeVersionId = version(p, retained) ? retained : currentVersion(p)?.id;
  compareVersionId = oldCompare;
  if (samePhoto) {
    shownRevisions();
    showActiveInComparison();
  }
  selected = index;
  zoomed = false;
  marking = false;
  point = null;
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
  marking = false;
  $("mark").setAttribute("aria-pressed", "false");
  const v = active(),
    isVideo = v.mediaType === "video",
    canCompare = !!compareVersionId && compareVersionId !== v.id;
  $("beforeLayer").hidden = !canCompare;
  $("threeView").hidden = isVideo || mode !== "triple";
  $("picture").hidden = isVideo || mode !== "compare";
  $("originalFrame").hidden = isVideo || mode !== "original";
  $("video").hidden = !isVideo;
  $("compareControls").hidden = isVideo;
  $("revisionControls").hidden = isVideo || mode !== "triple";
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
  $("picture").classList.remove("mark-mode");
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
}
function updateSplit() {
  const v = Number($("split").value);
  $("beforeLayer").style.clipPath = `inset(0 ${100 - v}% 0 0)`;
  $("divider").style.left = v + "%";
  $("beforeLabel").style.opacity = v < 12 ? "0" : "1";
  $("divider").hidden = v === 0 || v === 100 || !compareVersionId;
}
async function save(submit) {
  if (writing || !current) return;
  const value = $("commentText").value,
    text = value.trim();
  if (!text) {
    $("commentText").focus();
    return;
  }
  writing = true;
  $("submitComment").disabled = $("saveComment").disabled = true;
  $("formStatus").className = "";
  $("formStatus").textContent = submit ? "正在保存并提交…" : "正在保存…";
  const photoId = current.id,
    versionId = activeVersionId,
    commentPoint = point,
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
      submit,
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
        point = null;
        requestId = null;
        showPoint();
      }
      $("formStatus").textContent =
        result.status === "failed"
          ? result.reply || "启动失败，可在评论中重试"
          : submit
            ? "已提交修改意见"
            : "评论已保存";
      $("formStatus").className = result.status === "failed" ? "error" : "";
      renderComments();
    }
    renderGrid();
  } catch (error) {
    if (current?.id === photoId && activeVersionId === versionId) {
      $("formStatus").textContent = error.message + "，输入内容仍保留。";
      $("formStatus").className = "error";
      storeDraft();
    } else
      try {
        localStorage.setItem(
          key,
          JSON.stringify({ text: value, point: commentPoint, requestId: id }),
        );
      } catch {}
  } finally {
    writing = false;
    $("submitComment").disabled = $("saveComment").disabled = false;
  }
}
async function acceptCurrent() {
  if (
    !current ||
    active()?.kind !== "revision" ||
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
    toast("已通过 " + label(approved));
  } catch (error) {
    toast(error.message);
    $("acceptVersion").disabled = false;
  } finally {
    accepting = false;
  }
}
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
$("revisionCount").onchange = () => {
  const value = Number($("revisionCount").value);
  if (!Number.isSafeInteger(value) || value < 1) {
    $("revisionCount").value = String(requestedRevisionCount);
    return;
  }
  requestedRevisionCount = value;
  comparisonCustomized = false;
  try {
    localStorage.setItem(revisionCountKey, String(value));
  } catch {}
  if (current) populateVersions();
};
$("tripleMode").onclick = () => setMode("triple");
$("compareMode").onclick = () => setMode("compare");
$("originalMode").onclick = () => setMode("original");
$("zoom").onclick = () => {
  zoomed = !zoomed;
  $("zoom").setAttribute("aria-pressed", zoomed);
  $("zoom").textContent = zoomed ? "适合窗口" : "放大 2×";
  fitImage();
};
$("split").oninput = updateSplit;
$("mark").onclick = () => {
  const next = !marking;
  if (mode !== "compare" && active().mediaType !== "video") setMode("compare");
  marking = next;
  $("mark").setAttribute("aria-pressed", marking);
  $("picture").classList.toggle("mark-mode", marking);
  if (marking && mode === "compare") {
    $("split").value = 0;
    updateSplit();
    toast("点击当前版本画面中想修改的位置");
  }
};
$("clearPoint").onclick = () => {
  point = null;
  showPoint();
  storeDraft();
};
$("commentText").oninput = () => {
  requestId = null;
  storeDraft();
};
$("commentForm").onsubmit = (e) => {
  e.preventDefault();
  save(true);
};
$("saveComment").onclick = () => save(false);
$("acceptVersion").onclick = acceptCurrent;
let dragging = false;
$("picture").onpointerdown = (e) => {
  const r = $("picture").getBoundingClientRect(),
    x = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)),
    y = Math.max(0, Math.min(1, (e.clientY - r.top) / r.height));
  if (marking) {
    e.preventDefault();
    point = { x, y };
    marking = false;
    $("mark").setAttribute("aria-pressed", "false");
    $("picture").classList.remove("mark-mode");
    showPoint();
    storeDraft();
    $("commentText").focus();
    return;
  }
  dragging = true;
  $("picture").setPointerCapture(e.pointerId);
  $("split").value = x * 100;
  updateSplit();
};
$("picture").onpointermove = (e) => {
  if (!dragging) return;
  const r = $("picture").getBoundingClientRect();
  $("split").value = Math.max(
    0,
    Math.min(100, ((e.clientX - r.left) / r.width) * 100),
  );
  updateSplit();
};
$("picture").onpointerup = () => (dragging = false);
$("picture").onpointercancel = () => (dragging = false);
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
  if ($("exportDialog").open || $("settingsDialog").open) return;
  if (e.target.matches("input,textarea,select,[contenteditable=true]")) return;
  if ($("viewer").open) {
    if (["ArrowLeft", "ArrowUp", "ArrowRight", "ArrowDown"].includes(e.key)) {
      e.preventDefault();
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
    const option = el("option", null, `${a.name} · ${a.count ?? 0} 项`);
    option.value = a.id;
    select.append(option);
  }
  if (albumId) select.value = albumId;
  select.disabled = !albums.length;
  $("openSettings").disabled = !albumId;
  if (!albums.length) {
    $("projectName").textContent = "暂无相册目录";
    $("stats").textContent = "";
    $("grid").replaceChildren(
      el(
        "p",
        "empty",
        "请在指定照片根目录下放入照片文件夹，然后点击“刷新目录”。",
      ),
    );
  }
}
async function listAlbums() {
  const data = await api("/api/albums");
  token = data.token || token;
  albums = data.albums || [];
  renderAlbums();
  return albums;
}
async function openAlbum(id) {
  if (
    writing ||
    selectionSaving ||
    exporting ||
    submitting ||
    accepting ||
    settingsSaving
  ) {
    $("albumSelect").value = albumId;
    toast("请等待当前操作完成");
    return;
  }
  if (!albums.some((a) => a.id === id)) return;
  const previous = albumId,
    generation = ++albumGeneration;
  if (current) closeViewer();
  albumId = id;
  $("albumSelect").disabled = true;
  $("rescanAlbums").disabled = true;
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
    $("openSettings").disabled = false;
  } catch (error) {
    if (generation === albumGeneration) {
      albumId = previous;
      $("loadError").textContent = error.message;
      toast(error.message);
    }
  } finally {
    if (generation === albumGeneration) {
      $("albumSelect").disabled = false;
      $("rescanAlbums").disabled = false;
      $("albumSelect").value = albumId;
    }
  }
}
async function refresh() {
  if (!albumId) return;
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
    const changed = JSON.stringify(data.items) !== JSON.stringify(items),
      commentsChanged = JSON.stringify(fresh) !== JSON.stringify(comments),
      runsChanged = JSON.stringify(newRuns) !== JSON.stringify(runs);
    if (changed) {
      const openId = current?.id;
      items = data.items;
      renderFilters();
      renderGrid();
      if (openId) {
        const i = filtered.findIndex((p) => p.id === openId);
        if (i >= 0) openPhoto(i, true);
        else {
          current = items.find((p) => p.id === openId) || current;
          populateVersions();
          renderSelection();
        }
      }
    }
    if (commentsChanged) {
      comments = fresh;
      renderGrid();
      renderComments();
    }
    if (runsChanged) {
      runs = newRuns;
      renderRuns();
    }
  } catch {}
}
$("albumSelect").onchange = () => openAlbum($("albumSelect").value);
$("rescanAlbums").onclick = async () => {
  if (
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
      project = { name: "AI 修图工作台", root: "" };
      renderGrid();
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
  $("settingsResult").textContent = "";
  $("settingsResult").classList.remove("error");
  $("runAll").disabled = !$("requirementsInput").value.trim();
  $("settingsDialog").showModal();
  $("requirementsInput").focus();
};
$("closeSettings").onclick = () => $("settingsDialog").close();
$("requirementsInput").oninput = () => {
  $("runAll").disabled = settingsSaving || !$("requirementsInput").value.trim();
};
async function saveAlbumSettings(runAll) {
  if (settingsSaving) return;
  settingsSaving = true;
  let settingsSaved = false;
  $("saveSettings").disabled = $("runAll").disabled = true;
  $("settingsResult").textContent = "正在保存…";
  $("settingsResult").classList.remove("error");
  try {
    codex = await api("/api/codex/settings", {
      model: $("modelInput").value.trim(),
      reasoning_effort: $("effortSelect").value,
      requirements: $("requirementsInput").value.trim(),
    });
    settingsSaved = true;
    if (runAll) {
      $("settingsResult").textContent = "正在安排整册处理…";
      const result = await api("/api/codex/run-all", {});
      const started = result.started || [],
        skipped = result.skipped || [],
        failed = result.failed || [];
      $("settingsResult").textContent = [
        `已安排 ${started.length} 张照片；跳过 ${skipped.length} 张；启动失败 ${failed.length} 张。`,
        ...failed.map((item) => `${item.photoId || "照片"}：${item.error || "启动失败"}`),
      ].join("\n");
      $("settingsResult").classList.toggle("error", failed.length > 0);
      toast(`已安排 ${started.length} 张照片处理`);
      refresh();
    } else {
      $("settingsResult").textContent =
        "已保存。本相册后续提交将使用这些要求和模型设置。";
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
    $("runAll").disabled = !$("requirementsInput").value.trim();
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
