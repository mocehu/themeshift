/* ThemeShift Studio. Native ES module; no third-party or external requests. */
const API = "/api";
const LAST_JOB_KEY = "themeshift:lastJobId";
const ACTIVE_STATUSES = new Set(["queued", "running"]);
const JOB_STATUSES = new Set(["queued", "running", "succeeded", "failed"]);
const STATUS_LABELS = { queued: "排队中", running: "处理中", succeeded: "已完成", failed: "未能完成" };
const METADATA_FIELDS = ["name", "name_en", "author", "version_number", "brief", "brief_en"];
const SVG_NS = "http://www.w3.org/2000/svg";
const $ = (id) => document.getElementById(id);
const ui = {
  form: $("conversion-form"), fields: $("task-fields"), fileInput: $("file-input"),
  dropTarget: $("drop-target"), chooseFile: $("choose-file"), selectedFile: $("selected-file"),
  fileName: $("file-name"), fileDetails: $("file-details"), fileError: $("file-error"),
  fileCheck: $("file-check-status"), uploadLimit: $("upload-limit"),
  source: $("source"), target: $("target"), directionHint: $("direction-hint"),
  routeSummary: $("route-summary"), dryRun: $("dry-run"), submit: $("submit-job"),
  marginOption: $("icon-margin-option"), marginInput: $("icon-margin"),
  marginValue: $("margin-value"), marginHelp: $("margin-help"),
  submitLabel: $("submit-label"), submitHint: $("submit-hint"), formError: $("form-error"),
  uncertain: $("uncertain-upload"), confirmResubmit: $("confirm-resubmit"),
  apiNotice: $("api-notice"), serviceStatus: $("service-status"), serviceLabel: $("service-label"),
  serviceVersion: $("service-version"), retryApi: $("retry-api"),
  taskPanel: $("task-panel"), taskBadge: $("task-badge"), taskEmpty: $("task-empty"),
  taskActive: $("task-active"), jobFilename: $("job-filename"), jobRoute: $("job-route"),
  progressLabel: $("progress-label"), progress: $("task-progress"), progressValue: $("progress-value"),
  progressNote: $("progress-note"), jobFacts: $("job-facts"), jobId: $("job-id"),
  jobCreated: $("job-created"), jobExpires: $("job-expires"), jobError: $("job-error"),
  jobNotice: $("job-notice"), recoveryActions: $("task-recovery-actions"),
  retryJob: $("retry-job"), forgetJob: $("forget-job"), result: $("job-result"),
  taskBottom: $("task-bottom"), deleteJob: $("delete-job"), taskConfirm: $("task-confirm"),
  confirmText: $("task-confirm-text"), confirmAction: $("confirm-task-action"),
  cancelAction: $("cancel-task-action"), navDot: $("nav-task-dot"),
  stepper: $("stepper"), stepPrev: $("step-prev"), stepNext: $("step-next"), stepHint: $("step-hint"),
  backToEdit: $("back-to-edit"), newJob: $("new-job"), navWorkspace: $("nav-workspace"), navTask: $("nav-task"),
  openCapabilities: $("open-capabilities"), closeCapabilities: $("close-capabilities"),
  capabilitiesDialog: $("capabilities-dialog"),
};
const state = {
  catalog: null, loadingServices: false, catalogError: "", healthError: "", healthVersion: "",
  connectionIssue: false, file: null, checkingFile: false, fileCheckToken: 0,
  submitting: false, uploadPercent: null, uploadSent: false, uncertainUpload: false,
  job: null, jobId: null, restoring: false, deleting: false, confirmation: null,
  pollTimer: null, pollController: null, pollInFlight: false, pollEpoch: 0, pollFailures: 0,
  notice: "", noticeTone: "warning", lastAnnouncement: "", leaving: false,
  marginTarget: "", step: 1,
};

// Dynamic API/file content is always assigned as text, never parsed as markup.
function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = String(text);
  return element;
}
function icon(name) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(SVG_NS, "use");
  use.setAttribute("href", `#icon-${name}`);
  svg.append(use);
  return svg;
}
function message(element, text) {
  element.textContent = text || "";
  element.hidden = !text;
}
function announce(text) {
  if (!text || text === state.lastAnnouncement) return;
  state.lastAnnouncement = text;
  $("announcer").textContent = text;
}
function isRecord(value) { return value !== null && typeof value === "object" && !Array.isArray(value); }
function string(value) { return typeof value === "string" ? value : ""; }
function stringList(value) { return Array.isArray(value) ? value.filter((item) => typeof item === "string") : []; }
function isActive(job = state.job) { return Boolean(job && ACTIVE_STATUSES.has(job.status)); }
function isLocked() { return state.submitting || state.restoring || state.deleting || isActive(); }
function formatBytes(value) {
  if (!Number.isFinite(value) || value < 0) return "大小未知";
  if (value < 1024) return `${value.toLocaleString("zh-CN")} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let amount = value / 1024;
  let unit = 0;
  while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit += 1; }
  return `${amount.toLocaleString("zh-CN", { maximumFractionDigits: amount < 10 ? 1 : 0 })} ${units[unit]}`;
}
function formatCount(value) {
  return Number.isInteger(value) && value >= 0 ? value.toLocaleString("zh-CN") : "—";
}
function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
  });
}
function formatDuration(seconds) {
  if (seconds >= 86400 && seconds % 86400 === 0) return `${seconds / 86400} 天`;
  if (seconds >= 3600 && seconds % 3600 === 0) return `${seconds / 3600} 小时`;
  if (seconds >= 60 && seconds % 60 === 0) return `${seconds / 60} 分钟`;
  return `${seconds} 秒`;
}
function platformName(id) { return id === "auto" ? "自动识别" : state.catalog?.byId.get(id)?.name || string(id) || "自动识别"; }
function moduleName(id) {
  return new Map([["icons", "图标"], ["dynamic_icons", "动态图标"], ["wallpapers", "壁纸"], ["lockscreen", "锁屏"], ["system_ui", "系统 UI"]]).get(id) || id;
}
function setJobNotice(text, tone = "warning") {
  state.notice = text;
  state.noticeTone = tone;
  ui.jobNotice.className = `notice notice-${tone}`;
  message(ui.jobNotice, text);
}

class RequestError extends Error {
  constructor(status, code, text) {
    super(text);
    this.name = "RequestError";
    this.status = status;
    this.code = code;
  }
}
function serverError(status, body) {
  const error = isRecord(body?.error) ? body.error : {};
  const fallback = status === 413 ? "文件超过服务端允许的大小，请选择更小的主题。"
    : status === 429 ? "服务当前繁忙，请稍后重试。"
    : `服务请求未成功（HTTP ${status}），请稍后重试。`;
  return new RequestError(status, string(error.code) || "http_error", string(error.message) || fallback);
}
async function requestJSON(path, { method = "GET", signal } = {}) {
  const controller = new AbortController();
  let timedOut = false;
  const abort = () => controller.abort();
  if (signal?.aborted) controller.abort();
  else signal?.addEventListener("abort", abort, { once: true });
  const timer = window.setTimeout(() => { timedOut = true; controller.abort(); }, 15000);
  try {
    const headers = { Accept: "application/json" };
    if (method !== "GET") headers["X-ThemeShift-Request"] = "1";
    const response = await fetch(path, { method, headers, credentials: "same-origin", cache: "no-store", signal: controller.signal });
    let body = null;
    if (response.status !== 204) {
      try { body = await response.json(); }
      catch {
        if (!response.ok) throw serverError(response.status, null);
        throw new RequestError(response.status, "invalid_response", "服务未返回有效的 JSON 数据，请检查服务版本后重试。");
      }
    }
    if (!response.ok) throw serverError(response.status, body);
    return body;
  } catch (error) {
    if (error instanceof RequestError || signal?.aborted) throw error;
    throw new RequestError(0, timedOut ? "request_timeout" : "network_error",
      timedOut ? "连接服务超时，请检查连接后重试。" : "暂时无法连接服务，请检查网络或确认 ThemeShift 服务仍在运行。");
  } finally {
    window.clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}

// Only the server's registered readers, writers, and routes are selectable.
function normalizeMargin(value) {
  if (!isRecord(value)) return null;
  const { min, max, default: fallback, canvas } = value;
  if (!Number.isSafeInteger(min) || !Number.isSafeInteger(max) || !Number.isSafeInteger(fallback)) return null;
  if (min < 0 || max < min || fallback < min || fallback > max) return null;
  return { min, max, fallback, canvas: Number.isSafeInteger(canvas) && canvas > 0 ? canvas : null };
}
function normalizeCatalog(body) {
  if (!isRecord(body) || !Array.isArray(body.platforms) || !Array.isArray(body.routes)
      || !Number.isSafeInteger(body.limits?.max_upload_bytes) || body.limits.max_upload_bytes <= 0) {
    throw new RequestError(200, "invalid_platforms", "平台能力或上传限制不完整，已暂停上传。请检查服务端 API 版本。");
  }
  const byId = new Map();
  for (const item of body.platforms) {
    if (!isRecord(item) || !string(item.id) || byId.has(item.id)) continue;
    const extensions = [...new Set(stringList(item.input_extensions).map((extension) => {
      const normalized = extension.trim().toLowerCase();
      return normalized.startsWith(".") ? normalized : `.${normalized}`;
    }).filter((extension) => /^\.[a-z0-9][a-z0-9._-]*$/.test(extension)))];
    byId.set(item.id, {
      id: item.id, name: string(item.display_name) || item.id,
      canRead: item.can_read === true, canWrite: item.can_write === true,
      extensions, outputExtension: string(item.output_extension),
      readModules: stringList(item.read_modules), writeModules: stringList(item.write_modules),
      // Only a well-formed descriptor enables the control; never guess limits.
      margin: normalizeMargin(item.icon_margin),
    });
  }
  const routes = [];
  const seen = new Set();
  for (const route of body.routes) {
    if (!isRecord(route) || !byId.get(route.source)?.canRead || !byId.get(route.target)?.canWrite) continue;
    const key = JSON.stringify([route.source, route.target]);
    if (seen.has(key)) continue;
    seen.add(key);
    routes.push({ source: route.source, target: route.target, modules: stringList(route.modules) });
  }
  return { byId, platforms: [...byId.values()], routes, limits: body.limits, version: string(body.version) };
}
function renderServiceStatus() {
  const issue = state.catalogError || state.healthError || state.connectionIssue;
  let label = "正在连接服务";
  let tone = "connecting";
  if (issue) { label = state.connectionIssue ? "连接暂时中断" : "服务连接异常"; tone = "error"; }
  else if (state.catalog) { label = "服务已连接"; tone = "ok"; }
  ui.serviceLabel.textContent = label;
  ui.serviceStatus.dataset.state = tone;
  ui.retryApi.hidden = !issue;
  ui.retryApi.disabled = state.loadingServices;
  const version = state.healthVersion || state.catalog?.version;
  message(ui.serviceVersion, version ? `v${version.replace(/^v/i, "")}` : "");
  message(ui.apiNotice, state.catalogError);
}
async function loadServices() {
  if (state.loadingServices) return;
  state.loadingServices = true;
  ui.retryApi.disabled = true;
  const catalogWork = (async () => {
    try {
      state.catalog = normalizeCatalog(await requestJSON(`${API}/platforms`));
      state.catalogError = "";
      state.connectionIssue = false;
      populatePlatforms();
      renderCapabilities();
    } catch (error) {
      state.catalogError = `无法读取平台能力：${error.message} 可使用页面右上角的按钮重新连接。`;
      state.catalog = null;
      ui.uploadLimit.textContent = "连接服务后显示可上传类型与大小限制";
      ui.target.replaceChildren(new Option("平台能力暂不可用", ""));
      $("route-list").replaceChildren(node("li", "muted", "平台能力未获取成功，不展示未确认的转换方向。"));
      $("platform-list").replaceChildren(node("li", "muted", "请重新连接服务。"));
      $("platform-count").hidden = true;
    }
    syncDirection();
    updateControls();
    renderServiceStatus();
    renderTask();
  })();
  const healthWork = (async () => {
    try {
      const health = await requestJSON(`${API}/health`);
      if (health?.status !== "ok") throw new Error("健康检查未通过");
      state.healthError = "";
      state.healthVersion = string(health.version);
    } catch (error) { state.healthError = error.message; }
    renderServiceStatus();
  })();
  await Promise.all([catalogWork, healthWork]);
  state.loadingServices = false;
  renderServiceStatus();
}
function populatePlatforms() {
  const previous = ui.source.value;
  const readable = state.catalog.platforms.filter((platform) => platform.canRead);
  ui.source.replaceChildren(new Option("自动识别来源", "auto"));
  readable.forEach((platform) => ui.source.add(new Option(platform.name, platform.id)));
  if (readable.some((platform) => platform.id === previous)) ui.source.value = previous;
  const extensions = [...new Set(readable.flatMap((platform) => platform.extensions))];
  ui.fileInput.accept = extensions.join(",");
  ui.uploadLimit.textContent = extensions.length
    ? `${extensions.join(" / ")} · 单个文件不超过 ${formatBytes(state.catalog.limits.max_upload_bytes)}`
    : "当前没有已配置输入文件类型的读取平台";
  if (state.file && state.file.size > state.catalog.limits.max_upload_bytes) {
    state.file = null;
    renderFile();
    message(ui.fileError, "服务的上传限制已变更，原文件超出当前限制，请重新选择。");
  }
}
function getDirection() {
  if (!state.catalog) return { sources: [], routes: [], targets: [], mismatch: false, ambiguous: false };
  let sources = state.catalog.platforms.filter((platform) => platform.canRead);
  if (ui.source.value !== "auto") sources = sources.filter((platform) => platform.id === ui.source.value);
  if (state.file) sources = sources.filter((platform) => matchesExtension(state.file.name, platform));
  const routes = state.catalog.routes.filter((route) => sources.some((platform) => platform.id === route.source));
  const targetIds = new Set(routes.map((route) => route.target));
  return {
    sources, routes, targets: state.catalog.platforms.filter((platform) => targetIds.has(platform.id)),
    mismatch: Boolean(state.file && !sources.length),
    ambiguous: Boolean(state.file && ui.source.value === "auto" && sources.length > 1),
  };
}
function renderMarginValue() {
  const spec = state.catalog?.byId.get(ui.target.value)?.margin;
  if (!spec) return;
  const raw = Number(ui.marginInput.value);
  const margin = Number.isFinite(raw) ? Math.min(spec.max, Math.max(spec.min, raw)) : spec.fallback;
  ui.marginValue.textContent = `${margin} px`;
  ui.marginHelp.textContent = spec.canvas
    ? `画布 ${spec.canvas} px 时，图标内容区为 ${spec.canvas - margin * 2} px。边距越大图标越小；过小可能被系统圆角裁切。`
    : "边距越大，图标越小、留白越多；设为 0 时内容可贴边，可能被系统圆角裁切。";
}
function syncMargin(target) {
  const spec = target?.margin || null;
  const id = spec ? target.id : "";
  const changed = state.marginTarget !== id;
  state.marginTarget = id;
  ui.marginOption.hidden = !spec;
  ui.marginInput.disabled = !spec;
  if (!spec) return;
  ui.marginInput.min = String(spec.min);
  ui.marginInput.max = String(spec.max);
  const current = Number(ui.marginInput.value);
  const next = changed || !Number.isFinite(current)
    ? spec.fallback : Math.min(spec.max, Math.max(spec.min, current));
  ui.marginInput.value = String(next);
  renderMarginValue();
}
function syncDirection() {
  const { sources, targets, routes, mismatch, ambiguous } = getDirection();
  const previous = ui.target.value;
  const placeholder = !state.catalog ? "等待平台信息" : ambiguous ? "请先明确选择源平台"
    : targets.length ? "请选择目标平台" : "没有可用的目标平台";
  ui.target.replaceChildren(new Option(placeholder, ""));
  targets.forEach((target) => ui.target.add(new Option(`${target.name}${target.outputExtension ? ` · ${target.outputExtension}` : ""}`, target.id)));
  if (!ambiguous) {
    if (targets.some((target) => target.id === previous)) ui.target.value = previous;
    else if (targets.length === 1) ui.target.value = targets[0].id;
  }
  ui.target.disabled = !targets.length || ambiguous;
  let hint = "上传文件后，将按已支持的转换方向筛选目标平台。";
  if (mismatch) hint = "文件类型与所选来源不匹配。请切换为自动识别，或重新选择主题文件。";
  else if (ambiguous) hint = `此扩展名同时匹配 ${sources.map((source) => source.name).join("、")}。请在“源平台”中明确选择来源，不能使用自动识别。`;
  else if (state.catalog && !routes.length) hint = "当前来源没有可用的移植路由；可读取不代表能够转换至其他平台。";
  else if (state.file && ui.source.value === "auto") hint = `按扩展名匹配：${sources.map((source) => source.name).join("、")}。主题内容仍需服务端验证。`;
  else if (ui.source.value !== "auto" && state.catalog) hint = "已指定源平台；目标仅列出服务端为此来源提供的转换方向。";
  ui.directionHint.textContent = hint;
  ui.directionHint.classList.toggle("is-error", mismatch || ambiguous || Boolean(state.catalog && !routes.length));
  ui.source.setAttribute("aria-invalid", mismatch || ambiguous ? "true" : "false");
  if (ambiguous) announce(hint);
  const target = state.catalog?.byId.get(ui.target.value);
  const routeSources = routes.filter((route) => route.target === target?.id).map((route) => platformName(route.source));
  ui.routeSummary.textContent = target
    ? `${[...new Set(routeSources)].join(" / ")} → ${target.name}${target.outputExtension ? ` ${target.outputExtension}` : ""}`
    : ambiguous ? "请先指定源平台，再确认移植方向"
    : state.catalog ? "仅支持上方实际可选的转换方向" : "等待读取平台能力";
  syncMargin(target);
  updateControls();
}
function renderCapabilities() {
  const { platforms, byId, routes, limits } = state.catalog;
  message($("platform-count"), String(platforms.length));
  const routeNodes = routes.map((route) => {
    const source = byId.get(route.source);
    const target = byId.get(route.target);
    const row = node("li", "capability-route");
    const sourceChip = node("span", "platform-chip", source.name);
    if (source.extensions.length) sourceChip.append(node("small", "", source.extensions.join(" / ")));
    const targetChip = node("span", "platform-chip", target.name);
    if (target.outputExtension) targetChip.append(node("small", "", target.outputExtension));
    row.append(sourceChip, icon("arrow"), targetChip);
    if (route.modules.length) row.append(node("span", "module-chip", route.modules.map(moduleName).join("、")));
    return row;
  });
  $("route-list").replaceChildren(...(routeNodes.length ? routeNodes : [node("li", "muted", "当前没有已注册的移植路由。请在服务端配置读取器、写出器与映射。") ]));
  $("platform-list").replaceChildren(...platforms.map((platform) => {
    const row = node("li");
    row.append(node("strong", "", platform.name));
    const read = platform.canRead ? `可读取${platform.readModules.length ? `：${platform.readModules.map(moduleName).join("、")}` : ""}` : "不支持读取";
    const write = platform.canWrite ? `可写出${platform.writeModules.length ? `：${platform.writeModules.map(moduleName).join("、")}` : ""}` : "不支持写出";
    row.append(node("span", "", `${read} · ${write}`));
    return row;
  }));
  if (!platforms.length) $("platform-list").append(node("li", "muted", "服务端尚未注册平台。"));
  const parts = [];
  if (Number.isFinite(limits.retention_seconds) && limits.retention_seconds >= 0) parts.push(`任务保留 ${formatDuration(limits.retention_seconds)}`);
  if (Number.isInteger(limits.workers) && limits.workers > 0) parts.push(`并发处理数 ${limits.workers}`);
  if (Number.isInteger(limits.max_jobs) && limits.max_jobs > 0) parts.push(`任务上限 ${limits.max_jobs}`);
  $("service-limits").textContent = `${parts.join(" · ")}${parts.length ? "。" : ""}更多平台由服务端扩展，界面按能力自动呈现。`;
}

// Cheap preflight checks are not a substitute for server-side archive validation.
function matchesExtension(filename, platform) {
  const lower = filename.toLowerCase();
  return platform.extensions.some((extension) => lower.endsWith(extension));
}
async function chooseFiles(files) {
  if (isLocked() || !state.catalog) return;
  const token = ++state.fileCheckToken;
  state.file = null;
  state.checkingFile = true;
  message(ui.fileError, "");
  message(ui.formError, "");
  message(ui.fileCheck, "正在检查文件类型、大小与基础签名…");
  renderFile();
  syncDirection();
  try {
    if (files.length !== 1) throw new Error("每次只能上传一个主题文件，请重新选择；文件夹或多个文件不受支持。");
    const file = files[0];
    const readers = state.catalog.platforms.filter((platform) => platform.canRead);
    if (!readers.some((platform) => matchesExtension(file.name, platform))) {
      const extensions = [...new Set(readers.flatMap((platform) => platform.extensions))];
      throw new Error(`当前服务不支持此文件类型。请选择 ${extensions.join("、") || "已配置的源主题格式"} 文件，不要通过修改扩展名伪装格式。`);
    }
    if (!file.size) throw new Error("所选文件为空，无法读取主题。请重新导出或选择完整的主题文件。");
    if (file.size > state.catalog.limits.max_upload_bytes) {
      throw new Error(`文件为 ${formatBytes(file.size)}，超过 ${formatBytes(state.catalog.limits.max_upload_bytes)} 的上传限制。请选择较小的完整主题。`);
    }
    // MTZ is ZIP-based. Other future registered formats defer content checks to their reader.
    if (file.name.toLowerCase().endsWith(".mtz")) {
      const bytes = new Uint8Array(await file.slice(0, 4).arrayBuffer());
      const signature = bytes.length === 4 && bytes[0] === 0x50 && bytes[1] === 0x4b
        && ((bytes[2] === 0x03 && bytes[3] === 0x04) || (bytes[2] === 0x05 && bytes[3] === 0x06)
          || (bytes[2] === 0x07 && bytes[3] === 0x08));
      if (!signature) throw new Error("此 .mtz 文件缺少 ZIP 容器签名，可能已损坏或只是改了扩展名。请选择原始主题包；最终结构仍由服务端验证。");
    }
    if (token !== state.fileCheckToken) return;
    state.file = file;
    announce(`已选择 ${file.name}，${formatBytes(file.size)}。`);
  } catch (error) {
    if (token !== state.fileCheckToken) return;
    message(ui.fileError, error instanceof Error ? error.message : "无法读取此文件，请重新选择。");
  } finally {
    if (token === state.fileCheckToken) {
      state.checkingFile = false;
      message(ui.fileCheck, "");
      renderFile();
      syncDirection();
    }
  }
}
function renderFile() {
  ui.chooseFile.hidden = Boolean(state.file);
  ui.selectedFile.hidden = !state.file;
  ui.fileName.textContent = state.file?.name || "";
  ui.fileDetails.textContent = state.file ? `${formatBytes(state.file.size)} · 已选中` : "";
}
function removeFile() {
  if (isLocked()) return;
  state.fileCheckToken += 1;
  state.checkingFile = false;
  state.file = null;
  ui.fileInput.value = "";
  message(ui.fileError, "");
  message(ui.fileCheck, "");
  message(ui.formError, "");
  renderFile();
  syncDirection();
  ui.chooseFile.focus();
  announce("已移除源主题文件。");
}
function setStep(step, { focus = true } = {}) {
  const next = Math.min(3, Math.max(1, Number(step) || 1));
  state.step = next;
  for (const panel of document.querySelectorAll("[data-panel]")) {
    panel.hidden = panel.dataset.panel !== String(next);
  }
  for (const item of ui.stepper.querySelectorAll("[data-stepper]")) {
    const active = item.dataset.stepper === String(next);
    item.classList.toggle("is-current", active);
    if (active) item.setAttribute("aria-current", "step");
    else item.removeAttribute("aria-current");
  }
  const onTask = next === 3;
  ui.navWorkspace.classList.toggle("is-current", !onTask);
  ui.navTask.classList.toggle("is-current", onTask);
  if (onTask) {
    ui.navWorkspace.removeAttribute("aria-current");
    ui.navTask.setAttribute("aria-current", "page");
  } else {
    ui.navTask.removeAttribute("aria-current");
    ui.navWorkspace.setAttribute("aria-current", "page");
  }
  if (onTask) renderTask();
  updateControls();
  if (focus) {
    const panel = document.querySelector(`.step-panel[data-panel="${next}"]`)
      || document.querySelector(`[data-panel="${next}"]`);
    panel?.focus({ preventScroll: false });
    announce(next === 1 ? "第一步：选择文件并确认移植方向。" : next === 2 ? "第二步：调整图标设置。" : "第三步：任务进度与结果。");
  }
}
function updateControls() {
  const locked = isLocked();
  ui.fields.disabled = !state.catalog || locked;
  const direction = getDirection();
  const valid = state.catalog && state.file && !state.checkingFile && !direction.mismatch && !direction.ambiguous
    && direction.targets.some((target) => target.id === ui.target.value);
  const acknowledged = !state.uncertainUpload || ui.confirmResubmit.checked;
  ui.submit.disabled = locked || !valid || !acknowledged;
  ui.submitLabel.textContent = state.submitting ? "正在上传…" : state.restoring ? "正在恢复任务…"
    : isActive() ? "任务进行中…" : state.uncertainUpload ? "确认后重新提交" : ui.dryRun.checked ? "开始分析" : "开始移植";
  ui.submitHint.textContent = state.submitting ? "上传完成后将自动接收任务编号，请勿关闭页面。"
    : state.restoring ? "先确认上次任务的状态，避免重复创建任务。"
    : isActive() ? "当前任务结束后，可继续创建新任务。"
    : state.checkingFile ? "正在检查所选文件…"
    : !state.catalog ? "连接服务后即可创建任务。"
    : !state.file ? "请先选择主题文件"
    : direction.ambiguous ? "该扩展名对应多个源平台，请明确选择源平台。"
    : !valid ? "请确认源平台与可用的目标平台。"
    : !acknowledged ? "请先确认是否需要重新提交。"
    : ui.dryRun.checked ? "仅返回分析统计与警告，不生成下载文件。" : "生成目标主题包与包名映射报告。";
  ui.uncertain.hidden = !state.uncertainUpload;
  ui.marginInput.disabled = locked || ui.marginOption.hidden || !state.catalog;
  ui.confirmResubmit.disabled = state.submitting;
  ui.deleteJob.disabled = state.deleting || state.submitting || state.restoring || isActive();
  ui.retryJob.disabled = state.pollInFlight || state.deleting || state.submitting;
  $("retry-job-label").textContent = state.pollInFlight ? "正在连接…" : "重试连接";
  ui.forgetJob.disabled = state.pollInFlight || state.submitting;
  ui.confirmAction.disabled = state.deleting;
  ui.cancelAction.disabled = state.deleting;
  ui.navDot.hidden = !state.submitting && !state.restoring && !isActive();
  ui.form.setAttribute("aria-busy", state.submitting ? "true" : "false");
  const readyForSettings = Boolean(state.catalog && state.file && !state.checkingFile) && !locked;
  ui.stepNext.hidden = state.step !== 1;
  ui.stepPrev.hidden = state.step !== 2;
  ui.stepNext.disabled = state.step !== 1 || !readyForSettings;
  ui.stepPrev.disabled = state.step !== 2 || locked;
  ui.stepHint.textContent = state.step === 1
    ? state.file && !state.checkingFile ? "文件已就绪，确认方向后继续。" : "选择源主题，并确认来源与目标平台。"
    : state.step === 2 ? "按需调整图标边距与主题信息，然后开始移植。" : "任务完成后可下载主题包与映射报告。";
}
function uploadJob(formData) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API}/jobs`);
    xhr.responseType = "json";
    // A long upload timeout is distinct from a conversion failure. Never auto-resubmit a POST.
    xhr.timeout = 10 * 60 * 1000;
    xhr.setRequestHeader("Accept", "application/json");
    xhr.setRequestHeader("X-ThemeShift-Request", "1");
    xhr.upload.addEventListener("progress", (event) => {
      state.uploadPercent = event.lengthComputable && event.total > 0 ? Math.min(100, event.loaded / event.total * 100) : null;
      renderUploadProgress();
    });
    xhr.upload.addEventListener("load", () => {
      state.uploadSent = true;
      if (state.uploadPercent !== null) state.uploadPercent = 100;
      renderUploadProgress();
    });
    xhr.addEventListener("load", () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        if (xhr.status !== 202) reject(new RequestError(xhr.status, "invalid_response", "服务未按约定返回 202 任务确认，请先核对服务状态。"));
        else {
          try { resolve(validateJob(xhr.response)); }
          catch (error) { reject(error); }
        }
      } else reject(serverError(xhr.status, xhr.response));
    });
    xhr.addEventListener("error", () => reject(new RequestError(0, "network_error", "上传连接中断，未能确认任务是否已创建。请检查服务连接。")));
    xhr.addEventListener("timeout", () => reject(new RequestError(0, "upload_timeout", "上传等待超时，未能确认任务是否已创建。")));
    xhr.addEventListener("abort", () => reject(new RequestError(0, "upload_aborted", "上传连接已中断，未能确认任务是否已创建。")));
    xhr.send(formData);
  });
}
async function submitJob(event) {
  event.preventDefault();
  updateControls();
  if (ui.submit.disabled || !ui.form.reportValidity()) return;
  const formData = new FormData();
  formData.append("file", state.file, state.file.name);
  formData.append("source", ui.source.value || "auto");
  formData.append("target", ui.target.value);
  formData.append("dry_run", ui.dryRun.checked ? "true" : "false");
  if (!ui.marginOption.hidden) formData.append("icon_margin", ui.marginInput.value);
  for (const name of METADATA_FIELDS) {
    const value = ui.form.elements.namedItem(name).value.trim();
    if (value) formData.append(name, value);
  }
  state.submitting = true;
  state.uploadPercent = null;
  state.uploadSent = false;
  state.confirmation = null;
  message(ui.formError, "");
  setJobNotice("");
  renderTask();
  updateControls();
  ui.taskPanel.focus({ preventScroll: true });
  if (window.matchMedia("(max-width: 959px)").matches) ui.taskPanel.scrollIntoView({ block: "start", behavior: "auto" });
  announce("开始上传源主题。上传完成后将显示服务端转换进度。");
  try {
    const snapshot = await uploadJob(formData);
    state.submitting = false;
    state.uncertainUpload = false;
    ui.confirmResubmit.checked = false;
    state.connectionIssue = false;
    trackJob(snapshot);
  } catch (error) {
    state.submitting = false;
    state.uncertainUpload = error.status === 0 || error.status >= 500 || error.code === "invalid_response";
    ui.confirmResubmit.checked = false;
    message(ui.formError, error.message || "无法提交主题，请检查输入后重试。");
    if (error.status === 0) { state.connectionIssue = true; renderServiceStatus(); }
    renderTask();
    updateControls();
    ui.formError.setAttribute("tabindex", "-1");
    ui.formError.focus({ preventScroll: false });
  }
}

// Keep only the latest opaque job ID. No file data or theme metadata is persisted.
function storageWarning() {
  message($("storage-notice"), "浏览器阻止了本地存储。本次任务仍可正常使用，但刷新页面后可能无法恢复任务，请及时下载结果。");
}
function savedJobId() {
  try {
    const id = window.localStorage.getItem(LAST_JOB_KEY);
    return id && id.length <= 256 ? id : null;
  } catch { storageWarning(); return null; }
}
function saveJobId(id) {
  try { window.localStorage.setItem(LAST_JOB_KEY, id); }
  catch { storageWarning(); }
}
function removeSavedJobId(id) {
  try { if (window.localStorage.getItem(LAST_JOB_KEY) === id) window.localStorage.removeItem(LAST_JOB_KEY); }
  catch { storageWarning(); }
}
function validateJob(snapshot, expectedId) {
  if (!isRecord(snapshot) || !string(snapshot.id) || !JOB_STATUSES.has(snapshot.status)
      || (expectedId && snapshot.id !== expectedId)) {
    throw new RequestError(200, "invalid_response", "服务返回的任务状态无效。未将其视为转换失败，请重试连接或检查服务版本。");
  }
  return snapshot;
}
function stopPolling() {
  window.clearTimeout(state.pollTimer);
  state.pollTimer = null;
  state.pollEpoch += 1;
  state.pollController?.abort();
  state.pollController = null;
  state.pollInFlight = false;
}
function schedulePoll(delay) {
  window.clearTimeout(state.pollTimer);
  state.pollTimer = null;
  if (!state.jobId || state.leaving || document.hidden) return;
  state.pollTimer = window.setTimeout(() => { state.pollTimer = null; void pollJob(); }, delay);
}
function trackJob(snapshot) {
  stopPolling();
  state.job = snapshot;
  state.jobId = snapshot.id;
  state.restoring = false;
  state.pollFailures = 0;
  saveJobId(snapshot.id);
  setJobNotice("");
  renderTask();
  updateControls();
  renderServiceStatus();
  setStep(3);
  if (isActive(snapshot)) schedulePoll(1200);
}

function forgetTrackedJob(text) {
  const previous = state.jobId;
  stopPolling();
  state.job = null;
  state.jobId = null;
  state.restoring = false;
  state.confirmation = null;
  state.pollFailures = 0;
  if (previous) removeSavedJobId(previous);
  setJobNotice(text, "info");
  renderTask();
  updateControls();
  announce(text);
}
async function pollJob() {
  if (!state.jobId || state.pollInFlight || state.deleting || state.leaving) return;
  window.clearTimeout(state.pollTimer);
  state.pollTimer = null;
  const id = state.jobId;
  const epoch = state.pollEpoch;
  const controller = new AbortController();
  state.pollController = controller;
  state.pollInFlight = true;
  updateControls();
  let nextDelay = null;
  try {
    const snapshot = validateJob(await requestJSON(`${API}/jobs/${encodeURIComponent(id)}`, { signal: controller.signal }), id);
    if (epoch !== state.pollEpoch || id !== state.jobId) return;
    state.job = snapshot;
    state.restoring = false;
    state.pollFailures = 0;
    state.connectionIssue = false;
    setJobNotice("");
    if (state.confirmation === "forget") state.confirmation = null;
    renderTask();
    renderServiceStatus();
    if (isActive(snapshot)) nextDelay = 1500;
  } catch (error) {
    if (controller.signal.aborted || epoch !== state.pollEpoch || id !== state.jobId) return;
    if (error.status === 404) {
      forgetTrackedJob("此任务已过期、已清理或服务已重启；浏览器中的任务记录已移除。请重新上传主题，已失效的下载无法恢复。");
      return;
    }
    state.pollFailures += 1;
    if (error.status === 0) state.connectionIssue = true;
    const context = state.job ? "已保留最后一次确认的任务状态，这不代表转换失败。" : "尚未确认上次任务状态，为避免重复提交，暂不创建新任务。";
    setJobNotice(`${error.message} ${context} 将自动重连，也可手动重试。`);
    nextDelay = Math.min(30000, 2000 * 2 ** Math.min(state.pollFailures - 1, 4));
    renderTask();
    renderServiceStatus();
  } finally {
    if (epoch === state.pollEpoch && state.pollController === controller) {
      state.pollController = null;
      state.pollInFlight = false;
      updateControls();
      if (nextDelay !== null) schedulePoll(nextDelay);
    }
  }
}
function restoreLastJob() {
  const id = savedJobId();
  if (!id) return;
  state.jobId = id;
  state.restoring = true;
  setStep(3, { focus: false });
  renderTask();
  updateControls();
  void pollJob();
}

function setProgress(value, stage, note) {
  ui.progressLabel.textContent = stage;
  ui.progressNote.textContent = note;
  if (Number.isFinite(value) && value >= 0 && value <= 100) {
    ui.progress.value = value;
    ui.progressValue.textContent = `${Math.round(value)}%`;
    ui.progress.setAttribute("aria-valuetext", `${stage}，${Math.round(value)}%`);
  } else {
    ui.progress.removeAttribute("value");
    ui.progress.removeAttribute("aria-valuetext");
    ui.progressValue.textContent = "";
  }
}
function setTaskBadge(status, text) {
  ui.taskPanel.dataset.state = status;
  ui.taskBadge.dataset.state = status;
  ui.taskBadge.textContent = text;
}
function renderUploadProgress() {
  if (!state.submitting) return;
  setProgress(state.uploadPercent, state.uploadSent ? "上传完成，等待任务确认" : "正在上传源主题",
    state.uploadSent ? "上传完成不代表转换完成；服务端确认后将显示转换进度。" : "此处为浏览器测得的真实上传进度，不是转换进度。");
}
function renderTask() {
  const job = state.job;
  const special = state.submitting || state.restoring || (!job && state.uncertainUpload);
  const showTask = Boolean(job || special);
  ui.taskEmpty.hidden = showTask;
  ui.taskActive.hidden = !showTask;
  ui.jobFacts.hidden = !job || Boolean(special);
  ui.result.hidden = !job?.result || Boolean(special);
  ui.taskBottom.hidden = !job || isActive(job) || Boolean(special);
  ui.taskConfirm.hidden = !state.confirmation;
  ui.recoveryActions.hidden = !state.notice || !state.jobId;
  ui.forgetJob.hidden = !state.restoring || Boolean(job);
  message(ui.jobError, "");
  if (state.submitting) {
    setTaskBadge("uploading", "上传中");
    ui.jobFilename.textContent = state.file?.name || "源主题";
    ui.jobRoute.textContent = ui.routeSummary.textContent;
    renderUploadProgress();
  } else if (state.restoring) {
    setTaskBadge("restoring", "恢复中");
    ui.jobFilename.textContent = "正在恢复上一次任务";
    ui.jobRoute.textContent = `任务编号：${state.jobId}`;
    setProgress(null, state.notice ? "等待重新连接" : "正在查询任务状态", "只恢复任务编号，不会重复上传主题文件。");
  } else if (job) {
    setTaskBadge(job.status, job.dry_run && job.status === "succeeded" ? "分析完成" : STATUS_LABELS[job.status]);
    ui.jobFilename.textContent = string(job.filename) || "当前主题任务";
    ui.jobRoute.textContent = `${platformName(job.source)} → ${platformName(job.target)}${job.dry_run ? " · 只分析" : " · 图标移植"}`;
    const stage = string(job.stage) || STATUS_LABELS[job.status];
    setProgress(job.progress, stage, isActive(job) ? "转换阶段与进度由服务端返回；切回此页面时会自动刷新。"
      : job.status === "failed" ? "任务已结束。请根据错误调整输入后再提交。" : "任务已结束。请在保留时间内下载需要的文件。");
    if (job.dry_run && job.status === "succeeded") ui.progressNote.textContent = "分析已结束，没有生成主题包或下载文件。";
    ui.jobId.textContent = job.id;
    ui.jobCreated.textContent = formatDate(job.created_at);
    ui.jobExpires.textContent = job.expires_at ? formatDate(job.expires_at) : "以服务端保留策略为准";
    ui.jobExpires.title = string(job.expires_at);
    if (job.status === "failed") {
      const error = isRecord(job.error) ? job.error : {};
      message(ui.jobError, `${string(error.message) || "服务端未能完成任务，请检查源主题是否完整以及平台方向是否受支持。"}${error.code ? `（${string(error.code)}）` : ""}`);
    }
    if (job.result) renderResult(job);
    announce(`${STATUS_LABELS[job.status]}：${stage}${job.dry_run ? "，只分析任务" : ""}`);
  } else if (state.uncertainUpload) {
    setTaskBadge("unconfirmed", "尚未确认");
    ui.jobFilename.textContent = state.file?.name || "源主题";
    ui.jobRoute.textContent = "上传未收到任务编号";
    setProgress(null, "无法确认是否已创建任务", "请先检查连接，不会自动重新提交，以免生成重复任务。");
  } else setTaskBadge("idle", "尚未开始");
  if (state.notice) setJobNotice(state.notice, state.noticeTone);
}
function safeArtifactURL(raw, jobId, kind) {
  if (!string(raw)) return null;
  try {
    const url = new URL(raw, window.location.href);
    const expected = `${API}/jobs/${encodeURIComponent(jobId)}/artifacts/${kind}`;
    if ((url.protocol !== "http:" && url.protocol !== "https:") || url.origin !== window.location.origin
        || url.pathname !== expected || url.username || url.password) return null;
    return raw;
  } catch { return null; }
}
function renderResult(job) {
  const result = job.result;
  if (!isRecord(result)) {
    ui.result.replaceChildren(node("p", "result-info", "服务未返回可读取的结果信息。"));
    return;
  }
  const fragment = document.createDocumentFragment();
  const heading = node("div", "result-heading");
  heading.append(icon(job.dry_run ? "search" : "circle-check"), node("h3", "", job.dry_run ? "分析结果" : "移植结果"));
  fragment.append(heading);
  if (string(result.meta?.title)) fragment.append(node("p", "result-title", result.meta.title));
  if (isRecord(result.stats)) {
    const stats = node("dl", "stats-grid");
    for (const [key, label] of [["mapped", "系统映射"], ["passthrough", "直接保留"], ["skipped", "已跳过"], ["dynamic", "动态映射"]]) {
      const cell = node("div", "stat-item");
      cell.append(node("dt", "", label), node("dd", "", formatCount(result.stats[key])));
      stats.append(cell);
    }
    fragment.append(stats);
  }
  const counts = `静态图标 ${formatCount(result.static_icons)} · 动态资源 ${formatCount(result.dynamic_assets)}`;
  fragment.append(node("p", "result-counts", Number.isSafeInteger(result.icon_margin)
    ? `${counts} · 图标边距 ${result.icon_margin} px` : counts));
  if (isRecord(result.meta)) {
    const entries = [["title", "主题名称"], ["title_en", "英文名称"], ["designer", "作者"], ["version", "版本号"], ["brief_info", "简介"], ["brief_info_en", "英文简介"]]
      .filter(([key]) => string(result.meta[key]));
    if (entries.length) {
      const details = node("details", "result-metadata");
      const summary = node("summary", "", "主题信息");
      const chevron = icon("chevron"); chevron.classList.add("disclosure-icon");
      summary.append(chevron);
      const list = node("dl");
      for (const [key, label] of entries) {
        const row = node("div");
        row.append(node("dt", "", label), node("dd", "", result.meta[key]));
        list.append(row);
      }
      details.append(summary, list);
      fragment.append(details);
    }
  }
  if (isRecord(result.validation)) {
    const valid = result.validation.ok === true;
    const note = node("div", `validation-note${valid ? "" : " is-warning"}`);
    const copy = node("div");
    copy.append(node("strong", "", valid ? "输出自校验通过" : "输出自校验需注意"));
    const summaries = stringList(result.validation.summary);
    if (summaries.length) {
      const list = node("ul");
      summaries.forEach((text) => list.append(node("li", "", text)));
      copy.append(list);
    }
    note.append(icon(valid ? "shield" : "alert"), copy);
    fragment.append(note);
  }
  const warnings = stringList(result.warnings);
  if (warnings.length) {
    const details = node("details", "warning-details");
    details.open = true;
    const summary = node("summary");
    const chevron = icon("chevron"); chevron.classList.add("disclosure-icon");
    summary.append(icon("alert"), node("span", "", `注意事项 · ${warnings.length}`), chevron);
    const list = node("ul");
    warnings.forEach((warning) => list.append(node("li", "", warning)));
    details.append(summary, list);
    fragment.append(details);
  }
  if (job.dry_run) {
    fragment.append(node("p", "result-info", "本次为只分析模式：未生成主题包或映射报告，也不执行输出包自校验。取消“只分析”后可新建移植任务。"));
  } else if (job.status === "succeeded") {
    const artifacts = isRecord(result.artifacts) ? result.artifacts : {};
    const downloads = node("div", "artifact-list");
    let invalidURL = false;
    for (const [kind, label] of [["theme", "下载主题包"], ["report", "下载映射报告"]]) {
      const artifact = artifacts[kind];
      if (!isRecord(artifact)) continue;
      const url = safeArtifactURL(artifact.url, job.id, kind);
      if (!url) { invalidURL = true; continue; }
      const link = node("a", `button artifact-link ${kind === "theme" ? "button-primary" : "button-secondary"}`);
      link.href = url;
      link.download = string(artifact.filename);
      const copy = node("span", "artifact-copy");
      copy.append(node("strong", "", label), node("span", "", `${string(artifact.filename) || "结果文件"} · ${formatBytes(artifact.size)}`));
      link.append(icon(kind === "theme" ? "download" : "file"), copy);
      link.addEventListener("click", () => {
        if (job.expires_at && new Date(job.expires_at).getTime() <= Date.now()) {
          setJobNotice("已超过标注的保留时间，正在刷新任务；下载是否有效以服务端为准。");
          void pollJob();
        }
      });
      downloads.append(link);
    }
    if (downloads.childElementCount) fragment.append(downloads);
    else fragment.append(node("p", "result-info", "服务端没有提供可用的下载文件。请保留任务编号并检查服务端结果。"));
    if (invalidURL) fragment.append(node("p", "field-error", "已阻止不属于当前任务或不同源的下载地址，请检查服务端返回的 artifact URL。"));
  }
  ui.result.replaceChildren(fragment);
}

function showConfirmation(mode) {
  if (state.deleting || state.submitting) return;
  if (mode === "delete" && (!state.job || isActive())) return;
  if (mode === "forget" && (!state.restoring || state.job)) return;
  state.confirmation = mode;
  ui.confirmText.textContent = mode === "delete"
    ? "清理将删除服务端的主题包、报告及任务记录，无法恢复。确定继续吗？"
    : "仅移除浏览器保存的任务编号，不会取消或清理服务端任务。请确认不再恢复此任务，避免重复提交。";
  ui.confirmAction.textContent = mode === "delete" ? "确认清理" : "放弃恢复";
  ui.taskConfirm.hidden = false;
  ui.cancelAction.focus();
}
function hideConfirmation() {
  const mode = state.confirmation;
  state.confirmation = null;
  ui.taskConfirm.hidden = true;
  if (mode === "delete" && !ui.taskBottom.hidden) ui.deleteJob.focus();
  else if (!ui.forgetJob.hidden) ui.forgetJob.focus();
}
async function confirmTaskAction() {
  if (state.confirmation === "forget") {
    forgetTrackedJob("已移除浏览器中的任务编号。这不会取消或清理服务端任务，旧任务仍按服务端保留策略处理。");
    ui.taskPanel.focus();
    return;
  }
  if (state.confirmation !== "delete" || !state.job || isActive() || state.deleting) return;
  const id = state.jobId;
  stopPolling();
  state.deleting = true;
  updateControls();
  ui.confirmAction.textContent = "正在清理…";
  let shouldRefresh = false;
  try {
    await requestJSON(`${API}/jobs/${encodeURIComponent(id)}`, { method: "DELETE" });
    forgetTrackedJob("任务及其服务端文件已清理。可以选择另一份主题开始新任务。");
    ui.taskPanel.focus();
  } catch (error) {
    if (error.status === 404) {
      forgetTrackedJob("任务已过期或已被清理，浏览器中的记录也已移除。");
      ui.taskPanel.focus();
    } else {
      setJobNotice(`${error.message} ${error.status === 409 ? "活动任务不能清理，将重新读取任务状态。" : "未确认清理完成，将重新查询；不会将此任务标为转换失败。"}`);
      state.confirmation = null;
      shouldRefresh = true;
    }
  } finally {
    state.deleting = false;
    renderTask();
    updateControls();
    if (shouldRefresh) void pollJob();
  }
}

// Event bindings. Only one tracked job and one GET poll can be in flight.
ui.stepNext.addEventListener("click", () => {
  if (ui.stepNext.disabled) return;
  message(ui.formError, "");
  setStep(2);
});
ui.stepPrev.addEventListener("click", () => { if (!ui.stepPrev.disabled) setStep(1); });
ui.backToEdit.addEventListener("click", () => setStep(2));
ui.newJob.addEventListener("click", () => { setJobNotice(""); setStep(1); announce("可以开始新的移植任务。"); });
ui.navWorkspace.addEventListener("click", (event) => { event.preventDefault(); setStep(1); });
ui.navTask.addEventListener("click", (event) => { event.preventDefault(); setStep(3); });
ui.openCapabilities.addEventListener("click", () => {
  if (typeof ui.capabilitiesDialog.showModal === "function") ui.capabilitiesDialog.showModal();
  else ui.capabilitiesDialog.setAttribute("open", "");
});
ui.closeCapabilities.addEventListener("click", () => ui.capabilitiesDialog.close());
ui.capabilitiesDialog.addEventListener("click", (event) => {
  if (event.target === ui.capabilitiesDialog) ui.capabilitiesDialog.close();
});
ui.form.addEventListener("submit", submitJob);
ui.chooseFile.addEventListener("click", () => ui.fileInput.click());
$("replace-file").addEventListener("click", () => ui.fileInput.click());
$("remove-file").addEventListener("click", removeFile);
ui.fileInput.addEventListener("change", () => {
  const files = Array.from(ui.fileInput.files || []);
  ui.fileInput.value = "";
  if (files.length) void chooseFiles(files);
});
ui.source.addEventListener("change", () => { message(ui.formError, ""); syncDirection(); });
ui.target.addEventListener("change", () => { message(ui.formError, ""); syncDirection(); });
ui.dryRun.addEventListener("change", updateControls);
ui.marginInput.addEventListener("input", renderMarginValue);
ui.confirmResubmit.addEventListener("change", updateControls);
ui.retryApi.addEventListener("click", () => { void loadServices(); if (state.jobId) void pollJob(); });
ui.retryJob.addEventListener("click", () => void pollJob());
ui.forgetJob.addEventListener("click", () => showConfirmation("forget"));
ui.deleteJob.addEventListener("click", () => showConfirmation("delete"));
ui.cancelAction.addEventListener("click", hideConfirmation);
ui.confirmAction.addEventListener("click", () => void confirmTaskAction());
ui.taskConfirm.addEventListener("keydown", (event) => { if (event.key === "Escape" && !state.deleting) hideConfirmation(); });

function isFileDrag(event) { return Array.from(event.dataTransfer?.types || []).includes("Files"); }
ui.dropTarget.addEventListener("dragover", (event) => {
  if (!isFileDrag(event)) return;
  event.preventDefault();
  if (isLocked() || !state.catalog) { event.dataTransfer.dropEffect = "none"; return; }
  event.dataTransfer.dropEffect = "copy";
  ui.dropTarget.classList.add("is-dragging");
});
ui.dropTarget.addEventListener("dragleave", (event) => {
  if (!ui.dropTarget.contains(event.relatedTarget)) ui.dropTarget.classList.remove("is-dragging");
});
ui.dropTarget.addEventListener("drop", (event) => {
  if (!isFileDrag(event)) return;
  event.preventDefault();
  ui.dropTarget.classList.remove("is-dragging");
  if (isLocked() || !state.catalog) return;
  void chooseFiles(Array.from(event.dataTransfer?.files || []));
});
window.addEventListener("dragover", (event) => { if (isFileDrag(event)) event.preventDefault(); });
window.addEventListener("drop", (event) => { if (isFileDrag(event)) event.preventDefault(); });
document.addEventListener("visibilitychange", () => {
  if (document.hidden) { window.clearTimeout(state.pollTimer); state.pollTimer = null; }
  else if (state.jobId) void pollJob();
});
window.addEventListener("online", () => {
  if (!state.catalog || state.healthError || state.connectionIssue) void loadServices();
  if (state.jobId) void pollJob();
});
window.addEventListener("beforeunload", (event) => {
  if (state.submitting) { event.preventDefault(); event.returnValue = ""; }
});
window.addEventListener("pagehide", () => { state.leaving = true; stopPolling(); });
window.addEventListener("pageshow", (event) => {
  state.leaving = false;
  if (event.persisted && state.jobId) void pollJob();
});

renderTask();
updateControls();
restoreLastJob();
void loadServices();
