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
  const imageSourceConfig = document.querySelector('[data-component="ImageSourceConfig"]');
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
  let persistenceInFlight = false;
  let pendingPersistenceMode = null;
  let localEditVersion = 0;
  const persistenceRequestIds = new Map();
  let currentProcessingClaim = null;
  let currentCollectionStatus = null;
  let currentHandoffStatus = null;
  let currentWorkflowDispatch = null;
  let currentGalleryProgress = null;
  let currentGalleryJob = null;
  let isHydrating = false;
  let currentStageInputLoaded = false;
  let currentStageHasPersistedInput = false;
  let folderCountsLoading = false;
  let folderCountsLoadedFor = "";
  let setupLoginReady = !setupLoginGate;
  let setupLoginGateStarted = false;
  let selectedAssetValidation = null;
  const selectionPreflights = new Map();
  const selectionPreflightCardRefreshers = new Map();
  const appliedSelectionPreflightIntents = new Map();
  const selectionPreflightScheduler = UiState.createSelectionPreflightScheduler({
    maxConcurrent: 6,
    maxWeight: 8,
    execute: async (candidate) => {
      const assetId = String(candidate.asset_id || "");
      const entry = await fetchJson(apiPath(
        `/stages/asset_matching/assets/${encodeURIComponent(assetId)}/selection-preflight`,
      ), { method: "POST", body: "{}" });
      selectionPreflights.set(assetId, entry);
      return entry;
    },
    onChange: (job) => {
      selectionPreflightCardRefreshers.get(job.assetId)?.();
      if (currentStageId === "asset_matching") renderStatus();
    },
  });
  let selectionPreflightHydrationKey = "";
  let selectionPreflightHydrationInFlight = false;

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

  function formatBytes(value) {
    if (value == null || value === "") return "大小未知";
    const bytes = Number(value);
    if (!Number.isFinite(bytes)) return "读取失败";
    if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)}KB`;
    return `${(bytes / (1024 * 1024)).toFixed(2)}MB`;
  }

  async function fetchJson(path, options = {}) {
    const response = await fetch(path, {
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
    imageSourceConfig.querySelector("[data-image-source-list]").addEventListener("click", (event) => {
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
      button.closest("[data-image-source-row]").remove();
      updateImageSourceConfig();
      uiState = UiState.markDirty(uiState);
      renderStatus();
      scheduleAutoSave();
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
      ? `选择器已验证：${selector.profile_name} · ${selector.profile_version}`
      : `选择器待修复：${selector.reason_code || "SELECTOR_PROFILE_NOT_FOUND"}`;
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

  function showFieldErrors(form, fieldErrors) {
    Object.entries(fieldErrors).forEach(([name, message]) => {
      const errorNode = form.querySelector(`[data-field-error="${CSS.escape(name)}"]`);
      const control = form.querySelector(`[name="${CSS.escape(name)}"]`);
      if (errorNode) errorNode.textContent = message;
      if (control) control.setAttribute("aria-invalid", "true");
    });
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
    const copy = statusCopy[status] || statusCopy.draft;
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
    submitButton.textContent = currentStageId === "slots_copy"
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
    const approvalHasSelectedTasks = currentStageId !== "approval" || String(
      activeForm()?.querySelector('[name="task_ids"]')?.value || "",
    ).split(/\r?\n/).some((taskId) => taskId.trim());
    submitButton.disabled = lockedByServer || !approvalHasSelectedTasks;
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
    saveButton.disabled = lockedByServer;
    if (currentStageId === "setup" && !setupLoginReady) {
      saveButton.disabled = true;
      submitButton.disabled = true;
    }
    goCurrentStageButton.hidden = !(
      lockedByServer
      && stages.has(sessionCurrentStageId)
      && sessionCurrentStageId !== currentStageId
    );
    if (lockedByServer) {
      const reason = {
        completed: "该阶段已完成，不能再次保存或提交。",
        ready_for_agent: "该阶段已提交，工作台后台正在排队处理。",
        processing: "工作台后台正在处理该阶段，当前输入已锁定。",
      }[uiState.serverStatus] || "该阶段当前不可编辑。";
      actionMessage.textContent = goCurrentStageButton.hidden
        ? reason
        : `${reason} 可进入任务当前阶段继续。`;
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
      + `${Number(progress.content_duplicate_count || 0)} 张，可展示候选 `
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
          `检测到滑动验证，${savedPage}。请在 CDP Chrome 中完成验证；` +
          `验证通过后会自动继续采集。等待 ${Math.floor(Number(worker.elapsed_ms || 0) / 1000)} 秒。`;
        return;
      }
      const page = worker.last_completed_page == null
        ? "首个 checkpoint 尚未完成"
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
        "Worker 归属或存活状态暂时无法确认；为避免误杀其他进程，将等待租约过期后再恢复。";
      return;
    }
    if (recoverable) {
      actionMessage.textContent =
        "已确认本任务拥有的采集 Worker 退出；可以立即从同一任务 checkpoint 恢复。";
      return;
    }
    if (!currentProcessingClaim) {
      actionMessage.textContent =
        "该阶段处于处理中，但没有有效租约；工作台后台会校验并恢复原任务。";
      return;
    }
    if (currentProcessingClaim.expired) {
      if (specializedRecovery) {
        actionMessage.textContent =
          "商品选择已经保留，工作台后台将通过唯一处理入口继续。";
        return;
      }
      actionMessage.textContent =
        `工作台后台的处理租约已于 ` +
        `${formatClaimTime(currentProcessingClaim.lease_expires_at)} 过期；` +
        "可以恢复原任务并从已验证断点继续。";
      return;
    }
    actionMessage.textContent =
      `工作台后台正在处理，租约有效至 ` +
      `${formatClaimTime(currentProcessingClaim.lease_expires_at)}；当前输入保持锁定。`;
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
        actionMessage.textContent = "提交已持久化，工作台后台已接收并正在排队。";
        return;
      }
      if (
        workflowDispatch.stage_id === currentStageId
        && workflowDispatch.status === "running"
      ) {
        actionMessage.textContent = "工作台后台正在处理本阶段，页面会自动刷新结果。";
        return;
      }
      if (
        workflowDispatch.stage_id === currentStageId
        && workflowDispatch.status === "failed"
      ) {
        actionMessage.textContent =
          "工作台后台未能完成本阶段，诊断信息已保留；请按页面提示处理异常。";
        return;
      }
      if (currentHandoffStatus.base_status === "ready") {
        actionMessage.textContent = "工作台后台已接收提交，正在准备处理。";
        return;
      }
    }
    const wait = currentHandoffStatus.agent_wait;
    if (currentHandoffStatus.status === "waiting" && wait) {
      actionMessage.textContent =
        `兼容监听已连接（心跳租约约 ${Math.max(0, Number(wait.remaining_seconds || 0))} 秒）；` +
        `本轮最长等待还剩约 ${Math.max(0, Number(wait.budget_remaining_seconds || 0))} 秒。` +
        `表单仍可正常编辑。`;
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
    actionMessage.textContent = "正在校验并恢复过期处理租约…";
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
    if (Array.isArray(result.evidence) && result.evidence.length) {
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
    })[status] || "需人工确认";
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

  function renderInspectionMatrix(view) {
    if (view.mode === "empty") return;
    const products = Array.isArray(view.result?.data?.products)
      ? view.result.data.products
      : [];
    if (!products.length) return;
    const module = document.querySelector('[data-component="InspectionMatrix"]');
    const content = module?.querySelector("[data-result-content]");
    if (!content) return;
    content.replaceChildren();

    const locked = ["ready_for_agent", "processing", "completed"].includes(uiState.serverStatus);
    const selected = completenessSelectedIds();
    const selectableIds = new Set(
      products
        .filter((product) => product.selectable !== false && product.status !== "excluded")
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
    const bulkSelect = element("button", "secondary-button", "选择当前筛选结果");
    bulkSelect.type = "button";
    bulkSelect.disabled = locked;
    const bulkClear = element("button", "secondary-button", "取消当前筛选结果");
    bulkClear.type = "button";
    bulkClear.disabled = locked;
    toolbar.append(search, filter, ownerFilter, bulkSelect, bulkClear);
    content.appendChild(toolbar);

    const list = element("div", "inspection-list");
    content.appendChild(list);

    const visibleProducts = () => {
      return UiState.filterCompletenessProducts(products, {
        query: search.value,
        status: filter.value,
        owner: ownerFilter.value,
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
        const selectable = product.selectable !== false && product.status !== "excluded";
        const card = element("article", "inspection-row");
        card.dataset.status = product.status || "needs_manual_review";

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
        promotion.append(
          element("small", "", "搜推素材"),
          element("strong", "", target == null || current == null ? "坑位待补采" : `${current} / ${target} 篇`),
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
        const choice = document.createElement("label");
        choice.className = "inspection-choice";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = selectable && selected.has(productId);
        checkbox.disabled = locked || !selectable;
        checkbox.setAttribute("aria-label", `选择商品 ${productId} 进入素材匹配`);
        choice.append(
          checkbox,
          document.createTextNode(selectable ? "选择进入素材匹配" : "已按规则排除"),
        );
        controls.append(choice);
        checkbox.addEventListener("change", () => {
          if (checkbox.checked) selected.add(productId);
          else selected.delete(productId);
          writeCompletenessSelectedIds(selected);
          if (selectedCount) selectedCount.textContent = String(selected.size);
        });
        card.append(identity, promotion, controls);
        list.appendChild(card);
      });
    };

    search.addEventListener("input", draw);
    filter.addEventListener("change", draw);
    ownerFilter.addEventListener("change", draw);
    bulkSelect.addEventListener("click", () => {
      visibleProducts().forEach((product) => {
        if (product.selectable !== false && product.status !== "excluded") {
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

  function selectedAssetDecisions() {
    return readJsonListControl("asset_decisions")
      .filter((item) => item?.decision === "selected" && item.asset_id);
  }

  function selectionPreflightFor(assetId) {
    return selectionPreflights.get(String(assetId || "")) || null;
  }

  async function hydrateSelectionPreflights() {
    const key = `${sessionId}:${revision}`;
    if (
      !sessionId
      || selectionPreflightHydrationKey === key
      || selectionPreflightHydrationInFlight
    ) return;
    selectionPreflightHydrationInFlight = true;
    try {
      const payload = await fetchJson(apiPath(
        "/stages/asset_matching/selection-preflights",
      ));
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
    } catch (_error) {
      selectionPreflightHydrationKey = key;
    } finally {
      selectionPreflightHydrationInFlight = false;
    }
  }

  function runSelectionPreflight(candidate) {
    const assetId = String(candidate.asset_id || "");
    if (!assetId) return Promise.reject(new Error("候选图片缺少稳定标识"));
    const existing = selectionPreflightFor(assetId);
    if (existing?.status && existing.status !== "pending") {
      return selectionPreflightScheduler.get(assetId)
        ? selectionPreflightScheduler.select(candidate)
        : Promise.resolve(existing);
    }
    return selectionPreflightScheduler.select(candidate);
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

  function persistSelectedCandidates(productId, selected) {
    const otherProducts = selectedAssetDecisions()
      .filter((item) => String(item.product_id) !== String(productId));
    const selectedRows = selected.map((candidate, index) => {
      const selectionPreflight = selectionPreflightFor(candidate.asset_id);
      return {
        product_id: String(productId),
        asset_id: String(candidate.asset_id),
        sha256: String(candidate.sha256),
        folder_id: String(candidate.folder_id || candidate.resolved_folder_id || ""),
        folder_path: String(candidate.folder_path || candidate.candidate_directory || ""),
        source_system: String(candidate.source_system || ""),
        source_path: String(candidate.source_path || ""),
        decision: "selected",
        selection_order: index + 1,
        selection_preflight_identity: String(
          selectionPreflight?.identity_sha256 || "",
        ),
        feasible_ratios: [...(selectionPreflight?.feasible_ratios || [])],
      };
    });
    const nextDecisions = [...otherProducts, ...selectedRows];
    const changed = writeJsonListControl(
      "asset_decisions",
      nextDecisions,
      { notify: true },
    );
    if (changed && selectedAssetValidation) {
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
  }

  function persistLicense(assetId, confirmed) {
    const retained = readJsonListControl("license_decisions")
      .filter((item) => String(item.asset_id) !== String(assetId));
    if (confirmed) retained.push({ asset_id: String(assetId), status: "confirmed" });
    writeJsonListControl("license_decisions", retained, { notify: true });
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
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
    const candidates = Array.isArray(data?.folder_candidates)
      ? data.folder_candidates.filter(
        (candidate) => candidate?.match_type !== "confirmed_alias",
      )
      : [];
    if (!candidates.length) return;
    const module = document.querySelector('[data-component="AssetMatchGallery"]');
    const content = module?.querySelector("[data-result-content]");
    if (!content) return;

    const review = element("section", "folder-review");
    const safety = element("div", "asset-safety");
    safety.dataset.status = "checked";
    safety.append(
      element("strong", "", "请先筛选候选文件夹"),
      element(
        "span",
        "",
        "精确候选和不少于 5 字的完整名称片段默认采用；3–4 字短片段与 50% 粗略候选默认排除。采用文件夹只会加载候选图片，不会自动采用其中图片。",
      ),
    );
    review.appendChild(safety);

    const decisionsByKey = new Map(
      folderDecisions().map((item) => [
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
      const group = element("section", "folder-product");
      const heading = element("div", "asset-product-heading");
      const headingText = element("div");
      headingText.append(
        element("strong", "", productCandidates[0]?.product_title || `商品 ${productId}`),
        element(
          "span",
          "",
          `商品 ID ${productId} · 货号 ${productCandidates[0]?.sku || "未知"} · ${productCandidates.length} 个候选文件夹`,
        ),
      );
      const progress = element("span", "folder-review-progress");
      heading.append(headingText, progress);
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
        progress.textContent = `采用 ${productCandidates.length - rejected} · 排除 ${rejected}`;
      };

      productCandidates.forEach((candidate) => {
        const key = `${productId}\u0000${candidate.folder_id}`;
        const saved = decisionsByKey.get(key) || {};
        const card = element("article", "folder-card");
        const identity = element("div", "folder-card-identity");
        const countStatus = String(
          candidate.image_count_status || "pending",
        );
        let countText = "素材数统计中";
        if (countStatus === "ready") {
          countText = (
            `文件夹内素材（递归统计）：`
            + `${Number(candidate.raw_recursive_image_count || 0)} 张`
          );
        } else if (countStatus === "unknown") {
          countText = (
            `素材数未知`
            + (
              candidate.image_count_reason_code
                ? `（${candidate.image_count_reason_code}）`
                : ""
            )
          );
        }
        if (candidate.gallery_unique_path_count != null) {
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
          element("small", "folder-image-count", countText),
          element("code", "", candidate.folder_path || ""),
        );
        const controls = element("div", "folder-card-controls");
        const decision = document.createElement("select");
        decision.setAttribute("aria-label", `${candidate.folder_name} 归属决定`);
        [
          ["confirmed", "采用"],
          ["rejected", "排除该文件夹"],
        ].forEach(([value, label]) => {
          const option = document.createElement("option");
          option.value = value;
          option.textContent = label;
          decision.appendChild(option);
        });
        decision.value = (
          saved.decision === "rejected" || candidate.decision === "rejected"
        ) ? "rejected" : "confirmed";
        const note = document.createElement("input");
        note.type = "text";
        note.placeholder = "备注（可选）";
        note.value = saved.note || candidate.note || "";
        const warning = element(
          "span",
          "asset-warning",
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
        controls.append(decision, note, warning);
        card.append(identity, controls);
        list.appendChild(card);

        const syncDecision = () => {
          card.dataset.decision = decision.value;
          persistFolderDecision(
            candidate,
            decision.value,
            note.value,
          );
          updateProgress();
          content.dispatchEvent(new CustomEvent("folder-decision-changed", {
            detail: {
              productId,
              folderId: String(candidate.folder_id || ""),
              decision: decision.value,
            },
          }));
        };
        decision.addEventListener("change", syncDecision);
        note.addEventListener("change", () => {
          persistFolderDecision(candidate, decision.value, note.value);
        });
        card.dataset.decision = decision.value;
      });
      updateProgress();
    });
    if (
      inferAssetMatchingStep(view.result?.data, uiState.serverStatus)
      !== "image_selection"
    ) {
      const localAction = element("div", "local-gallery-action");
      const localButton = element(
        "button",
        "primary-button",
        "确认文件夹并加载图片",
      );
      localButton.type = "button";
      localButton.disabled = ["queued", "running"].includes(
        currentGalleryJob?.status,
      );
      localButton.addEventListener("click", () => {
        prepareLocalGallery(localButton);
      });
      localAction.append(
        element(
          "p",
          "asset-selection-summary",
          "这是本机固定操作，只读取已采用文件夹并生成候选图片，不会创建阶段交接。",
        ),
        localButton,
      );
      review.appendChild(localAction);
    }
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
    const galleryComplete = inferAssetMatchingStep(data, uiState.serverStatus)
      === "image_selection"
      && !["queued", "running"].includes(currentGalleryJob?.status);
    if (galleryComplete) void hydrateSelectionPreflights();
    const folderCandidates = Array.isArray(data?.folder_candidates)
      ? data.folder_candidates.filter(
        (candidate) => candidate?.match_type !== "confirmed_alias",
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
          && (
            supportedFolderProducts === null
            || supportedFolderProducts.has(String(candidate.product_id || ""))
          ),
      )
      : [];
    const requirements = Array.isArray(data?.requirements)
      ? data.requirements.filter(
        (requirement) => supportedFolderProducts === null
          || supportedFolderProducts.has(String(requirement.product_id || "")),
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
            ? "选择本次采用的图片；采用即确认该图片可用于本次发布，每次勾选都会立即检查 1:1、3:4 预裁剪。全部已选图片检查完成后，提交将按每坑 3–9 张自动生成坑位草稿。"
            : "可以先浏览已完成的候选。全部图片准备完成后，系统会自动开放采用和提交。",
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
          ? "已知远端指纹会从候选中排除。"
          : "后台仅有素材 ID，尚无图片指纹；最终上传前必须再次核对。",
      ),
    );
    content.appendChild(safety);

    requirements.forEach((requirement) => {
      const productId = String(requirement.product_id || "");
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
      const discoveredCount = Number(
        prepared?.discovered_images || rawProductCandidates.length,
      );
      const preparedCandidateCount = Number(
        prepared?.planned_inspection_count
          ?? prepared?.prepared_candidates
          ?? rawProductCandidates.length,
      );
      const sizeEligibleCount = Number(
        prepared?.size_eligible_count ?? discoveredCount,
      );
      const sizeFilteredCount = Number(
        prepared?.size_filtered_count || 0,
      );
      const inspectedCandidateCount = Number(
        prepared?.inspected_count ?? preparedCandidateCount,
      );
      const inspectionFailureCount = Number(
        prepared?.inspection_failure_count || 0,
      );
      const contentDuplicateCount = Number(
        prepared?.content_duplicate_count || 0,
      );
      const finalCandidateCount = Number(
        prepared?.final_candidate_count
          ?? rawProductCandidates.length,
      );
      const validCandidateCount = Number(
        prepared?.valid_candidates
          ?? rawProductCandidates.filter((candidate) => candidate.validation_status === "valid").length,
      );
      const candidateLimit = Math.max(1, Number(data.candidate_limit || 100));
      const pageSize = Math.max(1, Number(data.page_size || 30));
      let pageCount = 1;
      let pageIndex = 0;
      const product = element("section", "asset-product");
      const heading = element("div", "asset-product-heading");
      const title = element("div");
      const candidateSummary = element("span");
      title.append(
        element("strong", "", requirement.product_title || `商品 ${productId}`),
        candidateSummary,
      );
      const preflightFilter = document.createElement("select");
      preflightFilter.className = "asset-preflight-filter";
      preflightFilter.setAttribute("aria-label", `${requirement.product_title || productId} 图片状态筛选`);
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
      heading.append(title, preflightFilter);
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
        selectionSummary.textContent = guidance.usableUnique < 3
          ? `已选 ${guidance.selectedCount} 张、可用唯一 ${guidance.usableUnique} 张；还差 ${guidance.minimumShortage} 张才能提交。草稿仍可保存${duplicateSuffix}。`
          : `已选 ${guidance.selectedCount} 张、可用唯一 ${guidance.usableUnique} 张；预计创建 ${guidance.completeSlots} 个完整坑位（${preview}），提交后仍可人工调整${duplicateSuffix}。`;
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
          + ` · 文件夹内共发现 ${discoveredCount} 张`
          + ` · 大小预筛可用 ${sizeEligibleCount} 张`
          + `${sizeFilteredCount ? `（跳过 ${sizeFilteredCount} 张）` : ""}`
          + ` · 本轮计划检查 ${preparedCandidateCount} 张`
          + ` · 检查成功 ${inspectedCandidateCount} 张`
          + ` · 检查失败 ${inspectionFailureCount} 张`
          + ` · 内容重复 ${contentDuplicateCount} 张`
          + ` · 最终可选素材 ${finalCandidateCount} 张`
          + ` · 预检有效 ${validCandidateCount} 张`
          + ` · 当前可见 ${productCandidates.length} 张`
          + ` / 每商品检查上限 ${candidateLimit} 张`
          + ` · 每批显示 ${pageSize} 张`
        );
        const currentDecisions = selectedAssetDecisions();
        const hashesUsedElsewhere = new Set(
          currentDecisions
            .filter((item) => String(item.product_id) !== productId)
            .map((item) => String(item.sha256 || "")),
        );
        const previousForProduct = currentDecisions
          .filter((item) => String(item.product_id) === productId);
        const selectedIds = new Set(previousForProduct.map((item) => String(item.asset_id)));
        const displayedCandidates = filteredCandidates.slice(
          pageIndex * pageSize,
          (pageIndex + 1) * pageSize,
        );
        grid.replaceChildren();

        displayedCandidates.forEach((candidate) => {
          const assetId = String(candidate.asset_id || "");
          const selectionCheck = selectionPreflightFor(assetId);
          const selectionJob = selectionPreflightScheduler.get(assetId);
          const checking = ["queued", "running"].includes(selectionJob?.state);
          const selectable = galleryComplete && candidateIsSelectable(candidate)
            && !hashesUsedElsewhere.has(String(candidate.sha256 || ""))
            && selectionCheck?.status !== "blocked";
          const card = element("article", "asset-card");
          card.dataset.selectionPreflightStatus = checking
            ? "checking"
            : selectionCheck?.status || "unchecked";
          if (selectedIds.has(assetId)) card.classList.add("is-selected");
          const image = document.createElement("img");
          image.loading = "lazy";
          image.alt = `${requirement.product_title || productId} 候选图片`;
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
          const selectLabel = element("label", "asset-check");
          const select = document.createElement("input");
          select.type = "checkbox";
          select.checked = selectionJob?.desiredSelected || selectedIds.has(assetId);
          select.disabled = !selectable;
          selectLabel.append(select, document.createTextNode("采用"));
          controls.append(selectLabel);
          if (!galleryComplete) {
            controls.appendChild(
              element("span", "asset-warning", "候选仍在加载，完成后可采用"),
            );
          }
          const selectionFeedback = element("span", "asset-selection-check");
          controls.appendChild(selectionFeedback);
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

          const persistCurrentSelection = () => {
            persistSelectedCandidates(
              productId,
              productCandidates.filter(
                (item) => selectedIds.has(String(item.asset_id)),
              ),
            );
          };

          const refreshSelectionCard = () => {
            const job = selectionPreflightScheduler.get(assetId);
            const result = selectionPreflightFor(assetId);
            const isQueued = job?.state === "queued" && job.desiredSelected;
            const isRunning = job?.state === "running";
            const desired = job
              ? job.desiredSelected
              : selectedIds.has(assetId);
            select.checked = desired;
            select.disabled = !selectable || result?.status === "blocked";
            select.indeterminate = false;
            card.classList.toggle("is-selected", selectedIds.has(assetId));
            card.dataset.selectionPreflightStatus = isQueued
              ? "queued"
              : isRunning && desired
                ? "checking"
                : isRunning
                  ? "cancelled"
                  : result?.status || "unchecked";
            selectionFeedback.className = "asset-selection-check";
            if (isQueued) {
              selectionFeedback.classList.add("asset-warning");
              selectionFeedback.textContent = "排队中，可取消采用";
            } else if (isRunning && desired) {
              selectionFeedback.classList.add("asset-warning");
              selectionFeedback.textContent = "正在检查 1:1、3:4 预裁剪，可取消采用";
            } else if (isRunning) {
              selectionFeedback.classList.add("asset-warning");
              selectionFeedback.textContent = "已取消采用；后台结果仅用于缓存";
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
          selectionPreflightCardRefreshers.set(assetId, refreshSelectionCard);
          refreshSelectionCard();

          const applySelectionPreflight = (result) => {
            if (!result || result.cancelled || result.status === "cancelled") {
              refreshSelectionCard();
              return;
            }
            const job = selectionPreflightScheduler.get(assetId);
            if ((job && !job.desiredSelected) || (!job && !select.checked)) {
              refreshSelectionCard();
              return;
            }
            const intentVersion = job?.intentVersion || 0;
            if (
              job
              && appliedSelectionPreflightIntents.get(assetId) === intentVersion
            ) return;
            if (job) {
              appliedSelectionPreflightIntents.set(assetId, intentVersion);
            }
            if (result.status === "blocked") {
              selectionPreflightScheduler.cancel(assetId);
              selectedIds.delete(assetId);
              persistLicense(assetId, false);
              actionMessage.textContent = result.message;
            } else {
              selectedIds.add(assetId);
              persistLicense(assetId, true);
              actionMessage.textContent = result.status === "warning"
                ? result.message
                : "图片预裁剪检查通过。";
            }
            persistCurrentSelection();
            refreshSelectionCard();
            updateSelectionSummary();
            renderSelected();
          };

          select.addEventListener("change", () => {
            if (select.checked) {
              actionMessage.textContent = "正在检查所选图片的 1:1、3:4 预裁剪…";
              const request = runSelectionPreflight(candidate);
              refreshSelectionCard();
              request.then(applySelectionPreflight).catch((error) => {
                selectionPreflightScheduler.cancel(assetId);
                selectedIds.delete(assetId);
                persistLicense(assetId, false);
                actionMessage.textContent = error.userMessage || error.message;
                persistCurrentSelection();
                refreshSelectionCard();
                updateSelectionSummary();
                renderSelected();
              });
              return;
            }
            selectionPreflightScheduler.cancel(assetId);
            selectedIds.delete(assetId);
            persistLicense(assetId, false);
            persistCurrentSelection();
            refreshSelectionCard();
            updateSelectionSummary();
            renderSelected();
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
            image.alt = `${requirement.product_title || productId} 已选图片`;
            image.src = apiPath(
              `/stages/asset_matching/assets/${encodeURIComponent(candidate.asset_id)}`,
            );
            const remove = element("button", "button-secondary", "取消采用");
            remove.type = "button";
            remove.addEventListener("click", () => {
              const assetId = String(candidate.asset_id);
              selectionPreflightScheduler.cancel(assetId);
              selectedIds.delete(assetId);
              persistLicense(assetId, false);
              persistSelectedCandidates(
                productId,
                productCandidates.filter(
                  (value) => selectedIds.has(String(value.asset_id)),
                ),
              );
              selectionPreflightCardRefreshers.get(assetId)?.();
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
      draw();
    });
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
      actionMessage.textContent = "当前阶段由子页面主按钮推进；修改仍会自动保存到任务目录。";
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
    let slotPlanDirty = false;
    let processedOutputs = null;
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
      if (notify) slotPlanDirty = true;
      writeJsonListControl(
        "slot_assignments",
        [...stateByProduct.values()].flat(),
        { notify },
      );
    };
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
      slotPlanDirty = false;
      const page = twoPageWorkflow
        ? UiState.twoStepFifthStagePage(plan.workflow_state)
        : UiState.fifthStagePage(plan.workflow_state);
      maxUnlockedPage = Math.max(maxUnlockedPage, pageOrder.indexOf(page));
      setSubpage(page);
      persist();
      draw();
      if (page === "process" && plan.confirmed === true) {
        processPanel.hidden = false;
        renderProcessingPage();
      } else if (
        page === "copy"
        && processedOutputs?.workflow_state === "outputs_ready"
      ) {
        renderCopyEditor(processedOutputs);
      }
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
    let activeSubpage = pageOrder[0];
    let maxUnlockedPage = 0;
    const subpages = {};
    const setSubpage = (page, { userRequested = false } = {}) => {
      const requestedIndex = pageOrder.indexOf(page);
      if (requestedIndex < 0) return;
      if (userRequested && requestedIndex > maxUnlockedPage) return;
      activeSubpage = page;
      Object.entries(subpages).forEach(([name, panel]) => {
        panel.hidden = name !== page;
      });
      [...wizard.children].forEach((item, index) => {
        item.dataset.active = index === requestedIndex ? "true" : "false";
        item.dataset.complete = index < maxUnlockedPage ? "true" : "false";
        item.disabled = index > maxUnlockedPage;
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
    if (wizard.firstElementChild) wizard.firstElementChild.dataset.active = "true";
    content.appendChild(wizard);
    pageOrder.forEach((page) => {
      const panel = element("section", "slot-subpage");
      panel.dataset.slotSubpage = page;
      panel.hidden = page !== pageOrder[0];
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
          if (!window.confirm(`${difference}\n确认采用为草稿吗？此操作不会处理图片或上传。`)) {
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
      if (
        hasDraft
        && !window.confirm(
          "重新 AI 编排会在返回成功后替换当前未确认坑位草稿。是否继续？",
        )
      ) {
        agentStatus.textContent = "已取消 AI 重新编排，当前草稿保持不变。";
        return;
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
      element("span", "", `未确认坑位前不会生成正式图片。已阻止 ${data.blocked_outputs?.length || 0} 张图片。`),
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
        if (!window.confirm(
          "重新自动编排会替换当前坑位草稿，并使已有裁剪输出和文案失效。是否继续？",
        )) return;
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
    const board = element("div", "slot-board-products");
    planningPage.appendChild(board);
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
    processPanel.append(processHeading, processWorkspace, processActions);
    subpages.process.appendChild(processPanel);
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
    const invalidateCropPreflight = () => {
      cropPreflight = null;
      processPlanButton.disabled = true;
      processStatus.textContent = "裁剪参数已变化，请重新执行裁剪预校验。";
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
      const original = element(
        "button",
        "button-secondary",
        cropState.use_original ? "已使用原图" : "使用原图",
      );
      original.type = "button";
      original.hidden = !output.native_ratio;
      controls.append(restore, original);
      const currentBox = () => cropState.normalized_box;
      const update = () => {
        const box = currentBox();
        const useOriginal = cropState.use_original === true;
        overlay.hidden = useOriginal || !box;
        preview.hidden = useOriginal || !box;
        restore.disabled = useOriginal;
        original.textContent = useOriginal ? "已使用原图" : "使用原图";
        if (useOriginal || !box) {
          outputSize.textContent = `原图直出 ${width}×${height}`;
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
        if (cropState.use_original || !currentBox() || !width || !height) return;
        event.preventDefault();
        const start = [...currentBox()];
        const startX = event.clientX;
        const startY = event.clientY;
        const rect = frame.getBoundingClientRect();
        const targetRatio = assignment.target_ratio === "1:1" ? 1 : 0.75;
        const normalizedRatio = targetRatio * height / width;
        const move = (pointerEvent) => {
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
          cropState.use_original
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
        cropState.use_original = false;
        cropState.normalized_box = normalizedCropBox(output);
        invalidateCropPreflight();
        update();
      });
      original.addEventListener("click", () => {
        cropState.use_original = true;
        cropState.normalized_box = null;
        invalidateCropPreflight();
        update();
      });
      update();
      const editor = element("div", "slot-crop-editor");
      editor.append(frame, controls, outputSize, preview);
      return editor;
    };
    const renderProcessingPage = () => {
      processWorkspace.replaceChildren();
      let blockedReason = "";
      [...stateByProduct.values()].flat().forEach((assignment) => {
        const product = products.find(
          (item) => String(item.product_id) === String(assignment.product_id),
        );
        const card = element("article", "slot-process-card");
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
          if (!cropParameters[cropKey]) {
            cropParameters[cropKey] = {
              normalized_box: output.native_ratio
                ? null
                : normalizedCropBox(output),
              use_original: Boolean(output.native_ratio),
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
              `目标 ${assignment.target_ratio} · ${
                output.native_ratio ? "原生比例直出" : "需要裁剪"
              }${output.requires_compression ? " · 需要压缩" : ""}`,
            ),
            createSlotCropEditor(
              output,
              assignment,
              cropParameters[cropKey],
            ),
            compressionLabel,
          );
          grid.appendChild(imageCard);
        });
        card.append(heading, grid);
        processWorkspace.appendChild(card);
      });
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
      processStatus.textContent = blockedReason
        || (cropPreflight?.workflow_state === "crop_preflight_passed"
          ? `裁剪预校验已通过：${cropPreflight.checked_count || 0} 张图片均不少于 204,800 字节。`
          : "请先执行裁剪预校验；通过后才能进入文案生成。");
    };
    const renderProcessedPreview = (processed) => {
      processPanel.querySelector(".processed-preview-grid")?.remove();
      const preview = element("div", "slot-asset-grid processed-preview-grid");
      (processed.slots || []).forEach((slot) => {
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
      processPanel.appendChild(preview);
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
        || item?.confirmed === true
      ));
      const savedCopy = new Map(
        savedCopyItems.map((item) => [String(item.slot_id), item]),
      );
      const form = element("div", "copy-slot-list");
      const copyState = [];
      let finishButton = null;
      let finishHint = null;
      let batchConfirmation = null;
      let copyVersionCount = 0;
      const copyActions = element("div", "copy-toolbar");
      const copyStatus = element(
        "span",
        "copy-toolbar-status",
        "千牛会按商品坑位生成文案；全部生成后统一核对并确认。",
      );
      const copyButton = element(
        "button",
        "primary-button",
        "重新生成新版本",
      );
      copyButton.type = "button";
      copyButton.hidden = true;
      const copyVersions = document.createElement("select");
      copyVersions.setAttribute("aria-label", "AI 文案版本");
      const copyToolbarActions = element("div", "copy-toolbar-actions");
      copyToolbarActions.append(
        copyButton,
        copyVersions,
        element(
          "small",
          "copy-version-help",
          "重新生成会创建独立新版本，不覆盖历史版本，也不会发布。",
        ),
      );
      copyActions.append(copyStatus, copyToolbarActions);
      const updateCopyActions = () => {
        const incompleteCount = copyState.filter(
          (draft) => !draft.title || !draft.description,
        ).length;
        const hasCompleteDrafts = copyState.length > 0 && incompleteCount === 0;
        const batchConfirmed = hasCompleteDrafts
          && copyState.every((draft) => draft.confirmed === true);
        copyButton.className = hasCompleteDrafts
          ? "button-secondary"
          : "primary-button";
        if (batchConfirmation) {
          batchConfirmation.disabled = !hasCompleteDrafts;
          batchConfirmation.checked = batchConfirmed;
        }
        if (finishButton) {
          finishButton.hidden = !hasCompleteDrafts;
          finishButton.disabled = !batchConfirmed;
          finishButton.title = incompleteCount
            ? `还有 ${incompleteCount} 个坑位缺少标题或描述`
            : batchConfirmed
              ? "标题和描述已确认，可以进入上传任务确认"
              : "请统一确认全部标题和描述";
        }
        if (finishHint) {
          finishHint.textContent = incompleteCount
            ? `还有 ${incompleteCount} 个坑位缺少标题或描述。`
            : batchConfirmed
              ? "标题和描述已确认，可以进入上传任务确认。"
              : "请核对全部标题和描述后统一确认。";
          finishHint.dataset.status = incompleteCount || !batchConfirmed
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
          title: String(existing.title || ""),
          description: String(existing.description || ""),
          confirmed: existing.confirmed === true,
          source: String(existing.source || "manual"),
          evidence: Array.isArray(existing.evidence) ? existing.evidence : [],
          risks: Array.isArray(existing.risks) ? existing.risks : [],
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

        const save = () => {
          item.title = title.value;
          item.description = description.value;
          copyState.forEach((draft) => { draft.confirmed = false; });
          titleCount.textContent = `${title.value.length}/30`;
          descriptionCount.textContent = `${description.value.length}/1000`;
          writeJsonListControl("copy_edits", copyState, { notify: true });
          updateCopyActions();
        };
        title.addEventListener("input", save);
        description.addEventListener("input", save);
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
          "系统校验并本地上传当前坑位第 1 张最终图片，唤起千牛文案；只读取草稿、不填充、不发布，全部完成后统一核对并确认。",
        ),
      );
      const batchConfirmationLabel = element(
        "label",
        "check-control copy-confirmation-control",
      );
      batchConfirmation = document.createElement("input");
      batchConfirmation.type = "checkbox";
      batchConfirmationLabel.append(
        batchConfirmation,
        element("span", "", "已确认标题、描述，可进入上传任务确认"),
      );
      batchConfirmation.addEventListener("change", () => {
        copyState.forEach((draft) => {
          draft.confirmed = batchConfirmation.checked;
        });
        writeJsonListControl("copy_edits", copyState, { notify: true });
        updateCopyActions();
      });
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
      const backToProcess = element(
        "button",
        "button-secondary",
        "返回图片裁剪",
      );
      backToProcess.type = "button";
      backToProcess.addEventListener("click", () => setSubpage(
        "process",
        { userRequested: true },
      ));
      const finalActions = element("div", "slot-page-actions");
      finalActions.append(
        batchConfirmationLabel,
        finishHint,
        backToProcess,
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
        copyWorkspace,
        finalActions,
        technical,
      );
      writeJsonListControl("copy_edits", copyState);
      const applyCopyDrafts = (drafts, requestId) => {
        const draftsBySlot = new Map(
          drafts.map((draft) => [String(draft.slot_id), draft]),
        );
        const existingBySlot = new Map(
          readJsonListControl("copy_edits")
            .filter((item) => item?.slot_id)
            .map((item) => [String(item.slot_id), item]),
        );
        const merged = assignments.map((assignment) => {
          const slotId = String(assignment.slot_id);
          const draft = draftsBySlot.get(slotId);
          const existing = existingBySlot.get(slotId) || {};
          if (!draft) return existing.slot_id
            ? existing
            : {
              slot_id: slotId,
              product_id: assignment.product_id,
              title: "",
              description: "",
              evidence: [],
              risks: [],
              confirmed: false,
              source: "pending_qianniu_builtin_ai",
              request_id: requestId,
            };
          return {
            slot_id: slotId,
            product_id: assignment.product_id,
            title: draft.title || "",
            description: draft.description || "",
            evidence: draft.evidence || [],
            risks: draft.risks || [],
            confirmed: false,
            source: String(draft.source || "qianniu_builtin_ai"),
            request_id: requestId,
          };
        });
        writeJsonListControl("copy_edits", merged, { notify: true });
        copyStatus.textContent = drafts.length === assignments.length
          ? "千牛文案已载入；请核对全部依据和风险后统一确认。"
          : `工作台后台已回填 ${drafts.length}/${assignments.length} 个坑位，正在继续处理。`;
        renderCopyEditor(processed, requestId);
      };
      const pollCopyRequest = async (requestId) => {
        if (!requestId || !copyVersions.isConnected) return;
        try {
          const detail = await fetchJson(
            apiPath(`/stages/slots_copy/agent-requests/${encodeURIComponent(requestId)}`),
          );
          if (!copyVersions.isConnected) return;
          const requestState = detail.request?.status || "";
          const progressDrafts = detail.progress?.copy_drafts || [];
          const completedDrafts = detail.response?.result?.copy_drafts || [];
          const drafts = completedDrafts.length
            ? completedDrafts
            : progressDrafts;
          const knownCount = savedCopyItems.filter(
            (item) => String(item.request_id || "") === requestId
              && String(item.title || "").trim()
              && String(item.description || "").trim(),
          ).length;
          if (drafts.length > knownCount) {
            applyCopyDrafts(drafts, requestId);
            return;
          }
          if (requestState === "completed") {
            copyStatus.textContent = "千牛文案已载入；请核对全部内容后统一确认。";
            copyButton.disabled = false;
            return;
          }
          if (requestState === "failed") {
            copyStatus.textContent = `文案生成遇到问题，已保留 ${progressDrafts.length}/${assignments.length} 个坑位并将诊断交给 Codex。`;
            copyButton.disabled = false;
            return;
          }
          copyButton.disabled = true;
          copyStatus.textContent = requestState === "processing"
            ? `工作台后台正在复用千牛文案流程：已完成 ${progressDrafts.length}/${assignments.length} 个坑位。`
            : "图片已确认，工作台后台正在获取千牛文案。";
          window.setTimeout(() => pollCopyRequest(requestId), 1500);
        } catch (error) {
          if (!copyVersions.isConnected) return;
          copyStatus.textContent = "文案状态暂时无法读取，系统会自动重试。";
          window.setTimeout(() => pollCopyRequest(requestId), 2500);
        }
      };
      const requestCopy = async (regenerate = false) => {
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
          copyStatus.textContent = "新版本已提交给工作台后台。";
          pollCopyRequest(copyRequest.request_id);
        } catch (error) {
          copyStatus.textContent = error.userMessage || error.message;
          copyButton.disabled = false;
        } finally {
          copyButton.textContent = "重新生成新版本";
        }
      };
      copyButton.addEventListener("click", () => requestCopy(true));
      fetchJson(apiPath("/stages/slots_copy/agent-requests"))
        .then(async (payload) => {
          const copyRequests = (payload.requests || []).filter(
            (item) => item.kind === "copy_draft",
          );
          const versions = copyRequests.filter(
            (item) => item.kind === "copy_draft" && item.status === "completed",
          );
          copyVersionCount = versions.length;
          copyButton.textContent = "重新生成新版本";
          copyButton.hidden = copyRequests.length === 0;
          copyVersions.replaceChildren();
          if (!versions.length) {
            const option = document.createElement("option");
            option.textContent = "暂无千牛文案版本";
            copyVersions.appendChild(option);
            copyVersions.disabled = true;
            const active = copyRequests.find(
              (item) => item.request_id === restoredRequestId,
            ) || copyRequests[0];
            if (active) pollCopyRequest(active.request_id);
            return;
          }
          versions.forEach((item, index) => {
            const option = document.createElement("option");
            option.value = item.request_id;
            option.textContent = `版本 ${versions.length - index} · ${item.request_id}`;
            copyVersions.appendChild(option);
          });
          const selectedVersionExists = versions.some(
            (item) => item.request_id === restoredRequestId,
          );
          if (selectedVersionExists) {
            copyVersions.value = restoredRequestId;
            pollCopyRequest(restoredRequestId);
            return;
          }
          const currentDrafts = readJsonListControl("copy_edits")
            .filter((item) => item?.slot_id);
          if (!copyVersions.isConnected) return;
          if (
            hasMeaningfulCopyDrafts(savedCopyItems)
            || hasMeaningfulCopyDrafts(currentDrafts)
          ) {
            const option = document.createElement("option");
            option.value = "";
            option.textContent = "当前草稿 · 未绑定 AI 版本";
            option.selected = true;
            copyVersions.prepend(option);
            const active = copyRequests.find(
              (item) => item.request_id === restoredRequestId
                && ["pending_agent", "processing"].includes(item.status),
            );
            if (active) pollCopyRequest(active.request_id);
            return;
          }
          const latestRequestId = versions[0].request_id;
          pollCopyRequest(latestRequestId);
        })
        .catch(() => {});
      copyVersions.addEventListener("change", async () => {
        if (!copyVersions.value) return;
        const detail = await fetchJson(
          apiPath(`/stages/slots_copy/agent-requests/${encodeURIComponent(copyVersions.value)}`),
        );
        applyCopyDrafts(
          detail.response?.result?.copy_drafts || [],
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
          slotPlanDirty = false;
        }
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
        slotPlanDirty = false;
      }
      return assignments;
    };

    cropPreflightButton.addEventListener("click", async () => {
      cropPreflightButton.disabled = true;
      processPlanButton.disabled = true;
      processStatus.textContent = "正在逐张试裁并检查最终文件大小…";
      try {
        const assignments = await ensureConfirmedSlotPlan();
        cropPreflight = await fetchJson(
          apiPath("/stages/slots_copy/crop-preflight"),
          {
            method: "POST",
            body: JSON.stringify({
              slot_assignments: assignments,
              crop_parameters: cropParameters,
            }),
          },
        );
        currentPlanRevision = Number(
          cropPreflight.plan_revision || currentPlanRevision,
        );
        processStatus.textContent = `裁剪预校验已通过：${cropPreflight.checked_count || 0} 张图片均不少于 204,800 字节。`;
        processPlanButton.disabled = false;
      } catch (error) {
        cropPreflight = null;
        processStatus.textContent = error.userMessage
          || Object.values(error.fieldErrors || {})[0]
          || error.message;
      } finally {
        cropPreflightButton.disabled = false;
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
      processStatus.textContent = "正在确认图片输出并创建千牛文案任务…";
      try {
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
        processedOutputs = processed;
        processStatus.textContent = `图片处理完成：${processed.slots?.length || 0} 个坑位；文案任务已交给工作台后台。`;
        renderProcessedPreview(processed);
        renderCopyEditor(
          processed,
          processed.copy_request?.request_id || processed.copy_request_id || "",
        );
        maxUnlockedPage = Math.max(
          maxUnlockedPage,
          pageOrder.indexOf("copy"),
        );
        setSubpage("copy");
      } catch (error) {
        processStatus.textContent = error.userMessage
          || Object.values(error.fieldErrors || {})[0]
          || error.message;
      } finally {
        processPlanButton.disabled = cropPreflight?.workflow_state
          !== "crop_preflight_passed";
      }
    });
    fetchJson(apiPath("/stages/slots_copy/crop-preflight"))
      .then((preflight) => {
        if (preflight.workflow_state !== "crop_preflight_passed") return;
        cropPreflight = preflight;
        if (preflight.crop_parameters) {
          Object.keys(cropParameters).forEach((key) => delete cropParameters[key]);
          Object.assign(cropParameters, preflight.crop_parameters);
        }
        currentPlanRevision = Number(
          preflight.plan_revision || currentPlanRevision,
        );
        renderProcessingPage();
      })
      .catch(() => {});
    fetchJson(apiPath("/stages/slots_copy/processed-outputs"))
      .then((processed) => {
        if (processed.workflow_state === "outputs_ready" && processed.plan_sha256) {
          processedOutputs = processed;
          processStatus.textContent = "当前坑位计划已有通过校验的输出；可以继续确认文案。";
          renderProcessedPreview(processed);
          renderCopyEditor(processed, processed.copy_request_id || "");
          processPanel.hidden = false;
          maxUnlockedPage = Math.max(
            maxUnlockedPage,
            pageOrder.indexOf("copy"),
          );
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
      const hasDraft = [...stateByProduct.values()].some(
        (assignments) => assignments.length > 0,
      );
      confirmPlanButton.disabled = !hasDraft;
      agentButton.className = hasDraft ? "button-secondary" : "primary-button";
      products.forEach((product) => {
        const productId = String(product.product_id || "");
        const section = element("section", "slot-product");
        const heading = element("div", "slot-product-heading");
        heading.append(
          element("strong", "", `商品 ${productId}`),
          element("span", "", `3:4 ${product.available_by_ratio?.["3:4"] || 0} 张 · 1:1 ${product.available_by_ratio?.["1:1"] || 0} 张`),
        );
        const add = element("button", "button-secondary", "人工添加坑位");
        add.type = "button";
        add.addEventListener("click", () => {
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
          const remove = element("button", "button-secondary", "删除坑位");
          remove.type = "button";
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
                event.dataTransfer?.setData("text/plain", assetId);
              });
              card.addEventListener("dragover", (event) => {
                event.preventDefault();
              });
              card.addEventListener("drop", (event) => {
                event.preventDefault();
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
            assignment.slot_id = slotId.value.trim();
            persist(true);
          });
          ratio.addEventListener("change", () => {
            assignment.target_ratio = ratio.value;
            assignment.plan_source = assignment.plan_source === "agent_assisted"
              ? "manual_override"
              : "manual";
            persist(true);
            draw();
          });
          remove.addEventListener("click", () => {
            const assignments = stateByProduct.get(productId);
            assignments.splice(slotIndex, 1);
            persist(true);
            draw();
          });
          drawAssets();
        });
      });
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
    const tasks = Array.isArray(documentData.tasks) ? documentData.tasks : [];
    const warnings = Array.isArray(documentData.warnings) ? documentData.warnings : [];
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

    const summary = element("div", "upload-confirmation-summary");
    [
      ["商品", documentData.product_count || 0],
      ["可审任务", tasks.filter((task) => task.status === "ready_for_review").length],
      ["图片", documentData.media_count || 0],
      ["警告", warnings.length],
    ].forEach(([label, value]) => {
      const item = element("div", "upload-confirmation-metric");
      item.append(element("strong", "", String(value)), element("span", "", label));
      summary.appendChild(item);
    });
    content.appendChild(summary);

    if (warnings.length) {
      const warningPanel = element("div", "upload-confirmation-warnings");
      warningPanel.appendChild(element("strong", "", "上传前检查提醒"));
      warnings.forEach((warning) => {
        warningPanel.appendChild(element("p", "", warning.message || warning.code || "待复查"));
      });
      content.appendChild(warningPanel);
    }

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
      (task.warnings || []).forEach((warning) => {
        card.appendChild(element("p", "upload-task-warning", warning));
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
    if (!sessionId) {
      currentStageInputLoaded = true;
      currentStageHasPersistedInput = false;
      revision = 0;
      revisionLabel.textContent = "0";
      renderStatus();
      renderSubmission();
      renderStageResult(stages.get(requestedStageId).component);
      return;
    }
    try {
      const payload = await fetchJson(apiPath(`/stages/${requestedStageId}`));
      if (requestedStageId !== currentStageId) return;
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
        if (payload.input) {
          hydrateForm(activeForm(), payload.input.values);
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
      renderStatus();
      renderHandoffStatus(currentHandoffStatus, currentWorkflowDispatch);
      renderProcessingClaim(currentProcessingClaim, currentCollectionStatus);
      renderSubmission();
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
      if (requestedStageId !== currentStageId) return;
      actionMessage.textContent = error.message;
    }
  }

  async function prepareLocalGallery(button) {
    if (persistenceInFlight) {
      actionMessage.textContent = "保存正在进行，请稍后再次确认文件夹。";
      return;
    }
    if (
      currentStageId !== "asset_matching"
      || inferAssetMatchingStep(
        uiState.result?.data,
        uiState.serverStatus,
      ) === "image_selection"
    ) return;
    const form = activeForm();
    if (!form) return;
    window.clearTimeout(autoSaveTimer);
    clearFieldErrors(form);
    let values;
    try {
      values = serializeForm(form);
    } catch (error) {
      actionMessage.textContent = error.message;
      return;
    }
    persistenceInFlight = true;
    button.disabled = true;
    actionMessage.textContent =
      "正在确认文件夹并启动本机图片加载…";
    const requestedGeneration = stageGeneration;
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
          revision,
          request_id: persistenceIdentity.value,
          values,
        }),
      });
      if (
        currentStageId !== "asset_matching"
        || requestedGeneration !== stageGeneration
      ) return;
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
        "文件夹决定已保存，本机正在加载候选图片；不会创建阶段交接。";
    } catch (error) {
      const fieldErrors = error.payload?.field_errors;
      if (fieldErrors) showFieldErrors(form, fieldErrors);
      actionMessage.textContent = error.userMessage || error.message;
      button.disabled = false;
    } finally {
      persistenceInFlight = false;
    }
  }

  async function persistStage(mode, { automatic = false, queued = false } = {}) {
    if (persistenceInFlight) {
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
    persistenceInFlight = true;
    const startedEditVersion = localEditVersion;
    let completedSuccessfully = false;
    if (mode === "submit") window.clearTimeout(autoSaveTimer);
    const requestedStageId = currentStageId;
    const requestedGeneration = stageGeneration;
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
      persistenceInFlight = false;
      return;
    }
    const form = activeForm();
    if (!form) {
      persistenceInFlight = false;
      return;
    }
    clearFieldErrors(form);
    if (
      mode === "submit"
      && requestedStageId === "setup"
      && focusFirstIncompleteImageSource()
    ) {
      const count = incompleteImageSourceRows().length;
      updateImageSourceIncompleteHint();
      actionMessage.textContent = `还有 ${count} 个图片源未填写完整，请补充后再提交。`;
      persistenceInFlight = false;
      return;
    }
    let values;
    try {
      values = serializeForm(form);
    } catch (error) {
      actionMessage.textContent = error.message;
      persistenceInFlight = false;
      return;
    }
    if (
      mode === "submit"
      && requestedStageId === "setup"
      && !(await checkImageSources())
    ) {
      actionMessage.textContent =
        "图片源检测未通过；请修改本次配置后再提交，工作台尚未接收。";
      persistenceInFlight = false;
      return;
    }
    if (
      mode === "submit"
      && requestedStageId === "asset_matching"
      && inferAssetMatchingStep(uiState.result?.data, uiState.serverStatus)
        === "image_selection"
    ) {
      const decisions = Array.isArray(values.asset_decisions)
        ? values.asset_decisions.filter((item) => item?.decision === "selected")
        : [];
      const pendingSelectionCount = (
        selectionPreflightScheduler.desiredPendingCount()
      );
      if (pendingSelectionCount) {
        const message = `还有 ${pendingSelectionCount} 张图片正在进行预裁剪检查，请等待完成。`;
        showFieldErrors(form, { asset_decisions: message });
        actionMessage.textContent = message;
        persistenceInFlight = false;
        return;
      }
      let unchecked = decisions.filter((item) => {
        const result = selectionPreflightFor(item.asset_id);
        return !result || !["passed", "warning"].includes(result.status);
      });
      if (unchecked.length) {
        const allCandidates = Array.isArray(uiState.result?.data?.asset_candidates)
          ? uiState.result.data.asset_candidates
          : [];
        const byAssetId = new Map(
          allCandidates.map((candidate) => [String(candidate.asset_id || ""), candidate]),
        );
        actionMessage.textContent = `正在补检 ${unchecked.length} 张历史已选图片…`;
        await Promise.all(unchecked.map((item) => {
          const candidate = byAssetId.get(String(item.asset_id || ""));
          return candidate ? runSelectionPreflight(candidate) : Promise.resolve(null);
        }));
        unchecked = decisions.filter((item) => {
          const result = selectionPreflightFor(item.asset_id);
          return !result || !["passed", "warning"].includes(result.status);
        });
        if (unchecked.length) {
          const message = `有 ${unchecked.length} 张已选图片未通过预裁剪，已在“已选素材”中标明，请取消后更换。`;
          showFieldErrors(form, { asset_decisions: message });
          actionMessage.textContent = message;
          persistenceInFlight = false;
          renderStageResult(stages.get(currentStageId).component);
          return;
        }
      }
      const counts = new Map();
      const ratioCounts = new Map();
      decisions.forEach((item) => {
        const productId = String(item.product_id || "");
        counts.set(productId, (counts.get(productId) || 0) + 1);
        const current = ratioCounts.get(productId) || { "3:4": 0, "1:1": 0 };
        (selectionPreflightFor(item.asset_id)?.feasible_ratios || [])
          .forEach((ratio) => {
            if (Object.hasOwn(current, ratio)) current[ratio] += 1;
          });
        ratioCounts.set(productId, current);
      });
      const requiredProducts = Array.isArray(uiState.result?.data?.requirements)
        ? uiState.result.data.requirements
          .map((item) => String(item?.product_id || ""))
          .filter(Boolean)
        : [];
      const shortages = requiredProducts
        .filter((productId) => (counts.get(productId) || 0) < 3)
        .map((productId) => (
          `${productId} 还差 ${3 - (counts.get(productId) || 0)} 张`
        ));
      const incompatibleRatios = requiredProducts.filter((productId) => {
        const current = ratioCounts.get(productId) || { "3:4": 0, "1:1": 0 };
        return Math.max(current["3:4"], current["1:1"]) < 3;
      });
      if (!requiredProducts.length || shortages.length || incompatibleRatios.length) {
        const message = !decisions.length
          ? "每个商品至少采用 3 张图片；当前草稿可以继续保存。"
          : shortages.length
            ? `完整坑位至少需要 3 张图片：${shortages.join("；")}`
            : `每个商品至少需要 3 张共同支持同一比例的图片：${incompatibleRatios.join("、")}`;
        showFieldErrors(form, { asset_decisions: message });
        actionMessage.textContent = message;
        persistenceInFlight = false;
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
        : "正在创建交接…";
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
            ? "发布授权已提交；工作台后台将自动上传所选任务。"
            : "交接已持久化，工作台后台已接收并正在排队。";
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
          : "草稿已保存；不会触发工作台后台处理。";
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
      showFieldErrors(form, error.fieldErrors || {});
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
        ? "；兼容监听仍在线，但本次提交尚未成功"
        : "";
      actionMessage.textContent = pausedSubmission
        ? `保存失败，排队的提交已暂停：${error.userMessage || error.message}${suffix}`
        : mode === "submit"
          ? `尚未提交，工作台未接收：${error.userMessage || error.message}${suffix}`
          : `${error.userMessage || error.message}${suffix}`;
      uiState = UiState.markDirty(uiState);
      renderStatus();
    } finally {
      persistenceInFlight = false;
      const pending = pendingPersistenceMode;
      pendingPersistenceMode = null;
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
    actionMessage.textContent = "正在撤回尚未被工作台后台领取的提交…";
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
      if (stageChanged || galleryChanged) {
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
      connectionLabel.textContent = "工作台后台状态暂不可用";
      if (taskAwareness && taskStatusLabel && taskStatusSummary) {
        taskAwareness.dataset.phase = "offline";
        taskStatusLabel.textContent = "工作台后台已停止或暂不可用";
        taskStatusSummary.textContent =
          "任务记录仍然保留；需要继续查看或处理时，可恢复同一个任务。";
      }
    }
  }

  function activateStage(stageId) {
    if (!stages.has(stageId)) return;
    stageGeneration += 1;
    window.clearTimeout(autoSaveTimer);
    pendingPersistenceMode = null;
    currentStageId = stageId;
    uiState = UiState.switchStage(uiState, stageId);
    currentProcessingClaim = null;
    currentHandoffStatus = null;
    currentWorkflowDispatch = null;
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
    actionMessage.textContent = "填写完成后可保存草稿，或提交给工作台后台处理。";
    renderStatus();
    renderSubmission();
    renderStageResult(stage.component);
    if (stageId === "setup") initializeSetupLoginGate();
    window.scrollTo({ top: 0, behavior: "smooth" });
    loadStage();
    if (sessionId) loadRecoveryInstruction(stageId);
  }

  railButtons.forEach((button) => {
    button.addEventListener("click", () => activateStage(button.dataset.stageId));
  });
  goCurrentStageButton.addEventListener("click", () => {
    if (stages.has(sessionCurrentStageId)) activateStage(sessionCurrentStageId);
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
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) pollStage();
  });

  setInterval(pollStage, 2000);
  initializeImageSourceConfig();
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
        warning = `任务状态加载失败，已回退到第一阶段：${error.message}`;
      }
    }
    activateStage(initialStage);
    if (warning) actionMessage.textContent = warning;
  }
  bootstrap();
})();
