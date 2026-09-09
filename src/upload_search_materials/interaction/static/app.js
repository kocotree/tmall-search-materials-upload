(() => {
  "use strict";

  const shell = document.querySelector("[data-component='AppShell']");
  if (!shell) return;
  const UiState = window.InteractionUiState;
  if (!UiState) throw new Error("interaction UI state module is unavailable");

  const registry = JSON.parse(document.querySelector("#stage-registry").textContent);
  const stages = new Map(registry.map((stage) => [stage.id, stage]));
  const railButtons = [...document.querySelectorAll("[data-stage-id]")];
  const panels = [...document.querySelectorAll("[data-stage-panel]")];
  const saveButton = document.querySelector("[data-save-draft]");
  const submitButton = document.querySelector("[data-submit-stage]");
  const setupImageSourceIncomplete = document.querySelector(
    "[data-setup-image-source-incomplete]",
  );
  const recoveryButton = document.querySelector("[data-copy-recovery]");
  const backButton = document.querySelector("[data-go-back]");
  const withdrawButton = document.querySelector("[data-withdraw-submission]");
  const offlinePanel = document.querySelector("[data-offline-panel]");
  const statusBadge = document.querySelector("[data-current-status]");
  const handoffStatus = document.querySelector("[data-handoff-status]");
  const actionMessage = document.querySelector("[data-action-message]");
  const revisionLabel = document.querySelector("[data-revision]");
  const titleLabel = document.querySelector("[data-current-title]");
  const currentStageLabel = document.querySelector("[data-current-stage-label]");
  const lastSubmittedLabel = document.querySelector("[data-last-submitted]");
  const taskDirectoryLabel = document.querySelector("[data-task-directory]");
  const connectionLabel = document.querySelector("[data-connection-label]");
  const taskAwareness = document.querySelector("[data-task-awareness]");
  const taskStatusLabel = document.querySelector("[data-task-status-label]");
  const taskStatusSummary = document.querySelector("[data-task-status-summary]");
  const taskStageProgress = document.querySelector("[data-task-stage-progress]");
  const taskAutoShutdown = document.querySelector("[data-task-auto-shutdown]");
  const endCurrentTaskButton = document.querySelector("[data-end-current-task]");
  const confirmationModal = document.querySelector("[data-confirmation-modal]");
  const confirmationCard = confirmationModal?.querySelector("[data-confirmation-card]");
  const confirmationTitle = confirmationModal?.querySelector("[data-confirmation-title]");
  const confirmationMessage = confirmationModal?.querySelector("[data-confirmation-message]");
  const confirmationAccept = confirmationModal?.querySelector("[data-confirmation-accept]");
  const confirmationCancel = confirmationModal?.querySelector(
    ".confirmation-modal-card [data-confirmation-cancel]",
  );
  const imageSourceConfig = document.querySelector('[data-component="ImageSourceConfig"]');
  const teamIndexConfig = document.querySelector('[data-component="TeamIndexConfig"]');
  const larkBaseConfig = document.querySelector('[data-component="LarkBaseConfig"]');
  const headerLarkUser = document.querySelector("[data-header-lark-user]");
  const headerLarkUserName = document.querySelector("[data-header-lark-user-name]");
  const collectionRuntimeConfig = document.querySelector(
    '[data-component="CollectionRuntimeConfig"]',
  );
  const setupLoginGate = document.querySelector("[data-setup-login-gate]");
  const setupForm = document.querySelector('[data-stage-form="setup"]');
  const handoffActions = document.querySelector(".handoff-actions");
  const goCurrentStageButton = document.createElement("button");
  goCurrentStageButton.type = "button";
  goCurrentStageButton.className = "secondary-button";
  goCurrentStageButton.textContent = "进入当前阶段";
  goCurrentStageButton.hidden = true;
  handoffActions?.prepend(goCurrentStageButton);
  const recoverProcessingButton = document.createElement("button");
  recoverProcessingButton.type = "button";
  recoverProcessingButton.className = "secondary-button";
  recoverProcessingButton.textContent = "恢复过期处理";
  recoverProcessingButton.hidden = true;
  handoffActions?.prepend(recoverProcessingButton);
  const retryGalleryButton = document.createElement("button");
  retryGalleryButton.type = "button";
  retryGalleryButton.className = "secondary-button";
  retryGalleryButton.textContent = "重试加载图片";
  retryGalleryButton.hidden = true;
  handoffActions?.prepend(retryGalleryButton);

  let sessionId = shell.dataset.sessionId || "";
  let currentStageId = railButtons[0]?.dataset.stageId || "setup";
  let sessionCurrentStageId = currentStageId;
  let revision = 0;
  let uiState = UiState.createState(currentStageId);
  let stageGeneration = 0;
  let autoSaveTimer = null;
  let larkAuthPollTimer = null;
  let larkAuthRecheckUntil = 0;
  let larkActivationInFlight = false;
  let approvalAuthorizationInFlight = false;
  let currentApprovalUploadIdentity = null;
  let persistenceInFlight = false;
  let activePersistenceMode = "";
  let endCurrentTaskInFlight = false;
  let confirmationResolver = null;
  let confirmationReturnFocus = null;
  let stageLocalActionInFlight = false;
  let globalAssetSelectionInFlight = false;
  let cropPreflightInFlight = false;
  let pendingBackNavigation = false;
  let pendingPersistenceMode = null;
  let localEditVersion = 0;
  const persistenceRequestIds = new Map();
  let currentProcessingClaim = null;
  let currentCollectionStatus = null;
  let currentHandoffStatus = null;
  let currentWorkflowDispatch = null;
  let currentBackNavigation = null;
  let currentGalleryProgress = null;
  let currentGalleryJob = null;
  let localCopyRequestInFlight = false;
  let slotsCopyActiveSubpage = "";
  let isHydrating = false;
  let currentStageInputLoaded = false;
  let currentStageHasPersistedInput = false;
  let hasSavedSetupConfiguration = false;
  let stageLoadSequence = 0;
  let folderCountsLoading = false;
  let folderCountsLoadedFor = "";
  let setupLoginReady = !setupLoginGate;
  let setupLoginGateStarted = false;
  let imageSourceDiscoveryStarted = false;
  let teamIndexDiscoveryPromise = null;
  let selectedAssetValidation = null;
  const selectionPreflights = new Map();
  const selectionPreflightCardRefreshers = new Map();
  const appliedSelectionPreflightIntents = new Map();
  const productNavigatorCleanups = new WeakMap();
  const activeProductNavigatorCleanups = new Set();
  const selectionPreflightScheduler = UiState.createSelectionPreflightScheduler({
    maxConcurrent: 6,
    maxWeight: 8,
    execute: async (candidate) => {
      const assetId = String(candidate.asset_id || "");
      const entry = await fetchJson(apiPath(
        `/stages/asset_matching/assets/${encodeURIComponent(assetId)}/selection-preflight`,
      ), {
        method: "POST",
        body: JSON.stringify({ product_id: String(candidate.product_id || "") }),
      });
      return entry;
    },
    onChange: (job) => {
      selectionPreflightCardRefreshers.get(job.assetId)?.();
      if (currentStageId === "asset_matching") renderStatus();
    },
  });
  let selectionPreflightHydrationKey = "";
  let selectionPreflightHydrationInFlightFor = "";
  let selectionPreflightHydrationPromise = null;
  let selectionPreflightActiveContextKey = "";
  let selectionPreflightContextGeneration = 0;
  let galleryAutoFocusedFor = "";

  const statusCopy = UiState.statusLabels;
  const stageActions = { draft: "/draft", submit: "/submit" };
  const resultRenderers = {
    "setup_form": [],
    "inspection_matrix": ["InspectionMatrix"],
    "asset_match_gallery": ["AssetMatchGallery"],
    "image_review": ["CropDecision"],
    "slots_copy_editor": ["SlotBoard", "CopyEditor"],
    "dry_run_review": ["DryRunSummary"],
    "approval_table": ["ApprovalChecklist"],
    "production_confirmation": ["ProductionConfirmation"],
    "upload_results": [],
  };

  function apiPath(suffix = "") {
    return `/api/sessions/${encodeURIComponent(sessionId)}${suffix}`;
  }

  function paintBusyState() {
    return new Promise((resolve) => {
      window.requestAnimationFrame(() => {
        window.requestAnimationFrame(resolve);
      });
    });
  }

  function beginPersistenceUi(mode) {
    persistenceInFlight = true;
    activePersistenceMode = mode;
    saveButton.disabled = true;
    if (mode === "submit") {
      submitButton.disabled = true;
      submitButton.setAttribute("aria-busy", "true");
      submitButton.textContent = "正在检查并提交…";
    } else {
      saveButton.setAttribute("aria-busy", "true");
    }
    renderStatus();
  }

  function releasePersistenceUi() {
    persistenceInFlight = false;
    activePersistenceMode = "";
    submitButton.removeAttribute("aria-busy");
    saveButton.removeAttribute("aria-busy");
    renderStatus();
  }

  function closeConfirmation(confirmed) {
    if (!confirmationModal || confirmationModal.hidden) return;
    confirmationModal.hidden = true;
    document.body.classList.remove("confirmation-modal-open");
    const resolve = confirmationResolver;
    const returnFocus = confirmationReturnFocus;
    confirmationResolver = null;
    confirmationReturnFocus = null;
    if (returnFocus instanceof HTMLElement && returnFocus.isConnected) {
      returnFocus.focus({ preventScroll: true });
    }
    resolve?.(Boolean(confirmed));
  }

  function confirmAction({
    title = "确认操作",
    message = "确定继续吗？",
    confirmLabel = "确认",
    danger = false,
  } = {}) {
    if (
      !confirmationModal
      || !confirmationCard
      || !confirmationTitle
      || !confirmationMessage
      || !confirmationAccept
      || !confirmationCancel
    ) return Promise.resolve(false);
    if (!confirmationModal.hidden) closeConfirmation(false);
    confirmationReturnFocus = document.activeElement;
    confirmationTitle.textContent = title;
    confirmationMessage.textContent = message;
    confirmationAccept.textContent = confirmLabel;
    confirmationCard.dataset.danger = danger ? "true" : "false";
    confirmationModal.hidden = false;
    document.body.classList.add("confirmation-modal-open");
    window.requestAnimationFrame(() => confirmationCancel.focus());
    return new Promise((resolve) => {
      confirmationResolver = resolve;
    });
  }

  confirmationModal?.addEventListener("click", (event) => {
    if (event.target.closest("[data-confirmation-accept]")) {
      closeConfirmation(true);
      return;
    }
    if (event.target.closest("[data-confirmation-cancel]")) {
      closeConfirmation(false);
    }
  });
  document.addEventListener("keydown", (event) => {
    if (!confirmationModal || confirmationModal.hidden) return;
    if (event.key === "Escape") {
      event.preventDefault();
      closeConfirmation(false);
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = [confirmationCancel, confirmationAccept].filter(Boolean);
    if (!focusable.length) return;
    const currentIndex = focusable.indexOf(document.activeElement);
    const nextIndex = event.shiftKey
      ? (currentIndex <= 0 ? focusable.length - 1 : currentIndex - 1)
      : (currentIndex >= focusable.length - 1 ? 0 : currentIndex + 1);
    event.preventDefault();
    focusable[nextIndex].focus();
  });

  function formatBytes(value) {
    if (value == null || value === "") return "大小未知";
    const bytes = Number(value);
    if (!Number.isFinite(bytes)) return "读取失败";
    if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)}KB`;
    return `${(bytes / (1024 * 1024)).toFixed(2)}MB`;
  }

  async function fetchJson(path, options = {}) {
    const response = await fetch(path, {
      cache: "no-store",
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const payload = await response.json().catch(() => ({ error: "响应格式无效" }));
    if (!response.ok) {
      const error = new Error(payload.error || `请求失败（${response.status}）`);
      error.fieldErrors = payload.field_errors || {};
      error.reasonCode = payload.reason_code || "";
      error.userMessage = payload.message || "";
      error.allowedActions = payload.allowed_actions || [];
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  function imageSourceRows() {
    return imageSourceConfig
      ? [...imageSourceConfig.querySelectorAll("[data-image-source-row]")]
      : [];
  }

  function incompleteImageSourceRows() {
    return imageSourceRows().filter((row) =>
      !row.querySelector('[name="image_source_labels"]')?.value.trim()
      || !row.querySelector('[name="image_roots"]')?.value.trim()
    );
  }

  function updateImageSourceIncompleteHint() {
    if (!setupImageSourceIncomplete) return;
    const count = currentStageId === "setup"
      ? incompleteImageSourceRows().length
      : 0;
    setupImageSourceIncomplete.hidden = count === 0;
    setupImageSourceIncomplete.textContent = count
      ? `还有 ${count} 个图片源未填写完整`
      : "";
  }

  function focusFirstIncompleteImageSource() {
    const row = incompleteImageSourceRows()[0];
    if (!row) return false;
    const labelInput = row.querySelector('[name="image_source_labels"]');
    const pathInput = row.querySelector('[name="image_roots"]');
    const target = !labelInput?.value.trim() ? labelInput : pathInput;
    row.scrollIntoView({ behavior: "smooth", block: "center" });
    target?.setAttribute("aria-invalid", "true");
    target?.focus({ preventScroll: true });
    return true;
  }

  function updateImageSourceConfig() {
    if (!imageSourceConfig) return;
    const rows = imageSourceRows();
    const validCount = rows.filter((row) =>
      row.querySelector('[name="image_source_labels"]')?.value.trim()
      && row.querySelector('[name="image_roots"]')?.value.trim()
    ).length;
    imageSourceConfig.querySelector("[data-image-source-summary]").textContent =
      `${rows.length} 个图片源 · 本次尚未提交`;
    imageSourceConfig.dataset.ready = validCount === rows.length && rows.length > 0 ? "true" : "false";
    rows.forEach((row) => {
      row.querySelector("[data-remove-image-source]").disabled = rows.length <= 1;
    });
    updateImageSourceIncompleteHint();
  }

  function appendImageSource(
    label = "",
    path = "",
    sourceId = "",
    canonicalUnc = "",
  ) {
    if (!imageSourceConfig) return null;
    const template = imageSourceConfig.querySelector("[data-image-source-template]");
    const row = template.content.firstElementChild.cloneNode(true);
    row.querySelector('[name="image_source_labels"]').value = label;
    row.querySelector('[name="image_roots"]').value = path;
    row.dataset.sourceId = sourceId;
    row.dataset.canonicalUnc = canonicalUnc;
    imageSourceConfig.querySelector("[data-image-source-list]").appendChild(row);
    updateImageSourceConfig();
    return row;
  }

  function renderImageSourceCandidates(row, candidates) {
    const picker = row.querySelector("[data-image-source-candidate-picker]");
    const select = row.querySelector("[data-image-source-candidate-select]");
    if (!picker || !select) return;
    const values = Array.isArray(candidates)
      ? candidates.filter((candidate) => candidate?.path)
      : [];
    select.replaceChildren(new Option("请选择", ""));
    values.forEach((candidate) => {
      const option = new Option(candidate.path, candidate.path);
      option.dataset.canonicalUnc = candidate.canonical_unc || "";
      select.appendChild(option);
    });
    const show = values.length > 1;
    picker.hidden = !show;
    row.dataset.hasCandidates = show ? "true" : "false";
    if (show) {
      const currentPath = row.querySelector('[name="image_roots"]')?.value || "";
      select.value = values.some((candidate) => candidate.path === currentPath)
        ? currentPath
        : "";
    }
  }

  function applyImageSourceDiscovery(sources) {
    if (!Array.isArray(sources) || !sources.length) return;
    const list = imageSourceConfig.querySelector("[data-image-source-list]");
    list.replaceChildren();
    sources.forEach((source) => {
      const row = appendImageSource(
        source.label || "图片源",
        source.path || "",
        source.source_id || "",
        source.canonical_unc || "",
      );
      renderImageSourceCandidates(row, source.candidates);
      const state = row.querySelector("[data-image-source-state]");
      const candidates = Array.isArray(source.candidates) ? source.candidates : [];
      state.textContent = candidates.length > 1
        ? "找到多个文件夹位置，请选择本次使用的位置。"
        : source.path
          ? "已自动找到，提交时会再次检测。"
          : "未自动找到，请选择文件夹。";
    });
    updateImageSourceConfig();
  }

  async function discoverImageSources() {
    if (!imageSourceConfig || imageSourceDiscoveryStarted) return;
    imageSourceDiscoveryStarted = true;
    const feedback = imageSourceConfig.querySelector("[data-image-source-feedback]");
    feedback.textContent = "正在查找可用图片源…";
    try {
      const payload = await fetchJson("/api/runtime/image-sources/discover");
      if (payload.auto_fill) applyImageSourceDiscovery(payload.image_sources);
      feedback.textContent = payload.message || "图片源准备完成。";
    } catch (error) {
      feedback.textContent = error.userMessage
        || "暂未自动找到图片源，可以手动选择文件夹。";
    }
  }

  function configuredImageSources() {
    const rows = imageSourceRows();
    const rawSources = rows.map((row) => {
      const source = {
        label: row.querySelector('[name="image_source_labels"]').value.trim(),
        path: row.querySelector('[name="image_roots"]').value.trim(),
      };
      if (row.dataset.sourceId) source.source_id = row.dataset.sourceId;
      if (row.dataset.canonicalUnc) {
        source.canonical_unc = row.dataset.canonicalUnc;
      }
      if (row.dataset.lastVerifiedSid) {
        source.last_verified_sid = row.dataset.lastVerifiedSid;
      }
      if (row.dataset.lastVerifiedAt) {
        source.last_verified_at = row.dataset.lastVerifiedAt;
      }
      if (row.dataset.lastStatus) {
        source.last_status = row.dataset.lastStatus;
      }
      return source;
    });
    const sources = UiState.disambiguateImageSourceLabels(rawSources);
    let renamedCount = 0;
    sources.forEach((source, index) => {
      const input = rows[index].querySelector('[name="image_source_labels"]');
      if (input.value.trim() === source.label) return;
      input.value = source.label;
      renamedCount += 1;
    });
    if (renamedCount) {
      imageSourceConfig.dataset.autoRenamedCount = String(renamedCount);
    }
    if (!sources.length || sources.some((source) => !source.label || !source.path)) {
      throw new Error("每个图片源都必须填写来源名称并选择图片文件夹");
    }
    return sources;
  }

  function hydrateImageSources(labels, paths) {
    if (!imageSourceConfig || !Array.isArray(paths) || !paths.length) return;
    const safeLabels = Array.isArray(labels) ? labels : [];
    const configuredSources = new Map(
      imageSourceRows().map((row) => [
        row.querySelector('[name="image_roots"]').value.trim(),
        {
          label: row.querySelector('[name="image_source_labels"]').value.trim(),
          sourceId: row.dataset.sourceId || "",
          canonicalUnc: row.dataset.canonicalUnc || "",
        },
      ]),
    );
    const list = imageSourceConfig.querySelector("[data-image-source-list]");
    list.replaceChildren();
    paths.forEach((path, index) => {
      const textPath = String(path || "");
      const configured = configuredSources.get(textPath) || {};
      appendImageSource(
        safeLabels[index] || configured.label || `图片源 ${index + 1}`,
        textPath,
        configured.sourceId || "",
        configured.canonicalUnc || "",
      );
    });
    updateImageSourceConfig();
  }

  async function checkImageSources() {
    const feedback = imageSourceConfig.querySelector("[data-image-source-feedback]");
    feedback.textContent = "正在检测图片源路径…";
    try {
      const payload = await fetchJson("/api/runtime/image-sources/check", {
        method: "POST",
        body: JSON.stringify({ image_sources: configuredImageSources() }),
      });
      imageSourceRows().forEach((row, index) => {
        const diagnostic = payload.image_sources[index] || {};
        const status = diagnostic.status || "unavailable";
        if (diagnostic.source_id) {
          row.dataset.sourceId = diagnostic.source_id;
        }
        const node = row.querySelector("[data-image-source-state]");
        node.dataset.status = status;
        node.dataset.reasonCode = diagnostic.reason_code || "";
        node.textContent = diagnostic.message
          || (status === "available" ? "路径可访问" : "当前不可访问");
        node.title = [
          diagnostic.reason_code,
          diagnostic.checked_at,
        ].filter(Boolean).join(" · ");
        const portable = row.querySelector("[data-use-portable-path]");
        const suggestion = diagnostic.portable_path_suggestion || "";
        if (suggestion) row.dataset.canonicalUnc = suggestion;
        row.dataset.lastVerifiedSid = diagnostic.last_verified_sid || "";
        row.dataset.lastVerifiedAt = diagnostic.last_verified_at || "";
        row.dataset.lastStatus = diagnostic.last_status || "";
        portable.hidden = !suggestion;
        portable.dataset.path = suggestion;
        portable.title = suggestion ? "使用系统推荐的跨电脑路径" : "";
      });
      const available = payload.image_sources.filter((source) => source.status === "available").length;
      const renamedCount = Number(imageSourceConfig.dataset.autoRenamedCount || 0);
      const renamed = renamedCount
        ? `已根据业务目录自动区分 ${renamedCount} 个重复来源名称；`
        : "";
      feedback.textContent = `${renamed}检测完成：${available} / ${payload.image_sources.length} 个路径可访问。`;
      imageSourceConfig.dataset.autoRenamedCount = "0";
      return available === payload.image_sources.length;
    } catch (error) {
      feedback.textContent = error.message;
      return false;
    }
  }

  async function saveImageSources() {
    const feedback = imageSourceConfig.querySelector("[data-image-source-feedback]");
    feedback.textContent = "正在保存常用图片源…";
    try {
      const payload = await fetchJson("/api/runtime/image-sources", {
        method: "PUT",
        body: JSON.stringify({ image_sources: configuredImageSources() }),
      });
      imageSourceRows().forEach((row, index) => {
        const source = payload.image_sources[index] || {};
        row.dataset.sourceId = source.source_id || "";
        row.dataset.canonicalUnc = source.canonical_unc || "";
      });
      const renamedCount = Number(imageSourceConfig.dataset.autoRenamedCount || 0);
      const renamed = renamedCount
        ? `已根据业务目录自动区分 ${renamedCount} 个重复来源名称；`
        : "";
      feedback.textContent = `${renamed}已保存 ${payload.image_sources.length} 个常用图片源。`;
      imageSourceConfig.dataset.autoRenamedCount = "0";
      updateImageSourceConfig();
    } catch (error) {
      feedback.textContent = error.message;
    }
  }

  async function pickImageSource(row) {
    const pathInput = row.querySelector('[name="image_roots"]');
    const state = row.querySelector("[data-image-source-state]");
    state.textContent = "正在打开选择窗口…";
    try {
      const payload = await fetchJson("/api/runtime/folder-picker", {
        method: "POST",
        body: JSON.stringify({ initial_path: pathInput.value.trim() }),
      });
      if (!payload.cancelled) {
        pathInput.value = payload.path;
        pathInput.dispatchEvent(new CustomEvent(
          "input",
          { bubbles: true, detail: { source: "explicit-user-edit" } },
        ));
        state.textContent = "已选择，等待检测";
      } else {
        state.textContent = "已取消选择";
      }
    } catch (error) {
      state.dataset.status = "unavailable";
      state.dataset.reasonCode = error.reasonCode || "";
      state.textContent = error.userMessage
        || error.message
        || "选择窗口不可用";
      imageSourceConfig.querySelector("[data-image-source-feedback]").textContent =
        error.userMessage || error.message;
      pathInput.focus();
    }
  }

  function initializeImageSourceConfig() {
    if (!imageSourceConfig) return;
    imageSourceConfig.querySelector("[data-add-image-source]").addEventListener("click", () => {
      const next = imageSourceRows().length + 1;
      appendImageSource(`图片源 ${next}`, "")?.querySelector('[name="image_roots"]')?.focus();
      uiState = UiState.markDirty(uiState);
      renderStatus();
      scheduleAutoSave();
    });
    imageSourceConfig.querySelector("[data-image-source-list]").addEventListener("click", async (event) => {
      const picker = event.target.closest("[data-pick-image-source]");
      if (picker) {
        pickImageSource(picker.closest("[data-image-source-row]"));
        return;
      }
      const portable = event.target.closest("[data-use-portable-path]");
      if (portable && portable.dataset.path) {
        const row = portable.closest("[data-image-source-row]");
        const input = row.querySelector('[name="image_roots"]');
        input.value = portable.dataset.path;
        input.dispatchEvent(new CustomEvent(
          "input",
          { bubbles: true, detail: { source: "explicit-user-edit" } },
        ));
        row.querySelector("[data-image-source-state]").textContent =
          "已使用推荐路径，等待检测";
        portable.hidden = true;
        return;
      }
      const button = event.target.closest("[data-remove-image-source]");
      if (!button || imageSourceRows().length <= 1) return;
      const row = button.closest("[data-image-source-row]");
      const label = row.querySelector('[name="image_source_labels"]')?.value.trim()
        || "这个图片源";
      const confirmed = await confirmAction({
        title: "删除图片源",
        message: `确定从本次配置中删除“${label}”吗？\n\n只会移除当前配置，不会删除共享盘中的文件。`,
        confirmLabel: "确认删除",
        danger: true,
      });
      if (!confirmed) return;
      row.remove();
      updateImageSourceConfig();
      uiState = UiState.markDirty(uiState);
      renderStatus();
      scheduleAutoSave();
    });
    imageSourceConfig.querySelector("[data-image-source-list]").addEventListener("change", (event) => {
      const select = event.target.closest("[data-image-source-candidate-select]");
      if (!select || !select.value) return;
      const row = select.closest("[data-image-source-row]");
      const pathInput = row.querySelector('[name="image_roots"]');
      pathInput.value = select.value;
      row.dataset.canonicalUnc = select.selectedOptions[0]?.dataset.canonicalUnc || "";
      row.querySelector("[data-image-source-state]").textContent =
        "已选择，提交时会再次检测。";
      pathInput.dispatchEvent(new CustomEvent(
        "input",
        { bubbles: true, detail: { source: "explicit-user-edit" } },
      ));
    });
    imageSourceConfig.addEventListener("input", (event) => {
      if (event.target.matches('[name="image_source_labels"], [name="image_roots"]')) {
        event.target.closest("[data-image-source-row]")
          ?.querySelector("[data-image-source-state]")
          ?.removeAttribute("data-status");
        updateImageSourceConfig();
      }
    });
    imageSourceConfig.querySelector("[data-check-image-sources]").addEventListener("click", checkImageSources);
    imageSourceConfig.querySelector("[data-save-image-sources]").addEventListener("click", saveImageSources);
    updateImageSourceConfig();
  }

  function renderTeamIndexStatus(payload) {
    if (!teamIndexConfig) return;
    const available = payload?.status === "available";
    const count = Number(payload?.snapshot_source_count || 0);
    teamIndexConfig.dataset.ready = available ? "true" : "false";
    teamIndexConfig.querySelector("[data-team-index-summary]").textContent = available
      ? count
        ? `可使用 · ${count} 个索引来源`
        : "可访问 · 暂无索引来源"
      : "需要选择";
    teamIndexConfig.querySelector("[data-team-index-feedback]").textContent =
      payload?.message || (available ? "索引文件夹可访问。" : "请选择团队索引文件夹。");
  }

  async function checkTeamIndex() {
    if (!teamIndexConfig) return true;
    const input = teamIndexConfig.querySelector('[name="team_folder_index_root"]');
    const feedback = teamIndexConfig.querySelector("[data-team-index-feedback]");
    feedback.textContent = "正在检测团队索引文件夹…";
    try {
      const payload = await fetchJson("/api/runtime/team-folder-index/check", {
        method: "POST",
        body: JSON.stringify({ path: input.value.trim() }),
      });
      renderTeamIndexStatus(payload);
      return payload.status === "available";
    } catch (error) {
      renderTeamIndexStatus({ status: "unavailable", message: error.userMessage || error.message });
      return false;
    }
  }

  async function pickTeamIndex() {
    const input = teamIndexConfig.querySelector('[name="team_folder_index_root"]');
    const feedback = teamIndexConfig.querySelector("[data-team-index-feedback]");
    feedback.textContent = "正在打开选择窗口…";
    try {
      const payload = await fetchJson("/api/runtime/folder-picker", {
        method: "POST",
        body: JSON.stringify({ initial_path: input.value.trim() }),
      });
      if (payload.cancelled) {
        feedback.textContent = "已取消选择。";
        return;
      }
      input.value = payload.path;
      input.dispatchEvent(new CustomEvent(
        "input",
        { bubbles: true, detail: { source: "explicit-user-edit" } },
      ));
      await checkTeamIndex();
    } catch (error) {
      renderTeamIndexStatus({ status: "unavailable", message: error.userMessage || error.message });
      input.focus();
    }
  }

  async function saveTeamIndex() {
    const input = teamIndexConfig.querySelector('[name="team_folder_index_root"]');
    const feedback = teamIndexConfig.querySelector("[data-team-index-feedback]");
    feedback.textContent = "正在保存团队索引文件夹…";
    try {
      const payload = await fetchJson("/api/runtime/team-folder-index", {
        method: "PUT",
        body: JSON.stringify({ path: input.value.trim(), session_id: sessionId }),
      });
      input.value = payload.path || input.value;
      renderTeamIndexStatus(payload);
      feedback.textContent = payload.retry_enqueued
        ? "已保存，系统正在自动继续匹配候选文件夹。"
        : "已保存为这台电脑的团队索引文件夹。";
    } catch (error) {
      renderTeamIndexStatus({
        status: "unavailable",
        message: error.fieldErrors?.team_folder_index_root
          || error.userMessage
          || error.message,
      });
    }
  }

  async function discoverTeamIndex() {
    if (!teamIndexConfig) return;
    if (teamIndexDiscoveryPromise) return teamIndexDiscoveryPromise;
    const discoveryPromise = (async () => {
      const input = teamIndexConfig.querySelector('[name="team_folder_index_root"]');
      const feedback = teamIndexConfig.querySelector("[data-team-index-feedback]");
      const initialPath = input.value.trim();
      feedback.textContent = "正在查找本机映射盘中的团队索引文件夹…";
      try {
        const payload = await fetchJson("/api/runtime/team-folder-index/discover");
        if (
          payload.auto_fill
          && payload.path
          && input.value.trim() === initialPath
        ) {
          input.value = payload.path;
          teamIndexConfig.dataset.ready = "false";
          teamIndexConfig.querySelector("[data-team-index-summary]").textContent = "已自动找到";
        }
        if (payload.message) feedback.textContent = payload.message;
        return payload;
      } catch (error) {
        feedback.textContent = error.userMessage
          || "暂未自动找到团队索引文件夹，可以手动选择。";
        return null;
      } finally {
        if (teamIndexDiscoveryPromise === discoveryPromise) {
          teamIndexDiscoveryPromise = null;
        }
      }
    })();
    teamIndexDiscoveryPromise = discoveryPromise;
    return discoveryPromise;
  }

  function initializeTeamIndexConfig() {
    if (!teamIndexConfig) return;
    const input = teamIndexConfig.querySelector('[name="team_folder_index_root"]');
    teamIndexConfig.querySelector("[data-pick-team-index]").addEventListener("click", pickTeamIndex);
    teamIndexConfig.querySelector("[data-check-team-index]").addEventListener("click", checkTeamIndex);
    teamIndexConfig.querySelector("[data-save-team-index]").addEventListener("click", saveTeamIndex);
    input.addEventListener("input", () => {
      teamIndexConfig.dataset.ready = "false";
      teamIndexConfig.querySelector("[data-team-index-summary]").textContent = "等待检测";
      teamIndexConfig.querySelector("[data-team-index-feedback]").textContent =
        "路径已修改，请检测或保存。";
    });
  }

  function larkBasePayload() {
    if (!larkBaseConfig) return {};
    return {
      enabled: true,
      product_base_url: larkBaseConfig.dataset.larkProductBaseUrl || "",
      product_table_id: larkBaseConfig.dataset.larkProductTableId || "",
      upload_log_base_url: larkBaseConfig.dataset.larkUploadLogBaseUrl || "",
      upload_log_table_id: larkBaseConfig.dataset.larkUploadLogTableId || "",
    };
  }

  function larkBaseReadinessFeedback(product, uploadLog) {
    const productReady = product.status === "available";
    const uploadLogReady = uploadLog.status === "available";
    const productNeedsPermission = product.reason_code === "LARK_PERMISSION_REQUIRED";
    const uploadLogNeedsPermission = uploadLog.reason_code === "LARK_PERMISSION_REQUIRED";
    const productRecordsUnavailable =
      product.reason_code === "LARK_PRODUCT_TABLE_EMPTY_OR_INVISIBLE";
    const uploadHistoryFieldMissing =
      uploadLog.reason_code === "LARK_UPLOAD_LOG_SCHEMA_INCOMPLETE";
    if (productRecordsUnavailable) {
      return "商品信息表可以打开，但当前飞书账号没有读取到任何商品记录。请确认该账号能查看默认商品表后重新检测。";
    }
    if (productNeedsPermission && uploadLogReady) {
      return "上传记录表已可用；商品信息表缺少飞书 Wiki 读取权限。请点击“补充授权”，在飞书页面完成一次授权。";
    }
    if (uploadLogNeedsPermission && productReady) {
      return "商品信息表已可用；上传记录表缺少飞书多维表格权限。请点击“补充授权”，在飞书页面完成一次授权。";
    }
    if (productNeedsPermission || uploadLogNeedsPermission) {
      return "默认数据表缺少必要的飞书读取或写入权限。请点击“补充授权”，在飞书页面完成一次授权。";
    }
    if (uploadHistoryFieldMissing) {
      return "上传记录表缺少“原图 SHA-256”字段，暂时不能排除历史重复素材。";
    }
    if (!productReady && uploadLogReady) {
      return "上传记录表已可用；商品负责人同步暂时不可用。请稍后重新检测。";
    }
    if (productReady && !uploadLogReady) {
      return "商品负责人同步已可用；上传记录表暂时不可用。请稍后重新检测。";
    }
    return "默认数据表暂时无法访问，请稍后重新检测；本地任务流程仍可继续。";
  }

  function renderLarkBaseStatus(payload) {
    if (!larkBaseConfig) return;
    const summary = larkBaseConfig.querySelector("[data-lark-base-summary]");
    const feedback = larkBaseConfig.querySelector("[data-lark-base-feedback]");
    const status = payload?.status || "not_configured";
    const product = payload?.product_sync || {};
    const uploadLog = payload?.upload_log || {};
    const readyCount = [product, uploadLog].filter(
      (item) => item.status === "available",
    ).length;
    const ready = status === "available" && readyCount === 2;
    const permissionRequired = [product, uploadLog].some(
      (item) => item.reason_code === "LARK_PERMISSION_REQUIRED",
    );
    larkBaseConfig.dataset.ready = ready ? "true" : "pending";
    summary.textContent = ready
      ? "已授权 · 可用"
      : permissionRequired
        ? "已授权 · 需补充权限"
        : "已授权 · 待完成";
    feedback.textContent = ready
      ? "飞书已可用：负责人同步和成功上传记录均已启用。"
      : larkBaseReadinessFeedback(product, uploadLog);
    const authorizeButton = larkBaseConfig.querySelector("[data-authorize-lark-base]");
    if (authorizeButton) {
      authorizeButton.textContent = ready
        ? "重新检查授权"
        : permissionRequired
          ? "补充授权"
          : "重新检测";
    }
    return ready;
  }

  async function checkLarkBase() {
    if (!larkBaseConfig) return true;
    const feedback = larkBaseConfig.querySelector("[data-lark-base-feedback]");
    feedback.textContent = "正在检测飞书多维表格…";
    try {
      const payload = await fetchJson("/api/runtime/lark-base/check", {
        method: "POST",
        body: JSON.stringify({ lark_base: larkBasePayload() }),
      });
      return renderLarkBaseStatus(payload);
    } catch (error) {
      larkBaseConfig.dataset.ready = "pending";
      larkBaseConfig.querySelector("[data-lark-base-summary]").textContent = "检查未完成";
      feedback.textContent = "暂时无法检查默认数据表，请稍后重试。";
      return false;
    }
  }

  async function saveLarkBase({ quiet = false } = {}) {
    if (!larkBaseConfig) return false;
    const feedback = larkBaseConfig.querySelector("[data-lark-base-feedback]");
    if (!quiet) feedback.textContent = "正在启用团队默认数据表…";
    try {
      const payload = await fetchJson("/api/runtime/lark-base", {
        method: "PUT",
        body: JSON.stringify({ lark_base: larkBasePayload() }),
      });
      larkBaseConfig.dataset.ready = payload.product_sync_configured || payload.upload_log_configured
        ? "true"
        : "pending";
      if (!quiet) feedback.textContent = "团队默认数据表已启用。";
      return Boolean(payload.enabled);
    } catch (_error) {
      larkBaseConfig.dataset.ready = "pending";
      larkBaseConfig.querySelector("[data-lark-base-summary]").textContent = "保存未完成";
      feedback.textContent = "授权已完成，但本机配置暂时无法保存，请稍后重试。";
      return false;
    }
  }

  function renderHeaderLarkStatus(payload) {
    const status = payload?.status || "authorization_required";
    const awaiting = status === "awaiting_user";
    const authorized = status === "authorized";
    const userName = String(payload?.user_name || "").trim();
    if (!headerLarkUser || !headerLarkUserName) return;
    headerLarkUser.dataset.status = authorized
      ? userName ? "authorized" : "checking"
      : awaiting ? "awaiting" : "required";
    headerLarkUserName.textContent = authorized
      ? userName || "已授权"
      : awaiting ? "等待授权" : "未授权";
    headerLarkUser.title = authorized && userName
      ? `当前飞书账号：${userName}`
      : String(payload?.message || "可在任务配置中完成飞书授权");
  }

  function applyApprovalUploadIdentity(payload, { rerender = false } = {}) {
    const next = payload && typeof payload === "object" ? payload : null;
    const changed = !UiState.jsonSemanticallyEqual(
      currentApprovalUploadIdentity,
      next,
    );
    currentApprovalUploadIdentity = next;
    renderHeaderLarkStatus(next);
    if (
      rerender
      && changed
      && currentStageId === "approval"
      && uiState.result
    ) {
      renderUploadTaskConfirmation(UiState.resultView(uiState));
      renderStatus();
    }
  }

  async function ensureApprovalUploadIdentityForSubmit() {
    if (approvalAuthorizationInFlight) {
      actionMessage.textContent = "正在确认飞书授权，请勿重复点击。";
      return false;
    }
    approvalAuthorizationInFlight = true;
    submitButton.disabled = true;
    submitButton.setAttribute("aria-busy", "true");
    submitButton.textContent = "正在确认飞书授权…";
    actionMessage.textContent = "正在确认当前飞书账号…";
    try {
      const payload = await authorizeLarkBase({ activateDefaults: false });
      if (
        payload?.status === "authorized"
        && String(payload?.user_name || "").trim()
      ) {
        applyApprovalUploadIdentity(payload, { rerender: true });
        return true;
      }
      actionMessage.textContent = payload?.status === "awaiting_user"
        ? "请在新打开的飞书页面完成授权；授权完成后，请再次点击上传。"
        : String(payload?.message || "暂时无法确认飞书账号，请稍后重试。");
      return false;
    } finally {
      approvalAuthorizationInFlight = false;
      submitButton.removeAttribute("aria-busy");
      renderStatus();
    }
  }

  function renderLarkAuthStatus(payload) {
    renderHeaderLarkStatus(payload);
    if (!larkBaseConfig) return;
    const status = payload?.status || "authorization_required";
    const summary = larkBaseConfig.querySelector("[data-lark-base-summary]");
    const feedback = larkBaseConfig.querySelector("[data-lark-base-feedback]");
    const button = larkBaseConfig.querySelector("[data-authorize-lark-base]");
    const refreshButton = larkBaseConfig.querySelector("[data-refresh-lark-owner-snapshot]");
    const link = larkBaseConfig.querySelector("[data-lark-auth-link]");
    const awaiting = status === "awaiting_user";
    const authorized = status === "authorized";
    const userName = String(payload?.user_name || "").trim();
    larkBaseConfig.dataset.ready = authorized ? "pending" : awaiting ? "pending" : "false";
    summary.textContent = authorized
      ? "已授权 · 正在检查"
      : awaiting
        ? "等待飞书授权"
        : status === "failed"
          ? "授权未完成"
          : "等待授权";
    feedback.textContent = payload?.message || "请在工作台完成飞书授权。";
    if (button) {
      button.disabled = awaiting;
      button.textContent = authorized
        ? "重新检查授权"
        : status === "failed"
          ? "重新授权飞书"
          : awaiting
            ? "等待授权完成"
            : "授权飞书";
    }
    if (refreshButton) refreshButton.disabled = !authorized;
    if (link) {
      const url = payload?.verification_url || "";
      link.hidden = !awaiting || !url;
      if (url) link.href = url;
      else link.removeAttribute("href");
    }
  }

  async function activateLarkBase() {
    if (!larkBaseConfig || larkActivationInFlight) return;
    larkActivationInFlight = true;
    try {
      const saved = await saveLarkBase({ quiet: true });
      if (!saved) return;
      await checkLarkBase();
      await refreshLarkProductOwnerSnapshot();
    } finally {
      larkActivationInFlight = false;
    }
  }

  function larkSnapshotTime(value) {
    if (!value) return "";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString("zh-CN");
  }

  async function applyLocalOwnerSnapshotToCompleteness() {
    if (!sessionId) return;
    const feedback = larkBaseConfig?.querySelector("[data-lark-base-feedback]");
    try {
      const payload = await fetchJson(apiPath(
        "/stages/completeness/refresh-lark-owners",
      ), {
        method: "POST",
        body: JSON.stringify({}),
      });
      if (payload.status === "refreshed") {
        const message = payload.message || "已按本机负责人数据刷新当前巡检。";
        if (feedback) feedback.textContent = message;
        actionMessage.textContent = message;
        if (currentStageId === "completeness") await loadStage();
      } else if (payload.status === "unavailable" && feedback) {
        feedback.textContent = payload.message
          || "本机负责人数据暂时不可用，本地任务仍可继续。";
      }
    } catch (_error) {
      if (feedback) {
        feedback.textContent = "负责人数据已保存在本机，但当前巡检暂时无法更新。";
      }
    }
  }

  async function refreshLarkProductOwnerSnapshot() {
    if (!larkBaseConfig) return false;
    const feedback = larkBaseConfig.querySelector("[data-lark-base-feedback]");
    const button = larkBaseConfig.querySelector("[data-refresh-lark-owner-snapshot]");
    if (button) button.disabled = true;
    feedback.textContent = "正在把负责人和成功上传记录更新到本机…";
    try {
      const payload = await fetchJson(
        "/api/runtime/lark-base/snapshots/refresh",
        { method: "POST", body: JSON.stringify({}) },
      );
      const owner = payload.product_owner_snapshot || {};
      const history = payload.upload_history_snapshot || {};
      if (owner.status === "completed") {
        await applyLocalOwnerSnapshotToCompleteness();
      }
      const updatedAt = larkSnapshotTime(
        owner.updated_at || history.updated_at,
      );
      const ownerCount = Number(
        owner.metadata_count || owner.previous_metadata_count || 0,
      );
      const fingerprintCount = Number(
        history.fingerprint_count || history.previous_fingerprint_count || 0,
      );
      if (payload.status === "completed") {
        feedback.textContent = (
          `飞书数据已更新：负责人 ${ownerCount} 条，`
          + `成功上传记录 ${fingerprintCount} 条`
          + `${updatedAt ? ` · ${updatedAt}` : ""}。`
        );
        return true;
      }
      feedback.textContent = (
        `${payload.message || "本次更新未全部完成。"}`
        + ` 当前可用负责人 ${ownerCount} 条，`
        + `成功上传记录 ${fingerprintCount} 条。`
      );
      return payload.status === "partial";
    } catch (_error) {
      feedback.textContent = "飞书数据更新未完成；已有本机数据不会被覆盖。";
      return false;
    } finally {
      if (button) button.disabled = false;
    }
  }

  function scheduleLarkAuthPoll() {
    if (larkAuthPollTimer) window.clearTimeout(larkAuthPollTimer);
    larkAuthPollTimer = window.setTimeout(refreshLarkAuthStatus, 2000);
  }

  function keepLarkAuthRecheckAlive() {
    larkAuthRecheckUntil = Date.now() + 60000;
  }

  function shouldRecheckLarkAuth() {
    return Date.now() < larkAuthRecheckUntil;
  }

  function finishLarkAuthRecheck() {
    larkAuthRecheckUntil = 0;
    if (larkAuthPollTimer) window.clearTimeout(larkAuthPollTimer);
    larkAuthPollTimer = null;
  }

  async function refreshLarkAuthStatus() {
    if (!larkBaseConfig) return;
    try {
      const payload = await fetchJson("/api/runtime/lark-base/auth");
      renderLarkAuthStatus(payload);
      if (currentStageId === "approval") {
        applyApprovalUploadIdentity(payload, { rerender: true });
      }
      if (payload.status === "awaiting_user") {
        keepLarkAuthRecheckAlive();
        scheduleLarkAuthPoll();
      } else if (payload.status === "authorized") {
        finishLarkAuthRecheck();
        await activateLarkBase();
      } else if (shouldRecheckLarkAuth()) {
        scheduleLarkAuthPoll();
      }
    } catch (_error) {
      const retrying = shouldRecheckLarkAuth();
      const failed = retrying
        ? {
            status: "awaiting_user",
            message: "正在确认飞书授权结果，工作台会自动继续。",
          }
        : {
            status: "failed",
            message: "暂时无法检查飞书授权，请稍后重试。",
          };
      renderLarkAuthStatus(failed);
      if (currentStageId === "approval") {
        applyApprovalUploadIdentity(failed, { rerender: true });
      }
      if (retrying) scheduleLarkAuthPoll();
    }
  }

  async function authorizeLarkBase({ activateDefaults = true } = {}) {
    if (!larkBaseConfig) return null;
    keepLarkAuthRecheckAlive();
    const popup = window.open("about:blank", "tmallFeishuAuthorization");
    try {
      const payload = await fetchJson("/api/runtime/lark-base/auth/start", {
        method: "POST",
        body: JSON.stringify({}),
      });
      renderLarkAuthStatus(payload);
      if (currentStageId === "approval") {
        applyApprovalUploadIdentity(payload, { rerender: true });
      }
      if (payload.verification_url && popup) {
        popup.location.replace(payload.verification_url);
      } else if (popup) {
        popup.close();
      }
      if (payload.status === "awaiting_user") {
        scheduleLarkAuthPoll();
      } else if (payload.status === "authorized") {
        finishLarkAuthRecheck();
        if (activateDefaults) await activateLarkBase();
      } else if (shouldRecheckLarkAuth()) {
        scheduleLarkAuthPoll();
      }
      return payload;
    } catch (_error) {
      if (popup) popup.close();
      const failed = {
        status: "failed",
        message: "飞书授权页面暂时无法打开，请稍后重试。",
      };
      renderLarkAuthStatus(failed);
      if (currentStageId === "approval") {
        applyApprovalUploadIdentity(failed, { rerender: true });
      }
      if (shouldRecheckLarkAuth()) scheduleLarkAuthPoll();
      return failed;
    }
  }

  function initializeLarkBaseConfig() {
    if (!larkBaseConfig) return;
    larkBaseConfig.querySelector("[data-authorize-lark-base]")?.addEventListener(
      "click",
      authorizeLarkBase,
    );
    larkBaseConfig.querySelector("[data-refresh-lark-owner-snapshot]")?.addEventListener(
      "click",
      refreshLarkProductOwnerSnapshot,
    );
    refreshLarkAuthStatus();
  }

  function renderCollectionRuntime(payload) {
    if (!collectionRuntimeConfig) return;
    const selector = payload.selector_profile || {};
    const cdp = payload.cdp || {};
    const selectorNode = collectionRuntimeConfig.querySelector(
      "[data-selector-profile-status]",
    );
    const cdpNode = collectionRuntimeConfig.querySelector("[data-cdp-status]");
    const summary = collectionRuntimeConfig.querySelector(
      "[data-collection-runtime-summary]",
    );
    selectorNode.textContent = selector.configured
      ? "采集页面适配已就绪"
      : "首次采集时将自动完成页面适配";
    cdpNode.textContent = cdp.connected
      ? `CDP Chrome 已连接：${cdp.endpoint} · ${cdp.pages?.length || 0} 个页面`
      : `CDP Chrome 未连接：${cdp.reason_code || "CDP_UNAVAILABLE"}`;
    const session = payload.session || {};
    if (session.session_id) {
      const details = [
        `登录状态 ${session.login_state || "unknown"}`,
        session.observed_store ? `店铺 ${session.observed_store}` : "",
        session.observed_url ? `页面 ${session.observed_url}` : "",
        session.next_action || "",
      ].filter(Boolean);
      cdpNode.textContent += `；${details.join(" · ")}`;
    }
    const readiness = payload.collection_readiness || {};
    const checks = Array.isArray(readiness.checks) ? readiness.checks : [];
    const list = collectionRuntimeConfig.querySelector(
      "[data-collection-readiness-list]",
    );
    list.replaceChildren(...checks.map((check) => {
      const row = document.createElement("div");
      row.className = "collection-readiness-item";
      row.dataset.ready = check.ready ? "true" : "false";
      const message = check.ready
        ? check.message
        : `${check.message}${check.next_action ? ` · ${check.next_action}` : ""}`;
      row.textContent = `${check.ready ? "✓" : "!"} ${message}`;
      row.title = check.reason_code || "";
      return row;
    }));
    summary.textContent = readiness.ready
      ? "采集前置条件已全部就绪"
      : "需要完成本机准备或用户登录";
    collectionRuntimeConfig.dataset.ready = readiness.ready
      ? "true"
      : "false";
  }

  async function refreshCollectionRuntime() {
    if (!collectionRuntimeConfig) return;
    try {
      const store = activeForm()?.querySelector('[name="store"]')?.value?.trim() || "";
      const parameters = new URLSearchParams();
      if (sessionId) parameters.set("session_id", sessionId);
      if (store) parameters.set("expected_store", store);
      const query = parameters.size ? `?${parameters.toString()}` : "";
      renderCollectionRuntime(
        await fetchJson(`/api/runtime/collection${query}`),
      );
    } catch (error) {
      collectionRuntimeConfig.querySelector(
        "[data-collection-runtime-summary]",
      ).textContent = error.message;
      collectionRuntimeConfig.dataset.ready = "false";
    }
  }

  async function openCollectionLoginBrowser() {
    if (!collectionRuntimeConfig) return;
    const status = collectionRuntimeConfig.querySelector("[data-cdp-status]");
    status.textContent = "正在打开或恢复千牛登录窗口…";
    try {
      const payload = await fetchJson(
        "/api/runtime/collection/login-browser",
        {
          method: "POST",
          body: JSON.stringify({}),
        },
      );
      status.textContent = payload.reused
        ? "登录窗口已恢复；请在独立浏览器中确认登录状态。"
        : "登录窗口已打开；如果尚未登录，请先完成登录。";
      await refreshCollectionRuntime();
    } catch (error) {
      status.textContent = error.userMessage || error.message;
    }
  }

  async function saveSelectorProfile() {
    if (!collectionRuntimeConfig) return;
    const input = collectionRuntimeConfig.querySelector(
      "[data-selector-profile-path]",
    );
    const status = collectionRuntimeConfig.querySelector(
      "[data-selector-profile-status]",
    );
    status.textContent = "正在验证生产选择器配置…";
    try {
      await fetchJson("/api/runtime/selector-profile", {
        method: "PUT",
        body: JSON.stringify({ selectors_file: input.value.trim() }),
      });
      await refreshCollectionRuntime();
    } catch (error) {
      status.textContent = error.fieldErrors?.selectors_file || error.message;
    }
  }

  async function bootstrapSelectorProfile() {
    const input = collectionRuntimeConfig.querySelector(
      "[data-selector-profile-path]",
    );
    const status = collectionRuntimeConfig.querySelector(
      "[data-selector-profile-status]",
    );
    status.textContent = "正在创建或读取本机候选…";
    try {
      const payload = await fetchJson("/api/runtime/selector-profile/bootstrap", {
        method: "POST",
        body: JSON.stringify({ selectors_file: input.value.trim() }),
      });
      input.value = payload.path || input.value;
      status.textContent = payload.production
        ? "已找到本机生产配置，可直接验证当前页面。"
        : "候选已创建；只有当前页面全部验证通过后才会提升为生产配置。";
      await refreshCollectionRuntime();
    } catch (error) {
      status.textContent = error.fieldErrors?.selectors_file || error.message;
    }
  }

  async function validateCollectionRuntime() {
    await ensureSession();
    const input = collectionRuntimeConfig.querySelector(
      "[data-selector-profile-path]",
    );
    const store = activeForm()?.querySelector('[name="store"]')?.value?.trim() || "";
    const status = collectionRuntimeConfig.querySelector(
      "[data-selector-profile-status]",
    );
    if (!store) {
      status.textContent = "请先填写目标店铺，再验证当前页面。";
      return;
    }
    status.textContent = "正在读取 CDP Chrome 当前页面并逐项验证…";
    try {
      const payload = await fetchJson("/api/runtime/collection/validate", {
        method: "POST",
        body: JSON.stringify({
          session_id: sessionId,
          expected_store: store,
          selectors_file: input.value.trim(),
        }),
      });
      status.textContent = payload.validation.ready
        ? "当前页面、店铺与选择器已验证。"
        : `验证未通过：${payload.validation.reason_code}`;
      await refreshCollectionRuntime();
    } catch (error) {
      status.textContent = error.userMessage || error.message;
    }
  }

  function initializeCollectionRuntime() {
    if (!collectionRuntimeConfig || collectionRuntimeConfig.hidden) return;
    collectionRuntimeConfig.querySelector(
      "[data-open-login-browser]",
    ).addEventListener("click", openCollectionLoginBrowser);
    collectionRuntimeConfig.querySelector(
      "[data-bootstrap-selector-profile]",
    ).addEventListener("click", bootstrapSelectorProfile);
    collectionRuntimeConfig.querySelector(
      "[data-save-selector-profile]",
    ).addEventListener("click", saveSelectorProfile);
    collectionRuntimeConfig.querySelector(
      "[data-validate-collection-runtime]",
    ).addEventListener("click", validateCollectionRuntime);
    collectionRuntimeConfig.querySelector(
      "[data-refresh-collection-runtime]",
    ).addEventListener("click", refreshCollectionRuntime);
    refreshCollectionRuntime();
  }

  async function initializeSetupLoginGate() {
    if (!setupLoginGate || !setupForm || setupLoginGateStarted) return;
    setupLoginGateStarted = true;
    const title = setupLoginGate.querySelector("[data-setup-login-title]");
    const message = setupLoginGate.querySelector("[data-setup-login-message]");
    const poll = async () => {
      try {
        const payload = await fetchJson("/api/runtime/login-status");
        if (payload.ready) {
          const storeInput = setupForm.querySelector('[name="store"]');
          if (storeInput && !storeInput.value.trim() && payload.observed_store) {
            storeInput.value = payload.observed_store;
          }
          setupLoginGate.hidden = false;
          setupLoginGate.dataset.state = "ready";
          title.textContent = "已识别店铺，可以配置";
          message.textContent = payload.observed_store
            ? `当前识别店铺：${payload.observed_store}`
            : payload.message;
          setupForm.hidden = false;
          setupLoginReady = true;
          renderStatus();
          return;
        }
        setupLoginReady = false;
        setupForm.hidden = true;
        setupLoginGate.hidden = false;
        setupLoginGate.dataset.state = payload.status || "preparing";
        title.textContent = payload.message || "已连接 Chrome，正在打开千牛";
        message.textContent = payload.status === "chrome_unavailable"
          ? "系统将继续尝试启动专用 Chrome。"
          : payload.status === "waiting_for_login"
            ? "请在 Plugin 自动打开的 Chrome 千牛窗口中完成登录。"
            : payload.status === "store_unrecognized"
              ? "请保持千牛素材中心页面打开，系统会继续识别当前店铺。"
              : "系统正在打开千牛素材中心，请稍候。";
      } catch (error) {
        setupLoginReady = false;
        setupForm.hidden = true;
        setupLoginGate.hidden = false;
        setupLoginGate.dataset.state = "service_unavailable";
        title.textContent = "工作台服务未运行";
        message.textContent = "请恢复当前工作台会话后再继续配置。";
      }
      renderStatus();
      window.setTimeout(poll, 1500);
    };
    await poll();
  }

  async function ensureSession() {
    if (sessionId) return sessionId;
    const payload = await fetchJson("/api/sessions", {
      method: "POST",
      body: JSON.stringify({}),
    });
    sessionId = payload.session_id;
    shell.dataset.sessionId = sessionId;
    document.querySelectorAll("[data-session-label]").forEach((node) => {
      node.textContent = sessionId;
    });
    const taskDirectory = `${shell.dataset.runsRoot}\\${sessionId}`;
    taskDirectoryLabel.textContent = taskDirectory;
    document.querySelectorAll("[data-setup-task-directory]").forEach((node) => {
      node.textContent = taskDirectory;
    });
    const url = new URL(window.location.href);
    url.searchParams.set("session_id", sessionId);
    window.history.replaceState({}, "", url);
    return sessionId;
  }

  function activeForm() {
    return document.querySelector(`[data-stage-form="${currentStageId}"]`);
  }

  function clearFieldErrors(form) {
    form.querySelectorAll("[data-field-error]").forEach((node) => {
      node.textContent = "";
    });
    form.querySelectorAll("[aria-invalid='true']").forEach((control) => {
      control.removeAttribute("aria-invalid");
    });
  }

  function fieldLabel(form, name) {
    const field = form.querySelector(`[data-field="${CSS.escape(name)}"]`);
    const label = field?.querySelector("label[for]");
    return String(label?.textContent || "").trim() || "此项";
  }

  function readableFieldError(form, name, message) {
    const label = fieldLabel(form, name);
    const detail = String(message || "").trim();
    if ([
      "is required",
      "must be a non-empty string",
      "must not be empty",
    ].includes(detail)) {
      return `请填写${label}`;
    }
    if (detail === "must be confirmed") return `请确认${label}`;
    if (detail === "must be a list") return `${label}格式不正确，请重新选择`;
    return detail || `请检查${label}`;
  }

  function showFieldErrors(form, fieldErrors) {
    Object.entries(fieldErrors).forEach(([name, message]) => {
      const errorNode = form.querySelector(`[data-field-error="${CSS.escape(name)}"]`);
      const control = form.querySelector(`[name="${CSS.escape(name)}"]`);
      if (errorNode) {
        errorNode.textContent = readableFieldError(form, name, message);
      }
      if (control) control.setAttribute("aria-invalid", "true");
    });
  }

  function elementIsVisible(element) {
    return Boolean(
      element
      && !element.hidden
      && (element.offsetWidth || element.offsetHeight || element.getClientRects().length),
    );
  }

  function focusFirstFieldError(form, fieldErrors) {
    const first = Object.entries(fieldErrors || {})[0];
    if (!first) return "";
    const [name, detail] = first;
    const escaped = CSS.escape(name);
    const field = form.querySelector(`[data-field="${escaped}"]`);
    const controls = [...form.querySelectorAll(`[name="${escaped}"]`)]
      .filter((control) => !control.disabled);
    const control = controls.find(elementIsVisible);
    let target = elementIsVisible(field) ? field : control;
    let focusControl = control;
    if (name === "task_ids" && (!target || !focusControl)) {
      target = document.querySelector('[data-component="ApprovalChecklist"]');
      focusControl = target?.querySelector('input[type="checkbox"]:not(:disabled)');
    }
    target ||= form.querySelector(`[data-field-error="${escaped}"]`) || form;
    window.requestAnimationFrame(() => {
      target.scrollIntoView({ behavior: "smooth", block: "center" });
      if (elementIsVisible(focusControl)) {
        focusControl.focus({ preventScroll: true });
      }
    });
    return readableFieldError(form, name, detail);
  }

  function clientFieldErrors(form) {
    const errors = {};
    form.querySelectorAll("[name][required]").forEach((control) => {
      if (control.disabled || control.checkValidity()) return;
      const name = control.name;
      if (Object.hasOwn(errors, name)) return;
      const label = fieldLabel(form, name);
      errors[name] = control.validity.valueMissing
        ? control.type === "checkbox"
          ? `请确认${label}`
          : `请填写${label}`
        : `请检查${label}`;
    });
    return errors;
  }

  function serializeForm(form) {
    const values = {};
    const processed = new Set();
    const controls = [...form.querySelectorAll("[name]")].filter((control) => !control.disabled);

    controls.forEach((control) => {
      const name = control.name;
      if (processed.has(name)) return;
      processed.add(name);
      const group = controls.filter((candidate) => candidate.name === name);
      const kind = control.dataset.valueKind || "string";

      if (kind === "list" || (group.length > 1 && control.type !== "radio")) {
        values[name] = group.map((candidate) => candidate.value.trim()).filter(Boolean);
      } else if (control.type === "radio") {
        values[name] = group.find((candidate) => candidate.checked)?.value || "";
      } else if (kind === "boolean") {
        values[name] = control.checked;
      } else if (kind === "number") {
        values[name] = control.value === "" ? null : Number(control.value);
      } else if (kind === "line-list") {
        values[name] = control.value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
      } else if (kind === "json-list") {
        const parsed = control.value.trim() ? JSON.parse(control.value) : [];
        if (!Array.isArray(parsed)) throw new Error(`${name} 必须是 JSON 数组`);
        values[name] = parsed;
      } else {
        values[name] = control.value;
      }
    });
    return values;
  }

  function hydrateForm(form, values) {
    if (form.dataset.stageForm === "setup") {
      hydrateImageSources(values?.image_source_labels, values?.image_roots);
    }
    Object.entries(values || {}).forEach(([name, value]) => {
      if (
        form.dataset.stageForm === "setup"
        && name === "team_folder_index_root"
        && uiState.serverStatus === "completed"
      ) return;
      const controls = [...form.querySelectorAll(`[name="${CSS.escape(name)}"]`)];
      if (!controls.length) return;
      const first = controls[0];
      if (first.type === "radio") {
        controls.forEach((control) => { control.checked = control.value === String(value); });
        return;
      }
      const kind = controls.length > 1 ? "list" : (first.dataset.valueKind || "string");
      const presentation = UiState.controlPresentation(value, kind, controls.length);
      controls.forEach((control, index) => {
        if (kind === "boolean") control.checked = presentation.checked;
        else control.value = presentation.values[index] ?? "";
      });
    });
  }

  function applySessionSnapshot(session) {
    if (!session || typeof session !== "object") return;
    hasSavedSetupConfiguration = UiState.hasSavedSetupConfiguration(session);
    if (stages.has(session.current_stage)) {
      sessionCurrentStageId = session.current_stage;
    }
    UiState.stageSnapshot(session, [...stages.keys()]).forEach((item) => {
      const railStatus = document.querySelector(
        `[data-rail-status="${CSS.escape(item.stageId)}"]`,
      );
      if (!railStatus) return;
      railStatus.textContent = statusCopy[item.status] || statusCopy.draft;
      railStatus.dataset.status = item.status;
      railStatus.dataset.revision = String(item.revision);
    });
  }

  function formatRemainingTime(value) {
    const seconds = Math.max(0, Number(value || 0));
    const minutes = Math.floor(seconds / 60);
    const remainder = Math.floor(seconds % 60);
    if (minutes) return `${minutes} 分 ${remainder} 秒`;
    return `${remainder} 秒`;
  }

  function renderTaskAwareness(taskStatus) {
    if (!taskAwareness || !taskStatus || typeof taskStatus !== "object") return;
    taskAwareness.dataset.phase = String(taskStatus.phase || "waiting_user");
    taskStatusLabel.textContent = taskStatus.status_label || "任务状态已更新";
    taskStatusSummary.textContent = [taskStatus.summary, taskStatus.next_action]
      .filter(Boolean)
      .join(" ");
    taskStageProgress.textContent =
      `第 ${Number(taskStatus.current_stage_index || 1)} / `
      + `${Number(taskStatus.total_stage_count || 1)} 阶段 · `
      + `${taskStatus.current_stage_title || "当前阶段"}`;
    const shutdown = taskStatus.auto_shutdown || {};
    if (endCurrentTaskButton) {
      endCurrentTaskButton.hidden = !sessionId || shutdown.enabled !== true;
      if (!endCurrentTaskInFlight) {
        endCurrentTaskButton.disabled = shutdown.status === "closing";
        endCurrentTaskButton.textContent = shutdown.status === "review_window"
          ? "立即关闭工作台"
          : "结束当前任务";
      }
    }
    if (shutdown.status === "review_window") {
      taskAutoShutdown.hidden = false;
      taskAutoShutdown.textContent =
        `任务已结束，工作台将在 ${formatRemainingTime(shutdown.remaining_seconds)} 后自动关闭；任务记录会保留。`;
    } else if (shutdown.status === "closing") {
      taskAutoShutdown.hidden = false;
      taskAutoShutdown.textContent = "查看时间已结束，工作台正在自动关闭；任务记录会保留。";
    } else {
      taskAutoShutdown.hidden = true;
      taskAutoShutdown.textContent = "";
    }
  }

  function renderStatus() {
    updateImageSourceIncompleteHint();
    const status = uiState.dirty ? "draft" : uiState.serverStatus;
    const technicalDiagnostic = currentStageId === "setup"
      ? UiState.technicalDiagnosticView(uiState)
      : { active: false };
    const copy = technicalDiagnostic.active
      ? technicalDiagnostic.statusLabel
      : statusCopy[status] || statusCopy.draft;
    statusBadge.textContent = copy;
    statusBadge.dataset.status = status;
    handoffStatus.textContent = copy;
    const railStatus = document.querySelector(`[data-rail-status="${currentStageId}"]`);
    if (railStatus) railStatus.textContent = copy;
    const assetStep = currentStageId === "asset_matching"
      ? inferAssetMatchingStep(uiState.result?.data, uiState.serverStatus)
      : "";
    const initialCompletenessReview = currentStageId === "completeness"
      && !uiState.submission
      && status === "needs_user_input";
    submitButton.textContent = technicalDiagnostic.active
      ? technicalDiagnostic.submitLabel
      : currentStageId === "slots_copy"
      && !["ready_for_agent", "processing", "completed"].includes(status)
      ? "确认文案并自动预检"
      : currentStageId === "approval"
        && !["ready_for_agent", "processing", "completed"].includes(status)
        ? "提交并自动上传所选任务"
      : (
      currentStageId === "asset_matching"
      && assetStep === "image_selection"
      && !["ready_for_agent", "processing", "completed"].includes(status)
    )
      ? "确认选图并提交给工作台"
      : ["needs_user_input", "blocked"].includes(status)
        && !initialCompletenessReview
      ? "补充后重新提交"
      : status === "ready_for_agent"
        ? "已提交，等待工作台处理"
        : status === "processing"
          ? "工作台处理中"
          : "提交给工作台";
    const lockedByServer = !uiState.dirty
      && ["ready_for_agent", "processing", "completed"].includes(uiState.serverStatus);
    const checkingApprovalAuthorization = currentStageId === "approval"
      && approvalAuthorizationInFlight;
    const submittingNow = persistenceInFlight && activePersistenceMode === "submit";
    const approvalHasSelectedTasks = currentStageId !== "approval" || String(
      activeForm()?.querySelector('[name="task_ids"]')?.value || "",
    ).split(/\r?\n/).some((taskId) => taskId.trim());
    submitButton.disabled = lockedByServer
      || !approvalHasSelectedTasks
      || checkingApprovalAuthorization
      || submittingNow;
    if (checkingApprovalAuthorization) {
      submitButton.textContent = "正在确认飞书授权…";
      submitButton.setAttribute("aria-busy", "true");
    } else if (submittingNow) {
      submitButton.textContent = "正在检查并提交…";
      submitButton.setAttribute("aria-busy", "true");
    }
    if (
      currentStageId === "asset_matching"
      && ["queued", "running"].includes(currentGalleryJob?.status)
    ) {
      submitButton.disabled = true;
    }
    if (
      currentStageId === "asset_matching"
      && selectionPreflightScheduler.desiredPendingCount()
    ) {
      submitButton.disabled = true;
      submitButton.textContent = "正在检测所选图片…";
    }
    if (
      currentStageId === "asset_matching"
      && globalAssetSelectionInFlight
    ) {
      submitButton.disabled = true;
      submitButton.textContent = "正在为全部商品选图…";
    }
    saveButton.disabled = lockedByServer || persistenceInFlight;
    if (currentStageId === "slots_copy" && cropPreflightInFlight) {
      saveButton.disabled = true;
      submitButton.disabled = true;
    }
    if (currentStageId === "setup" && !setupLoginReady) {
      saveButton.disabled = true;
      submitButton.disabled = true;
    }
    goCurrentStageButton.hidden = !(
      lockedByServer
      && stages.has(sessionCurrentStageId)
      && sessionCurrentStageId !== currentStageId
    );
    const selectionCheckActive = currentStageId === "asset_matching"
      && selectionPreflightScheduler.desiredPendingCount() > 0;
    const backVisible = Boolean(
      currentBackNavigation?.visible
      && currentBackNavigation?.target_stage_id,
    );
    const localBackLockActive = stageLocalActionInFlight
      || globalAssetSelectionInFlight
      || (currentStageId === "slots_copy" && cropPreflightInFlight)
      || pendingBackNavigation
      || (currentStageId === "slots_copy" && localCopyRequestInFlight)
      || (
        persistenceInFlight
        && !["completeness", "asset_matching", "slots_copy"].includes(currentStageId)
      );
    const backEnabled = Boolean(
      backVisible
      && currentBackNavigation?.enabled
      && !localBackLockActive,
    );
    backButton.hidden = !backVisible;
    backButton.disabled = !backEnabled;
    backButton.textContent = currentBackNavigation?.label || "上一步";
    backButton.title = localBackLockActive
      ? globalAssetSelectionInFlight
        ? "正在为全部商品选图，完成后才能返回上一步。"
        : currentStageId === "slots_copy" && cropPreflightInFlight
          ? "正在执行裁剪预校验，完成后才能返回上一步。"
        : stageLocalActionInFlight
        ? "当前步骤正在保存或处理，完成后才能返回上一步。"
        : localCopyRequestInFlight
          ? "文案正在生成，完成后才能返回上一步。"
          : "当前步骤正在保存，完成后才能返回上一步。"
      : selectionCheckActive && currentBackNavigation?.enabled
        ? "仍有图片检测在后台进行；返回上一步会放弃当前素材选择。"
        : currentBackNavigation?.message || "";
    handoffActions?.classList.toggle("has-stage-back", backVisible);
    if (lockedByServer) {
      const reason = {
        completed: "该阶段已完成，不能再次保存或提交。",
        ready_for_agent: "已提交，正在排队处理。",
        processing: "正在处理，当前内容暂时不能修改。",
      }[uiState.serverStatus] || "该阶段当前不可编辑。";
      actionMessage.textContent = goCurrentStageButton.hidden
        ? reason
        : `${reason} 可进入任务当前阶段继续。`;
    }
    if (technicalDiagnostic.active) {
      actionMessage.textContent = technicalDiagnostic.message;
    }
    withdrawButton.hidden = uiState.serverStatus !== "ready_for_agent" || uiState.dirty;
    withdrawButton.disabled = uiState.serverStatus !== "ready_for_agent" || uiState.dirty;
    retryGalleryButton.hidden = !(
      currentStageId === "asset_matching"
      && ["failed", "stale"].includes(currentGalleryJob?.status)
    );
    retryGalleryButton.disabled = retryGalleryButton.hidden;
    setFormLocked(uiState.serverStatus);
    updateResultsRecovery(UiState.recoveryView(uiState));
    if (approvalRetryViewActive()) {
      saveButton.hidden = true;
      submitButton.hidden = true;
      saveButton.disabled = true;
      submitButton.disabled = true;
    }
    if (currentStageId !== "slots_copy") {
      handoffActions?.classList.remove("is-stage-managed");
    }
    if (currentStageId === "asset_matching") {
      submitButton.hidden = assetStep !== "image_selection";
    }
  }

  function formatClaimTime(value) {
    const timestamp = Date.parse(value || "");
    return Number.isFinite(timestamp)
      ? new Date(timestamp).toLocaleString("zh-CN", { hour12: false })
      : "未知时间";
  }

  function hasExplicitGalleryCounts(progress) {
    return progress
      && Object.prototype.hasOwnProperty.call(
        progress,
        "planned_inspection_count",
      );
  }

  function hasExplicitGallerySizeCounts(progress) {
    return progress
      && Object.prototype.hasOwnProperty.call(
        progress,
        "size_eligible_count",
      );
  }

  function galleryProgressMessage(job = currentGalleryJob) {
    const progress = job?.progress || {};
    if (!hasExplicitGalleryCounts(progress)) {
      return (
        `本机正在加载图片（历史进度口径）：已发现 `
        + `${progress.discovered_count || 0} 张，已检查 `
        + `${progress.prepared_count || 0} 张。`
      );
    }
    const failures = Number(progress.inspection_failure_count || 0);
    const duplicates = Number(progress.content_duplicate_count || 0);
    const uploadedHistory = Number(
      progress.uploaded_history_duplicate_count || 0,
    );
    const inspected = Number(progress.inspected_count || 0);
    const available = Number(progress.available_candidate_count || 0);
    const discovered = Number(progress.discovered_path_count || 0);
    const hasSizeCounts = hasExplicitGallerySizeCounts(progress);
    const sizeEligible = hasSizeCounts
      ? Number(progress.size_eligible_count || 0)
      : discovered;
    const sizeFiltered = hasSizeCounts
      ? Number(progress.size_filtered_count || 0)
      : 0;
    const belowMinimum = Number(progress.size_below_minimum_count || 0);
    const sizeExceeded = Number(progress.size_exceeded_count || 0);
    const statFailures = Number(progress.source_stat_failure_count || 0);
    return (
      `本机正在加载图片：发现 `
      + `${discovered} 张，大小预筛可用 `
      + `${sizeEligible} 张`
      + (
        sizeFiltered
          ? `，已跳过 ${sizeFiltered} 张（小于 200KiB ${belowMinimum} 张、大于 20MiB ${sizeExceeded} 张、无法读取信息 ${statFailures} 张）`
          : ""
      )
      + `；本轮进入候选检查 `
      + `${Number(progress.planned_inspection_count || 0)} 张，已处理 `
      + `${inspected + failures}/`
      + `${Number(progress.planned_inspection_count || 0)} 张`
      + `，检查成功 ${inspected} 张`
      + `${failures ? `，检查失败 ${failures} 张` : ""}`
      + `${duplicates ? `，内容重复 ${duplicates} 张` : ""}。`
      + `${uploadedHistory ? ` 已排除历史成功上传 ${uploadedHistory} 张。` : ""}`
      + `${available ? ` 已渐进展示 ${available} 张候选。` : ""}`
    );
  }

  function galleryCompletionMessage(job = currentGalleryJob) {
    const progress = job?.progress || {};
    if (!hasExplicitGalleryCounts(progress)) {
      return (
        `本机图片加载完成（历史进度口径）：候选 `
        + `${job?.result?.candidate_count ?? progress.prepared_count ?? 0} 张。`
      );
    }
    const discovered = Number(progress.discovered_path_count || 0);
    const hasSizeCounts = hasExplicitGallerySizeCounts(progress);
    const sizeEligible = hasSizeCounts
      ? Number(progress.size_eligible_count || 0)
      : discovered;
    const sizeFiltered = hasSizeCounts
      ? Number(progress.size_filtered_count || 0)
      : 0;
    return (
      `本机图片加载完成：发现 ${discovered} 张，大小预筛可用 `
      + `${sizeEligible} 张${sizeFiltered ? `、跳过 ${sizeFiltered} 张` : ""}；检查 `
      + `成功 ${Number(progress.inspected_count || 0)} 张，失败 `
      + `${Number(progress.inspection_failure_count || 0)} 张，内容重复 `
      + `${Number(progress.content_duplicate_count || 0)} 张，历史成功上传 `
      + `${Number(progress.uploaded_history_duplicate_count || 0)} 张，可展示候选 `
      + `${Number(progress.final_candidate_count || 0)} 张。`
    );
  }

  function renderProcessingClaim(claim, collectionStatus = currentCollectionStatus) {
    currentProcessingClaim = claim && typeof claim === "object" ? claim : null;
    currentCollectionStatus = collectionStatus && typeof collectionStatus === "object"
      ? collectionStatus
      : null;
    const processing = uiState.serverStatus === "processing";
    const recoverable = currentCollectionStatus?.status === "recoverable";
    const specializedRecovery = ["completeness", "asset_matching"].includes(
      currentStageId,
    );
    const dispatcherOwnsRecovery = currentWorkflowDispatch?.online === true;
    recoverProcessingButton.hidden = !(
      !dispatcherOwnsRecovery
      && !specializedRecovery
      && processing
      && (currentProcessingClaim?.expired || recoverable)
    );
    recoverProcessingButton.disabled = !(
      !dispatcherOwnsRecovery
      && !specializedRecovery
      && processing
      && (currentProcessingClaim?.expired || recoverable)
    );
    if (!processing) return;
    if (
      currentStageId === "asset_matching"
      && currentGalleryProgress?.workflow_step === "gallery_preparing"
    ) {
      actionMessage.textContent = galleryProgressMessage();
      return;
    }
    const worker = currentCollectionStatus?.worker;
    if (currentCollectionStatus?.status === "processing" && worker) {
      if (worker.phase === "waiting_human_check") {
        const savedPage = worker.last_completed_page == null
          ? "尚无完整页"
          : `已保存到第 ${worker.last_completed_page} 页`;
        actionMessage.textContent =
          `检测到滑动验证，${savedPage}。请在千牛窗口中完成验证；` +
          `验证通过后会自动继续采集。等待 ${Math.floor(Number(worker.elapsed_ms || 0) / 1000)} 秒。`;
        return;
      }
      const page = worker.last_completed_page == null
        ? "第一页尚未保存"
        : `已完成第 ${worker.last_completed_page} 页 · ${worker.row_count} 行`;
      const pagination = worker.pagination_origin_page == null
        ? "分页起点尚未验证"
        : (
          `分页起点 ${worker.pagination_origin_page} · 当前页 `
          + `${worker.observed_page ?? worker.current_page ?? "未知"}`
          + (
            worker.terminal_page == null
              ? ""
              : `/${worker.terminal_page}`
          )
        );
      actionMessage.textContent = `${page}；${pagination}`;
      return;
    }
    if (currentCollectionStatus?.status === "processing_indeterminate") {
      actionMessage.textContent =
        "暂时无法确认采集程序状态；系统会等待安全恢复，不会强行中断。";
      return;
    }
    if (recoverable) {
      actionMessage.textContent =
        "采集已经停止，可以从已保存的进度继续。";
      return;
    }
    if (!currentProcessingClaim) {
      actionMessage.textContent =
        "处理状态需要恢复，系统正在检查；任务数据已保留。";
      return;
    }
    if (currentProcessingClaim.expired) {
      if (specializedRecovery) {
        actionMessage.textContent =
          "商品选择已保存，系统正在继续处理。";
        return;
      }
      actionMessage.textContent =
        `处理等待已于 ${formatClaimTime(currentProcessingClaim.lease_expires_at)} 超时；` +
        "可以从已保存的进度继续。";
      return;
    }
    actionMessage.textContent =
      "正在处理，请稍候；完成前当前内容暂时不能修改。";
  }

  function persistenceRequestId(stageId, mode) {
    const key = `${sessionId}:${stageId}:${mode}`;
    if (!persistenceRequestIds.has(key)) {
      const generated = globalThis.crypto?.randomUUID?.()
        || `persist-${Date.now()}-${Math.random().toString(16).slice(2)}`;
      persistenceRequestIds.set(key, generated);
    }
    return { key, value: persistenceRequestIds.get(key) };
  }

  function renderHandoffStatus(display, workflowDispatch = currentWorkflowDispatch) {
    currentHandoffStatus = display && typeof display === "object" ? display : null;
    if (!currentHandoffStatus || uiState.serverStatus === "processing") return;
    if (workflowDispatch?.online === true) {
      if (
        workflowDispatch.stage_id === currentStageId
        && workflowDispatch.status === "queued"
      ) {
        actionMessage.textContent = "已提交，正在排队处理。";
        return;
      }
      if (
        workflowDispatch.stage_id === currentStageId
        && workflowDispatch.status === "running"
      ) {
        actionMessage.textContent = "正在处理，完成后页面会自动更新。";
        return;
      }
      if (
        workflowDispatch.stage_id === currentStageId
        && workflowDispatch.status === "failed"
      ) {
        actionMessage.textContent =
          "本步骤没有完成。排查信息已保留，请按页面提示处理。";
        return;
      }
      if (currentHandoffStatus.base_status === "ready") {
        actionMessage.textContent = "已提交，正在准备处理。";
        return;
      }
    }
    const wait = currentHandoffStatus.agent_wait;
    if (currentHandoffStatus.status === "waiting" && wait) {
      actionMessage.textContent = "等待处理期间仍可继续编辑。";
      return;
    }
    if (currentHandoffStatus.resume_prompt) {
      actionMessage.textContent = currentHandoffStatus.resume_prompt;
    }
  }

  async function recoverExpiredProcessing() {
    if (currentWorkflowDispatch?.online === true) return;
    if (
      !currentProcessingClaim?.expired
      && currentCollectionStatus?.status !== "recoverable"
    ) return;
    recoverProcessingButton.disabled = true;
    actionMessage.textContent = "正在恢复当前任务…";
    try {
      const payload = await fetchJson(
        apiPath(`/stages/${currentStageId}/recover-processing`),
        {
          method: "POST",
          body: JSON.stringify({ claimant_id: "codex-agent" }),
        },
      );
      renderProcessingClaim(payload.processing_claim);
      await loadStage();
    } catch (error) {
      actionMessage.textContent = error.userMessage || error.message;
      recoverProcessingButton.disabled = false;
    }
  }

  function setFormLocked(status) {
    const form = activeForm();
    if (!form) return;
    const locked = ["ready_for_agent", "processing", "completed"].includes(status);
    form.querySelectorAll("input, textarea, select, button").forEach((control) => {
      if (control.closest("[data-machine-runtime-control]")) return;
      if (locked && !control.disabled) {
        control.disabled = true;
        control.dataset.taskLocked = "true";
      } else if (!locked && control.dataset.taskLocked === "true") {
        control.disabled = false;
        delete control.dataset.taskLocked;
      }
    });
  }

  function scheduleAutoSave() {
    if (isHydrating) return;
    window.clearTimeout(autoSaveTimer);
    if (!["draft", "needs_user_input", "blocked"].includes(uiState.serverStatus)) return;
    if (
      currentStageId === "asset_matching"
      && globalAssetSelectionInFlight
    ) return;
    actionMessage.textContent = "有未保存更改；停止输入后将自动保存草稿。";
    autoSaveTimer = window.setTimeout(() => persistStage("draft", { automatic: true }), 1000);
  }

  function renderSubmission() {
    const { createdAt } = UiState.submissionView(uiState);
    if (!createdAt) {
      lastSubmittedLabel.textContent = "尚未提交";
      return;
    }
    const timestamp = Date.parse(createdAt);
    lastSubmittedLabel.textContent = Number.isFinite(timestamp)
      ? new Date(timestamp).toLocaleString("zh-CN", { hour12: false })
      : createdAt;
  }

  function updateResultsRecovery(recovery) {
    const isResults = currentStageId === "results";
    const mayRecover = isResults && recovery.visible;
    const recoveryForm = document.querySelector("[data-results-recovery]");
    if (recoveryForm) {
      recoveryForm.hidden = !mayRecover;
      recoveryForm.querySelectorAll("[name]").forEach((control) => {
        control.disabled = !mayRecover;
      });
    }
    if (isResults && !mayRecover) {
      saveButton.hidden = true;
      submitButton.hidden = true;
      saveButton.disabled = true;
      submitButton.disabled = true;
      actionMessage.textContent = "结果阶段只展示本次上传状态。";
    } else if (mayRecover) {
      saveButton.hidden = true;
      saveButton.disabled = true;
      submitButton.hidden = false;
      submitButton.disabled = !recovery.submitEnabled;
      submitButton.textContent = "补充后重新提交";
    } else {
      saveButton.hidden = false;
      submitButton.hidden = false;
    }
  }

  function renderResult(componentName, view) {
    const module = document.querySelector(`[data-component="${CSS.escape(componentName)}"]`);
    const content = module?.querySelector("[data-result-content]");
    if (!content) return;
    content.replaceChildren();

    if (view.mode === "empty") {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.dataset.emptyState = view.label;
      const mark = document.createElement("span");
      mark.setAttribute("aria-hidden", "true");
      mark.textContent = "◎";
      const label = document.createElement("strong");
      label.textContent = view.label;
      empty.append(mark, label);
      content.appendChild(empty);
      return;
    }

    const summary = document.createElement("div");
    summary.className = "result-data";
    const heading = document.createElement("strong");
    const result = view.result;
    const diagnostic = result.agent_diagnostic;
    if (diagnostic?.status === "open") {
      const teamIndexBlocked = Array.isArray(result.blocking_reasons)
        && result.blocking_reasons.some((reason) => String(reason).startsWith("TEAM_INDEX_"));
      if (teamIndexBlocked) {
        heading.textContent = "需要选择团队索引文件夹";
        const safeMessage = document.createElement("p");
        safeMessage.textContent = "返回任务配置，选择这台电脑可以访问的团队索引文件夹；保存后系统会自动继续。";
        const configure = document.createElement("button");
        configure.type = "button";
        configure.className = "secondary-button";
        configure.textContent = "返回任务配置";
        configure.addEventListener("click", () => activateStage("setup"));
        summary.append(heading, safeMessage, configure);
        content.appendChild(summary);
        return;
      }
      heading.textContent = diagnostic.user_action_required
        ? "需要你在已打开的窗口完成操作"
        : "系统正在处理异常";
      const safeMessage = document.createElement("p");
      safeMessage.textContent = diagnostic.user_action_required
        ? "请按照 Codex 的提示完成必要操作；当前业务数据已经保留。"
        : "异常详情已自动交给 Codex，当前业务数据已经保留，无需重新配置。";
      summary.append(heading, safeMessage);
      content.appendChild(summary);
      return;
    }
    heading.textContent = result.summary || "工作台已返回结果";
    summary.appendChild(heading);
    // 素材匹配证据包含本机运行目录，仅供后台诊断与审计，不在用户页展示。
    if (
      componentName !== "AssetMatchGallery"
      && Array.isArray(result.evidence)
      && result.evidence.length
    ) {
      const evidence = document.createElement("ul");
      result.evidence.forEach((item) => {
        const row = document.createElement("li");
        row.textContent = typeof item === "string" ? item : JSON.stringify(item);
        evidence.appendChild(row);
      });
      summary.appendChild(evidence);
    }
    const sections = UiState.resultSections(result);
    if (sections.blockingReasons.length) {
      const blocking = document.createElement("section");
      const label = document.createElement("strong");
      label.textContent = "阻塞原因";
      const reasons = document.createElement("ul");
      sections.blockingReasons.forEach((reason) => {
        const row = document.createElement("li");
        row.textContent = reason;
        reasons.appendChild(row);
      });
      blocking.append(label, reasons);
      summary.appendChild(blocking);
    }
    if (sections.nextAction) {
      const action = document.createElement("section");
      const label = document.createElement("strong");
      label.textContent = "下一步";
      const nextAction = document.createElement("p");
      nextAction.textContent = result.next_action;
      action.append(label, nextAction);
      summary.appendChild(action);
    }
    content.appendChild(summary);
  }

  function renderUploadResults(view) {
    const module = document.querySelector('[data-component="UploadResults"]');
    const content = module?.querySelector("[data-result-content]");
    if (!content) return;
    content.replaceChildren();

    if (view.mode === "empty") {
      const empty = element("div", "empty-state");
      empty.dataset.emptyState = "暂无结果";
      const mark = element("span", "", "◎");
      mark.setAttribute("aria-hidden", "true");
      empty.append(
        mark,
        element("strong", "", "暂无上传结果"),
        element("p", "", "完成上传后，这里会显示每个商品是否上传成功。"),
      );
      content.appendChild(empty);
      return;
    }

    const result = view.result || {};
    const data = result.data || {};
    const products = Array.isArray(data.products) ? data.products : [];
    content.appendChild(element("p", "upload-results-summary", result.summary || "上传结果已生成"));
    if (!products.length) {
      content.appendChild(element("p", "upload-results-empty", "没有找到可展示的商品上传记录。"));
      return;
    }

    const list = element("div", "upload-results-list");
    products.forEach((product) => {
      const card = element("article", "upload-result-card");
      card.dataset.status = product.status || "failed";
      const header = element("div", "upload-result-header");
      const identity = element("div", "upload-result-identity");
      identity.append(
        element("strong", "", product.product_name || `商品 ${product.product_id}`),
        element("span", "", `商品 ID：${product.product_id || "未知"}`),
      );
      const outcome = element("div", "upload-result-outcome");
      const badge = element("strong", "upload-result-badge", product.status_label || "上传失败");
      badge.dataset.status = product.status || "failed";
      outcome.append(
        badge,
        element("span", "", `${Number(product.success_count || 0)} / ${Number(product.task_count || 0)} 个坑位成功`),
      );
      header.append(identity, outcome);
      card.appendChild(header);

      const materials = Array.isArray(product.materials) ? product.materials : [];
      if (materials.length) {
        const materialList = element("div", "upload-material-list");
        materials.forEach((material) => {
          const row = element("div", "upload-material-row");
          row.dataset.status = material.status || "failed";
          row.append(
            element(
              "strong",
              "upload-material-slot",
              material.slot_index == null ? "坑位未知" : `坑位 ${material.slot_index}`,
            ),
            element(
              "code",
              "upload-material-id",
              material.remote_material_id
                ? `素材 ID：${material.remote_material_id}`
                : "素材 ID：未获取",
            ),
            element(
              "span",
              "upload-material-status",
              material.status_label || "上传失败",
            ),
          );
          materialList.appendChild(row);
        });
        card.appendChild(materialList);
      }
      list.appendChild(card);
    });
    content.appendChild(list);
  }

  function approvalRetryViewActive() {
    return currentStageId === "approval"
      && uiState.result?.data?.publish_retry?.available === true;
  }

  function renderUploadRetryPanel(content, retryState) {
    content.replaceChildren();
    const panel = element("section", "upload-retry-panel");
    panel.dataset.status = retryState.stage_status || "blocked";
    panel.append(
      element("strong", "upload-retry-title", "上传未全部完成"),
      element(
        "p",
        "upload-retry-message",
        retryState.message || "本次上传进度已经保留。",
      ),
    );

    const metrics = element("div", "upload-confirmation-summary");
    [
      ["已成功", Number(retryState.successful_count || 0)],
      ["可继续", Number(retryState.retryable_count || 0)],
      [
        "待确认",
        Number(retryState.uncertain_count || 0)
          + Number(retryState.manual_review_count || 0),
      ],
    ].forEach(([label, value]) => {
      const item = element("div", "upload-confirmation-metric");
      item.append(element("strong", "", String(value)), element("span", "", label));
      metrics.appendChild(item);
    });
    panel.appendChild(metrics);

    const retryableTasks = Array.isArray(retryState.retryable_tasks)
      ? retryState.retryable_tasks
      : [];
    if (retryableTasks.length) {
      const details = element("details", "upload-retry-details");
      details.appendChild(element(
        "summary",
        "",
        `查看 ${retryableTasks.length} 个未完成坑位`,
      ));
      const list = element("ul", "upload-retry-task-list");
      retryableTasks.forEach((task) => {
        const product = task.product_id ? `商品 ${task.product_id}` : "商品待确认";
        const slot = task.slot_index == null ? "坑位待确认" : `坑位 ${task.slot_index}`;
        list.appendChild(element(
          "li",
          "",
          `${product} · ${slot} · ${task.status_label || "未完成"}`,
        ));
      });
      details.appendChild(list);
      panel.appendChild(details);
    }

    const actions = element("div", "upload-retry-actions");
    const retryButton = element(
      "button",
      "primary-button",
      ["ready_for_agent", "processing"].includes(retryState.stage_status)
        ? "正在继续上传…"
        : "继续上传未完成坑位",
    );
    retryButton.type = "button";
    retryButton.disabled = retryState.can_retry !== true;
    if (["ready_for_agent", "processing"].includes(retryState.stage_status)) {
      retryButton.setAttribute("aria-busy", "true");
    }
    retryButton.addEventListener("click", async () => {
      if (retryButton.disabled) return;
      retryButton.disabled = true;
      retryButton.textContent = "正在继续上传…";
      retryButton.setAttribute("aria-busy", "true");
      actionMessage.textContent = "正在恢复未完成坑位，已成功坑位不会重复上传。";
      const requestIdentity = persistenceRequestId("approval", "retry_upload");
      try {
        const payload = await fetchJson(
          apiPath("/stages/approval/retry-upload"),
          {
            method: "POST",
            body: JSON.stringify({ request_id: requestIdentity.value }),
          },
        );
        persistenceRequestIds.delete(requestIdentity.key);
        actionMessage.textContent = payload.message || "正在继续上传未完成坑位。";
        await loadStage();
      } catch (error) {
        actionMessage.textContent = error.userMessage || error.message;
        retryButton.disabled = retryState.can_retry !== true;
        retryButton.textContent = "继续上传未完成坑位";
        retryButton.removeAttribute("aria-busy");
      }
    });
    actions.appendChild(retryButton);
    panel.appendChild(actions);
    content.appendChild(panel);
  }

  function completenessSelectedIds() {
    const control = activeForm()?.querySelector('[name="selected_product_ids"]');
    return new Set(
      String(control?.value || "")
        .split(/\r?\n/)
        .map((item) => item.trim())
        .filter(Boolean),
    );
  }

  function writeCompletenessSelectedIds(ids, { notify = true } = {}) {
    const control = activeForm()?.querySelector('[name="selected_product_ids"]');
    if (!control) return;
    const value = [...ids]
      .sort((left, right) => left.localeCompare(right, "zh-CN"))
      .join("\n");
    if (control.value === value) return;
    control.value = value;
    if (notify) {
      control.dispatchEvent(new CustomEvent(
        "input",
        { bubbles: true, detail: { source: "explicit-user-edit" } },
      ));
    }
  }

  function completenessStatusLabel(status) {
    return ({
      needs_supplement: "待补充",
      needs_manual_review: "需人工确认",
      complete: "已完整",
      excluded: "已自动排除",
      abnormal: "异常",
    })[status] || "待确认";
  }

  function completenessExclusionReasons(product) {
    const labels = {
      EXCLUDE_UVNO: "uvno",
      EXCLUDE_POINTS: "积分",
      EXCLUDE_CLEARANCE: "清仓",
      EXCLUDE_GOOD_EXPERIENCE: "好物体验",
      EXCLUDE_MEMBER_DAY: "会员日",
    };
    const reasonCodes = Array.isArray(product.eligibility?.reason_codes)
      ? product.eligibility.reason_codes
      : [];
    return reasonCodes.map((code) => labels[code] || code);
  }

  function completenessReinspectRequestId() {
    return `reinspect-${Date.now().toString(36)}-${Math.random()
      .toString(36)
      .slice(2, 10)}`;
  }

  async function requestCompletenessReinspection(trigger) {
    if (
      !sessionId
      || currentStageId !== "completeness"
      || persistenceInFlight
      || stageLocalActionInFlight
    ) return;
    stageLocalActionInFlight = true;
    trigger.disabled = true;
    actionMessage.textContent = "正在提交重新巡检请求…";
    try {
      const payload = await fetchJson(apiPath("/stages/completeness/reinspect"), {
        method: "POST",
        body: JSON.stringify({ request_id: completenessReinspectRequestId() }),
      });
      sessionCurrentStageId = payload.target_stage_id || "setup";
      activateStage(sessionCurrentStageId);
      actionMessage.textContent =
        payload.message || "已开始重新巡检，后台会重新采集“搜推高价值”。";
    } catch (error) {
      actionMessage.textContent = error.userMessage || error.message;
      await loadStage();
    } finally {
      stageLocalActionInFlight = false;
      if (trigger.isConnected) trigger.disabled = false;
      renderStatus();
    }
  }

  function createCompletenessReinspectButton(locked) {
    const button = element("button", "secondary-button inspection-reinspect", "重新巡检");
    button.type = "button";
    button.disabled =
      locked
      || currentStageId !== "completeness"
      || persistenceInFlight
      || stageLocalActionInFlight;
    button.title = button.disabled
      ? "当前阶段处理完成或后台处理中时不可重新巡检"
      : "重新采集“搜推高价值”，并清空后续素材匹配、坑位和上传确认的派生结果";
    button.addEventListener("click", () => requestCompletenessReinspection(button));
    return button;
  }

  function renderInspectionMatrix(view) {
    const module = document.querySelector('[data-component="InspectionMatrix"]');
    const content = module?.querySelector("[data-result-content]");
    if (!content) return;
    content.replaceChildren();

    const locked = ["ready_for_agent", "processing", "completed"].includes(uiState.serverStatus);
    const products = Array.isArray(view.result?.data?.products)
      ? view.result.data.products
      : [];
    if (view.mode === "empty" || !products.length) {
      const presentation = UiState.completenessEmptyView(uiState, view);
      const empty = element("div", "empty-state inspection-empty");
      empty.dataset.emptyState = presentation.label;
      const mark = document.createElement("span");
      mark.setAttribute("aria-hidden", "true");
      mark.textContent = "◎";
      const label = element("strong", "", presentation.label);
      const hint = element("p", "", presentation.hint);
      empty.append(mark, label, hint);
      if (presentation.allowReinspect) {
        empty.appendChild(createCompletenessReinspectButton(locked));
      }
      content.appendChild(empty);
      return;
    }
    const selected = completenessSelectedIds();
    const selectableIds = new Set(
      products
        .filter((product) => UiState.completenessProductSelectable(product))
        .map((product) => String(product.product_id || "")),
    );
    let selectionChanged = false;
    selected.forEach((productId) => {
      if (!selectableIds.has(productId)) {
        selected.delete(productId);
        selectionChanged = true;
      }
    });
    if (selectionChanged && !locked) {
      writeCompletenessSelectedIds(selected, { notify: false });
    }
    const statusCounts = view.result?.data?.summary?.status_counts || {};
    const summary = element("div", "inspection-summary");
    let selectedCount;
    [
      ["全部商品", products.length],
      ["待补充", statusCounts.needs_supplement || 0],
      ["已完整", statusCounts.complete || 0],
      ["已排除", view.result?.data?.summary?.excluded_count || 0],
      ["已选择", selected.size],
    ].forEach(([label, count]) => {
      const card = element("div", "inspection-stat");
      const countElement = element("strong", "", String(count));
      if (label === "已选择") selectedCount = countElement;
      card.append(element("span", "", label), countElement);
      summary.appendChild(card);
    });
    content.appendChild(summary);
    const anomalies = view.result?.data?.product_row_anomalies;
    if (anomalies?.blocked_row_count) {
      const anomalyPanel = element("details", "inspection-anomalies");
      const anomalySummary = document.createElement("summary");
      anomalySummary.textContent = (
        `商品表异常 ${anomalies.blocked_row_count} 行，`
        + `有效 ${anomalies.valid_row_count || 0} 行`
      );
      const anomalyList = element("ul", "");
      Object.entries(anomalies.reason_codes_by_row || {}).forEach(
        ([rowNumber, reasonCodes]) => {
          anomalyList.appendChild(
            element(
              "li",
              "",
              `源表第 ${rowNumber} 行：${(reasonCodes || []).join("、")}`,
            ),
          );
        },
      );
      anomalyPanel.append(anomalySummary, anomalyList);
      content.appendChild(anomalyPanel);
    }

    const toolbar = element("div", "inspection-toolbar");
    const search = document.createElement("input");
    search.type = "search";
    search.placeholder = "搜索商品 ID、货号或名称";
    search.setAttribute("aria-label", "搜索完整度巡检商品");
    const filter = document.createElement("select");
    filter.setAttribute("aria-label", "筛选完整度状态");
    [
      ["all", "全部"],
      ["selected", "已选"],
      ["needs_supplement", "待补充"],
      ["complete", "已完整"],
      ["excluded", "已自动排除"],
      ["abnormal", "异常"],
    ].forEach(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      filter.appendChild(option);
    });
    const ownerFilter = document.createElement("select");
    ownerFilter.setAttribute("aria-label", "筛选负责人");
    const ownerOptions = [
      ["all", "全部负责人"],
      ...[...new Set(
        products.map((product) => String(product.owner || "").trim()).filter(Boolean),
      )]
        .sort((left, right) => left.localeCompare(right, "zh-CN"))
        .map((owner) => [owner, owner]),
    ];
    if (products.some((product) => !String(product.owner || "").trim())) {
      ownerOptions.push(["__unassigned__", "未分配负责人"]);
    }
    ownerOptions.forEach(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      ownerFilter.appendChild(option);
    });
    const productGradeFilter = document.createElement("select");
    productGradeFilter.setAttribute("aria-label", "筛选产品等级");
    const productGradeOptions = [
      ["all", "全部产品等级"],
      ...[...new Set(
        products
          .map((product) => String(product.product_grade || product.grade || "").trim())
          .filter(Boolean),
      )]
        .sort((left, right) => left.localeCompare(right, "zh-CN", { numeric: true }))
        .map((productGrade) => [productGrade, productGrade]),
    ];
    if (products.some(
      (product) => !String(product.product_grade || product.grade || "").trim(),
    )) {
      productGradeOptions.push(["__ungraded__", "未标注产品等级"]);
    }
    productGradeOptions.forEach(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      productGradeFilter.appendChild(option);
    });
    const bulkSelect = element("button", "secondary-button", "选择当前筛选结果");
    bulkSelect.type = "button";
    bulkSelect.disabled = locked;
    const bulkClear = element("button", "secondary-button", "取消当前筛选结果");
    bulkClear.type = "button";
    bulkClear.disabled = locked;
    toolbar.append(
      createCompletenessReinspectButton(locked),
      search,
      filter,
      ownerFilter,
      productGradeFilter,
      bulkSelect,
      bulkClear,
    );
    content.appendChild(toolbar);

    const list = element("div", "inspection-list");
    content.appendChild(list);

    const visibleProducts = () => {
      return UiState.filterCompletenessProducts(products, {
        query: search.value,
        status: filter.value,
        owner: ownerFilter.value,
        productGrade: productGradeFilter.value,
        selectedProductIds: selected,
      });
    };

    const draw = () => {
      list.replaceChildren();
      const visible = visibleProducts();
      if (!visible.length) {
        list.appendChild(element("p", "inspection-no-results", "没有符合当前筛选条件的商品。"));
        return;
      }
      visible.forEach((product) => {
        const productId = String(product.product_id || "");
        const selectable = UiState.completenessProductSelectable(product);
        const slotsFull = !UiState.completenessProductHasOpenSlots(product);
        const card = element("article", "inspection-row");
        const selectionDisabled = locked || !selectable;
        card.dataset.status = product.status || "needs_manual_review";
        card.tabIndex = selectionDisabled ? -1 : 0;
        card.setAttribute("role", "checkbox");
        card.setAttribute("aria-disabled", String(selectionDisabled));

        const identity = element("div", "inspection-identity");
        identity.append(
          element("strong", "", product.product_title || `商品 ${productId}`),
          element("span", "", `商品 ID ${productId} · 货号 ${product.sku || "未知"}`),
          element("span", "", `负责人 ${product.owner || "未分配"}`),
          element("span", "inspection-status", completenessStatusLabel(product.status)),
        );
        const exclusionReasons = completenessExclusionReasons(product);
        if (!selectable && exclusionReasons.length) {
          identity.append(
            element(
              "span",
              "inspection-exclusion-reason",
              `命中自动排除规则：${exclusionReasons.join("、")}`,
            ),
          );
        }

        const promotion = element("div", "inspection-cell");
        const target = product.promotion?.target_slots;
        const current = product.promotion?.current_count;
        const missing = product.promotion?.missing_count;
        const productGrade = String(product.product_grade || product.grade || "").trim();
        const slotSummary = element("div", "inspection-slot-summary");
        slotSummary.append(
          element("strong", "", target == null || current == null ? "坑位待补采" : `${current} / ${target} 篇`),
          element("span", "inspection-product-grade", `产品等级 ${productGrade || "未标注"}`),
        );
        promotion.append(
          element("small", "", "搜推素材"),
          slotSummary,
          element("span", "", missing == null ? "缺失数未知" : `缺失 ${missing} 篇`),
          element(
            "span",
            "",
            product.candidate_asset_count == null
              ? "候选图片：待素材匹配"
              : `候选图片 ${product.candidate_asset_count} 张`,
          ),
        );
        if (product.promotion?.exact_slot_status !== "collected") {
          promotion.append(
            element("span", "", "精确空坑位尚未采集，不能推断具体坑位编号"),
          );
        }
        if (product.promotion?.evidence) {
          const evidence = document.createElement("details");
          const evidenceSummary = document.createElement("summary");
          evidenceSummary.textContent = "查看后台证据";
          evidence.append(evidenceSummary, element("code", "", product.promotion.evidence));
          promotion.appendChild(evidence);
        }

        const controls = element("div", "inspection-controls");
        const stateBadge = element("span", "inspection-selection-state");
        controls.append(stateBadge);
        card.append(identity, promotion, controls);
        list.appendChild(card);

        const syncSelection = (nextSelected, { persist = true } = {}) => {
          const isSelected = selectable && Boolean(nextSelected);
          if (isSelected) selected.add(productId);
          else selected.delete(productId);
          card.dataset.selected = String(isSelected);
          card.setAttribute("aria-checked", String(isSelected));
          stateBadge.textContent = selectable
            ? isSelected ? "已选择" : "未选择"
            : slotsFull ? "坑位已满" : "已排除";
          card.setAttribute(
            "aria-label",
            `${product.product_title || `商品 ${productId}`}，${stateBadge.textContent}`
              + (selectionDisabled ? "，不可选择" : "，点击切换"),
          );
          if (!persist) return;
          writeCompletenessSelectedIds(selected);
          if (selectedCount) selectedCount.textContent = String(selected.size);
        };
        const toggleSelection = () => {
          if (selectionDisabled) return;
          syncSelection(!selected.has(productId));
          if (filter.value === "selected") draw();
        };
        card.addEventListener("click", (event) => {
          if (event.target.closest("details, summary, a, button, input, select, textarea, label")) {
            return;
          }
          toggleSelection();
        });
        card.addEventListener("keydown", (event) => {
          if (!['Enter', ' '].includes(event.key)) return;
          event.preventDefault();
          toggleSelection();
        });
        syncSelection(selected.has(productId), { persist: false });
      });
    };

    search.addEventListener("input", draw);
    filter.addEventListener("change", draw);
    ownerFilter.addEventListener("change", draw);
    productGradeFilter.addEventListener("change", draw);
    bulkSelect.addEventListener("click", () => {
      visibleProducts().forEach((product) => {
        if (UiState.completenessProductSelectable(product)) {
          selected.add(String(product.product_id));
        }
      });
      writeCompletenessSelectedIds(selected);
      if (selectedCount) selectedCount.textContent = String(selected.size);
      draw();
    });
    bulkClear.addEventListener("click", () => {
      visibleProducts().forEach((product) => {
        selected.delete(String(product.product_id));
      });
      writeCompletenessSelectedIds(selected);
      if (selectedCount) selectedCount.textContent = String(selected.size);
      draw();
    });
    draw();
  }

  function readJsonListControl(name) {
    const control = activeForm()?.querySelector(`[name="${CSS.escape(name)}"]`);
    if (!control?.value.trim()) return [];
    try {
      const value = JSON.parse(control.value);
      return Array.isArray(value) ? value : [];
    } catch (_error) {
      return [];
    }
  }

  function writeJsonListControl(name, values, { notify = false } = {}) {
    const control = activeForm()?.querySelector(`[name="${CSS.escape(name)}"]`);
    if (!control) return false;
    let currentValue = null;
    try {
      currentValue = JSON.parse(control.value || "[]");
    } catch (_error) {
      currentValue = null;
    }
    if (UiState.jsonSemanticallyEqual(currentValue, values)) return false;
    const value = JSON.stringify(values, null, 2);
    control.value = value;
    if (notify) {
      control.dispatchEvent(new CustomEvent(
        "input",
        { bubbles: true, detail: { source: "explicit-user-edit" } },
      ));
    }
    return true;
  }

  function assetSortKey(candidate) {
    const matchRank = {
      exact_product_id: "0",
      exact_sku: "1",
      name_candidate: "2",
      split_name_candidate: "3",
      short_split_name_candidate: "4",
      fuzzy_name_candidate: "5",
    };
    return [
      matchRank[candidate.match_type] || "9",
      candidate.source_system || "",
      candidate.source_path || "",
      candidate.sha256 || "",
      candidate.asset_id || "",
    ].join("\u0000").toLocaleLowerCase("zh-CN");
  }

  function uniqueSortedCandidates(candidates) {
    const seen = new Set();
    return [...candidates]
      .sort((left, right) => assetSortKey(left).localeCompare(assetSortKey(right), "zh-CN"))
      .filter((candidate) => {
        const fingerprint = String(candidate.sha256 || "");
        if (!fingerprint || seen.has(fingerprint)) return false;
        seen.add(fingerprint);
        return true;
      });
  }

  function uniqueSelectedAssetDecisions(items) {
    const seen = new Set();
    return (Array.isArray(items) ? items : []).filter((item) => {
      if (item?.decision !== "selected" || !item.asset_id) return false;
      const key = `${String(item.product_id || "")}\u0000${String(item.asset_id)}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  function resetFormForAuthoritativeHydration(form) {
    if (!form) return;
    form.reset();
    form.querySelectorAll('[data-value-kind="json-list"]').forEach((control) => {
      control.value = "[]";
    });
  }

  function selectedAssetDecisions() {
    return uniqueSelectedAssetDecisions(readJsonListControl("asset_decisions"));
  }

  function selectionPreflightFor(assetId) {
    return selectionPreflights.get(String(assetId || "")) || null;
  }

  function selectionPreflightContextKey(data = uiState.result?.data) {
    const identity = data?.gallery_identity;
    if (!identity || !sessionId) return "";
    return [
      sessionId,
      String(data?.gallery_job_id || ""),
      String(data?.sampling_identity_sha256 || ""),
      String(identity.prepared_from_revision || ""),
      String(identity.prepared_from_input_sha256 || ""),
      String(identity.folder_decisions_sha256 || ""),
    ].join("|");
  }

  function resetSelectionPreflightClientState(nextContextKey = "") {
    selectionPreflightContextGeneration += 1;
    selectionPreflightScheduler.reset();
    selectionPreflights.clear();
    selectionPreflightCardRefreshers.clear();
    appliedSelectionPreflightIntents.clear();
    selectedAssetValidation = null;
    selectionPreflightHydrationKey = "";
    selectionPreflightHydrationInFlightFor = "";
    selectionPreflightHydrationPromise = null;
    selectionPreflightActiveContextKey = nextContextKey;
    galleryAutoFocusedFor = "";
  }

  async function hydrateSelectionPreflights() {
    const key = selectionPreflightContextKey();
    if (!key || selectionPreflightHydrationKey === key) return true;
    if (
      selectionPreflightHydrationInFlightFor === key
      && selectionPreflightHydrationPromise
    ) return selectionPreflightHydrationPromise;
    if (selectionPreflightActiveContextKey !== key) {
      resetSelectionPreflightClientState(key);
    }
    const contextGeneration = selectionPreflightContextGeneration;
    selectionPreflightHydrationInFlightFor = key;
    const hydrationPromise = (async () => {
      try {
        const payload = await fetchJson(apiPath(
          "/stages/asset_matching/selection-preflights",
        ));
        if (
          contextGeneration !== selectionPreflightContextGeneration
          || selectionPreflightActiveContextKey !== key
        ) return false;
        selectionPreflights.clear();
        (payload.entries || []).forEach((entry) => {
          if (entry?.asset_id) {
            selectionPreflights.set(String(entry.asset_id), entry);
          }
        });
        selectionPreflightHydrationKey = key;
        if (currentStageId === "asset_matching") {
          renderStageResult(stages.get(currentStageId).component);
        }
        return true;
      } catch (_error) {
        // A temporary read failure remains retryable and the submit endpoint
        // performs the same deterministic safety check as a final fallback.
        return false;
      } finally {
        if (selectionPreflightHydrationPromise === hydrationPromise) {
          selectionPreflightHydrationInFlightFor = "";
          selectionPreflightHydrationPromise = null;
        }
      }
    })();
    selectionPreflightHydrationPromise = hydrationPromise;
    return hydrationPromise;
  }

  function runSelectionPreflight(candidate) {
    const assetId = String(candidate.asset_id || "");
    if (!assetId) return Promise.reject(new Error("候选图片缺少稳定标识"));
    const existing = selectionPreflightFor(assetId);
    if (existing?.status && existing.status !== "pending") {
      return selectionPreflightScheduler.selectCached(candidate, existing);
    }
    const priorJob = selectionPreflightScheduler.get(assetId);
    if (priorJob?.state === "completed") {
      selectionPreflightScheduler.forget(assetId);
    }
    const contextGeneration = selectionPreflightContextGeneration;
    const contextKey = selectionPreflightActiveContextKey;
    return selectionPreflightScheduler.select(candidate).then((entry) => {
      if (
        entry
        && !entry.cancelled
        && entry.status !== "cancelled"
        && contextGeneration === selectionPreflightContextGeneration
        && contextKey === selectionPreflightActiveContextKey
      ) {
        selectionPreflights.set(assetId, entry);
      }
      return entry;
    });
  }

  function candidateIsSelectable(candidate) {
    const confirmedMatch = ["matched_unlicensed", "confirmed"]
      .includes(candidate.match_status);
    return candidate.validation_status === "valid"
      && candidate.preflight?.selectable === true
      && candidate.source_inspection?.size_bytes != null
      && candidate.source_inspection?.width
      && candidate.source_inspection?.height
      && confirmedMatch
      && !candidate.remote_duplicate;
  }

  function preflightLabel(candidate) {
    return {
      direct: "可直传",
      croppable: "可裁剪",
      needs_compression: "需要压缩",
      crop_and_compress: "裁剪并压缩",
      unusable: "不可使用",
    }[candidate.preflight_status || candidate.preflight?.status] || "待检测";
  }

  function resolutionSummary(candidate) {
    const checks = candidate.resolution_checks || candidate.preflight?.resolution_checks || {};
    const matching = candidate.preflight?.matching_ratios || [];
    if (!matching.length) {
      const feasible = ["3:4", "1:1"].filter(
        (ratio) => checks[ratio]?.minimum_status === "meets_minimum",
      );
      return feasible.length
        ? `需要裁剪；可尝试 ${feasible.join("、")}`
        : "无法裁出宽高均不少于 720px 的图片";
    }
    const belowRecommended = matching.filter(
      (ratio) => checks[ratio]?.status !== "meets_or_exceeds",
    );
    return belowRecommended.length
      ? `原图已满足 ${matching.join("、")}；${belowRecommended.join("、")} 未达到推荐分辨率`
      : `原图已满足 ${matching.join("、")}，并达到推荐分辨率`;
  }

  function selectedCandidateDecision(productId, candidate, currentDecisions) {
    const previous = currentDecisions.find(
      (item) => String(item.product_id || "") === String(productId)
        && String(item.asset_id || "") === String(candidate.asset_id || ""),
    );
    const selectionPreflight = selectionPreflightFor(candidate.asset_id);
    const canRetainPrevious = previous
      && String(previous.sha256 || "") === String(candidate.sha256 || "");
    return {
      product_id: String(productId),
      asset_id: String(candidate.asset_id),
      sha256: String(candidate.sha256),
      folder_id: String(candidate.folder_id || candidate.resolved_folder_id || ""),
      folder_path: String(candidate.folder_path || candidate.candidate_directory || ""),
      source_system: String(candidate.source_system || ""),
      source_path: String(candidate.source_path || ""),
      selection_preflight_identity: String(
        selectionPreflight?.identity_sha256
        || (canRetainPrevious ? previous.selection_preflight_identity : "")
        || "",
      ),
      feasible_ratios: [...(
        selectionPreflight?.feasible_ratios
        || (canRetainPrevious ? previous.feasible_ratios : [])
        || []
      )],
    };
  }

  function reconcileSelectedAssetValidation(nextDecisions) {
    if (!selectedAssetValidation) return;
    const selectedIds = new Set(
      nextDecisions.map((item) => String(item.asset_id || "")),
    );
    const retainedItems = (selectedAssetValidation.items || []).filter(
      (item) => selectedIds.has(String(item.asset_id || ""))
        && item.issue_type !== "duplicate",
    );
    selectedAssetValidation = retainedItems.length
      ? {
        ...selectedAssetValidation,
        selected_count: nextDecisions.length,
        blocking_count: retainedItems.filter(
          (item) => item.severity === "blocked",
        ).length,
        warning_count: retainedItems.filter(
          (item) => item.severity === "warning",
        ).length,
        items: retainedItems,
      }
      : null;
  }

  function persistSelectedCandidate(productId, candidate, selected) {
    const currentDecisions = selectedAssetDecisions();
    const nextDecisions = UiState.mergeSelectedAssetDecision(
      currentDecisions,
      selectedCandidateDecision(productId, candidate, currentDecisions),
      selected,
    );
    if (writeJsonListControl(
      "asset_decisions",
      nextDecisions,
      { notify: true },
    )) reconcileSelectedAssetValidation(nextDecisions);
  }

  function commitSelectedCandidateBatch(productId, candidates) {
    if (!Array.isArray(candidates) || !candidates.length) return false;
    let nextDecisions = selectedAssetDecisions();
    const selectedAssetIds = new Set();
    candidates.forEach((candidate) => {
      selectedAssetIds.add(String(candidate.asset_id || ""));
      nextDecisions = UiState.mergeSelectedAssetDecision(
        nextDecisions,
        selectedCandidateDecision(productId, candidate, nextDecisions),
        true,
      );
    });
    const decisionsChanged = writeJsonListControl(
      "asset_decisions",
      nextDecisions,
      { notify: true },
    );
    if (decisionsChanged) reconcileSelectedAssetValidation(nextDecisions);
    const nextLicenses = readJsonListControl("license_decisions")
      .filter((item) => !selectedAssetIds.has(String(item.asset_id || "")));
    selectedAssetIds.forEach((assetId) => {
      nextLicenses.push({ asset_id: assetId, status: "confirmed" });
    });
    const licensesChanged = writeJsonListControl(
      "license_decisions",
      nextLicenses,
      { notify: true },
    );
    return decisionsChanged || licensesChanged;
  }

  function persistLicense(assetId, confirmed, productId = "") {
    const retained = readJsonListControl("license_decisions")
      .filter((item) => String(item.asset_id) !== String(assetId));
    const selectedByAnotherProduct = selectedAssetDecisions().some(
      (item) => item?.decision === "selected"
        && String(item.asset_id || "") === String(assetId)
        && String(item.product_id || "") !== String(productId || ""),
    );
    if (confirmed || selectedByAnotherProduct) {
      retained.push({ asset_id: String(assetId), status: "confirmed" });
    }
    writeJsonListControl("license_decisions", retained, { notify: true });
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function renderProductNavigator(
    mount,
    items,
    { label = "商品导航" } = {},
  ) {
    if (!mount) return;
    productNavigatorCleanups.get(mount)?.();
    productNavigatorCleanups.delete(mount);
    const targets = (items || []).filter((item) => item?.target);
    if (targets.length < 2) {
      mount.replaceChildren();
      return;
    }
    const existing = mount.querySelector("details");
    const wasOpen = existing ? existing.open : true;
    const details = element("details", "product-jump-nav");
    details.open = wasOpen;
    const summary = document.createElement("summary");
    summary.append(
      element("strong", "", label),
      element("span", "", `${targets.length} 个商品`),
    );
    const links = element("nav", "product-jump-list");
    links.setAttribute("aria-label", label);
    const buttons = targets.map((item, index) => {
      const productId = String(item.productId || "");
      const productTitle = String(item.productTitle || "").trim();
      const primaryLabel = productTitle || "商品名称未获取";
      const button = element("button", "product-jump-button");
      button.type = "button";
      const hasNavigationStatus = Object.prototype.hasOwnProperty.call(
        item,
        "navigationStatus",
      );
      button.append(
        element("span", "", primaryLabel),
        element("small", "", `ID ${productId}`),
      );
      if (hasNavigationStatus) {
        const navigationStatus = element("small", "product-jump-status");
        navigationStatus.classList.toggle(
          "is-danger",
          item.navigationTone === "danger",
        );
        button.appendChild(navigationStatus);
        item.updateNavigationStatus = (value) => {
          item.navigationStatus = String(value || "").trim();
          navigationStatus.textContent = item.navigationStatus;
          navigationStatus.hidden = !item.navigationStatus;
          button.title = [
            `${primaryLabel} · ID ${productId}`,
            item.navigationStatus,
          ].filter(Boolean).join(" · ");
        };
        item.updateNavigationStatus(item.navigationStatus);
      } else {
        button.title = `${primaryLabel} · ID ${productId}`;
      }
      button.addEventListener("click", () => {
        buttons.forEach((candidateButton, candidateIndex) => {
          candidateButton.classList.toggle("is-current", candidateIndex === index);
          if (candidateIndex === index) {
            candidateButton.setAttribute("aria-current", "true");
          } else {
            candidateButton.removeAttribute("aria-current");
          }
        });
        const reduceMotion = window.matchMedia?.(
          "(prefers-reduced-motion: reduce)",
        )?.matches;
        item.target.scrollIntoView({
          behavior: reduceMotion ? "auto" : "smooth",
          block: "start",
        });
        item.target.focus({ preventScroll: true });
      });
      links.appendChild(button);
      return button;
    });
    details.append(summary, links);
    mount.replaceChildren(details);

    let animationFrame = null;
    const updateCurrentProduct = () => {
      animationFrame = null;
      const activationLine = Math.min(260, Math.max(150, window.innerHeight * 0.3));
      const activeIndex = UiState.activeProductTargetIndex(
        targets.map((item) => item.target.getBoundingClientRect()),
        activationLine,
      );
      buttons.forEach((button, index) => {
        const current = index === activeIndex;
        button.classList.toggle("is-current", current);
        if (current) button.setAttribute("aria-current", "true");
        else button.removeAttribute("aria-current");
      });
    };
    const scheduleCurrentProductUpdate = () => {
      if (animationFrame != null) return;
      animationFrame = window.requestAnimationFrame(updateCurrentProduct);
    };
    window.addEventListener("scroll", scheduleCurrentProductUpdate, { passive: true });
    window.addEventListener("resize", scheduleCurrentProductUpdate);
    details.addEventListener("toggle", scheduleCurrentProductUpdate);
    const cleanup = () => {
      if (animationFrame != null) window.cancelAnimationFrame(animationFrame);
      window.removeEventListener("scroll", scheduleCurrentProductUpdate);
      window.removeEventListener("resize", scheduleCurrentProductUpdate);
      details.removeEventListener("toggle", scheduleCurrentProductUpdate);
      productNavigatorCleanups.delete(mount);
      activeProductNavigatorCleanups.delete(cleanup);
    };
    productNavigatorCleanups.set(mount, cleanup);
    activeProductNavigatorCleanups.add(cleanup);
    scheduleCurrentProductUpdate();
  }

  function folderDecisions() {
    return readJsonListControl("folder_decisions")
      .filter(
        (item) => item?.folder_id
          && item?.product_id
          && ["confirmed", "rejected"].includes(String(item.decision || "")),
      )
      .map((item) => ({
        folder_id: String(item.folder_id),
        product_id: String(item.product_id),
        source_system: String(item.source_system || ""),
        source_id: String(item.source_id || item.source_system || ""),
        relative_path: String(item.relative_path || ""),
        folder_path: String(item.folder_path || ""),
        decision: String(item.decision),
        note: String(item.note || ""),
      }));
  }

  function removedProductIdSet() {
    return new Set(
      readJsonListControl("removed_product_ids")
        .map((value) => String(value || "").trim())
        .filter(Boolean),
    );
  }

  function galleryJobIsActive(job = currentGalleryJob) {
    return ["queued", "running"].includes(job?.status);
  }

  function setLocalCopyRequestInFlight(active) {
    const next = Boolean(active);
    if (localCopyRequestInFlight === next) return;
    localCopyRequestInFlight = next;
    if (currentStageId === "slots_copy") renderStatus();
  }

  function assetMatchingProductIds(data) {
    const ids = new Set();
    ["requirements", "folder_candidates", "asset_candidates"].forEach((key) => {
      (Array.isArray(data?.[key]) ? data[key] : []).forEach((item) => {
        const productId = String(item?.product_id || "").trim();
        if (productId) ids.add(productId);
      });
    });
    return ids;
  }

  async function removeProductFromCurrentTask(productId, productTitle, data) {
    if (galleryJobIsActive() || globalAssetSelectionInFlight) return false;
    const normalizedProductId = String(productId || "").trim();
    const removed = removedProductIdSet();
    const activeProducts = [...assetMatchingProductIds(data)]
      .filter((value) => !removed.has(value));
    if (!normalizedProductId || activeProducts.length <= 1) {
      actionMessage.textContent = "本次任务至少需要保留一个商品。";
      return false;
    }
    const displayTitle = String(productTitle || `商品 ${normalizedProductId}`).trim();
    const confirmed = await confirmAction({
      title: "去掉当前商品",
      message: `确定从本次任务中去掉“${displayTitle}”（商品 ID ${normalizedProductId}）吗？\n\n该商品的候选文件夹、已选图片和后续坑位会一并移除。`,
      confirmLabel: "确认去掉",
      danger: true,
    });
    if (!confirmed) return false;

    const selectedRows = selectedAssetDecisions();
    const retainedSelectedRows = selectedRows.filter(
      (item) => String(item.product_id || "") !== normalizedProductId,
    );
    const removedProductAssetIds = new Set(
      selectedRows
        .filter((item) => String(item.product_id || "") === normalizedProductId)
        .map((item) => String(item.asset_id || "")),
    );
    const retainedAssetIds = new Set(
      retainedSelectedRows.map((item) => String(item.asset_id || "")),
    );
    const exclusivelyRemovedAssetIds = new Set(
      [...removedProductAssetIds].filter(
        (assetId) => !retainedAssetIds.has(assetId),
      ),
    );
    exclusivelyRemovedAssetIds.forEach((assetId) => {
      selectionPreflightScheduler.cancel(assetId);
      selectionPreflights.delete(assetId);
    });
    writeJsonListControl(
      "asset_decisions",
      retainedSelectedRows,
    );
    writeJsonListControl(
      "license_decisions",
      readJsonListControl("license_decisions").filter(
        (item) => !exclusivelyRemovedAssetIds.has(
          String(item?.asset_id || ""),
        ),
      ),
    );
    writeJsonListControl(
      "folder_decisions",
      folderDecisions().map((item) => (
        item.product_id === normalizedProductId
          ? { ...item, decision: "rejected", note: "本次任务已去掉该商品" }
          : item
      )),
    );
    removed.add(normalizedProductId);
    writeJsonListControl(
      "removed_product_ids",
      [...removed].sort((left, right) => left.localeCompare(right, "zh-CN")),
      { notify: true },
    );
    if (selectedAssetValidation) {
      const retainedItems = (selectedAssetValidation.items || []).filter(
        (item) => String(item.product_id || "") !== normalizedProductId,
      );
      selectedAssetValidation = retainedItems.length
        ? {
          ...selectedAssetValidation,
          selected_count: retainedSelectedRows.length,
          blocking_count: retainedItems.filter(
            (item) => item.severity === "blocked",
          ).length,
          warning_count: retainedItems.filter(
            (item) => item.severity === "warning",
          ).length,
          items: retainedItems,
        }
        : null;
    }
    actionMessage.textContent = `已从本次任务去掉“${displayTitle}”；其余商品保持不变。`;
    renderStageResult(stages.get("asset_matching").component);
    return true;
  }

  function removeProductButton(productId, productTitle, data) {
    const button = element(
      "button",
      "button-danger-secondary",
      "去掉当前商品",
    );
    button.type = "button";
    const removed = removedProductIdSet();
    const activeCount = [...assetMatchingProductIds(data)]
      .filter((value) => !removed.has(value)).length;
    button.disabled = galleryJobIsActive()
      || globalAssetSelectionInFlight
      || activeCount <= 1;
    button.title = activeCount <= 1
      ? "本次任务至少需要保留一个商品"
      : galleryJobIsActive()
        ? ""
        : globalAssetSelectionInFlight
          ? "自动选图完成后可调整商品范围"
        : "仅从本次上传任务中去掉该商品";
    button.setAttribute(
      "aria-label",
      `去掉当前商品：${productTitle || productId}`,
    );
    button.addEventListener("click", async () => {
      await removeProductFromCurrentTask(productId, productTitle, data);
    });
    return button;
  }

  function materializeFolderDecisions(candidates) {
    const savedByKey = new Map(
      folderDecisions().map((item) => [
        `${item.product_id}\u0000${item.folder_id}`,
        item,
      ]),
    );
    const materialized = candidates
      .filter(
        (candidate) => candidate?.match_type !== "confirmed_alias"
          && candidate?.folder_id
          && candidate?.product_id,
      )
      .map((candidate) => {
        const key = `${candidate.product_id}\u0000${candidate.folder_id}`;
        const saved = savedByKey.get(key) || {};
        const defaultDecision = candidate.match_type === "fuzzy_name_candidate"
          ? "rejected"
          : candidate.decision === "rejected"
            ? "rejected"
            : "confirmed";
        const decision = ["confirmed", "rejected"].includes(saved.decision)
          ? saved.decision
          : defaultDecision;
        return {
          folder_id: String(candidate.folder_id),
          product_id: String(candidate.product_id),
          source_system: String(candidate.source_system || ""),
          source_id: String(
            candidate.source_id || candidate.source_system || "",
          ),
          relative_path: String(candidate.relative_path || ""),
          folder_path: String(candidate.folder_path || ""),
          decision,
          note: String(saved.note || candidate.note || ""),
        };
      });
    writeJsonListControl("folder_decisions", materialized);
    return materialized;
  }

  function folderDecisionState(productId, folderId, defaultDecision = "confirmed") {
    const saved = folderDecisions().find(
      (item) => String(item.product_id) === String(productId)
        && String(item.folder_id) === String(folderId),
    );
    if (saved?.decision === "rejected") return "rejected";
    if (saved?.decision === "confirmed") return "confirmed";
    return defaultDecision === "rejected" ? "rejected" : "confirmed";
  }

  function normalizedAssetPath(value) {
    return String(value || "")
      .replaceAll("/", "\\")
      .replace(/\\+$/, "")
      .toLocaleLowerCase("zh-CN");
  }

  function associateCandidateFolder(candidate, folders) {
    const explicitFolderId = String(candidate.folder_id || "").trim();
    if (explicitFolderId) {
      return {
        ...candidate,
        resolved_folder_id: explicitFolderId,
        folder_link_status: "linked",
      };
    }
    const sourcePath = normalizedAssetPath(candidate.source_path);
    const matchingFolders = folders
      .filter((folder) => {
        if (
          folder.source_system
          && candidate.source_system
          && String(folder.source_system) !== String(candidate.source_system)
        ) return false;
        const folderPath = normalizedAssetPath(folder.folder_path);
        return folderPath
          && (sourcePath === folderPath || sourcePath.startsWith(`${folderPath}\\`));
      })
      .sort(
        (left, right) => normalizedAssetPath(right.folder_path).length
          - normalizedAssetPath(left.folder_path).length,
      );
    if (!matchingFolders.length) {
      return {
        ...candidate,
        resolved_folder_id: "",
        folder_link_status: "legacy_unlinked",
      };
    }
    return {
      ...candidate,
      resolved_folder_id: String(matchingFolders[0].folder_id || ""),
      folder_path: String(
        candidate.folder_path
          || candidate.candidate_directory
          || matchingFolders[0].folder_path
          || "",
      ),
      folder_link_status: "legacy_path",
    };
  }

  function pruneSelectedCandidates(productId, allowedCandidates) {
    const allowedIds = new Set(
      allowedCandidates.map((candidate) => String(candidate.asset_id)),
    );
    const current = selectedAssetDecisions();
    const removedIds = new Set(
      current
        .filter(
          (item) => String(item.product_id) === String(productId)
            && !allowedIds.has(String(item.asset_id)),
        )
        .map((item) => String(item.asset_id)),
    );
    if (!removedIds.size) return 0;
    writeJsonListControl(
      "asset_decisions",
      current.filter((item) => !removedIds.has(String(item.asset_id))),
    );
    writeJsonListControl(
      "license_decisions",
      readJsonListControl("license_decisions")
        .filter((item) => !removedIds.has(String(item.asset_id))),
    );
    return removedIds.size;
  }

  function persistFolderDecision(candidate, decision, note = "") {
    const folderId = String(candidate.folder_id || "");
    const productId = String(candidate.product_id || "");
    const retained = folderDecisions().filter(
      (item) => String(item.folder_id) !== folderId
        || String(item.product_id) !== productId,
    );
    if (["confirmed", "rejected"].includes(decision)) {
      retained.push({
        folder_id: folderId,
        product_id: productId,
        source_system: String(candidate.source_system || ""),
        source_id: String(candidate.source_id || candidate.source_system || ""),
        relative_path: String(candidate.relative_path || ""),
        folder_path: String(candidate.folder_path || ""),
        decision,
        note: String(note || "").trim(),
      });
    }
    writeJsonListControl("folder_decisions", retained, { notify: true });
  }

  function renderFolderOwnershipReview(view) {
    if (view.mode === "empty") return;
    const data = view.result?.data;
    const allCandidates = Array.isArray(data?.folder_candidates)
      ? data.folder_candidates.filter(
        (candidate) => candidate?.match_type !== "confirmed_alias",
      )
      : [];
    const removedProducts = removedProductIdSet();
    const presentation = UiState.folderReviewPresentation(
      data,
      [...removedProducts],
    );
    const candidates = presentation.candidates;
    const module = document.querySelector('[data-component="AssetMatchGallery"]');
    const content = module?.querySelector("[data-result-content]");
    if (!content) return;
    const resultHeading = content.querySelector(".result-data > strong");
    if (resultHeading) resultHeading.textContent = presentation.summary;
    if (!candidates.length) return;

    const review = element("section", "folder-review");
    const productNavigator = element("div", "product-jump-mount");
    const productTargets = [];
    review.appendChild(productNavigator);
    let updateLocalGalleryAction = () => {};
    const decisionsByKey = new Map(
      materializeFolderDecisions(allCandidates).map((item) => [
        `${item.product_id}\u0000${item.folder_id}`,
        item,
      ]),
    );
    const productIds = [...new Set(candidates.map((item) => String(item.product_id || "")))]
      .sort((left, right) => left.localeCompare(right, "zh-CN"));

    productIds.forEach((productId) => {
      const productCandidates = candidates.filter(
        (item) => String(item.product_id || "") === productId,
      );
      const productTitle = UiState.resolveProductTitle(
        data,
        productId,
        productCandidates[0]?.product_title,
      );
      const group = element("section", "folder-product");
      group.tabIndex = -1;
      const productTarget = {
        productId,
        productTitle,
        target: group,
        navigationStatus: "",
      };
      productTargets.push(productTarget);
      const heading = element("div", "asset-product-heading");
      const headingText = element("div");
      headingText.append(
        element("strong", "", productTitle || "商品名称未获取"),
        element(
          "span",
          "",
          `商品 ID ${productId} · 货号 ${productCandidates[0]?.sku || "未知"} · ${productCandidates.length} 个候选文件夹`,
        ),
      );
      const progress = element("span", "folder-review-progress");
      const headingActions = element("div", "asset-product-actions");
      headingActions.append(
        progress,
        removeProductButton(
          productId,
          productTitle,
          data,
        ),
      );
      heading.append(headingText, headingActions);
      group.appendChild(heading);
      const list = element("div", "folder-list");
      group.appendChild(list);
      review.appendChild(group);

      const updateProgress = () => {
        const rejected = productCandidates.filter(
          (candidate) => folderDecisionState(
            productId,
            String(candidate.folder_id || ""),
            candidate.decision,
          ) === "rejected",
        ).length;
        const accepted = productCandidates.length - rejected;
        const navigationStatus = `${accepted}个采用`;
        progress.textContent = `采用 ${accepted} · 排除 ${rejected}`;
        productTarget.navigationStatus = navigationStatus;
        productTarget.updateNavigationStatus?.(navigationStatus);
      };

      productCandidates.forEach((candidate) => {
        const key = `${productId}\u0000${candidate.folder_id}`;
        const saved = decisionsByKey.get(key) || {};
        const card = element("article", "folder-card");
        const galleryActive = galleryJobIsActive()
          || globalAssetSelectionInFlight;
        card.tabIndex = galleryActive ? -1 : 0;
        card.setAttribute("role", "checkbox");
        card.setAttribute("aria-disabled", String(galleryActive));
        const identity = element("div", "folder-card-identity");
        const countStatus = String(
          candidate.image_count_status || "pending",
        );
        let countText = "";
        if (countStatus === "ready") {
          countText = (
            `文件夹内素材（递归统计）：`
            + `${Number(candidate.raw_recursive_image_count || 0)} 张`
          );
        }
        if (countText && candidate.gallery_unique_path_count != null) {
          countText += (
            ` · 大小预筛后可分配 `
            + `${Number(candidate.gallery_unique_path_count || 0)} 张`
            + (
              Number(candidate.gallery_size_filtered_count || 0)
                ? `（跳过 ${Number(candidate.gallery_size_filtered_count || 0)} 张）`
                : ""
            )
            + ` · 本轮进入候选检查 `
            + `${Number(candidate.gallery_sampled_inspection_count || 0)} 张`
            + ` · 最终可选素材 `
            + `${Number(candidate.gallery_final_candidate_count || 0)} 张`
            + (
              Number(candidate.gallery_uploaded_history_duplicate_count || 0)
                ? ` · 排除历史成功上传 ${Number(candidate.gallery_uploaded_history_duplicate_count || 0)} 张`
                : ""
            )
          );
        }
        identity.append(
          element("strong", "", candidate.folder_name || "未命名文件夹"),
          element(
            "span",
            "folder-match-badge",
            candidate.match_type === "exact_sku"
              ? "货号命中"
              : candidate.match_type === "exact_folder_query"
                ? "本次精确文件夹查询"
                : candidate.match_type === "split_name_candidate"
                  ? "完整名称片段命中"
                  : candidate.match_type === "short_split_name_candidate"
                    ? "短名称片段候选"
                : candidate.match_type === "fuzzy_name_candidate"
                  ? "50% 连续名称候选"
                  : "名称候选",
          ),
          element("small", "", candidate.source_system || "未知来源"),
        );
        if (countText) {
          identity.appendChild(element("small", "folder-image-count", countText));
        }
        identity.appendChild(element("code", "", candidate.folder_path || ""));
        const status = element("div", "folder-card-status");
        const stateBadge = element("span", "folder-selection-state");
        const warning = element(
          "span",
          candidate.match_type === "fuzzy_name_candidate"
            ? "asset-warning folder-match-warning-danger"
            : "asset-warning",
          candidate.match_type === "exact_sku"
            ? "货号命中；如不属于本商品请排除"
            : candidate.match_type === "exact_folder_query"
              ? "用户指定的完整文件夹名；仅本次任务有效"
              : candidate.match_type === "split_name_candidate"
                ? "文件夹包含商品名中以 / 分隔的完整片段；默认采用，可手动排除"
                : candidate.match_type === "short_split_name_candidate"
                  ? "文件夹包含 3–4 字完整片段；默认排除，确认属于本商品后再采用"
              : candidate.match_type === "fuzzy_name_candidate"
                ? "粗略名称命中；默认排除，确认属于本商品后再采用"
                : "名称精确命中；默认采用，可手动排除",
        );
        status.append(stateBadge, warning);
        card.append(identity, status);
        list.appendChild(card);

        let currentDecision = saved.decision === "rejected"
          ? "rejected"
          : "confirmed";
        const syncDecision = (nextDecision, { persist = true } = {}) => {
          currentDecision = nextDecision === "rejected"
            ? "rejected"
            : "confirmed";
          const selected = currentDecision === "confirmed";
          card.dataset.decision = currentDecision;
          card.setAttribute("aria-checked", String(selected));
          card.setAttribute(
            "aria-label",
            `${candidate.folder_name || "未命名文件夹"}，${selected ? "已采用" : "已排除"}，点击切换`,
          );
          stateBadge.textContent = selected ? "已采用" : "已排除";
          if (!persist) return;
          persistFolderDecision(
            candidate,
            currentDecision,
            saved.note || candidate.note || "",
          );
          updateProgress();
          updateLocalGalleryAction();
          content.dispatchEvent(new CustomEvent("folder-decision-changed", {
            detail: {
              productId,
              folderId: String(candidate.folder_id || ""),
              decision: currentDecision,
            },
          }));
        };
        const toggleDecision = () => {
          if (galleryJobIsActive() || globalAssetSelectionInFlight) return;
          syncDecision(
            currentDecision === "confirmed" ? "rejected" : "confirmed",
          );
        };
        card.addEventListener("click", toggleDecision);
        card.addEventListener("keydown", (event) => {
          if (!["Enter", " "].includes(event.key)) return;
          event.preventDefault();
          toggleDecision();
        });
        syncDecision(currentDecision, { persist: false });
      });
      updateProgress();
    });
    renderProductNavigator(productNavigator, productTargets);
    const localAction = element("div", "local-gallery-action");
    const localSummary = element("p", "asset-selection-summary");
    const localButton = element(
      "button",
      "primary-button",
      "确认文件夹并加载图片",
    );
    localButton.type = "button";
    updateLocalGalleryAction = () => {
      const currentStep = inferAssetMatchingStep(
        view.result?.data,
        uiState.serverStatus,
      );
      const galleryAlreadyLoaded = currentStep === "image_selection";
      const galleryNeedsReload = !galleryAlreadyLoaded
        || UiState.galleryNeedsReload(
          view.result?.data?.gallery_identity?.prepared_folder_keys,
          folderDecisions(),
          [...removedProducts],
        );
      const galleryActive = ["queued", "running"].includes(
        currentGalleryJob?.status,
      );
      localButton.textContent = galleryAlreadyLoaded
        ? "确认文件夹并重新加载图片"
        : "确认文件夹并加载图片";
      localButton.disabled = galleryActive
        || globalAssetSelectionInFlight
        || (galleryAlreadyLoaded && !galleryNeedsReload);
      localSummary.textContent = galleryActive
        ? "本机正在按最新文件夹选择加载图片。"
        : galleryAlreadyLoaded && galleryNeedsReload
          ? "文件夹选择已变更；已排除文件夹的图片会立即隐藏，请重新加载以按最新范围补足候选。"
          : galleryAlreadyLoaded
            ? "当前候选图片与文件夹选择一致，无需重复加载。"
            : "确认文件夹后加载候选图片。";
    };
    localButton.addEventListener("click", () => {
      prepareLocalGallery(localButton);
    });
    updateLocalGalleryAction();
    localAction.append(localSummary, localButton);
    review.appendChild(localAction);
    content.appendChild(review);
  }

  function inferAssetMatchingStep(data, status = "") {
    if (["queued", "running"].includes(currentGalleryJob?.status)) {
      return "gallery_preparing";
    }
    if (status === "processing") return "gallery_preparing";
    const explicit = String(data?.workflow_step || "");
    if (["folder_review", "gallery_preparing", "image_selection"].includes(explicit)) {
      return explicit;
    }
    if (
      (Array.isArray(data?.asset_candidates) && data.asset_candidates.length)
      || (Array.isArray(data?.requirements) && data.requirements.length)
    ) return "image_selection";
    return "folder_review";
  }

  function renderAssetMatchGallery(view) {
    if (view.mode === "empty") return;
    const data = view.result?.data;
    const removedProducts = removedProductIdSet();
    const galleryComplete = inferAssetMatchingStep(data, uiState.serverStatus)
      === "image_selection"
      && !["queued", "running"].includes(currentGalleryJob?.status);
    if (galleryComplete) void hydrateSelectionPreflights();
    const folderCandidates = Array.isArray(data?.folder_candidates)
      ? data.folder_candidates.filter(
        (candidate) => candidate?.match_type !== "confirmed_alias"
          && !removedProducts.has(String(candidate.product_id || "")),
      )
      : [];
    const supportedFolderProducts = folderCandidates.length
      ? new Set(
        folderCandidates.map((candidate) => String(candidate.product_id || "")),
      )
      : null;
    const candidates = Array.isArray(data?.asset_candidates)
      ? data.asset_candidates.filter(
        (candidate) => candidate?.match_type !== "confirmed_alias"
          && !removedProducts.has(String(candidate.product_id || ""))
          && (
            supportedFolderProducts === null
            || supportedFolderProducts.has(String(candidate.product_id || ""))
          ),
      )
      : [];
    const requirements = Array.isArray(data?.requirements)
      ? data.requirements.filter(
        (requirement) => !removedProducts.has(
          String(requirement.product_id || ""),
        ) && (
          supportedFolderProducts === null
          || supportedFolderProducts.has(String(requirement.product_id || ""))
        ),
      )
      : [];
    if (!candidates.length && !requirements.length) return;
    const module = document.querySelector('[data-component="AssetMatchGallery"]');
    const content = module?.querySelector("[data-result-content]");
    if (!content) return;
    const resultSummary = content.querySelector(".result-data");
    if (resultSummary) {
      const heading = element(
        "strong",
        "",
        galleryComplete
          ? `已按实际扫描结果展示 ${requirements.length} 个商品、${candidates.length} 张候选图片。`
          : `候选正在渐进加载：当前展示 ${requirements.length} 个商品、${candidates.length} 张图片，其余仍在准备。`,
      );
      const action = element("section");
      action.append(
        element("strong", "", "下一步"),
        element(
          "p",
          "",
          galleryComplete
            ? "点击图片即可选择；系统会立即检查 1:1、3:4 预裁剪。全部已选图片检查完成后，提交将按每坑 3–9 张自动生成坑位草稿。"
            : "可以先浏览已完成的候选。全部图片准备完成后，系统会自动开放选择和提交。",
        ),
      );
      resultSummary.replaceChildren(heading, action);
    }

    const safety = element("div", "asset-safety");
    const remoteChecked = data.remote_dedupe_status === "checked";
    safety.dataset.status = remoteChecked ? "checked" : "pending";
    safety.append(
      element("strong", "", remoteChecked ? "远端去重已检查" : "远端去重未完成"),
      element(
        "span",
        "",
        remoteChecked
          ? "已排除上传记录中重复的图片。"
          : "暂无可用的历史上传记录；上传前请核对是否重复。",
      ),
    );
    content.appendChild(safety);
    const candidateProductIdsBySha = new Map();
    candidates.forEach((candidate) => {
      const fingerprint = String(candidate.sha256 || "");
      if (!fingerprint) return;
      if (!candidateProductIdsBySha.has(fingerprint)) {
        candidateProductIdsBySha.set(fingerprint, new Set());
      }
      candidateProductIdsBySha.get(fingerprint).add(
        String(candidate.product_id || ""),
      );
    });
    const globalSelectionSeed = [
      sessionId,
      String(data.gallery_job_id || ""),
      String(data.sampling_identity_sha256 || ""),
      String(data.gallery_identity?.folder_decisions_sha256 || ""),
    ].join("|");
    const globalSelectionPanel = element("section", "asset-global-selection");
    const globalSelectionCopy = element("div");
    globalSelectionCopy.append(
      element("strong", "", "为全部商品自动选图"),
      element(
        "span",
        "",
        "按每个空坑位最低 3 张补足；优先选择 3:4，不足时补充 1:1，素材仍不足则选择全部可用素材。",
      ),
    );
    const globalSelectionButton = element(
      "button",
      "button-primary",
      "一键为全部商品选图",
    );
    globalSelectionButton.type = "button";
    globalSelectionButton.disabled = !galleryComplete
      || globalAssetSelectionInFlight;
    if (globalAssetSelectionInFlight) {
      globalSelectionButton.setAttribute("aria-busy", "true");
      globalSelectionButton.textContent = "正在为全部商品选图…";
    }
    const globalSelectionStatus = element("p", "asset-global-selection-status");
    globalSelectionStatus.setAttribute("role", "status");
    if (globalAssetSelectionInFlight) {
      globalSelectionStatus.textContent = "正在为全部商品选图，请稍候…";
    }
    globalSelectionPanel.hidden = !galleryComplete;
    globalSelectionPanel.append(globalSelectionCopy, globalSelectionButton);
    content.append(globalSelectionPanel, globalSelectionStatus);
    const productNavigator = element("div", "product-jump-mount");
    const productTargets = [];
    const globalSelectionContexts = [];
    const globalSelectionLockState = new Map();
    const setGlobalSelectionControlsLocked = (locked) => {
      const controls = [
        ...content.querySelectorAll(
          ".folder-card, .local-gallery-action button, .asset-product-actions button, .asset-card input[type='checkbox'], .selected-asset-card button",
        ),
      ];
      if (locked) {
        controls.forEach((control) => {
          if (!globalSelectionLockState.has(control)) {
            globalSelectionLockState.set(control, {
              disabled: "disabled" in control ? control.disabled : null,
              ariaDisabled: control.getAttribute("aria-disabled"),
              tabIndex: control.getAttribute("tabindex"),
            });
          }
          if ("disabled" in control) control.disabled = true;
          control.setAttribute("aria-disabled", "true");
          control.setAttribute("tabindex", "-1");
        });
        return;
      }
      globalSelectionLockState.forEach((previous, control) => {
        if ("disabled" in control && previous.disabled !== null) {
          control.disabled = previous.disabled;
        }
        if (previous.ariaDisabled === null) {
          control.removeAttribute("aria-disabled");
        } else {
          control.setAttribute("aria-disabled", previous.ariaDisabled);
        }
        if (previous.tabIndex === null) {
          control.removeAttribute("tabindex");
        } else {
          control.setAttribute("tabindex", previous.tabIndex);
        }
      });
      globalSelectionLockState.clear();
    };
    const pendingSelectionIntents = new Set();
    const selectionIntentKey = (productId, assetId) => (
      `${String(productId || "")}\u0000${String(assetId || "")}`
    );
    const hasSelectionIntent = (productId, assetId) => (
      pendingSelectionIntents.has(selectionIntentKey(productId, assetId))
    );
    const setSelectionIntent = (productId, assetId, selected) => {
      const key = selectionIntentKey(productId, assetId);
      if (selected) pendingSelectionIntents.add(key);
      else pendingSelectionIntents.delete(key);
    };
    const cancelSelectionPreflightIfUnused = (assetId) => {
      const suffix = `\u0000${String(assetId || "")}`;
      const stillPending = [...pendingSelectionIntents].some(
        (key) => key.endsWith(suffix),
      );
      if (!stillPending) selectionPreflightScheduler.cancel(assetId);
    };
    const selectionAvailabilityRefreshers = new Map();
    const clearProductSelectionAvailability = (productId) => {
      selectionAvailabilityRefreshers.forEach((refreshers, sha256) => {
        refreshers.delete(String(productId));
        if (!refreshers.size) selectionAvailabilityRefreshers.delete(sha256);
      });
    };
    const registerSelectionAvailability = (
      productId,
      sha256,
      refresher,
    ) => {
      const fingerprint = String(sha256 || "");
      if (!fingerprint) return;
      if (!selectionAvailabilityRefreshers.has(fingerprint)) {
        selectionAvailabilityRefreshers.set(fingerprint, new Map());
      }
      selectionAvailabilityRefreshers.get(fingerprint).set(
        String(productId),
        refresher,
      );
    };
    const refreshSelectionAvailability = (sha256) => {
      selectionAvailabilityRefreshers.get(String(sha256 || ""))
        ?.forEach((refresher) => refresher());
    };
    const refreshAllSelectionAvailability = () => {
      selectionAvailabilityRefreshers.forEach((refreshers) => {
        refreshers.forEach((refresher) => refresher());
      });
    };
    content.appendChild(productNavigator);

    requirements.forEach((requirement) => {
      const productId = String(requirement.product_id || "");
      const productTitle = UiState.resolveProductTitle(
        data,
        productId,
        requirement.product_title,
      );
      const missingMaterials = Number(requirement.missing_materials || 0);
      const productFolders = folderCandidates.filter(
        (candidate) => String(candidate.product_id || "") === productId,
      );
      const rawProductCandidates = uniqueSortedCandidates(
        candidates
          .filter((candidate) => String(candidate.product_id) === productId)
          .map((candidate) => associateCandidateFolder(candidate, productFolders)),
      );
      const prepared = data.scan_summary?.per_product?.find(
        (item) => String(item.product_id || "") === productId,
      );
      const candidateLimit = Math.max(1, Number(data.candidate_limit || 100));
      const pageSize = Math.max(1, Number(data.page_size || 30));
      let pageCount = 1;
      let pageIndex = 0;
      const product = element("section", "asset-product");
      product.tabIndex = -1;
      const productTarget = {
        productId,
        productTitle,
        target: product,
        navigationStatus: "",
      };
      productTargets.push(productTarget);
      const heading = element("div", "asset-product-heading");
      const title = element("div");
      const candidateSummary = element("span");
      title.append(
        element("strong", "", productTitle || "商品名称未获取"),
        candidateSummary,
      );
      const preflightFilter = document.createElement("select");
      preflightFilter.className = "asset-preflight-filter";
      preflightFilter.setAttribute("aria-label", `${productTitle || productId} 图片状态筛选`);
      [
        ["all", "全部图片"],
        ["direct", "可直传"],
        ["croppable", "可裁剪"],
        ["needs_compression", "需要压缩"],
        ["crop_and_compress", "裁剪并压缩"],
        ["unusable", "不可使用"],
      ].forEach(([value, label]) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = label;
        preflightFilter.appendChild(option);
      });
      const headingActions = element("div", "asset-product-actions");
      headingActions.append(
        preflightFilter,
        removeProductButton(
          productId,
          productTitle,
          data,
        ),
      );
      heading.append(title, headingActions);
      product.appendChild(heading);

      const selectionSummary = element("p", "asset-selection-summary");
      product.appendChild(selectionSummary);
      const synchronizationNotice = element("p", "asset-warning");
      synchronizationNotice.setAttribute("role", "status");
      product.appendChild(synchronizationNotice);
      const coverageNotice = element("p", "asset-warning");
      coverageNotice.setAttribute("role", "status");
      const allocations = Array.isArray(prepared?.folder_allocations)
        ? prepared.folder_allocations
        : [];
      const zeroAllocations = allocations.filter(
        (item) => Number(item?.sampled_images || 0) === 0,
      );
      if (prepared && typeof prepared.complete_folder_coverage === "boolean") {
        if (!prepared.complete_folder_coverage) {
          const uncovered = Number(
            prepared.uncovered_folders || zeroAllocations.length,
          );
          const samplePaths = zeroAllocations
            .filter((item) => item?.zero_allocation_reason === "FOLDER_COVERAGE_LIMIT_EXCEEDED")
            .slice(0, 5)
            .map((item) => item.folder_path || item.folder_id)
            .filter(Boolean);
          coverageNotice.textContent = `每商品候选上限 ${candidateLimit} 张，无法覆盖全部非空文件夹；${uncovered} 个文件夹本次为 0 张${samplePaths.length ? `：${samplePaths.join("、")}` : ""}。`;
        } else if (zeroAllocations.length) {
          coverageNotice.textContent = `${zeroAllocations.length} 个采用文件夹为空或未贡献新的唯一图片，已保留 0 张分配记录。`;
        }
      } else {
        coverageNotice.textContent = "历史候选未记录文件夹覆盖审计；保留现有候选和已选图片，不会自动重新抽样。";
      }
      if (coverageNotice.textContent) product.appendChild(coverageNotice);
      const selectedSection = element("section", "selected-assets");
      const selectedToolbar = element("div", "selected-assets-toolbar");
      const selectedHeading = element("strong", "", "已选素材");
      const issueFilter = element("button", "button-secondary", "只看需处理素材");
      issueFilter.type = "button";
      issueFilter.hidden = true;
      issueFilter.setAttribute("aria-pressed", "false");
      selectedToolbar.append(selectedHeading, issueFilter);
      const duplicateWarning = element("p", "asset-warning");
      const selectedGrid = element("div", "selected-asset-grid");
      selectedSection.append(selectedToolbar, duplicateWarning, selectedGrid);
      product.appendChild(selectedSection);
      const paging = element("div", "asset-paging");
      const previousPage = element("button", "button-secondary", "上一批");
      previousPage.type = "button";
      const pageLabel = element("span", "asset-page-label");
      const nextPage = element("button", "button-secondary", "换一批");
      nextPage.type = "button";
      paging.append(previousPage, pageLabel, nextPage);
      product.appendChild(paging);
      const grid = element("div", "asset-grid");
      product.appendChild(grid);
      content.appendChild(product);

      const visibleCandidates = () => uniqueSortedCandidates(
        rawProductCandidates.filter((candidate) => {
          const folderId = String(
            candidate.resolved_folder_id || candidate.folder_id || "",
          );
          return !folderId
            || folderDecisionState(
              productId,
              folderId,
              folderCandidates.find(
                (folder) => String(folder.product_id || "") === productId
                  && String(folder.folder_id || "") === folderId,
              )?.decision,
            ) !== "rejected";
        }),
      );

      const updateSelectionSummary = () => {
        const guidance = UiState.assetSelectionGuidance(
          selectedAssetDecisions().filter(
            (item) => String(item.product_id) === productId,
          ),
          missingMaterials,
        );
        const preview = guidance.balancedCounts.length
          ? guidance.balancedCounts.join("+")
          : "尚不能形成完整坑位";
        const duplicateSuffix = guidance.duplicateCount
          ? `；${guidance.duplicateCount} 张重复素材不计入可用数量`
          : "";
        const navigationStatus = (
          `缺少篇数 ${missingMaterials}`
          + ` · 填满所有的坑位还差 ${guidance.fillAllMinimumShortage} 张`
        );
        productTarget.navigationStatus = navigationStatus;
        productTarget.updateNavigationStatus?.(navigationStatus);
        selectionSummary.textContent = guidance.usableUnique < 3
          ? `已选 ${guidance.selectedCount} 张、可用唯一 ${guidance.usableUnique} 张；还差 ${guidance.minimumShortage} 张才能提交。草稿仍可保存${duplicateSuffix}。`
          : `已选 ${guidance.selectedCount} 张、可用唯一 ${guidance.usableUnique} 张；预计创建 ${guidance.completeSlots} 个完整坑位（${preview}），提交后仍可人工调整${duplicateSuffix}。`;
      };

      const exactOrEstimatedRatios = (candidate, decision = null) => {
        const exact = selectionPreflightFor(candidate?.asset_id);
        const exactRatios = exact?.feasible_ratios;
        if (Array.isArray(exactRatios)) {
          return UiState.candidateFeasibleRatios({
            feasible_ratios: exactRatios,
          });
        }
        const decisionRatios = decision?.feasible_ratios;
        if (Array.isArray(decisionRatios) && decisionRatios.length) {
          return UiState.candidateFeasibleRatios({
            feasible_ratios: decisionRatios,
          });
        }
        return UiState.candidateFeasibleRatios(candidate);
      };

      const selectMinimumForProduct = async (reportProgress) => {
        const productCandidates = visibleCandidates();
        const currentDecisions = selectedAssetDecisions().filter(
          (item) => String(item.product_id || "") === productId,
        );
        const minimumTarget = Math.max(
          0,
          Math.floor(Number(missingMaterials) || 0) * 3,
        );

        const selectable = productCandidates.filter((candidate) => (
          candidateIsSelectable(candidate)
          && selectionPreflightFor(candidate.asset_id)?.status !== "blocked"
          && !UiState.assetSelectedByOtherProduct(
            selectedAssetDecisions(),
            productId,
            candidate.sha256,
          )
        ));
        const stableCandidates = UiState.stableGlobalAssetOrder(
          selectable,
          globalSelectionSeed,
          productId,
        );
        const stableIndex = new Map(
          stableCandidates.map((candidate, index) => [
            String(candidate.asset_id || ""),
            index,
          ]),
        );
        stableCandidates.sort((left, right) => {
          const leftShared = candidateProductIdsBySha.get(
            String(left.sha256 || ""),
          )?.size || 1;
          const rightShared = candidateProductIdsBySha.get(
            String(right.sha256 || ""),
          )?.size || 1;
          return leftShared - rightShared
            || (stableIndex.get(String(left.asset_id || "")) || 0)
              - (stableIndex.get(String(right.asset_id || "")) || 0);
        });
        const ratioReadyCandidates = stableCandidates.map((candidate) => ({
          candidate,
          feasible_ratios: exactOrEstimatedRatios(candidate),
        }));
        const prioritizedCandidates = UiState.prioritizeGlobalAssetCandidates(
          ratioReadyCandidates,
        ).map((item) => item.candidate);
        const selectedIds = new Set(
          currentDecisions.map((item) => String(item.asset_id || "")),
        );
        const remaining = prioritizedCandidates.filter(
          (candidate) => !selectedIds.has(String(candidate.asset_id || "")),
        );
        const availableCount = currentDecisions.length + remaining.length;
        const desiredCount = UiState.globalAssetSelectionTarget(
          missingMaterials,
          availableCount,
        );
        let selectedCount = currentDecisions.length;
        let checkedCount = 0;
        let failedCount = 0;
        const activePreflights = new Set();
        const launchPreflights = () => {
          while (
            currentStageId === "asset_matching"
            && globalAssetSelectionInFlight
            && activePreflights.size < 6
            && selectedCount + activePreflights.size < desiredCount
            && remaining.length
          ) {
            const candidate = remaining.shift();
            const assetId = String(candidate.asset_id || "");
            setSelectionIntent(productId, assetId, true);
            selectionPreflightCardRefreshers.get(assetId)?.();
            const entry = { candidate, settled: null, promise: null };
            entry.promise = runSelectionPreflight(candidate).then(
              (value) => {
                entry.settled = { status: "fulfilled", value };
                return entry;
              },
              (reason) => {
                entry.settled = { status: "rejected", reason };
                return entry;
              },
            );
            activePreflights.add(entry);
          }
        };
        launchPreflights();
        while (
          selectedCount < desiredCount
          && (activePreflights.size || remaining.length)
        ) {
          if (
            currentStageId !== "asset_matching"
            || !globalAssetSelectionInFlight
          ) break;
          if (!activePreflights.size) {
            launchPreflights();
            if (!activePreflights.size) break;
          }
          await Promise.race(
            [...activePreflights].map((entry) => entry.promise),
          );
          await Promise.resolve();
          const completed = [...activePreflights].filter(
            (entry) => entry.settled,
          );
          const newlySelected = [];
          completed.forEach((entry) => {
            activePreflights.delete(entry);
            const { candidate, settled } = entry;
            const assetId = String(candidate.asset_id || "");
            const assetSha256 = String(candidate.sha256 || "");
            checkedCount += 1;
            const stillDesired = hasSelectionIntent(productId, assetId);
            setSelectionIntent(productId, assetId, false);
            if (
              !stillDesired
              || settled.status !== "fulfilled"
              || !settled.value
              || settled.value.cancelled
              || settled.value.status === "cancelled"
              || settled.value.status === "blocked"
              || exactOrEstimatedRatios(candidate).length === 0
              || UiState.assetSelectedByOtherProduct(
                selectedAssetDecisions(),
                productId,
                assetSha256,
              )
              || selectedCount >= desiredCount
            ) {
              failedCount += settled.status === "rejected"
                || settled.value?.status === "blocked"
                || exactOrEstimatedRatios(candidate).length === 0
                ? 1
                : 0;
              cancelSelectionPreflightIfUnused(assetId);
            } else {
              selectedIds.add(assetId);
              selectedCount += 1;
              newlySelected.push(candidate);
            }
            refreshSelectionAvailability(assetSha256);
            selectionPreflightCardRefreshers.get(assetId)?.();
          });
          commitSelectedCandidateBatch(productId, newlySelected);
          selectedCount = selectedAssetDecisions().filter(
            (item) => String(item.product_id || "") === productId,
          ).length;
          updateSelectionSummary();
          reportProgress?.({
            productId,
            productTitle,
            minimumTarget,
            desiredCount,
            selectedCount,
            checkedCount,
          });
          launchPreflights();
        }
        if (currentStageId === "asset_matching") draw();
        return {
          productId,
          productTitle,
          minimumTarget,
          desiredCount,
          availableCount,
          initialSelectedCount: currentDecisions.length,
          selectedCount,
          checkedCount,
          failedCount,
          shortage: Math.max(0, minimumTarget - selectedCount),
          status: selectedCount >= minimumTarget
            ? "fulfilled"
            : "insufficient",
        };
      };

      const draw = () => {
        const productCandidates = visibleCandidates();
        const removedSelectionCount = pruneSelectedCandidates(
          productId,
          productCandidates,
        );
        if (removedSelectionCount) {
          synchronizationNotice.textContent = `已同步取消 ${removedSelectionCount} 张来自已排除文件夹的已选素材。`;
        }
        const filteredCandidates = preflightFilter.value === "all"
          ? productCandidates
          : productCandidates.filter(
            (candidate) => String(
              candidate.preflight_status || candidate.preflight?.status || "",
            ) === preflightFilter.value,
          );
        pageCount = Math.max(1, Math.ceil(filteredCandidates.length / pageSize));
        pageIndex = Math.min(pageIndex, pageCount - 1);
        candidateSummary.textContent = (
          `商品 ID ${productId} · 后台缺 ${missingMaterials} 篇`
          + ` · 当前可见 ${productCandidates.length} 张`
          + ` / 每商品检查上限 ${candidateLimit} 张`
          + ` · 每批显示 ${pageSize} 张`
        );
        const currentDecisions = selectedAssetDecisions();
        const previousForProduct = currentDecisions
          .filter((item) => String(item.product_id) === productId);
        const selectedIds = new Set(previousForProduct.map((item) => String(item.asset_id)));
        const displayedCandidates = filteredCandidates.slice(
          pageIndex * pageSize,
          (pageIndex + 1) * pageSize,
        );
        clearProductSelectionAvailability(productId);
        grid.replaceChildren();

        displayedCandidates.forEach((candidate) => {
          const renderGeneration = stageGeneration;
          const assetId = String(candidate.asset_id || "");
          const assetSha256 = String(candidate.sha256 || "");
          const selectionCheck = selectionPreflightFor(assetId);
          const selectionJob = selectionPreflightScheduler.get(assetId);
          const selectionKey = selectionIntentKey(productId, assetId);
          const checking = hasSelectionIntent(productId, assetId)
            && ["queued", "running"].includes(selectionJob?.state);
          const baseSelectable = galleryComplete && candidateIsSelectable(candidate)
            && selectionCheck?.status !== "blocked";
          const selectedByOtherProduct = () => UiState.assetSelectedByOtherProduct(
            selectedAssetDecisions(),
            productId,
            assetSha256,
          );
          const card = element("article", "asset-card");
          card.tabIndex = 0;
          card.setAttribute("role", "checkbox");
          card.dataset.selectionPreflightStatus = checking
            ? "checking"
            : selectionCheck?.status || "unchecked";
          if (selectedIds.has(assetId)) card.classList.add("is-selected");
          const image = document.createElement("img");
          image.loading = "lazy";
          image.alt = `${productTitle || productId} 候选图片`;
          image.src = apiPath(
            `/stages/asset_matching/assets/${encodeURIComponent(assetId)}`,
          );
          const meta = element("div", "asset-card-meta");
          const dimensions = candidate.width && candidate.height
            ? `${candidate.width}×${candidate.height}`
            : "尺寸不可读";
          const inspection = candidate.source_inspection || {};
          const sizeText = inspection.size_display || candidate.size_display || candidate.preflight?.size_display || "读取失败";
          const formatText = inspection.format || candidate.format || "未知";
          const ratioText = inspection.ratio_display || candidate.preflight?.original_ratio || "读取失败";
          const maxSizeText = candidate.max_size_display || candidate.preflight?.max_size_display || "未知";
          const minSizeText = candidate.preflight?.min_size_display || "200KB";
          const status = element("span", "asset-preflight-badge", preflightLabel(candidate));
          status.dataset.status = candidate.preflight_status || candidate.preflight?.status || "pending";
          meta.append(
            element("strong", "", candidate.source_system || "未知来源"),
            status,
            element("span", "", `原图尺寸 ${dimensions} · 原图比例 ${ratioText}`),
            element("span", "", resolutionSummary(candidate) || "推荐分辨率待检测"),
            element("span", "", `原图格式 ${formatText} · 文件 ${sizeText} · 允许范围 ${minSizeText}–${maxSizeText}`),
            element("span", "", `处理建议：${preflightLabel(candidate)}`),
            element(
              "span",
              candidateIsSelectable(candidate) ? "asset-warning" : "asset-hard-error",
              (candidate.preflight?.reason_messages || []).join("；"),
            ),
            element("small", "", candidate.source_path || ""),
          );
          const controls = element("div", "asset-card-controls");
          const select = document.createElement("input");
          select.type = "checkbox";
          select.checked = hasSelectionIntent(productId, assetId)
            || selectedIds.has(assetId);
          select.disabled = !baseSelectable;
          if (!galleryComplete) {
            controls.appendChild(
              element("span", "asset-warning", "候选仍在加载，完成后可选择"),
            );
          }
          const selectionFeedback = element("span", "asset-selection-check");
          controls.append(select, selectionFeedback);
          if (candidate.match_status === "needs_manual_confirmation") {
            controls.appendChild(element("span", "asset-warning", "名称候选来自已采用文件夹"));
          } else if (!candidate.preflight || !candidate.source_inspection) {
            controls.appendChild(element("span", "asset-warning", "历史候选缺少完整原图预检，请 Agent 重新准备第三阶段"));
          } else if (candidate.remote_duplicate) {
            controls.appendChild(element("span", "asset-warning", "与已上传素材重复"));
          } else if (candidate.validation_status !== "valid") {
            controls.appendChild(element("span", "asset-warning", "图片校验未通过"));
          } else if (
            (candidate.preflight_status || candidate.preflight?.status) === "needs_compression"
          ) {
            controls.appendChild(element("span", "asset-warning", "超过大小上限；下一阶段将生成任务本地压缩文件"));
          } else if (
            (candidate.preflight_status || candidate.preflight?.status) === "crop_and_compress"
          ) {
            controls.appendChild(element("span", "asset-warning", "需要人工裁剪并生成任务本地压缩文件"));
          } else if (
            (candidate.preflight_status || candidate.preflight?.status) === "croppable"
          ) {
            controls.appendChild(element("span", "asset-warning", "原始比例不是 3:4 或 1:1；可进入下一阶段人工裁剪"));
          }
          if (candidate.folder_link_status === "legacy_unlinked") {
            controls.appendChild(
              element("span", "asset-warning", "历史候选未关联文件夹"),
            );
          }
          card.append(image, meta, controls);
          grid.appendChild(card);

          const refreshSelectionCard = () => {
            const job = selectionPreflightScheduler.get(assetId);
            const result = selectionPreflightFor(assetId);
            const pendingSelection = hasSelectionIntent(productId, assetId);
            const isQueued = job?.state === "queued" && pendingSelection;
            const jobRunning = job?.state === "running";
            const isRunning = jobRunning && pendingSelection;
            const desired = pendingSelection || selectedIds.has(assetId);
            const duplicateElsewhere = selectedByOtherProduct();
            select.checked = desired;
            select.disabled = globalAssetSelectionInFlight || (
              !desired && (
                !baseSelectable
                || duplicateElsewhere
                || result?.status === "blocked"
              )
            );
            select.indeterminate = false;
            card.classList.toggle("is-selected", selectedIds.has(assetId));
            card.classList.toggle(
              "is-unselectable",
              galleryComplete && select.disabled,
            );
            card.setAttribute("aria-checked", String(desired));
            card.setAttribute("aria-disabled", String(select.disabled));
            card.dataset.selectionPreflightStatus = isQueued
              ? "queued"
              : isRunning && desired
                ? "checking"
                : jobRunning
                  ? "cancelled"
                  : result?.status || "unchecked";
            selectionFeedback.className = "asset-selection-check";
            if (duplicateElsewhere) {
              selectionFeedback.classList.add("asset-warning");
              selectionFeedback.textContent = desired
                ? "该图片已在其他商品中重复选中，请取消其中一处"
                : "该图片已被其他商品选用";
            } else if (isQueued) {
              selectionFeedback.classList.add("asset-warning");
              selectionFeedback.textContent = globalAssetSelectionInFlight
                ? "自动选图排队中"
                : "排队中，可再次点击取消";
            } else if (isRunning && desired) {
              selectionFeedback.classList.add("asset-warning");
              selectionFeedback.textContent = globalAssetSelectionInFlight
                ? "自动选图正在检查 1:1、3:4 预裁剪"
                : "正在检查 1:1、3:4 预裁剪，可再次点击取消";
            } else if (jobRunning) {
              selectionFeedback.classList.add("asset-warning");
              selectionFeedback.textContent = "已取消选择；后台结果仅用于缓存";
            } else if (result?.status === "passed") {
              selectionFeedback.classList.add("is-passed");
              selectionFeedback.textContent = `预裁剪通过：${result.feasible_ratios.join("、")}`;
            } else if (result?.status === "warning") {
              selectionFeedback.classList.add("asset-warning");
              selectionFeedback.textContent = result.message;
            } else if (result?.status === "blocked") {
              selectionFeedback.classList.add("is-blocked");
              selectionFeedback.textContent = result.message;
            } else {
              selectionFeedback.textContent = "";
            }
          };
          registerSelectionAvailability(
            productId,
            assetSha256,
            refreshSelectionCard,
          );
          selectionPreflightCardRefreshers.set(assetId, refreshSelectionCard);
          refreshSelectionCard();

          const applySelectionPreflight = (result) => {
            if (
              currentStageId !== "asset_matching"
              || renderGeneration !== stageGeneration
            ) return;
            if (!result || result.cancelled || result.status === "cancelled") {
              refreshSelectionCard();
              return;
            }
            const job = selectionPreflightScheduler.get(assetId);
            if (!hasSelectionIntent(productId, assetId)) {
              refreshSelectionCard();
              return;
            }
            const intentVersion = job?.intentVersion || 0;
            if (
              job
              && appliedSelectionPreflightIntents.get(selectionKey) === intentVersion
            ) return;
            if (job) {
              appliedSelectionPreflightIntents.set(selectionKey, intentVersion);
            }
            setSelectionIntent(productId, assetId, false);
            if (result.status === "blocked") {
              cancelSelectionPreflightIfUnused(assetId);
              selectedIds.delete(assetId);
              persistLicense(assetId, false, productId);
              persistSelectedCandidate(productId, candidate, false);
              actionMessage.textContent = result.message;
            } else if (selectedByOtherProduct()) {
              cancelSelectionPreflightIfUnused(assetId);
              selectedIds.delete(assetId);
              persistLicense(assetId, false, productId);
              persistSelectedCandidate(productId, candidate, false);
              actionMessage.textContent = "这张图片已被其他商品选用，请选择其他图片。";
            } else {
              selectedIds.add(assetId);
              persistLicense(assetId, true, productId);
              persistSelectedCandidate(productId, candidate, true);
              actionMessage.textContent = result.status === "warning"
                ? result.message
                : "图片预裁剪检查通过。";
            }
            refreshSelectionAvailability(assetSha256);
            refreshSelectionCard();
            updateSelectionSummary();
            renderSelected();
          };

          select.addEventListener("change", () => {
            if (globalAssetSelectionInFlight) {
              refreshSelectionCard();
              return;
            }
            if (select.checked) {
              if (selectedByOtherProduct()) {
                select.checked = false;
                actionMessage.textContent = "这张图片已被其他商品选用，请选择其他图片。";
                refreshSelectionCard();
                return;
              }
              setSelectionIntent(productId, assetId, true);
              actionMessage.textContent = "正在检查所选图片的 1:1、3:4 预裁剪…";
              const request = runSelectionPreflight(candidate);
              refreshSelectionCard();
              request.then(applySelectionPreflight).catch((error) => {
                if (
                  currentStageId !== "asset_matching"
                  || renderGeneration !== stageGeneration
                ) return;
                setSelectionIntent(productId, assetId, false);
                cancelSelectionPreflightIfUnused(assetId);
                selectedIds.delete(assetId);
                persistLicense(assetId, false, productId);
                actionMessage.textContent = error.userMessage || error.message;
                persistSelectedCandidate(productId, candidate, false);
                refreshSelectionAvailability(assetSha256);
                refreshSelectionCard();
                updateSelectionSummary();
                renderSelected();
              });
              return;
            }
            setSelectionIntent(productId, assetId, false);
            cancelSelectionPreflightIfUnused(assetId);
            selectedIds.delete(assetId);
            persistLicense(assetId, false, productId);
            persistSelectedCandidate(productId, candidate, false);
            refreshSelectionAvailability(assetSha256);
            refreshSelectionCard();
            updateSelectionSummary();
            renderSelected();
          });
          const toggleCardSelection = () => {
            if (globalAssetSelectionInFlight || select.disabled) return;
            select.checked = !select.checked;
            select.dispatchEvent(new Event("change"));
          };
          card.addEventListener("click", (event) => {
            if (event.target.closest("button, a, input, select, textarea, label")) return;
            toggleCardSelection();
          });
          card.addEventListener("keydown", (event) => {
            if (!["Enter", " "].includes(event.key)) return;
            event.preventDefault();
            toggleCardSelection();
          });
        });
        updateSelectionSummary();
        pageLabel.textContent = `第 ${pageIndex + 1} / ${pageCount} 批 · 本批 ${displayedCandidates.length} 张`;
        previousPage.disabled = pageIndex === 0;
        nextPage.disabled = pageIndex >= pageCount - 1;
        renderSelected();

        function renderSelected() {
          const latestDecisions = selectedAssetDecisions()
            .filter((item) => String(item.product_id) === productId);
          const latestIds = new Set(
            latestDecisions.map((item) => String(item.asset_id)),
          );
          const selectedCandidates = productCandidates.filter(
            (candidate) => latestIds.has(String(candidate.asset_id)),
          );
          const validationItems = Array.isArray(selectedAssetValidation?.items)
            ? selectedAssetValidation.items.filter(
              (item) => String(item.product_id || "") === productId,
            )
            : [];
          const validationByAsset = new Map(
            validationItems.map((item) => [String(item.asset_id || ""), item]),
          );
          const issueCount = validationItems.filter(
            (item) => ["blocked", "warning"].includes(item.severity),
          ).length;
          selectedHeading.textContent = issueCount
            ? `已选素材 · ${selectedCandidates.length} 张 · 需处理 ${issueCount} 张`
            : `已选素材 · ${selectedCandidates.length} 张`;
          issueFilter.hidden = issueCount === 0;
          if (issueCount === 0) {
            issueFilter.setAttribute("aria-pressed", "false");
            issueFilter.textContent = "只看需处理素材";
          }
          selectedGrid.replaceChildren();
          const fingerprintCounts = new Map();
          latestDecisions.forEach((item) => {
            const fingerprint = String(item.sha256 || "");
            if (fingerprint) {
              fingerprintCounts.set(
                fingerprint,
                (fingerprintCounts.get(fingerprint) || 0) + 1,
              );
            }
          });
          const duplicateCount = [...fingerprintCounts.values()]
            .filter((count) => count > 1)
            .reduce((total, count) => total + count - 1, 0);
          duplicateWarning.textContent = duplicateCount
            ? `发现 ${duplicateCount} 张完全重复图片，请取消重复项。`
            : "";
          selectedCandidates
            .filter((candidate) => (
              issueFilter.getAttribute("aria-pressed") !== "true"
              || ["blocked", "warning"].includes(
                validationByAsset.get(String(candidate.asset_id))?.severity,
              )
            ))
            .forEach((candidate) => {
            const item = element("article", "selected-asset-card");
            const validation = validationByAsset.get(String(candidate.asset_id));
            const livePreflight = selectionPreflightFor(candidate.asset_id);
            if (validation && validation.severity !== "ok") {
              item.dataset.validationSeverity = validation.severity;
            } else if (livePreflight?.status === "blocked") {
              item.dataset.validationSeverity = "blocked";
            } else if (livePreflight?.status === "warning") {
              item.dataset.validationSeverity = "warning";
            }
            const image = document.createElement("img");
            image.loading = "lazy";
            image.alt = `${productTitle || productId} 已选图片`;
            image.src = apiPath(
              `/stages/asset_matching/assets/${encodeURIComponent(candidate.asset_id)}`,
            );
            const remove = element("button", "button-secondary", "取消选择");
            remove.type = "button";
            remove.disabled = globalAssetSelectionInFlight;
            remove.addEventListener("click", () => {
              if (globalAssetSelectionInFlight) return;
              const assetId = String(candidate.asset_id);
              setSelectionIntent(productId, assetId, false);
              cancelSelectionPreflightIfUnused(assetId);
              selectedIds.delete(assetId);
              persistLicense(assetId, false, productId);
              persistSelectedCandidate(productId, candidate, false);
              selectionPreflightCardRefreshers.get(assetId)?.();
              refreshSelectionAvailability(candidate.sha256);
              updateSelectionSummary();
              renderSelected();
            });
            item.append(image, element("small", "", candidate.source_system || "未知来源"));
            if (validation && validation.severity !== "ok") {
              const validationMessage = element(
                "p",
                "selected-asset-validation",
                validation.message,
              );
              validationMessage.setAttribute("role", "alert");
              item.appendChild(validationMessage);
            } else if (livePreflight?.status === "blocked") {
              const validationMessage = element(
                "p",
                "selected-asset-validation",
                livePreflight.message,
              );
              validationMessage.setAttribute("role", "alert");
              item.appendChild(validationMessage);
            } else if (livePreflight?.status === "warning") {
              item.appendChild(element(
                "p",
                "selected-asset-validation",
                livePreflight.message,
              ));
            }
            item.appendChild(remove);
            selectedGrid.appendChild(item);
          });
        }
      };

      previousPage.addEventListener("click", () => {
        if (pageIndex <= 0) return;
        pageIndex -= 1;
        draw();
      });
      nextPage.addEventListener("click", () => {
        if (pageIndex >= pageCount - 1) return;
        pageIndex += 1;
        draw();
      });
      content.addEventListener("folder-decision-changed", (event) => {
        if (String(event.detail?.productId || "") !== productId) return;
        synchronizationNotice.textContent = "";
        draw();
      });
      preflightFilter.addEventListener("change", () => {
        pageIndex = 0;
        draw();
      });
      issueFilter.addEventListener("click", () => {
        const active = issueFilter.getAttribute("aria-pressed") === "true";
        issueFilter.setAttribute("aria-pressed", String(!active));
        issueFilter.textContent = active ? "只看需处理素材" : "查看全部已选素材";
        renderSelected();
      });
      globalSelectionContexts.push({
        productId,
        productTitle,
        priority: () => (
          visibleCandidates().filter(candidateIsSelectable).length
          - Math.max(0, Math.floor(Number(missingMaterials) || 0) * 3)
        ),
        run: selectMinimumForProduct,
        redraw: draw,
      });
      draw();
    });
    globalSelectionButton.addEventListener("click", async () => {
      if (!galleryComplete || globalAssetSelectionInFlight) return;
      const renderGeneration = stageGeneration;
      globalAssetSelectionInFlight = true;
      window.clearTimeout(autoSaveTimer);
      globalSelectionButton.disabled = true;
      globalSelectionButton.setAttribute("aria-busy", "true");
      globalSelectionButton.textContent = "正在为全部商品选图…";
      globalSelectionStatus.textContent = "正在准备自动选图…";
      actionMessage.textContent = globalSelectionStatus.textContent;
      const outcomes = [];
      const orderedContexts = [...globalSelectionContexts].sort(
        (left, right) => left.priority() - right.priority()
          || left.productId.localeCompare(right.productId, "zh-CN"),
      );
      try {
        await paintBusyState();
        if (
          currentStageId !== "asset_matching"
          || renderGeneration !== stageGeneration
        ) return;
        setGlobalSelectionControlsLocked(true);
        refreshAllSelectionAvailability();
        renderStatus();
        for (let index = 0; index < orderedContexts.length; index += 1) {
          if (
            currentStageId !== "asset_matching"
            || renderGeneration !== stageGeneration
          ) break;
          const context = orderedContexts[index];
          globalSelectionStatus.textContent = (
            `正在处理第 ${index + 1}/${orderedContexts.length} 个商品：`
            + `${context.productTitle || context.productId}`
          );
          const outcome = await context.run((progress) => {
            globalSelectionStatus.textContent = (
              `正在处理第 ${index + 1}/${orderedContexts.length} 个商品：`
              + `${progress.productTitle || progress.productId}`
              + `，已选 ${progress.selectedCount}/${progress.minimumTarget} 张`
              + `，已检查 ${progress.checkedCount} 张`
            );
          });
          outcomes.push(outcome);
        }
        if (
          currentStageId === "asset_matching"
          && renderGeneration === stageGeneration
        ) {
          const fulfilledCount = outcomes.filter(
            (item) => item.status === "fulfilled",
          ).length;
          const insufficientCount = outcomes.filter(
            (item) => item.status === "insufficient",
          ).length;
          const addedCount = outcomes.reduce(
            (total, item) => total + Math.max(
              0,
              Number(item.selectedCount || 0)
                - Number(item.initialSelectedCount || 0),
            ),
            0,
          );
          const notices = [
            `已为 ${outcomes.length} 个商品完成自动选图，本次新增 ${addedCount} 张素材`,
            `${fulfilledCount} 个商品已满足全部空坑位`,
          ];
          if (insufficientCount) {
            notices.push(`${insufficientCount} 个商品素材不足，已选择全部可用素材`);
          }
          globalSelectionStatus.textContent = `${notices.join("；")}。`;
          actionMessage.textContent = globalSelectionStatus.textContent;
        }
      } catch (error) {
        if (
          currentStageId === "asset_matching"
          && renderGeneration === stageGeneration
        ) {
          globalSelectionStatus.textContent = (
            error.userMessage || error.message || "一键选图未完成，请重试。"
          );
          actionMessage.textContent = globalSelectionStatus.textContent;
        }
      } finally {
        globalAssetSelectionInFlight = false;
        setGlobalSelectionControlsLocked(false);
        globalSelectionButton.removeAttribute("aria-busy");
        if (
          currentStageId === "asset_matching"
          && renderGeneration === stageGeneration
        ) {
          globalSelectionContexts.forEach((context) => context.redraw());
          globalSelectionButton.disabled = false;
          globalSelectionButton.textContent = "一键为全部商品选图";
          refreshAllSelectionAvailability();
          renderStatus();
          if (uiState.dirty) scheduleAutoSave();
        }
      }
    });
    renderProductNavigator(productNavigator, productTargets);
    if (galleryComplete) {
      const focusIdentity = [
        sessionId,
        String(data.gallery_identity?.folder_decisions_sha256 || ""),
        requirements.map((item) => String(item.product_id || "")).join(","),
        candidates.length,
      ].join("|");
      if (galleryAutoFocusedFor !== focusIdentity) {
        galleryAutoFocusedFor = focusIdentity;
        window.requestAnimationFrame(() => {
          globalSelectionPanel.scrollIntoView({
            behavior: "smooth",
            block: "start",
          });
        });
      }
    }
  }

  function renderImageReview(view) {
    if (view.mode === "empty") return;
    const data = view.result?.data;
    const assets = Array.isArray(data?.assets) ? data.assets : [];
    const module = document.querySelector('[data-component="CropDecision"]');
    const content = module?.querySelector("[data-result-content]");
    if (!content || !assets.length || data?.stale) return;
    const saved = new Map(
      readJsonListControl("decisions")
        .filter((item) => item?.asset_id)
        .map((item) => [String(item.asset_id), item]),
    );
    const working = new Map();

    const initialState = (asset) => {
      const existing = saved.get(String(asset.asset_id));
      const ratios = Object.keys(asset.crop_options || {});
      const matching = asset.preflight?.matching_ratios || [];
      const boxes = {};
      ratios.forEach((ratio) => {
        boxes[ratio] = {
          ...(asset.crop_options[ratio]?.normalized || {}),
          ...(existing?.crop_candidates?.[ratio]?.normalized
            || existing?.crop_candidates?.[ratio]
            || {}),
          ...(existing?.target_ratio === ratio
            ? existing.crop_box?.normalized || existing.crop_box || {}
            : {}),
        };
      });
      let action = existing?.action;
      if (!action) {
        if (asset.status === "blocked") action = "excluded";
        else action = "candidate_only";
      }
      let targetRatio = existing?.target_ratio || matching[0] || ratios[0] || "";
      if (action === "direct" && !matching.includes(targetRatio)) {
        targetRatio = matching[0] || "";
      }
      return { action, targetRatio, boxes, output: existing?.output || null };
    };
    assets.forEach((asset) => working.set(String(asset.asset_id), initialState(asset)));

    const persist = (notify = false) => {
      const decisions = assets.map((asset) => {
        const state = working.get(String(asset.asset_id));
        const decision = {
          asset_id: String(asset.asset_id),
          action: state.action,
        };
        if (state.action === "candidate_only") {
          decision.crop_candidates = state.boxes;
        }
        return decision;
      });
      writeJsonListControl("decisions", decisions, { notify });
    };

    const overview = element("div", "image-review-overview");
    overview.append(
      element("strong", "", `第三阶段已选 ${data.selected_count || assets.length} 张`),
      element("span", "", `可审查 ${data.reviewable_count || 0} · 阻断 ${data.blocked_count || 0} · 重复 ${data.duplicate_count || 0}`),
      element("span", "", "本阶段只保存适用性和两种比例的候选裁剪框；正式裁剪、压缩在坑位比例确认后执行。"),
    );
    content.appendChild(overview);
    const list = element("div", "image-review-list");
    content.appendChild(list);

    assets.forEach((asset) => {
      const assetId = String(asset.asset_id);
      const state = working.get(assetId);
      const card = element("article", "image-review-card");
      card.dataset.status = asset.status || "pending";
      const sourcePane = element("section", "crop-source-pane");
      const sourceHeading = element("strong", "", "原图");
      const sourceFrame = element("div", "crop-source-frame");
      if (asset.width && asset.height) {
        sourceFrame.style.aspectRatio = `${asset.width} / ${asset.height}`;
      }
      const sourceImage = document.createElement("img");
      sourceImage.alt = `${asset.product_title || asset.product_id} 原图`;
      sourceImage.src = apiPath(
        `/stages/image_review/assets/${encodeURIComponent(assetId)}`,
      );
      const overlay = element("div", "crop-overlay");
      overlay.tabIndex = 0;
      overlay.setAttribute("role", "application");
      overlay.setAttribute(
        "aria-label",
        `${assignment.slot_id} 裁剪框；方向键移动，Shift 加方向键缩放`,
      );
      overlay.setAttribute(
        "aria-keyshortcuts",
        "ArrowUp ArrowDown ArrowLeft ArrowRight",
      );
      const resizeHandle = element("span", "crop-resize-handle");
      overlay.appendChild(resizeHandle);
      sourceFrame.append(sourceImage, overlay);
      sourcePane.append(sourceHeading, sourceFrame);

      const editor = element("section", "crop-editor");
      editor.append(
        element("strong", "", asset.product_title || `商品 ${asset.product_id}`),
        element("span", "", `原图尺寸 ${asset.width || "?"}×${asset.height || "?"} · 原图比例 ${asset.source_inspection?.ratio_display || asset.preflight?.original_ratio || "读取失败"}`),
        element("span", "", `原图文件 ${asset.preflight?.size_display || "读取失败"} · ${asset.source_inspection?.format || ""}`),
        element("span", "", `状态：${preflightLabel({ preflight: asset.preflight })}`),
      );
      const resolution = element("div", "crop-resolution-list");
      Object.values(asset.preflight?.resolution_checks || {}).forEach((check) => {
        resolution.appendChild(element(
          "span",
          check.status === "meets_or_exceeds" ? "is-ok" : "is-warning",
          `${check.target_ratio} 最大裁剪 ${check.max_crop_width}×${check.max_crop_height} · ${check.minimum_status === "meets_minimum" ? "满足最小尺寸" : "低于最小尺寸"} · ${check.status === "meets_or_exceeds" ? "达到推荐" : "低于推荐"}`,
        ));
      });
      editor.appendChild(resolution);

      const actionLabel = element("label", "crop-control-label", "候选决定");
      const actionSelect = document.createElement("select");
      [
        ["candidate_only", "进入坑位候选"],
        ["excluded", "排除"],
      ].forEach(([value, label]) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = label;
        actionSelect.appendChild(option);
      });
      actionSelect.value = state.action;
      actionSelect.disabled = asset.status === "blocked";
      actionLabel.appendChild(actionSelect);
      editor.appendChild(actionLabel);

      const ratioLabel = element("label", "crop-control-label", "检查比例（仅预览）");
      const ratioSelect = document.createElement("select");
      Object.keys(asset.crop_options || {}).forEach((ratio) => {
        const option = document.createElement("option");
        option.value = ratio;
        option.textContent = ratio;
        ratioSelect.appendChild(option);
      });
      ratioSelect.value = state.targetRatio;
      ratioLabel.appendChild(ratioSelect);
      editor.appendChild(ratioLabel);

      const previewHeading = element("strong", "", "裁剪预览");
      const previewCanvas = document.createElement("canvas");
      previewCanvas.className = "crop-preview-canvas";
      previewCanvas.width = 360;
      previewCanvas.height = 360;
      const outputSize = element("span", "crop-output-size");
      editor.append(previewHeading, previewCanvas, outputSize);
      const reasons = element(
        "small",
        "crop-reasons",
        (asset.reason_codes || []).join("、"),
      );
      editor.appendChild(reasons);
      const suitabilityNotice = element("section", "processed-output-summary");
      suitabilityNotice.append(
        element("strong", "", "正式处理尚未开始"),
        element("span", "", "第五阶段先编排坑位并确认唯一比例，再按坑位生成裁剪或压缩输出。"),
      );
      editor.appendChild(suitabilityNotice);
      card.append(sourcePane, editor);
      list.appendChild(card);

      const clamp = (value, minimum, maximum) => Math.min(
        maximum,
        Math.max(minimum, value),
      );
      const currentBox = () => state.boxes[state.targetRatio];
      const update = (notify = false) => {
        const cropMode = state.action === "candidate_only";
        ratioSelect.disabled = state.action !== "candidate_only";
        overlay.hidden = !cropMode || !currentBox();
        sourceFrame.classList.toggle("is-cropping", cropMode);
        if (currentBox()) {
          const box = currentBox();
          overlay.style.left = `${box.x * 100}%`;
          overlay.style.top = `${box.y * 100}%`;
          overlay.style.width = `${box.width * 100}%`;
          overlay.style.height = `${box.height * 100}%`;
          const outputWidth = Math.round(box.width * Number(asset.width || 0));
          const outputHeight = Math.round(box.height * Number(asset.height || 0));
          outputSize.textContent = `输出区域 ${outputWidth}×${outputHeight} · 固定 ${state.targetRatio}`;
          if (sourceImage.complete && sourceImage.naturalWidth) {
            const context = previewCanvas.getContext("2d");
            const sourceX = box.x * sourceImage.naturalWidth;
            const sourceY = box.y * sourceImage.naturalHeight;
            const sourceWidth = box.width * sourceImage.naturalWidth;
            const sourceHeight = box.height * sourceImage.naturalHeight;
            const ratio = sourceWidth / sourceHeight;
            previewCanvas.width = ratio >= 1 ? 360 : Math.round(360 * ratio);
            previewCanvas.height = ratio >= 1 ? Math.round(360 / ratio) : 360;
            context.clearRect(0, 0, previewCanvas.width, previewCanvas.height);
            context.drawImage(
              sourceImage,
              sourceX,
              sourceY,
              sourceWidth,
              sourceHeight,
              0,
              0,
              previewCanvas.width,
              previewCanvas.height,
            );
          }
        } else {
          outputSize.textContent = "";
        }
        previewCanvas.hidden = !cropMode;
        previewHeading.hidden = !cropMode;
        outputSize.hidden = !cropMode;
        persist(notify);
      };

      const matching = asset.preflight?.matching_ratios || [];
      actionSelect.addEventListener("change", () => {
        state.action = actionSelect.value;
        update(true);
      });
      ratioSelect.addEventListener("change", () => {
        state.targetRatio = ratioSelect.value;
        update(true);
      });
      sourceImage.addEventListener("load", () => update(false));

      const beginPointer = (event, mode) => {
        if (state.action !== "candidate_only" || !currentBox()) return;
        event.preventDefault();
        const startBox = { ...currentBox() };
        const startX = event.clientX;
        const startY = event.clientY;
        const rect = sourceFrame.getBoundingClientRect();
        const ratioValue = state.targetRatio === "1:1" ? 1 : 0.75;
        const normalizedRatio = ratioValue * Number(asset.height) / Number(asset.width);
        const move = (pointerEvent) => {
          const dx = (pointerEvent.clientX - startX) / rect.width;
          const dy = (pointerEvent.clientY - startY) / rect.height;
          if (mode === "move") {
            currentBox().x = clamp(startBox.x + dx, 0, 1 - startBox.width);
            currentBox().y = clamp(startBox.y + dy, 0, 1 - startBox.height);
          } else {
            const maximumWidth = Math.min(
              1 - startBox.x,
              (1 - startBox.y) * normalizedRatio,
            );
            const width = clamp(startBox.width + dx, 0.08, maximumWidth);
            currentBox().width = width;
            currentBox().height = width / normalizedRatio;
          }
          update(true);
        };
        const stop = () => {
          window.removeEventListener("pointermove", move);
          window.removeEventListener("pointerup", stop);
          invalidateCropPreflight();
        };
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", stop, { once: true });
      };
      overlay.addEventListener("pointerdown", (event) => {
        if (event.target === resizeHandle) return;
        beginPointer(event, "move");
      });
      resizeHandle.addEventListener("pointerdown", (event) => {
        event.stopPropagation();
        beginPointer(event, "resize");
      });
      update();
    });
    persist();
  }

  function renderSlotBoard(view) {
    if (view.mode === "empty") return;
    const data = view.result?.data;
    const products = Array.isArray(data?.products) ? data.products : [];
    const module = document.querySelector('[data-component="SlotBoard"]');
    const content = module?.querySelector("[data-result-content]");
    if (!content || !products.length || data?.stale) return;
    const copyModule = document.querySelector('[data-component="CopyEditor"]');
    if (copyModule) copyModule.hidden = true;
    handoffActions?.classList.add("is-stage-managed");
    if (actionMessage) {
      actionMessage.textContent = "请使用页面中的主按钮继续；修改会自动保存。";
    }
    content.replaceChildren();
    const blockingReasons = Array.isArray(view.result?.blocking_reasons)
      ? view.result.blocking_reasons
      : [];
    if (blockingReasons.length) {
      const blockingPanel = element("section", "slot-blocking-summary");
      const reasons = document.createElement("ul");
      blockingReasons.forEach((reason) => {
        reasons.appendChild(element("li", "", String(reason)));
      });
      blockingPanel.append(
        element("strong", "", "上传前检查发现需要修改的内容"),
        reasons,
        element(
          "p",
          "",
          view.result?.next_action
            || "请完成修改后重新确认标题与描述。",
        ),
      );
      content.appendChild(blockingPanel);
    }
    const saved = readJsonListControl("slot_assignments");
    const stateByProduct = new Map();
    let currentPlanRevision = 0;
    let currentPlanConfirmed = false;
    let slotPlanDirty = false;
    let processedOutputs = null;
    let invalidateProcessedOutputs = () => {
      processedOutputs = null;
    };
    let invalidateCropPreflight = () => {};
    const cropParameters = {};
    const candidatePageByProduct = new Map();
    products.forEach((product) => {
      const productId = String(product.product_id || "");
      let assignments = saved
        .filter((item) => String(item.product_id || "") === productId)
        .map((item) => ({
          ...item,
          slot_id: String(item.slot_id || ""),
          product_id: productId,
          target_ratio: String(item.target_ratio || ""),
          plan_source: String(item.plan_source || "manual"),
          asset_ids: Array.isArray(item.asset_ids)
            ? item.asset_ids.map(String)
            : [],
        }));
      if (!assignments.length) {
        assignments = [];
      }
      stateByProduct.set(productId, assignments);
    });
    const persist = (notify = false) => {
      if (notify) {
        slotPlanDirty = true;
        currentPlanConfirmed = false;
        invalidateCropPreflight();
      }
      writeJsonListControl(
        "slot_assignments",
        [...stateByProduct.values()].flat(),
        { notify },
      );
    };
    let workflowLoading = null;
    const applyCurrentPlan = (plan) => {
      if (!plan || !Array.isArray(plan.slot_assignments)) return;
      products.forEach((product) => {
        const productId = String(product.product_id || "");
        const assignments = plan.slot_assignments
          .filter((item) => String(item.product_id || "") === productId)
          .map((item) => ({
            ...item,
            slot_id: String(item.slot_id || ""),
            product_id: productId,
            target_ratio: String(item.target_ratio || ""),
            asset_ids: Array.isArray(item.asset_ids)
              ? item.asset_ids.map(String)
              : [],
          }));
        stateByProduct.set(productId, assignments);
      });
      currentPlanRevision = Number(plan.plan_revision || 0);
      currentPlanConfirmed = plan.confirmed === true;
      slotPlanDirty = false;
      const page = twoPageWorkflow
        ? UiState.twoStepFifthStagePage(plan.workflow_state)
        : UiState.fifthStagePage(plan.workflow_state);
      maxUnlockedPage = Math.max(maxUnlockedPage, pageOrder.indexOf(page));
      setSubpage(page);
      persist();
      draw();
      if (page === "process") {
        processPanel.hidden = plan.confirmed !== true;
        if (plan.confirmed === true) renderProcessingPage();
      } else if (
        page === "copy"
        && processedOutputs?.workflow_state === "outputs_ready"
      ) {
        renderCopyEditor(processedOutputs);
      }
    };
    const confirmCurrentSlotPlan = async () => {
      const confirmed = await fetchJson(
        apiPath("/stages/slots_copy/current-slot-plan/confirm"),
        {
          method: "POST",
          body: JSON.stringify({ plan_revision: currentPlanRevision }),
        },
      );
      currentPlanRevision = Number(
        confirmed.current_slot_plan?.plan_revision || currentPlanRevision,
      );
      currentPlanConfirmed = confirmed.current_slot_plan?.confirmed === true;
      return confirmed.current_slot_plan;
    };
    const wizard = element("nav", "slot-workflow-steps");
    const twoPageWorkflow = Boolean(
      data.deterministic_plan || data.two_page_workflow,
    );
    const pageOrder = twoPageWorkflow
      ? ["process", "copy"]
      : ["compose", "process", "copy"];
    const pageLabels = {
      compose: "候选素材与坑位编排",
      process: "图片裁剪与压缩",
      copy: "AI 标题与描述",
    };
    const rememberedSubpage = pageOrder.includes(slotsCopyActiveSubpage)
      ? slotsCopyActiveSubpage
      : "";
    let activeSubpage = rememberedSubpage || pageOrder[0];
    let maxUnlockedPage = Math.max(0, pageOrder.indexOf(activeSubpage));
    const subpages = {};
    const setSubpage = (page, { userRequested = false } = {}) => {
      const requestedIndex = pageOrder.indexOf(page);
      if (requestedIndex < 0) return;
      if (userRequested && requestedIndex > maxUnlockedPage) return;
      if (
        userRequested
        && localCopyRequestInFlight
        && page !== "copy"
      ) return;
      activeSubpage = page;
      slotsCopyActiveSubpage = page;
      if (workflowLoading) workflowLoading.hidden = true;
      wizard.hidden = false;
      Object.entries(subpages).forEach(([name, panel]) => {
        panel.hidden = name !== page;
      });
      [...wizard.children].forEach((item, index) => {
        item.dataset.active = index === requestedIndex ? "true" : "false";
        item.dataset.complete = index < maxUnlockedPage ? "true" : "false";
        item.disabled = index > maxUnlockedPage
          || (
            localCopyRequestInFlight
            && pageOrder[index] !== "copy"
          );
      });
    };
    pageOrder.forEach((page, index) => {
      const button = element(
        "button",
        "slot-workflow-step",
        `${index + 1}. ${pageLabels[page]}`,
      );
      button.type = "button";
      button.addEventListener("click", () => setSubpage(
        page,
        { userRequested: true },
      ));
      wizard.appendChild(button);
    });
    [...wizard.children].forEach((item, index) => {
      item.dataset.active = pageOrder[index] === activeSubpage ? "true" : "false";
      item.disabled = index > maxUnlockedPage;
    });
    wizard.hidden = !rememberedSubpage;
    content.appendChild(wizard);
    workflowLoading = element("div", "empty-state slot-workflow-loading");
    workflowLoading.dataset.emptyState = "正在恢复当前处理进度";
    workflowLoading.setAttribute("aria-live", "polite");
    workflowLoading.hidden = Boolean(rememberedSubpage);
    workflowLoading.append(
      element("span", "", "◎"),
      element("strong", "", "正在恢复当前处理进度…"),
      element("p", "", "页面会保留正在进行的步骤，恢复后继续显示当前进度。"),
    );
    content.appendChild(workflowLoading);
    pageOrder.forEach((page) => {
      const panel = element("section", "slot-subpage");
      panel.dataset.slotSubpage = page;
      panel.hidden = !rememberedSubpage || page !== activeSubpage;
      subpages[page] = panel;
      content.appendChild(panel);
    });
    const overview = element("div", "slot-board-overview");
    const agentPanel = element("section", "processed-output-summary");
    const modeHelp = element(
      "span",
      "",
      "可以由 Codex AI 直接编排，也可以人工添加坑位。两种方式共用一份当前草稿，AI 草稿仍可继续人工增删和调序。",
    );
    const agentStatus = element("span", "", "正在检查 AI 分析与编排任务…");
    const agentButton = element("button", "button-secondary", "AI 编排坑位");
    agentButton.type = "button";
    const agentProposalList = element("div", "slot-list");
    const recoveryPrompt = document.createElement("textarea");
    recoveryPrompt.readOnly = true;
    recoveryPrompt.hidden = true;
    recoveryPrompt.setAttribute("aria-label", "Agent 恢复提示词");
    const copyPrompt = element("button", "button-secondary", "复制恢复提示词");
    copyPrompt.type = "button";
    copyPrompt.hidden = true;
    const agentTechnical = document.createElement("details");
    agentTechnical.className = "slot-technical-details";
    agentTechnical.hidden = true;
    const agentTechnicalSummary = element("summary", "", "技术详情与恢复");
    const agentRequestId = element("code", "", "");
    agentTechnical.append(
      agentTechnicalSummary,
      agentRequestId,
      recoveryPrompt,
      copyPrompt,
    );
    const renderAgentResponse = (detail) => {
      agentProposalList.replaceChildren();
      const request = detail?.request;
      const combinedPlan = detail?.response?.result?.slot_plan || [];
      const proposals = detail?.response?.result?.proposals || [];
      if (!request) return;
      if (request.kind === "slot_plan_with_analysis") {
        const cache = request.analysis_cache || {};
        agentProposalList.append(
          element("span", "", `缓存命中 ${cache.hit_count || 0} 张 · 新分析 ${cache.pending_count || 0} 张 · AI 坑位 ${combinedPlan.length} 个。合法结果已自动载入唯一当前草稿。`),
          element(
            "small",
            "",
            "仅载入通过服务端硬校验的 AI 结果；失败商品保持原草稿或空状态，不会自动切换规则方案。",
          ),
        );
        fetchJson(apiPath("/stages/slots_copy/current-slot-plan"))
          .then((payload) => applyCurrentPlan(payload.current_slot_plan))
          .catch(() => {});
        return;
      }
      proposals.forEach((proposal, proposalIndex) => {
        const card = element("article", "slot-card");
        const ordered = proposal.ordered_asset_ids || [];
        const currentAssignments = stateByProduct.get(
          String(proposal.product_id),
        ) || [];
        const current = currentAssignments[0];
        const difference = current
          ? `与当前草稿差异：比例 ${current.target_ratio || "未定"} → ${proposal.target_ratio}；图片 ${current.asset_ids?.length || 0} → ${ordered.length} 张。采用会替换该商品当前坑位草稿。`
          : `与当前草稿差异：新增 ${proposal.target_ratio} 坑位，共 ${ordered.length} 张。`;
        const adopt = element("button", "button-secondary", "采用该方案");
        adopt.type = "button";
        const reject = element("button", "button-secondary", "拒绝");
        reject.type = "button";
        const actionStatus = element("small", "", "采用只会更新坑位草稿，仍需点击“确认计划并处理图片”。");
        adopt.addEventListener("click", async () => {
          const confirmed = await confirmAction({
            title: "采用该方案",
            message: `${difference}\n\n确认采用为草稿吗？此操作不会处理图片或上传。`,
            confirmLabel: "确认采用",
          });
          if (!confirmed) {
            actionStatus.textContent = "已取消采用，当前草稿保持不变。";
            return;
          }
          adopt.disabled = true;
          try {
            const adopted = await fetchJson(
              apiPath(`/stages/slots_copy/agent-requests/${encodeURIComponent(request.request_id)}/adopt`),
              {
                method: "POST",
                body: JSON.stringify({ proposal_index: proposalIndex }),
              },
            );
            revision = Number(adopted.revision || revision);
            revisionLabel.textContent = String(revision);
            actionStatus.textContent = "已采用为当前坑位草稿；请检查图片后确认计划。";
            await loadStage();
          } catch (error) {
            actionStatus.textContent = error.userMessage
              || Object.values(error.fieldErrors || {})[0]
              || error.message;
          } finally {
            adopt.disabled = false;
          }
        });
        reject.addEventListener("click", async () => {
          reject.disabled = true;
          try {
            await fetchJson(
              apiPath(`/stages/slots_copy/agent-requests/${encodeURIComponent(request.request_id)}/reject`),
              {
                method: "POST",
                body: JSON.stringify({ proposal_index: proposalIndex }),
              },
            );
            actionStatus.textContent = "已拒绝；当前坑位草稿保持不变。";
          } catch (error) {
            actionStatus.textContent = error.userMessage || error.message;
          } finally {
            reject.disabled = false;
          }
        });
        card.append(
          element("strong", "", `${proposal.product_title || proposal.product_id} · ${proposal.target_ratio} · ${ordered.length}张`),
          element("span", "", proposal.reason || ""),
          element("span", "", `AI置信度 ${proposal.ai_confidence ?? "—"} · 预计裁剪 ${proposal.estimated_crop_count ?? "—"}张 · 预计压缩 ${proposal.estimated_compression_count ?? "—"}张`),
          element("span", "", `裁剪风险：${proposal.crop_risk || "—"} · 多样性：${proposal.diversity_summary || "—"} · 重复：${proposal.duplicate_summary || "—"}`),
          element("span", "", difference),
          adopt,
          reject,
          actionStatus,
        );
        agentProposalList.appendChild(card);
      });
      if (proposals.length) agentButton.textContent = "换一套 AI 建议";
    };
    const loadAgentResponse = async (request) => {
      if (request?.status !== "completed") return;
      const detail = await fetchJson(
        apiPath(`/stages/slots_copy/agent-requests/${encodeURIComponent(request.request_id)}`),
      );
      renderAgentResponse(detail);
    };
    const showAgentRequest = (request) => {
      if (!request) return;
      const statusText = {
        pending_agent: "等待 Agent；网页不会自动唤醒 Codex。",
        processing: `Agent 已于 ${request.claimed_at || "未知时间"}领取。`,
        completed: "AI 分析和编排已返回，合法结果会自动载入当前草稿。",
        cancelled: "AI 请求已取消，当前坑位草稿保持不变。",
        superseded: "AI 请求已过期，不会覆盖当前草稿。",
        failed: "Agent 请求失败；当前草稿保持不变，可重试或人工编排。",
      }[request.status] || "Agent 请求状态未知。";
      agentStatus.textContent = statusText;
      agentRequestId.textContent = `请求 ${request.request_id}`;
      agentTechnical.hidden = false;
      agentButton.disabled = ["pending_agent", "processing"].includes(request.status);
      if (request.recovery_prompt && request.status === "pending_agent" && !request.claimed_at) {
        recoveryPrompt.value = request.recovery_prompt;
        recoveryPrompt.hidden = false;
        copyPrompt.hidden = false;
      }
      loadAgentResponse(request).catch((error) => {
        agentStatus.textContent = error.userMessage || error.message;
      });
    };
    agentButton.addEventListener("click", async () => {
      const hasDraft = [...stateByProduct.values()].some(
        (assignments) => assignments.length > 0,
      );
      if (hasDraft) {
        const confirmed = await confirmAction({
          title: "重新 AI 编排",
          message: "返回成功后会替换当前未确认的坑位草稿。是否继续？",
          confirmLabel: "继续编排",
          danger: true,
        });
        if (!confirmed) {
          agentStatus.textContent = "已取消 AI 重新编排，当前草稿保持不变。";
          return;
        }
      }
      agentButton.disabled = true;
      agentStatus.textContent = "正在生成受控缩略图并写入 Agent 请求…";
      try {
        const request = await fetchJson(
          apiPath("/stages/slots_copy/agent-requests"),
          {
            method: "POST",
            body: JSON.stringify({
              kind: "slot_plan_with_analysis",
              max_images: 30,
              thumbnail_max_edge: 768,
              max_proposals: 2,
              force_replan: hasDraft,
              plan_revision: currentPlanRevision,
            }),
          },
        );
        showAgentRequest(request);
      } catch (error) {
        const detail = error.userMessage
          || Object.values(error.fieldErrors || {})[0]
          || error.message;
        const actions = {
          AGENT_IMAGE_BUDGET_EXCEEDED: "可缩小候选范围，或直接人工编排。",
          AGENT_THUMBNAIL_UNAVAILABLE: "请重建任务缓存，或直接人工编排。",
          AGENT_CANDIDATE_IDENTITY_INCOMPLETE: "请重新准备当前坑位候选，或直接人工编排。",
        }[error.reasonCode] || "可重试 AI 或直接人工编排。";
        agentStatus.textContent = `${detail}（${error.reasonCode || "AGENT_REQUEST_FAILED"}）；${actions}`;
        agentButton.disabled = false;
      }
    });
    copyPrompt.addEventListener("click", async () => {
      await navigator.clipboard.writeText(recoveryPrompt.value);
      copyPrompt.textContent = "已复制";
      window.setTimeout(() => { copyPrompt.textContent = "复制恢复提示词"; }, 1500);
    });
    agentPanel.append(
      modeHelp,
      element("strong", "", "AI 编排（可选）"),
      agentStatus,
      agentButton,
      agentTechnical,
      agentProposalList,
    );
    overview.append(
      element("strong", "", `已选素材预检形成 ${products.reduce((sum, product) => sum + (product.assets || []).length, 0)} 张唯一候选图`),
      element("span", "", `每个坑位 ${data.slot_image_min || 3}–${data.slot_image_max || 9} 张，坑位内只能使用一种比例。`),
      element(
        "span",
        "",
        (data.deterministic_plan?.products || []).map(
          (item) => `${item.product_id}：${(item.slot_sizes || []).join("+") || "0 个完整坑位"}；未使用 ${item.unused_count || 0} 张`,
        ).join(" · "),
      ),
    );
    const planningPage = subpages.compose || subpages.process;
    planningPage.appendChild(overview);
    if (!twoPageWorkflow) planningPage.appendChild(agentPanel);
    if (twoPageWorkflow) {
      const replanButton = element(
        "button",
        "button-secondary",
        "重新自动编排",
      );
      replanButton.type = "button";
      const replanStatus = element(
        "small",
        "",
        "会按当前已选图片重新生成全部未确认坑位，并使旧裁剪输出与文案失效。",
      );
      replanButton.addEventListener("click", async () => {
        const confirmed = await confirmAction({
          title: "重新自动编排",
          message: "会替换当前坑位草稿，并使已有裁剪输出和文案失效。是否继续？",
          confirmLabel: "继续编排",
          danger: true,
        });
        if (!confirmed) return;
        replanButton.disabled = true;
        replanStatus.textContent = "正在按当前选择重新编排…";
        try {
          const payload = await fetchJson(
            apiPath("/stages/slots_copy/current-slot-plan/replan"),
            {
              method: "POST",
              body: JSON.stringify({
                plan_revision: currentPlanRevision,
              }),
            },
          );
          applyCurrentPlan(payload.current_slot_plan);
          replanStatus.textContent = "已重新编排；请检查后再次确认。";
        } catch (error) {
          replanStatus.textContent = error.userMessage || error.message;
        } finally {
          replanButton.disabled = false;
        }
      });
      planningPage.append(replanButton, replanStatus);
    }
    const composeProductNavigator = element("div", "product-jump-mount");
    const board = element("div", "slot-board-products");
    planningPage.append(composeProductNavigator, board);
    const composeActionPanel = element("section", "slot-primary-action");
    const composeStatus = element(
      "span",
      "",
      "确认每个坑位的图片、顺序和建议比例后，再进入裁剪页面。",
    );
    const confirmPlanButton = element(
      "button",
      "primary-button",
      "确认坑位并进入图片裁剪",
    );
    confirmPlanButton.type = "button";
    composeActionPanel.append(
      composeStatus,
      confirmPlanButton,
    );
    planningPage.appendChild(composeActionPanel);

    const processPanel = element("section", "slot-process-page");
    processPanel.hidden = twoPageWorkflow;
    const processHeading = element("div", "slot-page-heading");
    processHeading.append(
      element("strong", "", "图片裁剪与压缩"),
      element(
        "span",
        "",
        "比例按坑位统一选择；每张图片只调整当前比例的裁剪框。",
      ),
    );
    const cropFailureSummary = element(
      "section",
      "crop-preflight-failure-summary",
    );
    cropFailureSummary.hidden = true;
    const processProductNavigator = element("div", "product-jump-mount");
    const processWorkspace = element("div", "slot-process-workspace");
    const processStatus = element(
      "span",
      "",
      "请检查裁剪框、原图大小和压缩参数。",
    );
    const cropPreflightButton = element(
      "button",
      "button-secondary",
      "裁剪预校验",
    );
    cropPreflightButton.type = "button";
    const processPlanButton = element(
      "button",
      "primary-button",
      "完成图片处理并进入文案生成",
    );
    processPlanButton.type = "button";
    processPlanButton.disabled = true;
    let cropPreflight = null;
    const cropPreflightLockState = new Map();
    const setCropPreflightControlsLocked = (locked) => {
      processPanel.classList.toggle("is-crop-preflight-running", locked);
      processPanel.setAttribute("aria-busy", String(locked));
      const controls = [
        ...new Set([
          ...planningPage.querySelectorAll(
            "button, input, select, textarea, .crop-overlay, .slot-asset-card",
          ),
          ...processPanel.querySelectorAll(
            "button, input, select, textarea, .crop-overlay, .slot-asset-card",
          ),
          ...wizard.querySelectorAll("button"),
        ]),
      ].filter((control) => (
        control && !control.closest(".product-jump-nav")
      ));
      if (locked) {
        controls.forEach((control) => {
          if (!cropPreflightLockState.has(control)) {
            cropPreflightLockState.set(control, {
              disabled: "disabled" in control ? control.disabled : null,
              ariaDisabled: control.getAttribute("aria-disabled"),
              tabIndex: control.getAttribute("tabindex"),
              draggable: control.classList.contains("slot-asset-card")
                ? control.draggable
                : null,
            });
          }
          if ("disabled" in control) control.disabled = true;
          if (control.classList.contains("slot-asset-card")) {
            control.draggable = false;
          }
          if (control.classList.contains("crop-overlay")) {
            control.classList.add("is-disabled");
          }
          control.setAttribute("aria-disabled", "true");
          control.setAttribute("tabindex", "-1");
        });
        return;
      }
      cropPreflightLockState.forEach((previous, control) => {
        if ("disabled" in control && previous.disabled !== null) {
          control.disabled = previous.disabled;
        }
        if (
          control.classList.contains("slot-asset-card")
          && previous.draggable !== null
        ) {
          control.draggable = previous.draggable;
        }
        control.classList.remove("is-disabled");
        if (previous.ariaDisabled === null) {
          control.removeAttribute("aria-disabled");
        } else {
          control.setAttribute("aria-disabled", previous.ariaDisabled);
        }
        if (previous.tabIndex === null) {
          control.removeAttribute("tabindex");
        } else {
          control.setAttribute("tabindex", previous.tabIndex);
        }
      });
      cropPreflightLockState.clear();
    };
    const backToCompose = element(
      "button",
      "button-secondary",
      "返回坑位编排",
    );
    backToCompose.type = "button";
    backToCompose.addEventListener("click", () => setSubpage(
      "compose",
      { userRequested: true },
    ));
    const processActions = element("div", "slot-page-actions");
    if (!twoPageWorkflow) processActions.appendChild(backToCompose);
    processActions.append(
      processStatus,
      cropPreflightButton,
      processPlanButton,
    );
    processPanel.append(
      processHeading,
      cropFailureSummary,
      processProductNavigator,
      processWorkspace,
      processActions,
    );
    subpages.process.appendChild(processPanel);
    invalidateProcessedOutputs = () => {
      processedOutputs = null;
      processPanel.querySelector(".processed-preview-grid")?.remove();
      subpages.copy?.replaceChildren();
      const processPageIndex = pageOrder.indexOf("process");
      if (processPageIndex >= 0) {
        maxUnlockedPage = Math.min(maxUnlockedPage, processPageIndex);
      }
      if (activeSubpage === "copy") setSubpage("process");
    };
    const normalizedCropBox = (output) => {
      const value = output?.crop_box?.normalized || output?.crop_box;
      if (
        Array.isArray(value)
        && value.length === 4
        && value.every((item) => Number.isFinite(Number(item)))
      ) {
        return value.map(Number);
      }
      if (
        value
        && Number.isFinite(Number(value.x))
        && Number.isFinite(Number(value.y))
        && Number.isFinite(Number(value.width))
        && Number.isFinite(Number(value.height))
      ) {
        const x = Number(value.x);
        const y = Number(value.y);
        return [x, y, x + Number(value.width), y + Number(value.height)];
      }
      return [0, 0, 1, 1];
    };
    const candidateFor = (product, assetId, targetRatio) =>
      (product?.outputs || []).find(
        (output) =>
          String(output.asset_id) === String(assetId)
          && String(output.target_ratio) === String(targetRatio),
      );
    invalidateCropPreflight = () => {
      cropPreflight = null;
      invalidateProcessedOutputs();
      cropFailureSummary.hidden = true;
      cropFailureSummary.replaceChildren();
      processPlanButton.disabled = true;
      processStatus.textContent = "裁剪参数已变化，请重新执行裁剪预校验。";
    };
    const currentSlotAssignments = () => [...stateByProduct.values()].flat();
    const currentSlotIds = () => new Set(
      currentSlotAssignments().map(
        (assignment) => String(assignment.slot_id || ""),
      ),
    );
    const slotPlanSignature = (assignments) => JSON.stringify(
      assignments.map((assignment) => ({
        product_id: String(assignment.product_id || ""),
        slot_id: String(assignment.slot_id || ""),
        target_ratio: String(assignment.target_ratio || ""),
        asset_ids: (assignment.asset_ids || []).map(String),
      })),
    );
    const currentSlotPlanSignature = () => slotPlanSignature(
      currentSlotAssignments(),
    );
    const responseMatchesCurrentPlan = (response) => {
      const assignments = currentSlotAssignments();
      const responseSlots = Array.isArray(response?.slots) ? response.slots : [];
      if (responseSlots.length !== assignments.length) return false;
      const responseBySlot = new Map(
        responseSlots.map((slot) => [String(slot?.slot_id || ""), slot]),
      );
      return assignments.every((assignment) => {
        const slot = responseBySlot.get(String(assignment.slot_id || ""));
        if (!slot) return false;
        const orderedAssetIds = Array.isArray(slot.ordered_asset_ids)
          ? slot.ordered_asset_ids.map(String)
          : (slot.outputs || []).map((output) => String(output?.asset_id || ""));
        return String(slot.product_id || "") === String(assignment.product_id || "")
          && String(slot.target_ratio || "") === String(assignment.target_ratio || "")
          && JSON.stringify(orderedAssetIds)
            === JSON.stringify((assignment.asset_ids || []).map(String));
      });
    };
    const cropFailureKey = (slotId, assetId, order) => JSON.stringify([
      String(slotId || ""),
      String(assetId || ""),
      Number(order || 0),
    ]);
    const cropPreflightFailures = () => (
      Array.isArray(cropPreflight?.failures)
        ? cropPreflight.failures.filter((item) => (
          item
          && item.slot_id
          && currentSlotIds().has(String(item.slot_id))
        ))
        : []
    );
    const renderCropFailureSummary = (
      failures,
      failureTargets,
      { focusFirstFailure = false } = {},
    ) => {
      cropFailureSummary.replaceChildren();
      cropFailureSummary.hidden = failures.length === 0;
      if (!failures.length) return;
      const failedSlotCount = new Set(
        failures.map((failure) => String(failure.slot_id || "")),
      ).size;
      cropFailureSummary.append(
        element("strong", "", `有 ${failedSlotCount} 个坑位未通过裁剪预校验`),
        element(
          "p",
          "",
          `其中 ${failures.length} 张图片裁剪后文件不足 200KB。你可以更换图片，或去掉对应坑位后重新执行裁剪预校验。`,
        ),
      );
      const list = element("div", "crop-preflight-failure-list");
      failures.forEach((failure) => {
        const product = products.find(
          (item) => String(item.product_id) === String(failure.product_id),
        );
        const productLabel = failure.product_title
          || product?.product_title
          || `商品 ${failure.product_id}`;
        const actualSize = Number(failure.actual_output_size_bytes || 0);
        const label = `${productLabel} → ${failure.slot_id} → 第 ${failure.order} 张 → 实际输出 ${
          actualSize > 0 ? formatBytes(actualSize) : "大小未取得"
        }`;
        const jump = element(
          "button",
          "crop-preflight-failure-link",
          label,
        );
        jump.type = "button";
        const key = cropFailureKey(
          failure.slot_id,
          failure.asset_id,
          failure.order,
        );
        jump.addEventListener("click", () => {
          const target = failureTargets.get(key);
          if (!target) return;
          target.scrollIntoView({ behavior: "smooth", block: "center" });
          target.focus({ preventScroll: true });
        });
        list.appendChild(jump);
      });
      cropFailureSummary.appendChild(list);
      if (!focusFirstFailure) return;
      const first = failures[0];
      const firstTarget = failureTargets.get(cropFailureKey(
        first.slot_id,
        first.asset_id,
        first.order,
      ));
      if (!firstTarget) return;
      requestAnimationFrame(() => {
        firstTarget.scrollIntoView({ behavior: "smooth", block: "center" });
        firstTarget.focus({ preventScroll: true });
      });
    };
    const createSlotCropEditor = (output, assignment, cropState) => {
      const width = Number(output.width || output.source_width || 0);
      const height = Number(output.height || output.source_height || 0);
      const frame = element("div", "crop-source-frame slot-crop-frame");
      if (width && height) frame.style.aspectRatio = `${width} / ${height}`;
      const image = document.createElement("img");
      image.loading = "lazy";
      image.alt = `${assignment.slot_id} 可视化裁剪原图`;
      image.src = apiPath(
        `/stages/slots_copy/assets/${encodeURIComponent(output.asset_id)}`,
      );
      const overlay = element("div", "crop-overlay");
      overlay.tabIndex = 0;
      overlay.setAttribute("role", "application");
      overlay.setAttribute(
        "aria-label",
        `${assignment.slot_id} 图片裁剪框；方向键移动，Shift 加方向键缩放`,
      );
      overlay.setAttribute(
        "aria-keyshortcuts",
        "ArrowUp ArrowDown ArrowLeft ArrowRight Shift+ArrowUp Shift+ArrowDown Shift+ArrowLeft Shift+ArrowRight",
      );
      const handle = element("span", "crop-resize-handle");
      overlay.appendChild(handle);
      frame.append(image, overlay);
      const preview = document.createElement("canvas");
      preview.className = "crop-preview-canvas";
      const outputSize = element("small", "crop-output-size");
      const controls = element("div", "slot-crop-actions");
      const restore = element("button", "button-secondary", "恢复建议框");
      restore.type = "button";
      controls.append(restore);
      const currentBox = () => cropState.normalized_box;
      const update = () => {
        const box = currentBox();
        overlay.hidden = !box;
        preview.hidden = !box;
        restore.disabled = !box || cropPreflightInFlight;
        if (!box) {
          outputSize.textContent = "标准比例框暂不可用";
          return;
        }
        overlay.style.left = `${box[0] * 100}%`;
        overlay.style.top = `${box[1] * 100}%`;
        overlay.style.width = `${(box[2] - box[0]) * 100}%`;
        overlay.style.height = `${(box[3] - box[1]) * 100}%`;
        const cropWidth = Math.round((box[2] - box[0]) * width);
        const cropHeight = Math.round((box[3] - box[1]) * height);
        outputSize.textContent = `预计输出区域 ${cropWidth}×${cropHeight} · ${assignment.target_ratio}`;
        if (image.complete && image.naturalWidth) {
          const context = preview.getContext("2d");
          const sourceX = box[0] * image.naturalWidth;
          const sourceY = box[1] * image.naturalHeight;
          const sourceWidth = (box[2] - box[0]) * image.naturalWidth;
          const sourceHeight = (box[3] - box[1]) * image.naturalHeight;
          preview.width = assignment.target_ratio === "1:1" ? 260 : 195;
          preview.height = 260;
          context.clearRect(0, 0, preview.width, preview.height);
          context.drawImage(
            image,
            sourceX,
            sourceY,
            sourceWidth,
            sourceHeight,
            0,
            0,
            preview.width,
            preview.height,
          );
        }
      };
      const beginPointer = (event, mode) => {
        if (cropPreflightInFlight || !currentBox() || !width || !height) return;
        event.preventDefault();
        const start = [...currentBox()];
        const startX = event.clientX;
        const startY = event.clientY;
        const rect = frame.getBoundingClientRect();
        const targetRatio = assignment.target_ratio === "1:1" ? 1 : 0.75;
        const normalizedRatio = targetRatio * height / width;
        const move = (pointerEvent) => {
          if (cropPreflightInFlight) return;
          const dx = (pointerEvent.clientX - startX) / rect.width;
          const dy = (pointerEvent.clientY - startY) / rect.height;
          if (mode === "move") {
            const boxWidth = start[2] - start[0];
            const boxHeight = start[3] - start[1];
            const x = Math.min(1 - boxWidth, Math.max(0, start[0] + dx));
            const y = Math.min(1 - boxHeight, Math.max(0, start[1] + dy));
            cropState.normalized_box = [x, y, x + boxWidth, y + boxHeight];
          } else {
            const maxWidth = Math.min(
              1 - start[0],
              (1 - start[1]) * normalizedRatio,
            );
            const boxWidth = Math.min(
              maxWidth,
              Math.max(0.08, start[2] - start[0] + dx),
            );
            const boxHeight = boxWidth / normalizedRatio;
            cropState.normalized_box = [
              start[0],
              start[1],
              start[0] + boxWidth,
              start[1] + boxHeight,
            ];
          }
          update();
        };
        const stop = () => {
          window.removeEventListener("pointermove", move);
          window.removeEventListener("pointerup", stop);
          if (JSON.stringify(start) !== JSON.stringify(currentBox())) {
            invalidateCropPreflight();
          }
        };
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", stop, { once: true });
      };
      overlay.addEventListener("pointerdown", (event) => {
        if (event.target !== handle) beginPointer(event, "move");
      });
      handle.addEventListener("pointerdown", (event) => {
        event.stopPropagation();
        beginPointer(event, "resize");
      });
      overlay.addEventListener("keydown", (event) => {
        if (
          cropPreflightInFlight
          || !currentBox()
          || !width
          || !height
          || !["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(
            event.key,
          )
        ) return;
        event.preventDefault();
        const box = [...currentBox()];
        const step = 0.01;
        if (event.shiftKey) {
          const targetRatio = assignment.target_ratio === "1:1" ? 1 : 0.75;
          const normalizedRatio = targetRatio * height / width;
          const direction = ["ArrowRight", "ArrowDown"].includes(event.key)
            ? 1
            : -1;
          const boxWidth = Math.min(
            1 - box[0],
            (1 - box[1]) * normalizedRatio,
            Math.max(0.08, box[2] - box[0] + direction * step),
          );
          cropState.normalized_box = [
            box[0],
            box[1],
            box[0] + boxWidth,
            box[1] + boxWidth / normalizedRatio,
          ];
        } else {
          const boxWidth = box[2] - box[0];
          const boxHeight = box[3] - box[1];
          const dx = event.key === "ArrowLeft"
            ? -step
            : event.key === "ArrowRight" ? step : 0;
          const dy = event.key === "ArrowUp"
            ? -step
            : event.key === "ArrowDown" ? step : 0;
          const x = Math.min(1 - boxWidth, Math.max(0, box[0] + dx));
          const y = Math.min(1 - boxHeight, Math.max(0, box[1] + dy));
          cropState.normalized_box = [x, y, x + boxWidth, y + boxHeight];
        }
        invalidateCropPreflight();
        update();
      });
      image.addEventListener("load", update);
      restore.addEventListener("click", () => {
        if (cropPreflightInFlight) return;
        cropState.normalized_box = normalizedCropBox(output);
        invalidateCropPreflight();
        update();
      });
      update();
      const editor = element("div", "slot-crop-editor");
      editor.append(frame, controls, outputSize, preview);
      return editor;
    };
    const renderProcessingPage = ({ focusFirstFailure = false } = {}) => {
      processWorkspace.replaceChildren();
      const productTargets = new Map();
      const failures = cropPreflightFailures();
      const failuresByImage = new Map(
        failures.map((failure) => [
          cropFailureKey(failure.slot_id, failure.asset_id, failure.order),
          failure,
        ]),
      );
      const productIdBySlot = new Map(
        currentSlotAssignments().map((assignment) => [
          String(assignment.slot_id || ""),
          String(assignment.product_id || ""),
        ]),
      );
      const failedSlotIdsByProduct = new Map();
      failures.forEach((failure) => {
        const productId = String(
          failure.product_id
          || productIdBySlot.get(String(failure.slot_id || ""))
          || "",
        );
        if (!failedSlotIdsByProduct.has(productId)) {
          failedSlotIdsByProduct.set(productId, new Set());
        }
        failedSlotIdsByProduct.get(productId).add(String(failure.slot_id || ""));
      });
      const failureTargets = new Map();
      let blockedReason = "";
      [...stateByProduct.values()].flat().forEach((assignment) => {
        const product = products.find(
          (item) => String(item.product_id) === String(assignment.product_id),
        );
        const card = element("article", "slot-process-card");
        card.tabIndex = -1;
        const productId = String(assignment.product_id || "");
        if (!productTargets.has(productId)) {
          const failedSlotCount = failedSlotIdsByProduct.get(productId)?.size || 0;
          productTargets.set(productId, {
            productId,
            productTitle: product?.product_title,
            target: card,
            navigationStatus: failedSlotCount
              ? `${failedSlotCount} 个坑位未通过`
              : "",
            navigationTone: failedSlotCount ? "danger" : "",
          });
        }
        const heading = element("div", "slot-product-heading");
        const recommendationSource = {
          agent_assisted: "AI 建议",
          manual_override: "人工调整",
          rules: "规则建议",
          manual: "人工选择",
        }[assignment.plan_source] || "当前计划";
        const ratio = element("div", "slot-ratio-buttons");
        ["3:4", "1:1"].forEach((value) => {
          const button = element("button", "button-secondary", value);
          button.type = "button";
          button.dataset.active = value === assignment.target_ratio
            ? "true"
            : "false";
          button.setAttribute(
            "aria-label",
            `${assignment.slot_id} 使用 ${value} 比例`,
          );
          button.addEventListener("click", () => {
            if (cropPreflightInFlight) return;
            if (value === assignment.target_ratio) return;
            invalidateCropPreflight();
            assignment.target_ratio = value;
            assignment.plan_source = "manual_override";
            Object.keys(cropParameters)
              .filter((key) => key.startsWith(`${assignment.slot_id}:`))
              .forEach((key) => delete cropParameters[key]);
            persist(true);
            renderProcessingPage();
          });
          ratio.appendChild(button);
        });
        heading.append(
          element(
            "strong",
            "",
            `${assignment.theme || assignment.slot_id} · ${assignment.asset_ids.length} 张`,
          ),
          element(
            "span",
            "",
            `${recommendationSource} ${assignment.target_ratio}；整个坑位必须使用同一比例`,
          ),
          ratio,
        );
        const grid = element("div", "slot-process-grid");
        assignment.asset_ids.forEach((assetId, order) => {
          const output = candidateFor(
            product,
            assetId,
            assignment.target_ratio,
          );
          const imageCard = element("article", "slot-process-image");
          imageCard.tabIndex = -1;
          if (!output) {
            blockedReason = "有已选图片缺少当前坑位比例的处理数据，请返回坑位编排重新选择。";
            imageCard.append(
              element("strong", "", `第 ${order + 1} 张`),
              element("span", "slot-process-error", blockedReason),
            );
            grid.appendChild(imageCard);
            return;
          }
          const cropKey = `${assignment.slot_id}:${assetId}`;
          const failureKey = cropFailureKey(
            assignment.slot_id,
            assetId,
            order + 1,
          );
          const cropFailure = failuresByImage.get(failureKey);
          if (
            !cropParameters[cropKey]
            || cropParameters[cropKey].use_original === true
            || !Array.isArray(cropParameters[cropKey].normalized_box)
          ) {
            cropParameters[cropKey] = {
              normalized_box: normalizedCropBox(output),
              use_original: false,
              confirm_compression: !output.requires_compression,
            };
          }
          const originalWidth = Number(output.width || output.source_width || 0);
          const originalHeight = Number(output.height || output.source_height || 0);
          const originalSize = Number(
            output.size_bytes ?? output.output_size_bytes ?? 0,
          );
          if (!originalWidth || !originalHeight || !originalSize) {
            blockedReason = "有图片原图信息读取失败，请返回第四阶段重新检测。";
          }
          const compression = document.createElement("input");
          compression.type = "checkbox";
          compression.checked =
            cropParameters[cropKey].confirm_compression === true;
          compression.disabled = !output.requires_compression;
          compression.addEventListener("change", () => {
            if (cropPreflightInFlight) return;
            invalidateCropPreflight();
            cropParameters[cropKey].confirm_compression = compression.checked;
            renderProcessingPage();
          });
          const compressionLabel = element("label", "check-control");
          compressionLabel.append(
            compression,
            element(
              "span",
              "",
              output.requires_compression
                ? "确认压缩到 20MB 以内"
                : "无需压缩",
            ),
          );
          imageCard.append(
            element("strong", "", `第 ${order + 1} 张`),
            element(
              "span",
              "",
              originalWidth && originalHeight
                ? `原图 ${originalWidth}×${originalHeight} · ${output.original_ratio || ratioText(output)} · ${
                    originalSize
                      ? formatBytes(originalSize)
                      : "大小读取失败"
                  }${output.format ? ` · ${output.format}` : ""}`
                : "原图尺寸与大小读取失败",
            ),
            element(
              "small",
              "",
              `目标 ${assignment.target_ratio} · 标准比例框${
                output.requires_compression ? " · 需要压缩" : ""
              }`,
            ),
            createSlotCropEditor(
              output,
              assignment,
              cropParameters[cropKey],
            ),
            compressionLabel,
          );
          if (cropFailure) {
            imageCard.classList.add("crop-preflight-failed");
            imageCard.appendChild(
              element(
                "strong",
                "slot-process-error crop-preflight-image-error",
                cropFailure.message || "裁剪后文件不足 200KB，请更换该图片",
              ),
            );
            failureTargets.set(failureKey, imageCard);
          }
          grid.appendChild(imageCard);
        });
        card.append(heading, grid);
        processWorkspace.appendChild(card);
      });
      renderCropFailureSummary(
        failures,
        failureTargets,
        { focusFirstFailure },
      );
      renderProductNavigator(
        processProductNavigator,
        [...productTargets.values()],
        { label: "图片裁剪商品导航" },
      );
      const needsCompression = [...stateByProduct.values()]
        .flat()
        .some((assignment) => {
          const product = products.find(
            (item) => String(item.product_id) === String(assignment.product_id),
          );
          return assignment.asset_ids.some((assetId) => {
            const output = candidateFor(product, assetId, assignment.target_ratio);
            const cropKey = `${assignment.slot_id}:${assetId}`;
            return output?.requires_compression
              && cropParameters[cropKey]?.confirm_compression !== true;
          });
        });
      if (needsCompression) {
        blockedReason ||= "请先确认所有需要压缩的图片。";
      }
      cropPreflightButton.disabled = Boolean(blockedReason);
      processPlanButton.disabled = Boolean(blockedReason)
        || cropPreflight?.workflow_state !== "crop_preflight_passed";
      const failedSlotCount = new Set(
        failures.map((failure) => String(failure.slot_id || "")),
      ).size;
      processStatus.textContent = blockedReason
        || (failures.length
          ? `有 ${failedSlotCount} 个坑位未通过，其中 ${failures.length} 张图片裁剪后文件不足 200KB，请更换图片或去掉对应坑位后重新预校验。`
          : cropPreflight?.workflow_state === "crop_preflight_passed"
          ? `裁剪预校验已通过：${cropPreflight.checked_count || 0} 张图片均不少于 204,800 字节。`
          : "请先执行裁剪预校验；通过后才能进入文案生成。");
      if (cropPreflightInFlight) setCropPreflightControlsLocked(true);
    };
    const renderProcessedPreview = (processed) => {
      processPanel.querySelector(".processed-preview-grid")?.remove();
      const preview = element("div", "slot-asset-grid processed-preview-grid");
      const activeSlotIds = currentSlotIds();
      (processed.slots || [])
        .filter((slot) => activeSlotIds.has(String(slot?.slot_id || "")))
        .forEach((slot) => {
        (slot.outputs || []).forEach((output) => {
          const card = element("article", "slot-asset-card");
          const image = document.createElement("img");
          image.loading = "lazy";
          image.alt = `${slot.slot_id} 第 ${output.order} 张最终预览`;
          image.src = apiPath(
            `/stages/slots_copy/processed-assets/${encodeURIComponent(slot.slot_id)}/${output.order}`,
          );
          card.append(
            image,
            element("strong", "", `${slot.slot_id} · 第 ${output.order} 张`),
            element(
              "small",
              "",
              `${output.output_width}×${output.output_height} · ${formatBytes(output.output_size_bytes)} · ${output.crop_source || "已处理"}`,
            ),
          );
          preview.appendChild(card);
        });
      });
      if (preview.childElementCount) processPanel.appendChild(preview);
    };

    const renderCopyEditor = (processed, selectedRequestId = "") => {
      const copyContent = subpages.copy;
      const assignments = [...stateByProduct.values()].flat();
      const savedCopyItems = readJsonListControl("copy_edits")
        .filter((item) => item?.slot_id);
      const savedRequestIds = [...new Set(
        savedCopyItems
          .map((item) => String(item?.request_id || ""))
          .filter(Boolean),
      )];
      const restoredRequestId = selectedRequestId
        || (savedRequestIds.length === 1 ? savedRequestIds[0] : "");
      const hasMeaningfulCopyDrafts = (drafts) => drafts.some((item) => (
        String(item?.title || "").trim()
        || String(item?.description || "").trim()
      ));
      const savedCopy = new Map(
        savedCopyItems.map((item) => [String(item.slot_id), item]),
      );
      const form = element("div", "copy-slot-list");
      const copyState = [];
      let currentCopyRequestStatus = "";
      let finishButton = null;
      let finishHint = null;
      let copyVersionCount = 0;
      const copyActions = element("div", "copy-toolbar");
      const copyStatus = element(
        "span",
        "copy-toolbar-status",
        "千牛会按商品坑位生成文案；全部生成后请逐项核对标题和描述。",
      );
      const copySkippedNotice = element("section", "copy-skipped-notice");
      copySkippedNotice.hidden = true;
      copySkippedNotice.setAttribute("aria-live", "polite");
      const updateCopySkippedNotice = (drafts) => {
        const skipped = (Array.isArray(drafts) ? drafts : []).filter(
          (item) => String(item?.generation_status || "") === "skipped",
        );
        copySkippedNotice.hidden = skipped.length === 0;
        copySkippedNotice.replaceChildren();
        if (!skipped.length) return;
        const list = document.createElement("ul");
        skipped.forEach((item) => {
          const position = Number(item.remote_slot_position || 0);
          const slotLabel = position
            ? `第 ${position} 个坑位`
            : String(item.slot_id || "未知坑位");
          list.appendChild(element(
            "li",
            "",
            `商品 ${String(item.product_id || "未知")} · ${slotLabel}：${String(item.skip_message || "千牛自动获取文案未完成")}`,
          ));
        });
        copySkippedNotice.append(
          element(
            "strong",
            "",
            skipped.some((item) => Number(item.repair_pass_count || 0) > 0)
              ? `以下 ${skipped.length} 个坑位完成常规重试和 1 轮失败项补跑后仍未完成，系统已跳过：`
              : `以下 ${skipped.length} 个坑位自动处理后仍未完成，系统已跳过：`,
          ),
          element("p", "", "请核对这些坑位，并补充仍为空白的标题和描述；其他坑位不受影响。"),
          list,
        );
      };
      const copyButton = element(
        "button",
        "primary-button",
        "重新生成新版本",
      );
      copyButton.type = "button";
      copyButton.hidden = true;
      const resumeCopyButton = element(
        "button",
        "primary-button",
        "继续获取",
      );
      resumeCopyButton.type = "button";
      resumeCopyButton.hidden = true;
      const copyVersions = document.createElement("select");
      copyVersions.setAttribute("aria-label", "AI 文案版本");
      const copyToolbarActions = element("div", "copy-toolbar-actions");
      copyToolbarActions.append(
        resumeCopyButton,
        copyButton,
        copyVersions,
        element(
          "small",
          "copy-version-help",
          "继续获取会保留已完成内容；重新生成会创建独立新版本。",
        ),
      );
      copyActions.append(copyStatus, copyToolbarActions);
      const updateCopyActions = () => {
        const incompleteCount = copyState.filter(
          (draft) => !String(draft.title || "").trim()
            || !String(draft.description || "").trim(),
        ).length;
        const missingBindingCount = copyState.filter(
          (draft) => Number(draft.remote_slot_position || 0) <= 0,
        ).length;
        const hasCompleteDrafts = copyState.length > 0
          && incompleteCount === 0
          && missingBindingCount === 0;
        const canResume = (incompleteCount > 0 || missingBindingCount > 0)
          && ["failed", "completed"].includes(currentCopyRequestStatus);
        resumeCopyButton.hidden = !canResume;
        resumeCopyButton.disabled = !canResume;
        copyButton.className = hasCompleteDrafts
          ? "button-secondary"
          : "primary-button";
        if (finishButton) {
          finishButton.disabled = !hasCompleteDrafts;
          finishButton.title = missingBindingCount
            ? `还有 ${missingBindingCount} 个坑位需要重新确认千牛目标坑位`
            : incompleteCount
            ? `还有 ${incompleteCount} 个坑位缺少标题或描述`
            : "全部标题和描述已填写，可以进入上传任务确认";
        }
        if (finishHint) {
          finishHint.textContent = missingBindingCount
            ? `还有 ${missingBindingCount} 个坑位需要重新确认千牛目标坑位，请点击“继续获取”。`
            : incompleteCount
            ? `还有 ${incompleteCount} 个坑位缺少标题或描述。`
            : "全部标题和描述已填写，可以进入上传任务确认。";
          finishHint.dataset.status = incompleteCount || missingBindingCount
            ? "waiting"
            : "ready";
        }
      };
      assignments.forEach((assignment, assignmentIndex) => {
        const existing = savedCopy.get(String(assignment.slot_id)) || {};
        const processedSlot = (processed.slots || []).find(
          (slot) => String(slot.slot_id) === String(assignment.slot_id),
        ) || {};
        const item = {
          slot_id: assignment.slot_id,
          product_id: assignment.product_id,
          remote_slot_position: existing.remote_slot_position == null
            ? null
            : Number(existing.remote_slot_position),
          title: String(existing.title || ""),
          description: String(existing.description || ""),
          confirmed: Boolean(
            String(existing.title || "").trim()
            && String(existing.description || "").trim()
          ),
          source: String(existing.source || "manual"),
          manual_fields: existing.manual_fields
            && typeof existing.manual_fields === "object"
            ? { ...existing.manual_fields }
            : {},
          evidence: Array.isArray(existing.evidence) ? existing.evidence : [],
          risks: Array.isArray(existing.risks) ? existing.risks : [],
          generation_status: String(existing.generation_status || "pending"),
          skip_reason_code: String(existing.skip_reason_code || ""),
          skip_message: String(existing.skip_message || ""),
          failure_category: String(existing.failure_category || ""),
          attempt_count: Number(existing.attempt_count || 0),
          retry_count: Number(existing.retry_count || 0),
          repair_pass_count: Number(existing.repair_pass_count || 0),
          final_retry_exhausted: Boolean(existing.final_retry_exhausted),
          request_id: String(existing.request_id || ""),
          output_sha256: Array.isArray(existing.output_sha256)
            ? existing.output_sha256
            : (processed.slots || [])
              .find((slot) => String(slot.slot_id) === String(assignment.slot_id))
              ?.outputs?.map((output) => String(output.output_sha256 || ""))
              || [],
        };
        copyState.push(item);
        const card = element("article", "copy-slot-card");
        card.dataset.slotId = String(assignment.slot_id);

        const cardHeading = element("header", "copy-slot-heading");
        const headingIdentity = element("div", "copy-slot-identity");
        const slotNumber = element(
          "span",
          "copy-slot-number",
          `坑位 ${assignmentIndex + 1}`,
        );
        headingIdentity.append(
          slotNumber,
          element("strong", "", String(assignment.slot_id)),
          element(
            "small",
            "",
            `商品 ${assignment.product_id} · ${assignment.target_ratio} · ${assignment.asset_ids.length} 张素材`,
          ),
        );
        cardHeading.append(headingIdentity);

        const mediaPanel = element("section", "copy-media-panel");
        mediaPanel.setAttribute("aria-label", `${assignment.slot_id} 最终素材`);
        mediaPanel.append(
          element("span", "copy-section-label", "该坑位最终素材"),
          element(
            "p",
            "copy-media-theme",
            processedSlot.theme
              || assignment.theme
              || "按当前顺序生成文案",
          ),
        );
        const strip = element("div", "copy-story-strip");
        strip.dataset.ratio = String(assignment.target_ratio || "");
        (processedSlot.outputs || []).forEach((output) => {
          const figure = document.createElement("figure");
          figure.className = "copy-story-frame";
          const image = document.createElement("img");
          image.loading = "lazy";
          image.alt = `${assignment.slot_id} 第 ${output.order} 张最终素材`;
          image.src = apiPath(
            `/stages/slots_copy/processed-assets/${encodeURIComponent(assignment.slot_id)}/${output.order}`,
          );
          const caption = element(
            "figcaption",
            "",
            `第 ${output.order} 张`,
          );
          figure.append(image, caption);
          strip.appendChild(figure);
        });
        mediaPanel.appendChild(strip);

        const editor = element("section", "copy-editor-panel");
        editor.setAttribute("aria-label", `${assignment.slot_id} 标题和描述`);
        const title = document.createElement("input");
        title.type = "text";
        title.maxLength = 30;
        title.placeholder = "输入与该组素材一致的标题";
        title.value = item.title;
        const titleCount = element(
          "span",
          "copy-character-count",
          `${title.value.length}/30`,
        );
        const titleLabel = element("label", "copy-field");
        const titleLabelRow = element("span", "copy-field-label");
        titleLabelRow.append(
          element("strong", "", "标题"),
          titleCount,
        );
        titleLabel.append(titleLabelRow, title);

        const description = document.createElement("textarea");
        description.maxLength = 1000;
        description.placeholder = "描述这组素材呈现的卖点、场景和使用感受";
        description.rows = 5;
        description.value = item.description;
        const descriptionCount = element(
          "span",
          "copy-character-count",
          `${description.value.length}/1000`,
        );
        const descriptionLabel = element("label", "copy-field");
        const descriptionLabelRow = element("span", "copy-field-label");
        descriptionLabelRow.append(
          element("strong", "", "描述"),
          descriptionCount,
        );
        descriptionLabel.append(descriptionLabelRow, description);

        const reviewMeta = element("div", "copy-review-meta");
        const evidence = element("div", "copy-review-note");
        evidence.append(
          element("span", "", "生成依据"),
          element(
            "p",
            "",
            item.evidence.join("；") || "人工填写，或等待 AI 生成后显示",
          ),
        );
        const risks = element("div", "copy-review-note");
        risks.dataset.kind = item.risks.length ? "warning" : "clear";
        risks.append(
          element("span", "", "风险提示"),
          element("p", "", item.risks.join("；") || "暂无风险标记"),
        );
        reviewMeta.append(evidence, risks);

        const save = (fieldName) => {
          item.title = title.value;
          item.description = description.value;
          item.manual_fields = {
            ...(item.manual_fields || {}),
            [fieldName]: true,
          };
          item.source = "manual";
          item.generation_status = item.title.trim() && item.description.trim()
            ? "manual_completed"
            : "manual_editing";
          if (item.generation_status === "manual_completed") {
            item.skip_reason_code = "";
            item.skip_message = "";
            item.failure_category = "";
          }
          copyState.forEach((draft) => {
            draft.confirmed = Boolean(
              String(draft.title || "").trim()
              && String(draft.description || "").trim()
            );
          });
          titleCount.textContent = `${title.value.length}/30`;
          descriptionCount.textContent = `${description.value.length}/1000`;
          writeJsonListControl("copy_edits", copyState, { notify: true });
          updateCopyActions();
        };
        title.addEventListener("input", () => save("title"));
        description.addEventListener("input", () => save("description"));
        editor.append(
          titleLabel,
          descriptionLabel,
          reviewMeta,
        );
        const cardBody = element("div", "copy-slot-body");
        cardBody.append(mediaPanel, editor);
        card.append(cardHeading, cardBody);
        form.appendChild(card);
      });
      const copyWorkspace = element("div", "copy-workspace");
      copyWorkspace.appendChild(form);
      const copyHeading = element("div", "slot-page-heading");
      copyHeading.append(
        element("strong", "", "千牛 AI 标题与描述"),
        element(
          "span",
          "",
          "系统会为每个坑位生成文案；全部完成后，请核对标题和描述。",
        ),
      );
      const finish = element(
        "button",
        "primary-button",
        "进入上传任务确认",
      );
      finish.type = "button";
      finishButton = finish;
      finishHint = element("small", "copy-finish-hint");
      updateCopyActions();
      finish.addEventListener("click", () => persistStage("submit"));
      const finalActions = element("div", "slot-page-actions");
      finalActions.append(
        finishHint,
        finish,
      );
      const technical = document.createElement("details");
      technical.className = "slot-technical-details";
      technical.append(
        element("summary", "", "技术详情"),
        element(
          "code",
          "",
          `计划 ${String(processed.plan_sha256 || "").slice(0, 12)}`,
        ),
      );
      copyContent.replaceChildren(
        copyHeading,
        copyActions,
        copySkippedNotice,
        copyWorkspace,
        finalActions,
        technical,
      );
      writeJsonListControl("copy_edits", copyState);
      const applyCopyDrafts = (drafts, requestId) => {
        const existingItems =
          readJsonListControl("copy_edits")
            .filter((item) => item?.slot_id);
        const merged = UiState.copyDraftsForVersion(
          assignments,
          existingItems,
          drafts,
          requestId,
          processed.slots || [],
        );
        writeJsonListControl("copy_edits", merged, { notify: true });
        const skippedCount = drafts.filter(
          (item) => String(item?.generation_status || "") === "skipped",
        ).length;
        const generatedCount = drafts.length - skippedCount;
        copyStatus.textContent = drafts.length === assignments.length
          ? skippedCount
            ? `自动获取完成 ${generatedCount} 个坑位，跳过 ${skippedCount} 个坑位；请核对并补充跳过项。`
            : "千牛文案已载入；请核对全部标题、描述、依据和风险。"
          : `已处理 ${drafts.length}/${assignments.length} 个坑位，正在继续生成。`;
        renderCopyEditor(processed, requestId);
      };
      const pollCopyRequest = async (requestId) => {
        if (!requestId || !copyVersions.isConnected) return;
        try {
          const detail = await fetchJson(
            apiPath(`/stages/slots_copy/agent-requests/${encodeURIComponent(requestId)}`),
          );
          if (!copyVersions.isConnected) return;
          if (persistenceInFlight) {
            window.setTimeout(() => pollCopyRequest(requestId), 500);
            return;
          }
          const requestState = detail.request?.status || "";
          currentCopyRequestStatus = requestState;
          const requestIsActive = ["pending_agent", "processing"]
            .includes(requestState);
          setLocalCopyRequestInFlight(requestIsActive);
          setSubpage("copy");
          const progressDrafts = detail.progress?.copy_drafts || [];
          const completedDrafts = detail.response?.result?.copy_drafts || [];
          const drafts = completedDrafts.length
            ? completedDrafts
            : progressDrafts;
          const skippedDrafts = drafts.filter(
            (item) => String(item?.generation_status || "") === "skipped",
          );
          const generatedCount = drafts.length - skippedDrafts.length;
          updateCopySkippedNotice(drafts);
          const knownCount = readJsonListControl("copy_edits").filter(
            (item) => String(item.request_id || "") === requestId
              && (
                ["generated", "skipped"].includes(
                  String(item.generation_status || ""),
                )
                || (
                  String(item.title || "").trim()
                  && String(item.description || "").trim()
                )
              ),
          ).length;
          const localDrafts = new Map(
            readJsonListControl("copy_edits")
              .filter((item) => String(item.request_id || "") === requestId)
              .map((item) => [String(item.slot_id || ""), item]),
          );
          const draftsChanged = drafts.some((draft) => {
            const existing = localDrafts.get(String(draft.slot_id || ""));
            if (!existing) return true;
            const manualFields = existing.manual_fields
              && typeof existing.manual_fields === "object"
              ? existing.manual_fields
              : {};
            const hasExplicitManualFields = existing.manual_fields
              && typeof existing.manual_fields === "object";
            const legacyManualOverride = !hasExplicitManualFields
              && String(existing.source || "") === "manual";
            const hasManualOverride = legacyManualOverride
              || manualFields.title === true
              || manualFields.description === true;
            return (!legacyManualOverride
                && manualFields.title !== true
                && String(existing.title || "") !== String(draft.title || ""))
              || (!legacyManualOverride
                && manualFields.description !== true
                && String(existing.description || "") !== String(draft.description || ""))
              || Number(existing.remote_slot_position || 0)
                !== Number(draft.remote_slot_position || 0)
              || (!legacyManualOverride
                && manualFields.title !== true
                && manualFields.description !== true
                && String(existing.generation_status || "")
                  !== String(draft.generation_status || "generated"))
              || (!hasManualOverride
                && String(existing.skip_reason_code || "")
                  !== String(draft.skip_reason_code || ""))
              || (!hasManualOverride
                && Number(existing.repair_pass_count || 0)
                  !== Number(draft.repair_pass_count || 0));
          });
          if (drafts.length > knownCount || draftsChanged) {
            applyCopyDrafts(drafts, requestId);
            return;
          }
          if (requestState === "completed") {
            copyStatus.textContent = skippedDrafts.length
              ? `自动获取完成 ${generatedCount} 个坑位，跳过 ${skippedDrafts.length} 个坑位；请核对并补充跳过项。`
              : "千牛文案已载入；请核对全部内容后统一确认。";
            copyButton.disabled = false;
            updateCopyActions();
            return;
          }
          if (requestState === "failed") {
            copyStatus.textContent = `文案获取已中断，已保留 ${progressDrafts.length}/${assignments.length} 个坑位；可继续获取剩余内容。`;
            resumeCopyButton.hidden = false;
            resumeCopyButton.disabled = false;
            copyButton.disabled = false;
            updateCopyActions();
            return;
          }
          if (["cancelled", "superseded"].includes(requestState)) {
            copyStatus.textContent = "当前文案版本已停止，可以重新生成新版本。";
            copyButton.disabled = false;
            return;
          }
          copyButton.disabled = true;
          const retryCount = Number(detail.progress?.current_retry_count || 0);
          const repairPass = Number(detail.progress?.current_repair_pass || 0);
          const repairProcessed = Number(
            detail.progress?.repair_processed_count || 0,
          );
          const repairTotal = Number(detail.progress?.repair_total_count || 0);
          const retryText = retryCount
            ? `；当前坑位正在重试 ${retryCount}/3`
            : "";
          copyStatus.textContent = requestState === "processing"
            ? repairPass && repairTotal
              ? `正在单独补跑失败坑位：已处理 ${repairProcessed}/${repairTotal}${retryText}。`
              : `正在生成千牛文案：已处理 ${progressDrafts.length}/${assignments.length} 个坑位${retryText}。`
            : "图片已确认，正在获取千牛标题和描述。";
          window.setTimeout(() => pollCopyRequest(requestId), 1500);
        } catch (error) {
          if (!copyVersions.isConnected) return;
          copyStatus.textContent = "文案状态暂时无法读取，系统会自动重试。";
          window.setTimeout(() => pollCopyRequest(requestId), 2500);
        }
      };
      const requestCopy = async (regenerate = false) => {
        setLocalCopyRequestInFlight(true);
        setSubpage("copy");
        resumeCopyButton.hidden = true;
        copyButton.disabled = true;
        copyButton.textContent = `正在创建版本 ${copyVersionCount + 1}…`;
        copyStatus.textContent = "正在创建新的千牛文案任务…";
        try {
          const copyRequest = await fetchJson(
            apiPath("/stages/slots_copy/copy-request"),
            {
              method: "POST",
              body: JSON.stringify({
                regenerate,
                provider: "qianniu_builtin_ai",
              }),
            },
          );
          copyStatus.textContent = "新版本已提交，正在生成。";
          applyCopyDrafts([], copyRequest.request_id);
        } catch (error) {
          setLocalCopyRequestInFlight(false);
          setSubpage("copy");
          copyStatus.textContent = error.userMessage || error.message;
          copyButton.disabled = false;
        } finally {
          copyButton.textContent = "重新生成新版本";
        }
      };
      const resumeCopy = async () => {
        const requestId = String(copyVersions.value || "");
        if (!requestId) return;
        setLocalCopyRequestInFlight(true);
        setSubpage("copy");
        resumeCopyButton.disabled = true;
        copyButton.disabled = true;
        copyStatus.textContent = "正在恢复当前文案任务…";
        try {
          await fetchJson(
            apiPath(
              `/stages/slots_copy/agent-requests/${encodeURIComponent(requestId)}/resume`,
            ),
            {
              method: "POST",
              body: JSON.stringify({ copy_edits: copyState }),
            },
          );
          resumeCopyButton.hidden = true;
          copyStatus.textContent = "已恢复，将从未完成的坑位继续获取。";
          pollCopyRequest(requestId);
        } catch (error) {
          setLocalCopyRequestInFlight(false);
          resumeCopyButton.hidden = false;
          resumeCopyButton.disabled = false;
          copyButton.disabled = false;
          copyStatus.textContent = error.userMessage || error.message;
        }
      };
      resumeCopyButton.addEventListener("click", resumeCopy);
      copyButton.addEventListener("click", () => requestCopy(true));
      fetchJson(apiPath("/stages/slots_copy/agent-requests"))
        .then(async (payload) => {
          const copyRequests = (payload.requests || []).filter(
            (item) => item.kind === "copy_draft",
          );
          const versionView = UiState.copyRequestVersionView(
            copyRequests,
            restoredRequestId,
          );
          const versions = versionView.versions;
          const activeCopyRequest = versions.find(
            (item) => item.request_id === versionView.activeRequestId,
          ) || null;
          setLocalCopyRequestInFlight(Boolean(activeCopyRequest));
          setSubpage(activeCopyRequest ? "copy" : activeSubpage);
          copyVersionCount = versions.length;
          copyButton.textContent = "重新生成新版本";
          copyButton.hidden = copyRequests.length === 0;
          copyVersions.replaceChildren();
          if (!versions.length) {
            const option = document.createElement("option");
            option.textContent = "暂无千牛文案版本";
            copyVersions.appendChild(option);
            copyVersions.disabled = true;
            if (!activeCopyRequest) {
              const latest = copyRequests.find(
                (item) => item.request_id === restoredRequestId,
              ) || copyRequests[0];
              if (latest) pollCopyRequest(latest.request_id);
            }
            return;
          }
          versions.forEach((item, index) => {
            const option = document.createElement("option");
            option.value = item.request_id;
            option.textContent = `版本 ${item.version_number} · ${item.status_label}`;
            copyVersions.appendChild(option);
          });
          const currentDrafts = readJsonListControl("copy_edits")
            .filter((item) => item?.slot_id);
          if (!copyVersions.isConnected) return;
          if (
            !activeCopyRequest
            && !restoredRequestId
            && (
              hasMeaningfulCopyDrafts(savedCopyItems)
              || hasMeaningfulCopyDrafts(currentDrafts)
            )
          ) {
            const option = document.createElement("option");
            option.value = "";
            option.textContent = "当前草稿 · 未绑定 AI 版本";
            option.selected = true;
            copyVersions.prepend(option);
            return;
          }
          const selectedRequestId = versionView.selectedRequestId;
          if (!selectedRequestId) return;
          copyVersions.value = selectedRequestId;
          pollCopyRequest(selectedRequestId);
        })
        .catch(() => {
          if (restoredRequestId) pollCopyRequest(restoredRequestId);
        });
      copyVersions.addEventListener("change", async () => {
        if (!copyVersions.value) return;
        const detail = await fetchJson(
          apiPath(`/stages/slots_copy/agent-requests/${encodeURIComponent(copyVersions.value)}`),
        );
        const drafts = detail.response?.result?.copy_drafts?.length
          ? detail.response.result.copy_drafts
          : detail.progress?.copy_drafts || [];
        updateCopySkippedNotice(drafts);
        applyCopyDrafts(
          drafts,
          copyVersions.value,
        );
      });
    };

    confirmPlanButton.addEventListener("click", async () => {
      const assignments = [...stateByProduct.values()].flat();
      confirmPlanButton.disabled = true;
      composeStatus.textContent = "正在保存并锁定坑位图片、顺序和比例…";
      try {
        if (slotPlanDirty) invalidateCropPreflight();
        if (slotPlanDirty) {
          const updated = await fetchJson(
            apiPath("/stages/slots_copy/current-slot-plan"),
            {
              method: "POST",
              body: JSON.stringify({
                plan_revision: currentPlanRevision,
                slot_assignments: assignments,
              }),
            },
          );
          currentPlanRevision = Number(
            updated.current_slot_plan?.plan_revision || currentPlanRevision,
          );
          currentPlanConfirmed = false;
          slotPlanDirty = false;
        }
        await confirmCurrentSlotPlan();
        composeStatus.textContent = "坑位已确认；现在逐图检查裁剪与压缩。";
        processPanel.hidden = false;
        maxUnlockedPage = Math.max(
          maxUnlockedPage,
          pageOrder.indexOf("process"),
        );
        renderProcessingPage();
        setSubpage("process");
      } catch (error) {
        composeStatus.textContent = error.userMessage
          || Object.values(error.fieldErrors || {})[0]
          || error.message;
      } finally {
        confirmPlanButton.disabled = false;
      }
    });

    const ensureConfirmedSlotPlan = async () => {
      const assignments = [...stateByProduct.values()].flat();
      if (slotPlanDirty) {
        const updated = await fetchJson(
          apiPath("/stages/slots_copy/current-slot-plan"),
          {
            method: "POST",
            body: JSON.stringify({
              plan_revision: currentPlanRevision,
              slot_assignments: assignments,
            }),
          },
        );
        currentPlanRevision = Number(
          updated.current_slot_plan?.plan_revision || currentPlanRevision,
        );
        currentPlanConfirmed = false;
        slotPlanDirty = false;
      }
      if (!currentPlanConfirmed) await confirmCurrentSlotPlan();
      return assignments;
    };

    cropPreflightButton.addEventListener("click", async () => {
      if (cropPreflightInFlight) return;
      cropPreflightInFlight = true;
      setCropPreflightControlsLocked(true);
      renderStatus();
      processStatus.textContent = "正在逐张试裁并检查最终文件大小…";
      let focusFirstFailure = false;
      let completionMessage = "";
      try {
        const assignments = await ensureConfirmedSlotPlan();
        const requestPlanSignature = currentSlotPlanSignature();
        const preflight = await fetchJson(
          apiPath("/stages/slots_copy/crop-preflight"),
          {
            method: "POST",
            body: JSON.stringify({
              slot_assignments: assignments,
              crop_parameters: cropParameters,
            }),
          },
        );
        if (
          requestPlanSignature !== currentSlotPlanSignature()
          || !responseMatchesCurrentPlan(preflight)
        ) {
          cropPreflight = null;
          completionMessage = "坑位编排已经变化，旧的裁剪结果已忽略，请重新执行裁剪预校验。";
          return;
        }
        cropPreflight = preflight;
        currentPlanRevision = Number(
          cropPreflight.plan_revision || currentPlanRevision,
        );
        if (cropPreflight.workflow_state === "crop_preflight_failed") {
          focusFirstFailure = true;
          return;
        }
      } catch (error) {
        cropPreflight = null;
        completionMessage = error.userMessage
          || Object.values(error.fieldErrors || {})[0]
          || error.message;
      } finally {
        cropPreflightInFlight = false;
        setCropPreflightControlsLocked(false);
        renderProcessingPage({ focusFirstFailure });
        renderStatus();
        if (completionMessage) processStatus.textContent = completionMessage;
      }
    });

    processPlanButton.addEventListener("click", async () => {
      const assignments = [...stateByProduct.values()].flat();
      if (cropPreflight?.workflow_state !== "crop_preflight_passed") {
        processStatus.textContent = "请先执行裁剪预校验。";
        processPlanButton.disabled = true;
        return;
      }
      processPlanButton.disabled = true;
      setLocalCopyRequestInFlight(true);
      processStatus.textContent = "正在确认图片输出并创建千牛文案任务…";
      try {
        const requestPlanSignature = currentSlotPlanSignature();
        const processed = await fetchJson(
          apiPath("/stages/slots_copy/process-plan"),
          {
            method: "POST",
            body: JSON.stringify({
              slot_assignments: assignments,
              crop_parameters: cropParameters,
            }),
          },
        );
        if (
          requestPlanSignature !== currentSlotPlanSignature()
          || !responseMatchesCurrentPlan(processed)
        ) {
          throw new Error("坑位编排已经变化，旧的图片处理结果已忽略，请重新处理。");
        }
        processedOutputs = processed;
        const copyRequestId = String(
          processed.copy_request?.request_id || processed.copy_request_id || "",
        );
        setLocalCopyRequestInFlight(Boolean(copyRequestId));
        processStatus.textContent = `图片处理完成：${processed.slots?.length || 0} 个坑位；正在生成文案。`;
        renderProcessedPreview(processed);
        renderCopyEditor(
          processed,
          copyRequestId,
        );
        maxUnlockedPage = Math.max(
          maxUnlockedPage,
          pageOrder.indexOf("copy"),
        );
        setSubpage("copy");
      } catch (error) {
        setLocalCopyRequestInFlight(false);
        processStatus.textContent = error.userMessage
          || Object.values(error.fieldErrors || {})[0]
          || error.message;
      } finally {
        processPlanButton.disabled = cropPreflight?.workflow_state
          !== "crop_preflight_passed";
      }
    });
    const savedPreflightPlanSignature = currentSlotPlanSignature();
    fetchJson(apiPath("/stages/slots_copy/crop-preflight"))
      .then((preflight) => {
        if (!["crop_preflight_passed", "crop_preflight_failed"].includes(
          preflight.workflow_state,
        )) return;
        if (
          cropPreflightInFlight
          || slotPlanDirty
          || savedPreflightPlanSignature !== currentSlotPlanSignature()
          || !responseMatchesCurrentPlan(preflight)
        ) return;
        cropPreflight = preflight;
        if (preflight.crop_parameters) {
          Object.keys(cropParameters).forEach((key) => delete cropParameters[key]);
          Object.assign(cropParameters, preflight.crop_parameters);
        }
        currentPlanRevision = Number(
          preflight.plan_revision || currentPlanRevision,
        );
        renderProcessingPage({
          focusFirstFailure: preflight.workflow_state === "crop_preflight_failed",
        });
      })
      .catch(() => {});
    const savedProcessedPlanSignature = currentSlotPlanSignature();
    fetchJson(apiPath("/stages/slots_copy/processed-outputs"))
      .then((processed) => {
        if (
          processed.workflow_state === "outputs_ready"
          && processed.plan_sha256
          && !cropPreflightInFlight
          && !slotPlanDirty
          && savedProcessedPlanSignature === currentSlotPlanSignature()
          && responseMatchesCurrentPlan(processed)
        ) {
          processedOutputs = processed;
          processStatus.textContent = "当前坑位计划已有通过校验的输出；可以继续确认文案。";
          renderProcessedPreview(processed);
          renderCopyEditor(processed, processed.copy_request_id || "");
          processPanel.hidden = false;
          maxUnlockedPage = Math.max(
            maxUnlockedPage,
            pageOrder.indexOf("copy"),
          );
          setSubpage("copy");
        }
      })
      .catch(() => {});

    const productAssets = (product) => {
      if (Array.isArray(product.assets) && product.assets.length) {
        return product.assets;
      }
      const assets = new Map();
      (product.outputs || []).forEach((output) => {
        const assetId = String(output.asset_id);
        const current = assets.get(assetId) || {
          asset_id: assetId,
          product_id: String(output.product_id || product.product_id || ""),
          source_sha256: String(output.source_sha256 || ""),
          width: output.width || output.source_width || output.output_width,
          height: output.height || output.source_height || output.output_height,
          original_ratio: output.original_ratio || "",
          size_bytes: output.size_bytes ?? output.output_size_bytes,
          size_display: output.size_display || "",
          format: output.format || "",
          ratio_options: {},
        };
        current.ratio_options[String(output.target_ratio)] = {
          crop_box: output.crop_box,
          native_ratio: Boolean(output.native_ratio),
          requires_compression: Boolean(output.requires_compression),
          estimated_width: output.estimated_width,
          estimated_height: output.estimated_height,
        };
        assets.set(assetId, current);
      });
      return [...assets.values()];
    };
    const ratioText = (asset) => {
      if (asset.original_ratio) return asset.original_ratio;
      const width = Number(asset.width || 0);
      const height = Number(asset.height || 0);
      if (!width || !height) return "比例未知";
      const gcd = (left, right) => (right ? gcd(right, left % right) : left);
      const divisor = gcd(width, height);
      return `${width / divisor}:${height / divisor}`;
    };
    const assetMetaText = (asset) => {
      const width = asset.width || asset.source_width || asset.output_width || "—";
      const height = asset.height || asset.source_height || asset.output_height || "—";
      const size = asset.size_display
        || formatBytes(asset.size_bytes ?? asset.output_size_bytes);
      return `原图 ${width}×${height} · ${ratioText(asset)} · ${size}${asset.format ? ` · ${asset.format}` : ""}`;
    };
    const assignedAssetIds = (productId) => new Set(
      (stateByProduct.get(productId) || [])
        .flatMap((assignment) => assignment.asset_ids || [])
        .map(String),
    );

    const draw = () => {
      board.replaceChildren();
      const productTargets = [];
      const hasDraft = [...stateByProduct.values()].some(
        (assignments) => assignments.length > 0,
      );
      confirmPlanButton.disabled = !hasDraft;
      agentButton.className = hasDraft ? "button-secondary" : "primary-button";
      products.forEach((product) => {
        const productId = String(product.product_id || "");
        const section = element("section", "slot-product");
        section.tabIndex = -1;
        productTargets.push({
          productId,
          productTitle: product.product_title,
          target: section,
        });
        const heading = element("div", "slot-product-heading");
        heading.append(
          element("strong", "", product.product_title || `商品 ${productId}`),
          element("span", "", `商品 ID ${productId} · 3:4 ${product.available_by_ratio?.["3:4"] || 0} 张 · 1:1 ${product.available_by_ratio?.["1:1"] || 0} 张`),
        );
        const add = element("button", "button-secondary", "人工添加坑位");
        add.type = "button";
        add.addEventListener("click", () => {
          if (cropPreflightInFlight) return;
          const assignments = stateByProduct.get(productId);
          const index = assignments.length + 1;
          const ratio = (product.outputs || [])[0]?.target_ratio || "3:4";
          assignments.push({
            slot_id: `${productId}-slot-${index}`,
            product_id: productId,
            target_ratio: ratio,
            asset_ids: [],
            plan_source: "manual",
          });
          persist(true);
          draw();
        });
        heading.appendChild(add);
        section.appendChild(heading);
        section.appendChild(
          element("h4", "slot-section-title", "当前坑位草稿"),
        );
        const slots = element("div", "slot-list");
        section.appendChild(slots);
        board.appendChild(section);

        stateByProduct.get(productId).forEach((assignment, slotIndex) => {
          const slot = element("article", "slot-card");
          const controls = element("div", "slot-controls");
          const slotId = document.createElement("input");
          slotId.value = assignment.slot_id;
          slotId.setAttribute("aria-label", "坑位 ID");
          const ratio = document.createElement("select");
          ["3:4", "1:1"].forEach((value) => {
            const option = document.createElement("option");
            option.value = value;
            option.textContent = value;
            ratio.appendChild(option);
          });
          ratio.value = assignment.target_ratio;
          const remove = element(
            "button",
            "button-danger-secondary",
            "去掉当前坑位",
          );
          remove.type = "button";
          remove.setAttribute(
            "aria-label",
            `去掉当前坑位：${assignment.slot_id}`,
          );
          const count = element("span", "slot-count");
          controls.append(slotId, ratio, count, remove);
          const sourceLabel = {
            agent_assisted: "AI 生成",
            manual_override: "AI 生成、人工调整",
            rules: "历史规则草稿",
            agent_assisted_with_rules_fallback: "历史 AI/规则草稿",
            manual: "人工编排",
          }[assignment.plan_source] || assignment.plan_source || "未知";
          slot.append(
            element(
              "strong",
              "",
              assignment.theme || `${productId} 坑位`,
            ),
            element(
              "span",
              "",
              `来源：${sourceLabel} · ${assignment.quantity_reason || "请人工确认图片数量与互补性"}`,
            ),
            element(
              "small",
              "",
              `预计需处理 ${assignment.estimated_processing_count ?? "—"} 张`,
            ),
            element(
              "small",
              "slot-copy-pending",
              "标题与描述：待图片处理完成后生成",
            ),
          );
          slot.appendChild(controls);
          const grid = element("div", "slot-asset-grid");
          slot.appendChild(grid);
          const candidateSection = document.createElement("details");
          candidateSection.className = "slot-candidate-section";
          const candidateSummary = element("summary", "", "添加候选图片");
          const candidateGrid = element("div", "slot-candidate-grid");
          const batchActions = element("div", "slot-candidate-actions");
          candidateSection.append(
            candidateSummary,
            candidateGrid,
            batchActions,
          );
          slot.appendChild(candidateSection);
          slots.appendChild(slot);

          const drawAssets = () => {
            grid.replaceChildren();
            candidateGrid.replaceChildren();
            batchActions.replaceChildren();
            const assets = productAssets(product);
            const byId = new Map(
              assets.map((asset) => [String(asset.asset_id), asset]),
            );
            assignment.asset_ids = assignment.asset_ids.filter((assetId) =>
              byId.has(String(assetId))
            );
            assignment.asset_ids.forEach((assetId) => {
              const output = byId.get(String(assetId));
              const card = element("article", "slot-asset-card");
              const image = document.createElement("img");
              image.loading = "lazy";
              image.alt = `${productId} ${assignment.slot_id} 已选图片`;
              image.src = apiPath(
                `/stages/slots_copy/assets/${encodeURIComponent(output.asset_id)}`,
              );
              const removeAsset = element("button", "button-secondary", "从坑位移除");
              removeAsset.type = "button";
              removeAsset.addEventListener("click", () => {
                if (cropPreflightInFlight) return;
                assignment.asset_ids = assignment.asset_ids.filter(
                  (value) => String(value) !== String(assetId),
                );
                assignment.plan_source = assignment.plan_source === "agent_assisted"
                  ? "manual_override"
                  : "manual";
                persist(true);
                draw();
              });
              const role = (assignment.image_roles || []).find(
                (item) => String(item.asset_id || "") === assetId,
              );
              card.draggable = true;
              card.addEventListener("dragstart", (event) => {
                if (cropPreflightInFlight) {
                  event.preventDefault();
                  return;
                }
                event.dataTransfer?.setData("text/plain", assetId);
              });
              card.addEventListener("dragover", (event) => {
                event.preventDefault();
              });
              card.addEventListener("drop", (event) => {
                event.preventDefault();
                if (cropPreflightInFlight) return;
                const moved = event.dataTransfer?.getData("text/plain");
                const from = assignment.asset_ids.indexOf(String(moved));
                const to = assignment.asset_ids.indexOf(assetId);
                if (from < 0 || to < 0 || from === to) return;
                assignment.asset_ids.splice(from, 1);
                assignment.asset_ids.splice(to, 0, String(moved));
                persist(true);
                drawAssets();
              });
              card.append(
                image,
                element("span", "", assetMetaText(output)),
                element(
                  "small",
                  "",
                  role
                    ? `${role.role}：${role.reason}`
                    : "已加入当前坑位",
                ),
                removeAsset,
              );
              grid.appendChild(card);
            });
            const available = assets.filter((asset) => (
              !assignedAssetIds(productId).has(String(asset.asset_id))
              && asset.ratio_options?.[assignment.target_ratio]
            ));
            const pageKey = `${productId}:${assignment.slot_id}`;
            const pageSize = 30;
            const pageCount = Math.max(1, Math.ceil(available.length / pageSize));
            const requestedPage = candidatePageByProduct.get(pageKey) || 0;
            const pageIndex = Math.min(requestedPage, pageCount - 1);
            candidatePageByProduct.set(pageKey, pageIndex);
            available.slice(
              pageIndex * pageSize,
              (pageIndex + 1) * pageSize,
            ).forEach((candidate) => {
              const card = element("article", "slot-candidate-card");
              const image = document.createElement("img");
              image.loading = "lazy";
              image.alt = `${productId} 可加入 ${assignment.slot_id} 的候选图片`;
              image.src = apiPath(
                `/stages/slots_copy/assets/${encodeURIComponent(candidate.asset_id)}`,
              );
              const addAsset = element("button", "button-secondary", "加入当前坑位");
              addAsset.type = "button";
              addAsset.disabled = (
                assignment.asset_ids.length >= Number(data.slot_image_max || 9)
              );
              addAsset.addEventListener("click", () => {
                if (cropPreflightInFlight) return;
                assignment.asset_ids.push(String(candidate.asset_id));
                assignment.plan_source = assignment.plan_source === "agent_assisted"
                  ? "manual_override"
                  : "manual";
                persist(true);
                draw();
              });
              card.append(
                image,
                element("small", "", assetMetaText(candidate)),
                addAsset,
              );
              candidateGrid.appendChild(card);
            });
            candidateSummary.textContent = `添加候选图片 · 可用 ${available.length} 张`;
            if (pageCount > 1) {
              const previous = element("button", "button-secondary", "上一批");
              previous.type = "button";
              previous.disabled = pageIndex === 0;
              previous.addEventListener("click", () => {
                candidatePageByProduct.set(pageKey, pageIndex - 1);
                draw();
              });
              const next = element("button", "button-secondary", "下一批");
              next.type = "button";
              next.disabled = pageIndex >= pageCount - 1;
              next.addEventListener("click", () => {
                candidatePageByProduct.set(pageKey, pageIndex + 1);
                draw();
              });
              batchActions.append(
                previous,
                element("span", "", `${pageIndex + 1} / ${pageCount}`),
                next,
              );
            }
            const valid = assignment.asset_ids.length >= Number(data.slot_image_min || 3)
              && assignment.asset_ids.length <= Number(data.slot_image_max || 9);
            count.textContent = `已选 ${assignment.asset_ids.length} 张${valid ? "" : " · 数量不合规"}`;
            count.dataset.status = valid ? "valid" : "invalid";
            persist();
          };
          slotId.addEventListener("change", () => {
            if (cropPreflightInFlight) return;
            assignment.slot_id = slotId.value.trim();
            persist(true);
          });
          ratio.addEventListener("change", () => {
            if (cropPreflightInFlight) return;
            assignment.target_ratio = ratio.value;
            assignment.plan_source = assignment.plan_source === "agent_assisted"
              ? "manual_override"
              : "manual";
            persist(true);
            draw();
          });
          remove.addEventListener("click", async () => {
            if (cropPreflightInFlight) return;
            const currentSlotId = slotId.value.trim() || assignment.slot_id;
            const confirmed = await confirmAction({
              title: "去掉当前坑位",
              message: `确定去掉当前坑位“${currentSlotId}”吗？\n\n该坑位的图片编排会移除，已有图片处理结果和文案将失效。`,
              confirmLabel: "确认去掉",
              danger: true,
            });
            if (!confirmed || cropPreflightInFlight) return;
            const assignments = stateByProduct.get(productId);
            const nextAssignments = [...stateByProduct.values()]
              .flat()
              .filter((item) => item !== assignment);
            if (!nextAssignments.length) {
              composeStatus.textContent = "本次任务至少需要保留一个坑位。";
              return;
            }
            const requestPlanSignature = slotPlanSignature(nextAssignments);
            remove.disabled = true;
            remove.textContent = "正在去掉…";
            composeStatus.textContent = `正在保存坑位“${currentSlotId}”的删除结果…`;
            try {
              const updated = await fetchJson(
                apiPath("/stages/slots_copy/current-slot-plan"),
                {
                  method: "POST",
                  body: JSON.stringify({
                    plan_revision: currentPlanRevision,
                    slot_assignments: nextAssignments,
                  }),
                },
              );
              currentPlanRevision = Number(
                updated.current_slot_plan?.plan_revision || currentPlanRevision,
              );
              currentPlanConfirmed = false;
              assignments.splice(slotIndex, 1);
              Object.keys(cropParameters)
                .filter((key) => key.startsWith(`${currentSlotId}:`))
                .forEach((key) => delete cropParameters[key]);
              invalidateCropPreflight();
              persist();
              slotPlanDirty = currentSlotPlanSignature() !== requestPlanSignature;
              draw();
              if (slotPlanDirty) {
                currentPlanConfirmed = false;
                processPanel.hidden = true;
                composeStatus.textContent = `坑位“${currentSlotId}”已删除并保存；请确认剩余坑位后继续图片处理。`;
                actionMessage.textContent = "坑位删除已保存，请确认剩余坑位。";
                return;
              }
              try {
                await confirmCurrentSlotPlan();
              } catch (_error) {
                currentPlanConfirmed = false;
                processPanel.hidden = true;
                composeStatus.textContent = `坑位“${currentSlotId}”已删除并保存；请确认剩余坑位后继续图片处理。`;
                actionMessage.textContent = "坑位删除已保存，请确认剩余坑位。";
                return;
              }
              processPanel.hidden = !currentPlanConfirmed;
              renderProcessingPage();
              composeStatus.textContent = `已去掉坑位“${currentSlotId}”并确认剩余坑位；刷新后也不会恢复。`;
              actionMessage.textContent = `已去掉当前坑位“${currentSlotId}”。`;
            } catch (error) {
              remove.disabled = false;
              remove.textContent = "去掉当前坑位";
              composeStatus.textContent = error.userMessage
                || Object.values(error.fieldErrors || {})[0]
                || "坑位删除结果未保存，请重试。";
            }
          });
          drawAssets();
        });
      });
      renderProductNavigator(
        composeProductNavigator,
        productTargets,
        { label: "坑位编排商品导航" },
      );
      if (cropPreflightInFlight) setCropPreflightControlsLocked(true);
    };
    draw();
    persist();
    (async () => {
      try {
        const currentPayload = await fetchJson(
          apiPath("/stages/slots_copy/current-slot-plan"),
        );
        if (currentPayload.current_slot_plan) {
          applyCurrentPlan(currentPayload.current_slot_plan);
        } else if (data.deterministic_plan) {
          applyCurrentPlan(data.deterministic_plan);
        } else {
          setSubpage(pageOrder[0]);
        }
        const requestsPayload = await fetchJson(
          apiPath("/stages/slots_copy/agent-requests"),
        );
        let latest = Array.isArray(requestsPayload.requests)
          ? requestsPayload.requests.find((item) =>
            item.kind === "slot_plan_with_analysis"
            && ["pending_agent", "processing", "completed"].includes(item.status)
          )
          : null;
        if (!currentPayload.current_slot_plan && currentPayload.ai_default && !latest) {
          latest = await fetchJson(
            apiPath("/stages/slots_copy/agent-requests"),
            {
              method: "POST",
              body: JSON.stringify({
                kind: "slot_plan_with_analysis",
                max_images: 30,
                thumbnail_max_edge: 768,
                max_proposals: 2,
              }),
            },
          );
        }
        if (latest) showAgentRequest(latest);
      } catch (error) {
        agentStatus.textContent = error.userMessage || error.message;
      }
    })();
  }

  function writeApprovalTaskIds(taskIds) {
    const control = activeForm()?.querySelector('[name="task_ids"]');
    if (!control) return;
    const value = [...taskIds].join("\n");
    if (control.value === value) return;
    control.value = value;
    control.dispatchEvent(new CustomEvent(
      "input",
      { bubbles: true, detail: { source: "explicit-user-edit" } },
    ));
  }

  function renderUploadTaskConfirmation(view) {
    const module = document.querySelector('[data-component="ApprovalChecklist"]');
    const content = module?.querySelector("[data-result-content]");
    const taskField = activeForm()?.querySelector('[data-field="task_ids"]');
    if (taskField) taskField.hidden = true;
    if (!content || view.mode === "empty") return;

    const documentData = view.result?.data || {};
    const retryState = documentData.publish_retry;
    if (retryState?.available === true) {
      renderUploadRetryPanel(content, retryState);
      return;
    }
    const tasks = Array.isArray(documentData.tasks) ? documentData.tasks : [];
    const control = activeForm()?.querySelector('[name="task_ids"]');
    const selected = new Set(
      String(control?.value || "").split(/\r?\n/).map((item) => item.trim()).filter(Boolean),
    );
    const readyTaskIds = tasks
      .filter((task) => task.status === "ready_for_review")
      .map((task) => String(task.task_id || ""))
      .filter(Boolean);
    const selectionIdentity = [
      String(documentData.source_stage?.input_sha256 || ""),
      ...readyTaskIds,
    ].join("|");
    if (
      currentStageInputLoaded
      && !currentStageHasPersistedInput
      && !selected.size
      && content.dataset.approvalSelectionInitialized !== selectionIdentity
    ) {
      readyTaskIds.forEach((taskId) => selected.add(taskId));
      writeApprovalTaskIds(selected);
    }
    content.dataset.approvalSelectionInitialized = selectionIdentity;
    content.replaceChildren();

    const uploadOwnerName = String(
      currentApprovalUploadIdentity?.user_name || "",
    ).trim();
    const uploadOwnerReady = currentApprovalUploadIdentity?.status === "authorized"
      && Boolean(uploadOwnerName);
    const uploadOwner = element("div", "upload-owner-identity");
    uploadOwner.dataset.status = uploadOwnerReady ? "ready" : "required";
    uploadOwner.append(
      element("strong", "", "上传负责人"),
      element(
        "span",
        "",
        uploadOwnerReady
          ? `${uploadOwnerName}（飞书账号）`
          : String(
            currentApprovalUploadIdentity?.message
              || "尚未取得飞书授权账号",
          ),
      ),
    );
    if (!uploadOwnerReady) {
      uploadOwner.appendChild(element(
        "p",
        "",
        "点击“提交并自动上传所选任务”后，系统会重新检查并在需要时打开飞书授权页面。",
      ));
    }
    content.appendChild(uploadOwner);

    const summary = element("div", "upload-confirmation-summary");
    [
      ["商品", documentData.product_count || 0],
      ["可审任务", tasks.filter((task) => task.status === "ready_for_review").length],
      ["图片", documentData.media_count || 0],
    ].forEach(([label, value]) => {
      const item = element("div", "upload-confirmation-metric");
      item.append(element("strong", "", String(value)), element("span", "", label));
      summary.appendChild(item);
    });
    content.appendChild(summary);

    const actions = element("div", "upload-confirmation-actions");
    const selectAll = element("button", "secondary-button", "选择全部可授权任务");
    selectAll.type = "button";
    const clearAll = element("button", "secondary-button", "清空选择");
    clearAll.type = "button";
    actions.append(selectAll, clearAll);
    content.appendChild(actions);

    const grid = element("div", "upload-task-grid");
    const checkboxes = [];
    tasks.forEach((task) => {
      const ready = task.status === "ready_for_review";
      const card = element("article", "upload-task-card");
      if (selected.has(task.task_id)) card.classList.add("is-selected");
      const choice = element("label", "upload-task-choice");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = selected.has(task.task_id);
      checkbox.disabled = !ready;
      checkbox.dataset.taskId = task.task_id;
      checkboxes.push(checkbox);
      choice.append(
        checkbox,
        element("strong", "", `商品 ${task.product_id} · 坑位 ${task.remote_slot_position ?? "待确认"}`),
      );
      const meta = element(
        "p",
        "upload-task-meta",
        `${task.media?.length || 0} 张图片 · ${task.target_ratio || "原比例"}`,
      );
      const title = element("h4", "", task.title || "标题待补充");
      const description = element("p", "upload-task-description", task.description || "描述待补充");
      card.append(choice, meta, title, description);
      (task.blocking_reasons || []).forEach((reason) => {
        card.appendChild(element("p", "upload-task-blocker", reason));
      });
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) selected.add(task.task_id);
        else selected.delete(task.task_id);
        card.classList.toggle("is-selected", checkbox.checked);
        writeApprovalTaskIds(selected);
      });
      grid.appendChild(card);
    });
    content.appendChild(grid);
    selectAll.addEventListener("click", () => {
      checkboxes.filter((checkbox) => !checkbox.disabled).forEach((checkbox) => {
        checkbox.checked = true;
        selected.add(checkbox.dataset.taskId);
        checkbox.closest(".upload-task-card")?.classList.add("is-selected");
      });
      writeApprovalTaskIds(selected);
    });
    clearAll.addEventListener("click", () => {
      checkboxes.forEach((checkbox) => {
        checkbox.checked = false;
        selected.delete(checkbox.dataset.taskId);
        checkbox.closest(".upload-task-card")?.classList.remove("is-selected");
      });
      writeApprovalTaskIds(selected);
    });
  }

  function renderStageResult(schemaComponent) {
    [...activeProductNavigatorCleanups].forEach((cleanup) => cleanup());
    const view = UiState.resultView(uiState);
    (resultRenderers[schemaComponent] || []).forEach((rendererName) => {
      renderResult(rendererName, view);
    });
    if (schemaComponent === "upload_results") {
      renderUploadResults(view);
    }
    if (schemaComponent === "asset_match_gallery") {
      const step = inferAssetMatchingStep(
        view.result?.data,
        uiState.serverStatus,
      );
      const module = document.querySelector(
        '[data-component="AssetMatchGallery"]',
      );
      const content = module?.querySelector("[data-result-content]");
      if (content) {
        const indicator = element("div", "asset-workflow-step");
        indicator.dataset.step = step;
        indicator.append(
          element(
            "strong",
            "",
            step === "folder_review" ? "第 1 步：筛选文件夹" : "第 2 步：选择图片",
          ),
          element(
            "span",
            "",
            step === "gallery_preparing"
              ? "本机正在读取采用文件夹并准备图片预览"
              : step === "image_selection"
                ? "逐个商品选择图片，采用文件夹不会自动采用图片"
                : "先确认每个商品要读取的文件夹",
          ),
        );
        content.prepend(indicator);
      }
      renderFolderOwnershipReview(view);
      if (Array.isArray(view.result?.data?.asset_candidates)
        && view.result.data.asset_candidates.length) {
        renderAssetMatchGallery(view);
      }
    }
    if (schemaComponent === "image_review") renderImageReview(view);
    if (schemaComponent === "slots_copy_editor") renderSlotBoard(view);
    if (schemaComponent === "inspection_matrix") renderInspectionMatrix(view);
    if (schemaComponent === "approval_table") renderUploadTaskConfirmation(view);
  }

  function renderStageLoadingState(stageId) {
    if (stageId === "slots_copy") {
      const module = document.querySelector('[data-component="SlotBoard"]');
      const content = module?.querySelector("[data-result-content]");
      if (!content) return;
      const loading = element("div", "empty-state slot-workflow-loading");
      loading.dataset.emptyState = "正在恢复当前处理进度";
      loading.setAttribute("aria-live", "polite");
      loading.append(
        element("span", "", "◎"),
        element("strong", "", "正在恢复当前处理进度…"),
        element("p", "", "已保存的坑位、图片和文案不会丢失。"),
      );
      content.replaceChildren(loading);
      return;
    }
    if (stageId !== "asset_matching") return;
    const module = document.querySelector('[data-component="AssetMatchGallery"]');
    const content = module?.querySelector("[data-result-content]");
    if (!content) return;
    const loading = element("div", "empty-state");
    loading.dataset.emptyState = "正在加载候选文件夹";
    loading.setAttribute("aria-live", "polite");
    loading.append(
      element("span", "", "◎"),
      element("strong", "", "正在加载候选文件夹…"),
      element("p", "", "加载完成后将同时显示候选数量和文件夹列表。"),
    );
    content.replaceChildren(loading);
  }

  async function loadFolderImageCounts() {
    if (
      folderCountsLoading
      || currentStageId !== "asset_matching"
      || inferAssetMatchingStep(
        uiState.result?.data,
        uiState.serverStatus,
      ) !== "folder_review"
    ) return;
    const candidates = uiState.result?.data?.folder_candidates;
    if (!Array.isArray(candidates) || candidates.length === 0) return;
    const identity = `${sessionId}:${revision}:${
      candidates.map((item) => item.folder_id || "").join(",")
    }`;
    if (
      folderCountsLoadedFor === identity
      && candidates.every(
        (item) => item.image_count_status !== "pending",
      )
    ) return;
    folderCountsLoading = true;
    try {
      const payload = await fetchJson(apiPath(
        "/stages/asset_matching/folder-image-counts",
      ));
      if (currentStageId !== "asset_matching") return;
      const byFolderId = new Map(
        (payload.folder_counts || []).map((item) => [
          String(item.folder_id || ""),
          item,
        ]),
      );
      candidates.forEach((candidate) => {
        const count = byFolderId.get(String(candidate.folder_id || ""));
        if (count) Object.assign(candidate, count);
      });
      folderCountsLoadedFor = identity;
      renderStageResult(stages.get("asset_matching").component);
    } catch (error) {
      candidates.forEach((candidate) => {
        if (candidate.image_count_status === "pending") {
          candidate.image_count_status = "unknown";
          candidate.image_count_reason_code = "FOLDER_COUNT_REQUEST_FAILED";
        }
      });
      folderCountsLoadedFor = identity;
      renderStageResult(stages.get("asset_matching").component);
    } finally {
      folderCountsLoading = false;
    }
  }

  async function loadStage() {
    const requestedStageId = currentStageId;
    const requestedGeneration = stageGeneration;
    const requestedLoadSequence = ++stageLoadSequence;
    if (!sessionId) {
      currentStageInputLoaded = true;
      currentStageHasPersistedInput = false;
      revision = 0;
      revisionLabel.textContent = "0";
      renderStatus();
      renderSubmission();
      renderStageResult(stages.get(requestedStageId).component);
      if (requestedStageId === "setup") {
        await Promise.all([
          discoverImageSources(),
          discoverTeamIndex(),
        ]);
        actionMessage.textContent = UiState.setupConfigurationLoadView(
          "ready",
        ).message;
      }
      return;
    }
    try {
      const payload = await fetchJson(apiPath(`/stages/${requestedStageId}`));
      if (
        requestedStageId !== currentStageId
        || requestedGeneration !== stageGeneration
        || requestedLoadSequence !== stageLoadSequence
      ) return;
      if (requestedStageId === "asset_matching") selectedAssetValidation = null;
      revision = payload.state.revision;
      revisionLabel.textContent = String(revision);
      uiState = UiState.receiveStage(uiState, {
        stageId: requestedStageId,
        status: payload.state.status,
        result: payload.result,
        submission: payload.submission,
      });
      currentProcessingClaim = payload.processing_claim || null;
      currentCollectionStatus = payload.collection_status || null;
      currentHandoffStatus = payload.handoff_status || null;
      currentWorkflowDispatch = payload.workflow_dispatch || null;
      currentBackNavigation = payload.back_navigation || null;
      renderTaskAwareness(payload.task_status);
      currentGalleryJob = payload.gallery_job || null;
      currentGalleryProgress = currentGalleryJob?.progress || null;
      currentStageInputLoaded = true;
      currentStageHasPersistedInput = Boolean(
        payload.input
        && Object.prototype.hasOwnProperty.call(
          payload.input.values || {},
          "task_ids",
        ),
      );
      isHydrating = true;
      try {
        const form = activeForm();
        resetFormForAuthoritativeHydration(form);
        if (payload.input) {
          hydrateForm(form, payload.input.values);
        }
        if (requestedStageId === "approval" && payload.upload_identity) {
          applyApprovalUploadIdentity(payload.upload_identity);
        }
        renderStageResult(stages.get(requestedStageId).component);
      } finally {
        isHydrating = false;
      }
      if (payload.input) {
        const fallbackHistory = payload.input.interaction_history || [];
        const latestFallback = fallbackHistory[fallbackHistory.length - 1];
        if (latestFallback?.interaction_channel === "chat_fallback") {
          actionMessage.textContent =
            `已载入 Codex 对话保底数据（${latestFallback.fallback_reason_code}），` +
            "可在本页继续检查和修改。";
        }
      }
      if (requestedStageId === "setup") {
        hasSavedSetupConfiguration = Boolean(payload.input)
          || hasSavedSetupConfiguration;
        const setupDiscoveryTasks = [];
        if (!payload.input) setupDiscoveryTasks.push(discoverImageSources());
        const teamIndexInput = teamIndexConfig?.querySelector(
          '[name="team_folder_index_root"]',
        );
        if (!payload.input || !teamIndexInput?.value.trim()) {
          setupDiscoveryTasks.push(discoverTeamIndex());
        }
        await Promise.all(setupDiscoveryTasks);
      }
      renderStatus();
      renderHandoffStatus(currentHandoffStatus, currentWorkflowDispatch);
      renderProcessingClaim(currentProcessingClaim, currentCollectionStatus);
      renderSubmission();
      if (
        requestedStageId === "setup"
        && uiState.serverStatus === "draft"
      ) {
        actionMessage.textContent = UiState.setupConfigurationLoadView(
          "ready",
        ).message;
      }
      if (
        requestedStageId === "asset_matching"
        && ["queued", "running"].includes(currentGalleryJob?.status)
      ) {
        actionMessage.textContent = currentGalleryJob.status === "queued"
          ? "文件夹决定已保存，正在等待本机素材执行器。"
          : galleryProgressMessage();
      } else if (
        requestedStageId === "asset_matching"
        && currentGalleryJob?.status === "completed"
      ) {
        actionMessage.textContent = galleryCompletionMessage();
      }
      if (requestedStageId === "asset_matching") {
        loadFolderImageCounts();
      }
    } catch (error) {
      if (
        requestedStageId !== currentStageId
        || requestedGeneration !== stageGeneration
        || requestedLoadSequence !== stageLoadSequence
      ) return;
      actionMessage.textContent = requestedStageId === "setup"
        ? UiState.setupConfigurationLoadView("failed", {
          userMessage: error.userMessage,
        }).message
        : error.userMessage || error.message;
    }
  }

  async function prepareLocalGallery(button) {
    if (persistenceInFlight) {
      actionMessage.textContent = "保存正在进行，请稍后再次确认文件夹。";
      return;
    }
    if (currentStageId !== "asset_matching") return;
    const form = activeForm();
    if (!form) return;
    window.clearTimeout(autoSaveTimer);
    clearFieldErrors(form);
    const folderCandidates = Array.isArray(uiState.result?.data?.folder_candidates)
      ? uiState.result.data.folder_candidates
      : [];
    materializeFolderDecisions(folderCandidates);
    let values;
    try {
      values = serializeForm(form);
    } catch (error) {
      actionMessage.textContent = error.message;
      return;
    }
    const currentStep = inferAssetMatchingStep(
      uiState.result?.data,
      uiState.serverStatus,
    );
    if (
      currentStep === "image_selection"
      && !UiState.galleryNeedsReload(
        uiState.result?.data?.gallery_identity?.prepared_folder_keys,
        values.folder_decisions,
        [...removedProductIdSet()],
      )
    ) {
      actionMessage.textContent = "文件夹选择没有变化，无需重新加载图片。";
      return;
    }
    const requestRevision = Number(revision);
    if (!Number.isInteger(requestRevision)) {
      actionMessage.textContent = "页面状态尚未同步，请刷新当前页面后重试。";
      return;
    }
    const requestedGeneration = stageGeneration;
    let finalGeneration = requestedGeneration;
    persistenceInFlight = true;
    button.disabled = true;
    renderStatus();
    actionMessage.textContent =
      "正在确认文件夹并启动本机图片加载…";
    try {
      await ensureSession();
      const persistenceIdentity = persistenceRequestId(
        "asset_matching",
        "local_gallery",
      );
      const payload = await fetchJson(apiPath(
        "/stages/asset_matching/prepare-gallery",
      ), {
        method: "POST",
        body: JSON.stringify({
          revision: requestRevision,
          request_id: persistenceIdentity.value,
          values,
        }),
      });
      if (
        currentStageId !== "asset_matching"
        || requestedGeneration !== stageGeneration
      ) return;
      resetSelectionPreflightClientState("");
      stageGeneration += 1;
      finalGeneration = stageGeneration;
      revision = payload.revision;
      revisionLabel.textContent = String(revision);
      currentGalleryJob = payload.gallery_job || null;
      currentGalleryProgress = currentGalleryJob?.progress || null;
      uiState = UiState.receiveStage(uiState, {
        stageId: "asset_matching",
        status: "draft",
        result: uiState.result,
        submission: null,
      });
      persistenceRequestIds.delete(persistenceIdentity.key);
      renderStatus();
      renderSubmission();
      renderStageResult(stages.get("asset_matching").component);
      actionMessage.textContent =
        "文件夹选择已保存，正在加载候选图片。";
    } catch (error) {
      const fieldErrors = error.payload?.field_errors;
      if (fieldErrors) showFieldErrors(form, fieldErrors);
      const validationMessage = fieldErrors?.folder_decisions
        || (fieldErrors?.image_roots
          ? "图片源路径不完整，请返回任务配置核对图片源。"
          : "")
        || (fieldErrors?.revision
          ? "页面状态已经更新，请刷新当前页面后重试。"
          : "");
      actionMessage.textContent = validationMessage
        || error.userMessage
        || error.message;
      button.disabled = false;
    } finally {
      persistenceInFlight = false;
      if (
        currentStageId === "asset_matching"
        && finalGeneration === stageGeneration
      ) renderStatus();
    }
  }

  async function persistStage(mode, { automatic = false, queued = false } = {}) {
    if (persistenceInFlight) {
      if (mode === "submit" && activePersistenceMode === "submit") {
        actionMessage.textContent = "当前提交正在处理中，请勿重复点击。";
        return;
      }
      pendingPersistenceMode = UiState.mergePersistenceIntent(
        pendingPersistenceMode,
        mode,
      );
      actionMessage.textContent = pendingPersistenceMode === "submit"
        ? "草稿保存中；提交已排队，将在保存完成后继续。"
        : "保存正在进行；最新修改将合并到下一次草稿保存。";
      return;
    }
    if (["ready_for_agent", "processing", "completed"].includes(uiState.serverStatus)) {
      renderStatus();
      return;
    }
    if (
      mode === "submit"
      && currentStageId === "approval"
      && !(await ensureApprovalUploadIdentityForSubmit())
    ) {
      return;
    }
    const startedEditVersion = localEditVersion;
    let completedSuccessfully = false;
    if (mode === "submit") window.clearTimeout(autoSaveTimer);
    const requestedStageId = currentStageId;
    const requestedGeneration = stageGeneration;
    beginPersistenceUi(mode);
    if (mode === "submit") {
      actionMessage.textContent = "正在检查当前内容，请稍候…";
      await paintBusyState();
      if (
        requestedStageId !== currentStageId
        || requestedGeneration !== stageGeneration
      ) {
        releasePersistenceUi();
        return;
      }
    }
    if (
      mode === "submit"
      && requestedStageId === "asset_matching"
      && inferAssetMatchingStep(
        uiState.result?.data,
        uiState.serverStatus,
      ) !== "image_selection"
    ) {
      actionMessage.textContent =
        "请使用文件夹列表下方的“确认文件夹并加载图片”。";
      releasePersistenceUi();
      return;
    }
    if (
      mode === "submit"
      && requestedStageId === "asset_matching"
      && inferAssetMatchingStep(
        uiState.result?.data,
        uiState.serverStatus,
      ) === "image_selection"
    ) {
      const hydrationKey = selectionPreflightContextKey();
      if (
        selectedAssetDecisions().length
        && hydrationKey
        && selectionPreflightHydrationKey !== hydrationKey
      ) {
        actionMessage.textContent = "正在恢复已选图片的预裁剪检查状态…";
      }
      await hydrateSelectionPreflights();
      if (
        requestedStageId !== currentStageId
        || requestedGeneration !== stageGeneration
      ) {
        releasePersistenceUi();
        return;
      }
    }
    const form = activeForm();
    if (!form) {
      releasePersistenceUi();
      return;
    }
    clearFieldErrors(form);
    if (mode === "submit") {
      const fieldErrors = clientFieldErrors(form);
      if (Object.keys(fieldErrors).length) {
        showFieldErrors(form, fieldErrors);
        const message = focusFirstFieldError(form, fieldErrors);
        actionMessage.textContent = `${message}，页面已定位到需要补充的位置。`;
        releasePersistenceUi();
        return;
      }
    }
    if (
      mode === "submit"
      && requestedStageId === "setup"
      && focusFirstIncompleteImageSource()
    ) {
      const count = incompleteImageSourceRows().length;
      updateImageSourceIncompleteHint();
      actionMessage.textContent = `还有 ${count} 个图片源未填写完整，请补充后再提交。`;
      releasePersistenceUi();
      return;
    }
    let values;
    try {
      values = serializeForm(form);
    } catch (error) {
      actionMessage.textContent = error.message;
      releasePersistenceUi();
      return;
    }
    if (
      mode === "submit"
      && requestedStageId === "setup"
      && !(await checkTeamIndex())
    ) {
      actionMessage.textContent =
        "团队索引文件夹检测未通过；请选择可访问的索引文件夹后再提交。";
      releasePersistenceUi();
      return;
    }
    if (
      mode === "submit"
      && requestedStageId === "setup"
      && !(await checkImageSources())
    ) {
      actionMessage.textContent =
        "图片源检测未通过；请修改本次配置后再提交，工作台尚未接收。";
      releasePersistenceUi();
      return;
    }
    if (
      mode === "submit"
      && requestedStageId === "asset_matching"
      && inferAssetMatchingStep(uiState.result?.data, uiState.serverStatus)
        === "image_selection"
    ) {
      const removedProducts = new Set(
        (Array.isArray(values.removed_product_ids)
          ? values.removed_product_ids
          : [])
          .map((value) => String(value || "").trim())
          .filter(Boolean),
      );
      const decisions = uniqueSelectedAssetDecisions(values.asset_decisions)
        .filter(
          (item) => !removedProducts.has(String(item.product_id || "")),
        );
      values.asset_decisions = decisions;
      const selectedAssetIds = new Set(
        decisions.map((item) => String(item.asset_id || "")),
      );
      const seenLicenseAssetIds = new Set();
      values.license_decisions = (Array.isArray(values.license_decisions)
        ? values.license_decisions
        : []).filter((item) => {
        const assetId = String(item?.asset_id || "");
        if (
          item?.status !== "confirmed"
          || !selectedAssetIds.has(assetId)
          || seenLicenseAssetIds.has(assetId)
        ) return false;
        seenLicenseAssetIds.add(assetId);
        return true;
      });
      const pendingSelectionCount = (
        selectionPreflightScheduler.desiredPendingCount()
      );
      if (pendingSelectionCount) {
        const message = `还有 ${pendingSelectionCount} 张图片正在进行预裁剪检查，请等待完成。`;
        showFieldErrors(form, { asset_decisions: message });
        actionMessage.textContent = message;
        releasePersistenceUi();
        return;
      }
      let unchecked = decisions.filter((item) => {
        const result = selectionPreflightFor(item.asset_id);
        return !result || !["passed", "warning", "blocked"].includes(result.status);
      });
      if (unchecked.length) {
        const allCandidates = Array.isArray(uiState.result?.data?.asset_candidates)
          ? uiState.result.data.asset_candidates
          : [];
        const byCandidateKey = new Map(
          allCandidates.map((candidate) => [
            `${String(candidate.product_id || "")}\u0000${String(candidate.asset_id || "")}`,
            candidate,
          ]),
        );
        actionMessage.textContent = `正在补检 ${unchecked.length} 张历史已选图片…`;
        await Promise.allSettled(unchecked.map((item) => {
          const candidate = byCandidateKey.get(
            `${String(item.product_id || "")}\u0000${String(item.asset_id || "")}`,
          );
          return candidate ? runSelectionPreflight(candidate) : Promise.resolve(null);
        }));
        unchecked = decisions.filter((item) => {
          const result = selectionPreflightFor(item.asset_id);
          return !result || !["passed", "warning", "blocked"].includes(result.status);
        });
      }
      const blocked = decisions.filter(
        (item) => selectionPreflightFor(item.asset_id)?.status === "blocked",
      );
      if (blocked.length) {
        const message = `有 ${blocked.length} 张已选图片未通过预裁剪，已在“已选素材”中标红，请取消后更换。`;
        showFieldErrors(form, { asset_decisions: message });
        actionMessage.textContent = message;
        releasePersistenceUi();
        renderStageResult(stages.get(currentStageId).component);
        return;
      }
      if (unchecked.length) {
        actionMessage.textContent =
          `正在由工作台重新核验 ${unchecked.length} 张已选图片；已选内容不会丢失。`;
      }
      values.asset_decisions = decisions.map((item) => {
        const result = selectionPreflightFor(item.asset_id);
        return {
          ...item,
          selection_preflight_identity: String(
            result?.identity_sha256 || item.selection_preflight_identity || "",
          ),
          feasible_ratios: [...(
            result?.feasible_ratios || item.feasible_ratios || []
          )],
        };
      });
      writeJsonListControl("asset_decisions", values.asset_decisions);
      const counts = new Map();
      const ratioCounts = new Map();
      const ratioKnowledgeCounts = new Map();
      decisions.forEach((item) => {
        const productId = String(item.product_id || "");
        counts.set(productId, (counts.get(productId) || 0) + 1);
        const current = ratioCounts.get(productId) || { "3:4": 0, "1:1": 0 };
        const feasibleRatios = selectionPreflightFor(item.asset_id)?.feasible_ratios
          || item.feasible_ratios
          || [];
        if (feasibleRatios.length) {
          ratioKnowledgeCounts.set(
            productId,
            (ratioKnowledgeCounts.get(productId) || 0) + 1,
          );
        }
        feasibleRatios
          .forEach((ratio) => {
            if (Object.hasOwn(current, ratio)) current[ratio] += 1;
          });
        ratioCounts.set(productId, current);
      });
      const requiredProducts = Array.isArray(uiState.result?.data?.requirements)
        ? uiState.result.data.requirements
          .map((item) => String(item?.product_id || ""))
          .filter((productId) => productId && !removedProducts.has(productId))
        : [];
      const shortages = requiredProducts
        .filter((productId) => (counts.get(productId) || 0) < 3)
        .map((productId) => (
          `${productId} 还差 ${3 - (counts.get(productId) || 0)} 张`
        ));
      const incompatibleRatios = requiredProducts.filter((productId) => {
        const current = ratioCounts.get(productId) || { "3:4": 0, "1:1": 0 };
        return (ratioKnowledgeCounts.get(productId) || 0)
          === (counts.get(productId) || 0)
          && Math.max(current["3:4"], current["1:1"]) < 3;
      });
      if (!requiredProducts.length || shortages.length || incompatibleRatios.length) {
        const message = !decisions.length
          ? "每个商品至少采用 3 张图片；当前草稿可以继续保存。"
          : shortages.length
            ? `完整坑位至少需要 3 张图片：${shortages.join("；")}`
            : `每个商品至少需要 3 张共同支持同一比例的图片：${incompatibleRatios.join("、")}`;
        showFieldErrors(form, { asset_decisions: message });
        actionMessage.textContent = message;
        releasePersistenceUi();
        return;
      }
    }

    saveButton.disabled = true;
    submitButton.disabled = mode === "submit";
    submitButton.textContent = mode === "draft" ? "保存后提交" : "正在提交…";
    actionMessage.textContent = mode === "draft"
      ? "正在保存草稿…"
      : queued
        ? "草稿已保存，正在执行排队的提交…"
        : "正在提交…";
    let requestIdentity = null;
    try {
      await ensureSession();
      if (requestedStageId !== currentStageId || requestedGeneration !== stageGeneration) return;
      requestIdentity = UiState.createRequestIdentity(
        requestedStageId,
        sessionId,
        requestedGeneration,
      );
      const body = mode === "draft"
        ? UiState.draftRequestBody(values, revision)
        : { values };
      if (mode === "submit") body.revision = revision + 1;
      const persistenceIdentity = persistenceRequestId(
        requestedStageId,
        mode,
      );
      body.request_id = persistenceIdentity.value;
      const payload = await fetchJson(apiPath(
        `/stages/${requestedStageId}${stageActions[mode]}`,
      ), {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (!UiState.isCurrentRequest(
        requestIdentity,
        currentStageId,
        sessionId,
        stageGeneration,
      )) return;
      if (mode === "submit") {
        revision = payload.revision;
        uiState = UiState.receiveStage(uiState, {
          stageId: requestedStageId,
          status: payload.status || "ready_for_agent",
          result: null,
          submission: { created_at: payload.created_at },
        });
        renderStatus();
        renderSubmission();
        renderStageResult(stages.get(requestedStageId).component);
        actionMessage.textContent = payload.status === "completed"
          ? requestedStageId === "slots_copy"
            ? payload.next_stage === "approval"
              ? "上传前检查已通过，正在进入上传任务确认。"
              : "上传前检查发现需要修改的内容，已返回当前页面。"
            : "当前阶段已完成，可直接进入下一阶段检查。"
          : requestedStageId === "approval"
            ? "上传任务已提交，正在上传所选任务。"
            : "已提交，正在排队处理。";
        if (
          payload.status !== "completed"
          && currentWorkflowDispatch?.online !== true
        ) {
          await loadRecoveryInstruction(requestedStageId);
        } else if (payload.next_stage && stages.has(payload.next_stage)) {
          sessionCurrentStageId = payload.next_stage;
          activateStage(payload.next_stage);
        }
      } else {
        revision = UiState.persistedRevision(payload);
        const preservesReviewContext = [
          "completeness",
          "asset_matching",
          "image_review",
          "slots_copy",
          "dry_run",
          "approval",
        ]
          .includes(requestedStageId);
        uiState = UiState.receiveStage(uiState, {
          stageId: requestedStageId,
          status: "draft",
          result: preservesReviewContext ? uiState.result : null,
          submission: null,
        });
        renderStatus();
        renderSubmission();
        if (!preservesReviewContext) {
          renderStageResult(stages.get(requestedStageId).component);
        }
        actionMessage.textContent = automatic
          ? `草稿已自动保存 · ${new Date().toLocaleTimeString()}`
          : "当前进度已保存，不会开始下一步。";
      }
      revisionLabel.textContent = String(revision);
      persistenceRequestIds.delete(persistenceIdentity.key);
      completedSuccessfully = true;
    } catch (error) {
      if (requestedStageId !== currentStageId || requestedGeneration !== stageGeneration) return;
      if (requestIdentity && !UiState.isCurrentRequest(
        requestIdentity,
        currentStageId,
        sessionId,
        stageGeneration,
      )) return;
      const fieldErrors = error.fieldErrors || {};
      showFieldErrors(form, fieldErrors);
      const firstFieldMessage = error.payload?.asset_validation
        ? ""
        : focusFirstFieldError(form, fieldErrors);
      if (
        requestedStageId === "asset_matching"
        && error.payload?.asset_validation
      ) {
        selectedAssetValidation = error.payload.asset_validation;
        renderStageResult(stages.get(requestedStageId).component);
        window.requestAnimationFrame(() => {
          document.querySelector(
            '.selected-asset-card[data-validation-severity="blocked"]',
          )?.scrollIntoView({ behavior: "smooth", block: "center" });
        });
      }
      const pausedSubmission = pendingPersistenceMode === "submit";
      pendingPersistenceMode = null;
      const persistenceConflict = [
        "REVISION_CONTENT_CONFLICT",
        "STAGE_TRANSACTION_ACTIVE",
        "STAGE_PERSISTENCE_INCOMPLETE",
        "PERSISTENCE_ACCESS_DENIED",
      ].includes(error.reasonCode);
      if (persistenceConflict) {
        await loadStage();
      }
      const waitStillLive = currentHandoffStatus?.agent_wait
        && !currentHandoffStatus.agent_wait.expired;
      const suffix = waitStillLive
        ? "；任务仍在等待，但本次提交尚未成功"
        : "";
      const failureMessage = firstFieldMessage
        || error.userMessage
        || error.message;
      actionMessage.textContent = pausedSubmission
        ? `保存失败，排队的提交已暂停：${failureMessage}${suffix}`
        : mode === "submit"
          ? `尚未提交，工作台未接收：${failureMessage}${suffix}`
          : `${failureMessage}${suffix}`;
      uiState = UiState.markDirty(uiState);
      renderStatus();
    } finally {
      releasePersistenceUi();
      const pending = pendingPersistenceMode;
      pendingPersistenceMode = null;
      if (
        pendingBackNavigation
        && completedSuccessfully
        && requestedStageId === currentStageId
        && requestedGeneration === stageGeneration
      ) {
        pendingBackNavigation = false;
        await reopenPreviousStage({ alreadyConfirmed: true });
        return;
      }
      if (pendingBackNavigation && !completedSuccessfully) {
        pendingBackNavigation = false;
        renderStatus();
      }
      if (
        completedSuccessfully
        && requestedStageId === currentStageId
        && requestedGeneration === stageGeneration
        && pending
      ) {
        if (pending === "draft" && localEditVersion === startedEditVersion) {
          actionMessage.textContent = automatic
            ? `草稿已自动保存 · ${new Date().toLocaleTimeString()}`
            : "草稿已保存；重复保存已合并。";
        } else {
          await persistStage(pending, {
            automatic: pending === "draft",
            queued: true,
          });
        }
      }
    }
  }

  async function withdrawSubmission() {
    if (!sessionId || uiState.serverStatus !== "ready_for_agent") return;
    withdrawButton.disabled = true;
    actionMessage.textContent = "正在撤回尚未开始处理的提交…";
    try {
      await fetchJson(apiPath(`/stages/${currentStageId}/withdraw`), {
        method: "POST",
        body: JSON.stringify({ revision }),
      });
      await loadStage();
      actionMessage.textContent = "提交已撤回，可以继续修改。";
    } catch (error) {
      actionMessage.textContent = error.message;
      await loadStage();
    }
  }

  async function loadRecoveryInstruction(stageId = currentStageId) {
    recoveryButton.disabled = true;
    if (!sessionId) return;
    try {
      const payload = await fetchJson(apiPath(`/stages/${stageId}/recovery`));
      uiState = UiState.receiveRecovery(uiState, stageId, payload.instruction);
      if (
        currentStageId === stageId
        && uiState.recoveryInstruction
        && ["ready_for_agent", "processing", "needs_user_input", "blocked"].includes(uiState.serverStatus)
      ) {
        recoveryButton.disabled = false;
      }
    } catch (error) {
      if (currentStageId === stageId) recoveryButton.disabled = true;
    }
  }

  async function copyRecoveryInstruction() {
    if (!uiState.recoveryInstruction) await loadRecoveryInstruction(currentStageId);
    if (!uiState.recoveryInstruction) return;
    await navigator.clipboard.writeText(uiState.recoveryInstruction);
    recoveryButton.textContent = "已复制继续处理说明";
    window.setTimeout(() => { recoveryButton.textContent = "复制继续处理说明"; }, 1800);
  }

  async function pollStage() {
    if (document.hidden || !sessionId) return;
    const requestedStageId = currentStageId;
    try {
      const requests = [
        fetchJson(apiPath(`/stages/${requestedStageId}/status`)),
        fetchJson(apiPath()),
      ];
      if (requestedStageId === "asset_matching") {
        requests.push(fetchJson(apiPath(
          "/stages/asset_matching/gallery-job",
        )));
      }
      const [stageState, sessionPayload, galleryPayload] =
        await Promise.all(requests);
      if (requestedStageId !== currentStageId) return;
      const priorRevision = revision;
      const priorStatus = uiState.serverStatus;
      const stageChanged = UiState.stagePollChanged(
        priorRevision,
        priorStatus,
        stageState.revision,
        stageState.status,
      );
      revision = stageState.revision;
      revisionLabel.textContent = String(revision);
      const heartbeat = sessionPayload.session.last_agent_heartbeat;
      applySessionSnapshot(sessionPayload.session);
      if (
        stageState.status === "completed"
        && stages.has(sessionCurrentStageId)
        && sessionCurrentStageId !== requestedStageId
      ) {
        activateStage(sessionCurrentStageId);
        return;
      }
      uiState = UiState.receiveStatus(uiState, stageState.status, heartbeat);
      currentProcessingClaim = stageState.processing_claim || null;
      currentCollectionStatus = stageState.collection_status || null;
      currentHandoffStatus = stageState.handoff_status || null;
      currentWorkflowDispatch = stageState.workflow_dispatch || null;
      const priorBackNavigation = currentBackNavigation;
      currentBackNavigation = stageState.back_navigation || null;
      const backNavigationChanged = !UiState.jsonSemanticallyEqual(
        priorBackNavigation || null,
        currentBackNavigation || null,
      );
      renderTaskAwareness(sessionPayload.task_status || stageState.task_status);
      const priorGalleryStatus = currentGalleryJob?.status || null;
      const priorGalleryAttempt = currentGalleryJob?.attempt_id || null;
      const priorAvailableCandidates = Number(
        currentGalleryJob?.progress?.available_candidate_count || 0,
      );
      const priorPublishedBatches = Number(
        currentGalleryJob?.progress?.published_batch_count || 0,
      );
      if (requestedStageId === "asset_matching") {
        currentGalleryJob = galleryPayload?.gallery_job || null;
        currentGalleryProgress = currentGalleryJob?.progress || null;
      }
      const connection = UiState.connectionView(uiState, Date.now(), {
        agentWait: currentHandoffStatus?.agent_wait || null,
        processingClaim: currentProcessingClaim,
        workflowDispatch: currentWorkflowDispatch,
      });
      connectionLabel.textContent = connection.connectionLabel;
      offlinePanel.hidden = !connection.offline;
      const galleryChanged = requestedStageId === "asset_matching"
        && (
          priorGalleryStatus !== (currentGalleryJob?.status || null)
          || priorGalleryAttempt !== (currentGalleryJob?.attempt_id || null)
          || priorAvailableCandidates !== Number(
            currentGalleryJob?.progress?.available_candidate_count || 0,
          )
          || priorPublishedBatches !== Number(
            currentGalleryJob?.progress?.published_batch_count || 0,
          )
        );
      const resultHydrationRequired = UiState.stageNeedsResultHydration(
        requestedStageId,
        stageState.status,
        uiState.result,
        currentStageInputLoaded,
        stageState.revision,
      );
      const stageHydrationChanged = (
        stageChanged
        || galleryChanged
        || backNavigationChanged
        || resultHydrationRequired
      );
      const hydrationDeferred = UiState.shouldDeferEditableStageHydration({
        stageId: requestedStageId,
        workflowStep: requestedStageId === "asset_matching"
          ? inferAssetMatchingStep(uiState.result?.data, uiState.serverStatus)
          : requestedStageId === "slots_copy"
            ? slotsCopyActiveSubpage
            : "",
        dirty: uiState.dirty,
        persistenceInFlight,
        pendingPreflightCount: selectionPreflightScheduler.desiredPendingCount(),
        globalAssetSelectionInFlight,
        cropPreflightInFlight,
        copyRequestInFlight: localCopyRequestInFlight,
      });
      if (stageHydrationChanged && !hydrationDeferred) {
        renderStatus();
        await loadStage();
      } else {
        renderHandoffStatus(currentHandoffStatus, currentWorkflowDispatch);
        renderProcessingClaim(currentProcessingClaim, currentCollectionStatus);
        if (currentGalleryJob?.status === "queued") {
          actionMessage.textContent =
            "文件夹决定已保存，正在等待本机素材执行器。";
        } else if (currentGalleryJob?.status === "running") {
          actionMessage.textContent = galleryProgressMessage();
        } else if (currentGalleryJob?.status === "completed") {
          actionMessage.textContent = galleryCompletionMessage();
        } else if (["failed", "stale"].includes(currentGalleryJob?.status)) {
          actionMessage.textContent =
            `图片加载未完成：${currentGalleryJob.message || currentGalleryJob.reason_code || "未知错误"}。`;
        }
      }
    } catch (error) {
      offlinePanel.hidden = false;
      connectionLabel.textContent = "当前任务状态暂时无法读取";
      if (taskAwareness && taskStatusLabel && taskStatusSummary) {
        taskAwareness.dataset.phase = "offline";
        taskStatusLabel.textContent = "当前任务已停止或暂时无法连接";
        taskStatusSummary.textContent =
          "任务记录仍然保留；需要继续查看或处理时，可恢复同一个任务。";
      }
    }
  }

  async function activateStage(stageId) {
    if (!stages.has(stageId)) return;
    if (
      globalAssetSelectionInFlight
      && stageId !== currentStageId
    ) {
      actionMessage.textContent = "正在为全部商品选图，完成后可以切换步骤。";
      return;
    }
    const previousStageId = currentStageId;
    if (currentStageId === "asset_matching" || stageId === "asset_matching") {
      resetSelectionPreflightClientState();
    }
    if (previousStageId !== stageId) {
      localCopyRequestInFlight = false;
      if (previousStageId === "slots_copy" || stageId === "slots_copy") {
        slotsCopyActiveSubpage = "";
      }
    }
    stageGeneration += 1;
    window.clearTimeout(autoSaveTimer);
    pendingPersistenceMode = null;
    stageLocalActionInFlight = false;
    currentStageId = stageId;
    uiState = UiState.switchStage(uiState, stageId);
    currentProcessingClaim = null;
    currentHandoffStatus = null;
    currentWorkflowDispatch = null;
    currentBackNavigation = null;
    currentGalleryJob = null;
    currentGalleryProgress = null;
    currentStageInputLoaded = false;
    currentStageHasPersistedInput = false;
    recoverProcessingButton.hidden = true;
    recoveryButton.disabled = true;
    revision = 0;
    revisionLabel.textContent = "0";
    railButtons.forEach((button) => {
      const active = button.dataset.stageId === stageId;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-current", active ? "step" : "false");
    });
    panels.forEach((panel) => {
      const active = panel.dataset.stagePanel === stageId;
      panel.classList.toggle("is-active", active);
      panel.hidden = !active;
    });
    const stage = stages.get(stageId);
    titleLabel.textContent = stage.title;
    currentStageLabel.textContent = stage.title;
    actionMessage.textContent = stageId === "setup"
      ? UiState.setupConfigurationLoadView("loading", {
        hasSavedConfiguration: hasSavedSetupConfiguration,
      }).message
      : "正在加载当前步骤…";
    renderStageLoadingState(stageId);
    renderStatus();
    renderSubmission();
    if (stageId === "setup") initializeSetupLoginGate();
    window.scrollTo({ top: 0, behavior: "smooth" });
    await loadStage();
    if (sessionId) loadRecoveryInstruction(stageId);
  }

  railButtons.forEach((button) => {
    button.addEventListener("click", () => activateStage(button.dataset.stageId));
  });
  goCurrentStageButton.addEventListener("click", () => {
    if (stages.has(sessionCurrentStageId)) activateStage(sessionCurrentStageId);
  });
  async function reopenPreviousStage({ alreadyConfirmed = false } = {}) {
    if (
      !currentBackNavigation?.enabled
      || !currentBackNavigation?.target_stage_id
      || stageLocalActionInFlight
      || globalAssetSelectionInFlight
      || (currentStageId === "slots_copy" && cropPreflightInFlight)
    ) return;
    const unsavedWarning = uiState.dirty
      ? " 当前步骤尚未保存的修改也不会保留。"
      : "";
    if (!alreadyConfirmed) {
      const confirmed = await confirmAction({
        title: "返回上一步",
        message: `${currentBackNavigation.message || "返回后，当前步骤以及后面的选择会失效。"}`
          + `${unsavedWarning}\n\n是否继续？`,
        confirmLabel: "确认返回",
        danger: true,
      });
      if (!confirmed) return;
    }
    window.clearTimeout(autoSaveTimer);
    pendingPersistenceMode = null;
    if (persistenceInFlight) {
      pendingBackNavigation = true;
      actionMessage.textContent = "正在完成当前保存，随后自动返回上一步…";
      renderStatus();
      return;
    }
    backButton.disabled = true;
    actionMessage.textContent = "正在返回上一步…";
    try {
      const payload = await fetchJson(apiPath(
        `/stages/${encodeURIComponent(currentStageId)}/back`,
      ), {
        method: "POST",
        body: JSON.stringify({ revision }),
      });
      sessionCurrentStageId = payload.target_stage_id;
      currentBackNavigation = null;
      await activateStage(payload.target_stage_id);
    } catch (error) {
      actionMessage.textContent = error.userMessage || error.message;
      await loadStage();
    }
  }

  async function endCurrentTask() {
    if (!sessionId || endCurrentTaskInFlight) return;
    const confirmed = await confirmAction({
      title: "结束当前任务",
      message: "确定结束当前任务吗？未完成流程将暂停，任务记录会保留，之后仍可恢复。",
      confirmLabel: "确认结束",
      danger: true,
    });
    if (!confirmed) return;
    endCurrentTaskInFlight = true;
    endCurrentTaskButton.disabled = true;
    endCurrentTaskButton.textContent = "正在结束…";
    actionMessage.textContent = "正在安全结束当前任务…";
    let accepted = false;
    try {
      const payload = await fetchJson(apiPath("/end"), {
        method: "POST",
        body: JSON.stringify({}),
      });
      accepted = payload.status === "closing";
      taskAwareness.dataset.phase = "completed";
      taskStatusLabel.textContent = "当前任务正在结束";
      taskStatusSummary.textContent =
        "任务正在结束；记录已经保留，之后可以恢复同一个任务。";
      taskAutoShutdown.hidden = false;
      taskAutoShutdown.textContent = "关闭后当前页面将停止刷新。";
      actionMessage.textContent = payload.message || "当前任务正在结束。";
    } catch (error) {
      actionMessage.textContent = error.userMessage || error.message;
    } finally {
      if (!accepted) {
        endCurrentTaskInFlight = false;
        endCurrentTaskButton.disabled = false;
        endCurrentTaskButton.textContent = "结束当前任务";
      }
    }
  }

  backButton.addEventListener("click", async () => {
    await reopenPreviousStage();
  });
  recoverProcessingButton.addEventListener("click", recoverExpiredProcessing);
  retryGalleryButton.addEventListener("click", async () => {
    retryGalleryButton.disabled = true;
    actionMessage.textContent = "正在重新启动本地图片加载…";
    try {
      const payload = await fetchJson(apiPath(
        "/stages/asset_matching/gallery-job/retry",
      ), {
        method: "POST",
        body: JSON.stringify({}),
      });
      currentGalleryJob = payload.gallery_job || null;
      currentGalleryProgress = currentGalleryJob?.progress || null;
      renderStatus();
    } catch (error) {
      actionMessage.textContent = error.userMessage || error.message;
    } finally {
      retryGalleryButton.disabled = false;
    }
  });
  panels.forEach((panel) => {
    panel.addEventListener("input", (event) => {
      if (isHydrating) return;
      if (!event.target?.getAttribute?.("name")) return;
      if (
        !event.isTrusted
        && event.detail?.source !== "explicit-user-edit"
      ) return;
      if (panel.dataset.stagePanel === currentStageId) {
        localEditVersion += 1;
        persistenceRequestIds.delete(
          `${sessionId}:${currentStageId}:draft`,
        );
        persistenceRequestIds.delete(
          `${sessionId}:${currentStageId}:submit`,
        );
        uiState = UiState.markDirty(uiState);
        renderStatus();
        scheduleAutoSave();
      }
    });
  });
  saveButton.addEventListener("click", () => persistStage("draft"));
  submitButton.addEventListener("click", () => persistStage("submit"));
  recoveryButton.addEventListener("click", copyRecoveryInstruction);
  withdrawButton.addEventListener("click", withdrawSubmission);
  endCurrentTaskButton?.addEventListener("click", endCurrentTask);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) pollStage();
  });

  setInterval(pollStage, 2000);
  initializeImageSourceConfig();
  initializeTeamIndexConfig();
  initializeLarkBaseConfig();
  initializeCollectionRuntime();
  async function bootstrap() {
    let initialStage = currentStageId;
    let warning = null;
    if (sessionId) {
      try {
        const payload = await fetchJson(apiPath());
        applySessionSnapshot(payload.session);
        renderTaskAwareness(payload.task_status);
        const selected = UiState.selectInitialStage(
          [...stages.keys()],
          payload.session?.current_stage,
        );
        initialStage = selected.stageId;
        warning = selected.warning;
      } catch (error) {
        warning = UiState.setupConfigurationLoadView("failed", {
          userMessage: error.userMessage,
        }).message;
      }
    }
    activateStage(initialStage);
    if (warning) actionMessage.textContent = warning;
  }
  bootstrap();
})();
