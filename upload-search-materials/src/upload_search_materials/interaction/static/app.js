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
  const imageSourceConfig = document.querySelector('[data-component="ImageSourceConfig"]');
  const handoffActions = document.querySelector(".handoff-actions");
  const goCurrentStageButton = document.createElement("button");
  goCurrentStageButton.type = "button";
  goCurrentStageButton.className = "secondary-button";
  goCurrentStageButton.textContent = "进入当前阶段";
  goCurrentStageButton.hidden = true;
  handoffActions?.prepend(goCurrentStageButton);

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
    "result_timeline": ["ResultTimeline"],
  };

  function apiPath(suffix = "") {
    return `/api/sessions/${encodeURIComponent(sessionId)}${suffix}`;
  }

  function formatBytes(value) {
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
      throw error;
    }
    return payload;
  }

  function imageSourceRows() {
    return imageSourceConfig
      ? [...imageSourceConfig.querySelectorAll("[data-image-source-row]")]
      : [];
  }

  function updateImageSourceConfig() {
    if (!imageSourceConfig) return;
    const rows = imageSourceRows();
    const validCount = rows.filter((row) =>
      row.querySelector('[name="image_source_labels"]')?.value.trim()
      && row.querySelector('[name="image_roots"]')?.value.trim()
    ).length;
    imageSourceConfig.querySelector("[data-image-source-summary]").textContent = `${rows.length} 个图片源`;
    imageSourceConfig.dataset.ready = validCount === rows.length && rows.length > 0 ? "true" : "false";
    rows.forEach((row) => {
      row.querySelector("[data-remove-image-source]").disabled = rows.length <= 1;
    });
  }

  function appendImageSource(label = "", path = "") {
    if (!imageSourceConfig) return null;
    const template = imageSourceConfig.querySelector("[data-image-source-template]");
    const row = template.content.firstElementChild.cloneNode(true);
    row.querySelector('[name="image_source_labels"]').value = label;
    row.querySelector('[name="image_roots"]').value = path;
    imageSourceConfig.querySelector("[data-image-source-list]").appendChild(row);
    updateImageSourceConfig();
    return row;
  }

  function configuredImageSources() {
    const sources = imageSourceRows().map((row) => ({
      label: row.querySelector('[name="image_source_labels"]').value.trim(),
      path: row.querySelector('[name="image_roots"]').value.trim(),
    }));
    if (!sources.length || sources.some((source) => !source.label || !source.path)) {
      throw new Error("每个图片源都必须填写来源名称和根路径");
    }
    return sources;
  }

  function hydrateImageSources(labels, paths) {
    if (!imageSourceConfig || !Array.isArray(paths) || !paths.length) return;
    const safeLabels = Array.isArray(labels) ? labels : [];
    const configuredLabels = new Map(
      imageSourceRows().map((row) => [
        row.querySelector('[name="image_roots"]').value.trim(),
        row.querySelector('[name="image_source_labels"]').value.trim(),
      ]),
    );
    const list = imageSourceConfig.querySelector("[data-image-source-list]");
    list.replaceChildren();
    paths.forEach((path, index) => {
      const textPath = String(path || "");
      appendImageSource(
        safeLabels[index] || configuredLabels.get(textPath) || `图片源 ${index + 1}`,
        textPath,
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
        const status = payload.image_sources[index]?.status || "unavailable";
        const node = row.querySelector("[data-image-source-state]");
        node.dataset.status = status;
        node.textContent = status === "available" ? "路径可访问" : "当前不可访问";
      });
      const available = payload.image_sources.filter((source) => source.status === "available").length;
      feedback.textContent = `检测完成：${available} / ${payload.image_sources.length} 个路径可访问。`;
    } catch (error) {
      feedback.textContent = error.message;
    }
  }

  async function saveImageSources() {
    const feedback = imageSourceConfig.querySelector("[data-image-source-feedback]");
    feedback.textContent = "正在保存本机配置…";
    try {
      const payload = await fetchJson("/api/runtime/image-sources", {
        method: "PUT",
        body: JSON.stringify({ image_sources: configuredImageSources() }),
      });
      feedback.textContent = `已保存 ${payload.image_sources.length} 个图片源到本机配置。`;
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
        pathInput.dispatchEvent(new Event("input", { bubbles: true }));
        state.textContent = "已选择，等待检测";
      } else {
        state.textContent = "已取消选择";
      }
    } catch (error) {
      state.textContent = "选择窗口不可用";
      imageSourceConfig.querySelector("[data-image-source-feedback]").textContent = error.message;
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

      if (group.length > 1 && control.type !== "radio") {
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

  function renderStatus() {
    const status = uiState.dirty ? "draft" : uiState.serverStatus;
    const copy = statusCopy[status] || statusCopy.draft;
    statusBadge.textContent = copy;
    statusBadge.dataset.status = status;
    handoffStatus.textContent = copy;
    const railStatus = document.querySelector(`[data-rail-status="${currentStageId}"]`);
    if (railStatus) railStatus.textContent = copy;
    submitButton.textContent = ["needs_user_input", "blocked"].includes(status)
      ? "补充后重新提交"
      : status === "ready_for_agent"
        ? "已提交，等待 Agent"
        : status === "processing"
          ? "Agent 处理中"
          : "提交给 Agent";
    const lockedByServer = !uiState.dirty
      && ["ready_for_agent", "processing", "completed"].includes(uiState.serverStatus);
    submitButton.disabled = lockedByServer;
    saveButton.disabled = lockedByServer;
    goCurrentStageButton.hidden = !(
      lockedByServer
      && stages.has(sessionCurrentStageId)
      && sessionCurrentStageId !== currentStageId
    );
    if (lockedByServer) {
      const reason = {
        completed: "该阶段已完成，不能再次保存或提交。",
        ready_for_agent: "该阶段已提交，正在等待 Agent 接收。",
        processing: "Agent 正在处理该阶段，当前输入已锁定。",
      }[uiState.serverStatus] || "该阶段当前不可编辑。";
      actionMessage.textContent = goCurrentStageButton.hidden
        ? reason
        : `${reason} 可进入任务当前阶段继续。`;
    }
    withdrawButton.hidden = uiState.serverStatus !== "ready_for_agent" || uiState.dirty;
    withdrawButton.disabled = uiState.serverStatus !== "ready_for_agent" || uiState.dirty;
    setFormLocked(uiState.serverStatus);
    updateResultsRecovery(UiState.recoveryView(uiState));
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
      actionMessage.textContent = "结果阶段只读；仅当 Agent 明确要求补充时开放恢复输入。";
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
    heading.textContent = result.summary || "Agent 已返回结果";
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
    if (notify) control.dispatchEvent(new Event("input", { bubbles: true }));
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
      ["需人工确认", statusCounts.needs_manual_review || 0],
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
      ["needs_manual_review", "需人工确认"],
      ["complete", "已完整"],
      ["excluded", "已自动排除"],
      ["abnormal", "异常"],
    ].forEach(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      filter.appendChild(option);
    });
    const bulkSelect = element("button", "secondary-button", "选择当前筛选结果");
    bulkSelect.type = "button";
    bulkSelect.disabled = locked;
    const bulkClear = element("button", "secondary-button", "取消当前筛选结果");
    bulkClear.type = "button";
    bulkClear.disabled = locked;
    toolbar.append(search, filter, bulkSelect, bulkClear);
    content.appendChild(toolbar);

    const list = element("div", "inspection-list");
    content.appendChild(list);

    const visibleProducts = () => {
      const needle = search.value.trim().toLocaleLowerCase("zh-CN");
      return products.filter((product) => {
        const matchesFilter = filter.value === "all" || product.status === filter.value;
        const haystack = [product.product_id, product.sku, product.product_title]
          .join(" ")
          .toLocaleLowerCase("zh-CN");
        return matchesFilter && (!needle || haystack.includes(needle));
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
    const value = JSON.stringify(values, null, 2);
    if (control.value === value) return false;
    control.value = value;
    if (notify) control.dispatchEvent(new Event("input", { bubbles: true }));
    return true;
  }

  function assetSortKey(candidate) {
    const matchRank = {
      exact_product_id: "0",
      exact_sku: "1",
      name_candidate: "2",
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
    return ["3:4", "1:1"]
      .filter((ratio) => checks[ratio])
      .map((ratio) => {
        const check = checks[ratio];
        const minimum = check.minimum_status === "meets_minimum"
          ? "满足最小尺寸"
          : "低于最小尺寸";
        const status = check.status === "meets_or_exceeds" ? "达到推荐" : "低于推荐";
        return `${ratio} 最大裁剪 ${check.max_crop_width}×${check.max_crop_height} · ${minimum} · ${status}`;
      })
      .join("；");
  }

  function persistSelectedCandidates(productId, selected) {
    const otherProducts = selectedAssetDecisions()
      .filter((item) => String(item.product_id) !== String(productId));
    const selectedRows = selected.map((candidate, index) => ({
      product_id: String(productId),
      asset_id: String(candidate.asset_id),
      sha256: String(candidate.sha256),
      folder_id: String(candidate.folder_id || candidate.resolved_folder_id || ""),
      folder_path: String(candidate.folder_path || candidate.candidate_directory || ""),
      source_system: String(candidate.source_system || ""),
      source_path: String(candidate.source_path || ""),
      decision: "selected",
      selection_order: index + 1,
    }));
    writeJsonListControl(
      "asset_decisions",
      [...otherProducts, ...selectedRows],
      { notify: true },
    );
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
        folder_path: String(item.folder_path || ""),
        decision: String(item.decision),
        note: String(item.note || ""),
      }));
  }

  function folderDecisionState(productId, folderId) {
    const saved = folderDecisions().find(
      (item) => String(item.product_id) === String(productId)
        && String(item.folder_id) === String(folderId),
    );
    return saved?.decision === "rejected" ? "rejected" : "confirmed";
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
      element("strong", "", "候选文件夹默认采用"),
      element("span", "", "排除文件夹后，下方候选图片和已选素材会立即同步。"),
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
          ) === "rejected",
        ).length;
        progress.textContent = `采用 ${productCandidates.length - rejected} · 排除 ${rejected}`;
      };

      productCandidates.forEach((candidate) => {
        const key = `${productId}\u0000${candidate.folder_id}`;
        const saved = decisionsByKey.get(key) || {};
        const card = element("article", "folder-card");
        const identity = element("div", "folder-card-identity");
        identity.append(
          element("strong", "", candidate.folder_name || "未命名文件夹"),
          element(
            "span",
            "folder-match-badge",
            candidate.match_type === "exact_sku"
              ? "货号命中"
              : candidate.match_type === "exact_folder_query"
                ? "本次精确文件夹查询"
                : "名称候选",
          ),
          element("small", "", candidate.source_system || "未知来源"),
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
    content.appendChild(review);
  }

  function renderAssetMatchGallery(view) {
    if (view.mode === "empty") return;
    const data = view.result?.data;
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
        `已按实际扫描结果展示 ${requirements.length} 个商品、${candidates.length} 张候选图片。`,
      );
      const action = element("section");
      action.append(
        element("strong", "", "下一步"),
        element(
          "p",
          "",
          "选择本次采用的图片；采用即确认该图片可用于本次发布。坑位数量和每坑 3–9 张的分组在第五阶段决定。",
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
      const selectedSection = element("section", "selected-assets");
      const selectedHeading = element("strong", "", "已选素材");
      const duplicateWarning = element("p", "asset-warning");
      const selectedGrid = element("div", "selected-asset-grid");
      selectedSection.append(selectedHeading, duplicateWarning, selectedGrid);
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
            || folderDecisionState(productId, folderId) !== "rejected";
        }),
      );

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
        candidateSummary.textContent = `商品 ID ${productId} · 后台缺 ${missingMaterials} 篇 · 候选池 ${productCandidates.length} 张（目录发现 ${discoveredCount} 张）`;
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
          const selectable = candidateIsSelectable(candidate)
            && !hashesUsedElsewhere.has(String(candidate.sha256 || ""));
          const card = element("article", "asset-card");
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
            element("span", "", `原图文件 ${sizeText} · 允许范围 ${minSizeText}–${maxSizeText}`),
            element("span", "", `处理建议：${preflightLabel(candidate)}`),
            element("span", "asset-warning", (candidate.preflight?.reason_messages || []).join("；")),
            element("small", "", candidate.source_path || ""),
          );
          const controls = element("div", "asset-card-controls");
          const selectLabel = element("label", "asset-check");
          const select = document.createElement("input");
          select.type = "checkbox";
          select.checked = selectedIds.has(assetId);
          select.disabled = !selectable;
          selectLabel.append(select, document.createTextNode("采用"));
          controls.append(selectLabel);
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

          select.addEventListener("change", () => {
            if (select.checked) {
              selectedIds.add(assetId);
              persistLicense(assetId, true);
            } else {
              selectedIds.delete(assetId);
              persistLicense(assetId, false);
            }
            persistSelectedCandidates(
              productId,
              productCandidates.filter((item) => selectedIds.has(String(item.asset_id))),
            );
            card.classList.toggle("is-selected", select.checked);
            selectionSummary.textContent = `已选 ${selectedIds.size} 张；采用即确认可用于本次发布。第五阶段再按每个坑位 3–9 张且图片比例一致进行编排，本次不要求填满全部缺失坑位。`;
            renderSelected();
          });
        });
        selectionSummary.textContent = `已选 ${selectedIds.size} 张；采用即确认可用于本次发布。第五阶段再按每个坑位 3–9 张且图片比例一致进行编排，本次不要求填满全部缺失坑位。`;
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
          selectedHeading.textContent = `已选素材 · ${selectedCandidates.length} 张`;
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
          selectedCandidates.forEach((candidate) => {
            const item = element("article", "selected-asset-card");
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
              persistLicense(assetId, false);
              persistSelectedCandidates(
                productId,
                productCandidates.filter(
                  (value) => latestIds.has(String(value.asset_id))
                    && String(value.asset_id) !== assetId,
                ),
              );
              draw();
            });
            item.append(
              image,
              element("small", "", candidate.source_system || "未知来源"),
              remove,
            );
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
          ...(existing?.target_ratio === ratio
            ? existing.crop_box?.normalized || existing.crop_box || {}
            : {}),
        };
      });
      let action = existing?.action;
      if (!action) {
        if (asset.status === "blocked") action = "excluded";
        else if (asset.preflight?.size_exceeded && matching.length) action = "compress";
        else if (asset.preflight?.size_exceeded) action = "crop_and_compress";
        else if (matching.length) action = "direct";
        else action = "crop";
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
        if (["direct", "crop", "compress", "crop_and_compress"].includes(state.action)) {
          decision.target_ratio = state.targetRatio;
        }
        if (["crop", "crop_and_compress"].includes(state.action)) {
          decision.crop_box = state.boxes[state.targetRatio];
        }
        return decision;
      });
      writeJsonListControl("decisions", decisions, { notify });
    };

    const overview = element("div", "image-review-overview");
    overview.append(
      element("strong", "", `第三阶段已选 ${data.selected_count || assets.length} 张`),
      element("span", "", `可审查 ${data.reviewable_count || 0} · 阻断 ${data.blocked_count || 0} · 重复 ${data.duplicate_count || 0}`),
      element("span", "", "人工裁剪与本地图片压缩已启用；所有派生文件只写入当前任务目录。"),
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

      const actionLabel = element("label", "crop-control-label", "处理决定");
      const actionSelect = document.createElement("select");
      [
        ["direct", "直接使用"],
        ["crop", "人工裁剪"],
        ["compress", "压缩"],
        ["crop_and_compress", "人工裁剪并压缩"],
        ["candidate_only", "仅作候选"],
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

      const ratioLabel = element("label", "crop-control-label", "目标比例");
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
      const processed = element("section", "processed-output-summary");
      const processButton = element("button", "button-secondary", "生成处理预览");
      processButton.type = "button";
      const renderProcessed = () => {
        processed.replaceChildren();
        const processedOutput = state.output;
        if (processedOutput) {
          const compressionRate = asset.size_bytes && processedOutput.output_size_bytes
            ? `${Math.round((1 - processedOutput.output_size_bytes / asset.size_bytes) * 100)}%`
            : "—";
          const image = document.createElement("img");
          image.className = "processed-output-image";
          image.alt = `${asset.product_title || asset.product_id} 处理后预览`;
          image.src = processedOutput.kind === "direct"
            ? apiPath(`/stages/image_review/assets/${encodeURIComponent(assetId)}`)
            : apiPath(
              `/stages/image_review/outputs/${encodeURIComponent(assetId)}?sha256=${encodeURIComponent(processedOutput.output_sha256 || "")}`,
            );
          processed.append(
            element("strong", "", "处理后输出"),
            image,
            element("span", "", `尺寸 ${processedOutput.output_width || "?"}×${processedOutput.output_height || "?"} · 比例 ${processedOutput.target_ratio || state.targetRatio}`),
            element("span", "", `文件 ${formatBytes(processedOutput.output_size_bytes)} · ${processedOutput.output_format || "JPEG"} · 质量 ${processedOutput.quality ?? "原图"}`),
            element("span", "", `动作 ${processedOutput.kind || state.action} · 体积变化 ${compressionRate}`),
            processButton,
          );
        } else {
          processed.append(
            element("strong", "", "预计处理后"),
            element("span", "", "先生成任务本地预览并核对实际尺寸、比例和文件大小，再提交本阶段。"),
            processButton,
          );
        }
      };
      processButton.addEventListener("click", async () => {
        if (!["direct", "crop", "compress", "crop_and_compress"].includes(state.action)) {
          actionMessage.textContent = "当前决定不会生成发布输出。";
          return;
        }
        processButton.disabled = true;
        processButton.textContent = "处理中…";
        try {
          const payload = {
            action: state.action,
            target_ratio: state.targetRatio,
          };
          if (["crop", "crop_and_compress"].includes(state.action)) {
            payload.crop_box = state.boxes[state.targetRatio];
          }
          const response = await fetchJson(
            apiPath(`/stages/image_review/assets/${encodeURIComponent(assetId)}/process`),
            { method: "POST", body: JSON.stringify(payload) },
          );
          state.output = response.output || null;
          actionMessage.textContent = "已生成处理预览，请核对处理前后信息。";
        } catch (error) {
          state.output = null;
          actionMessage.textContent = error.message;
        } finally {
          processButton.disabled = false;
          processButton.textContent = "重新生成处理预览";
          renderProcessed();
        }
      });
      renderProcessed();
      editor.appendChild(processed);
      card.append(sourcePane, editor);
      list.appendChild(card);

      const clamp = (value, minimum, maximum) => Math.min(
        maximum,
        Math.max(minimum, value),
      );
      const currentBox = () => state.boxes[state.targetRatio];
      const update = (notify = false) => {
        const cropMode = ["crop", "crop_and_compress"].includes(state.action);
        ratioSelect.disabled = !["direct", "crop", "compress", "crop_and_compress"].includes(state.action);
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
        state.output = null;
        renderProcessed();
        if (["direct", "compress"].includes(state.action) && !matching.includes(state.targetRatio)) {
          state.targetRatio = matching[0] || "";
          ratioSelect.value = state.targetRatio;
        }
        update(true);
      });
      ratioSelect.addEventListener("change", () => {
        state.targetRatio = ratioSelect.value;
        state.output = null;
        renderProcessed();
        update(true);
      });
      sourceImage.addEventListener("load", () => update(false));

      const beginPointer = (event, mode) => {
        if (!["crop", "crop_and_compress"].includes(state.action) || !currentBox()) return;
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
          state.output = null;
          renderProcessed();
          update(true);
        };
        const stop = () => {
          window.removeEventListener("pointermove", move);
          window.removeEventListener("pointerup", stop);
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
    const saved = readJsonListControl("slot_assignments");
    const stateByProduct = new Map();
    products.forEach((product) => {
      const productId = String(product.product_id || "");
      let assignments = saved
        .filter((item) => String(item.product_id || "") === productId)
        .map((item) => ({
          slot_id: String(item.slot_id || ""),
          product_id: productId,
          target_ratio: String(item.target_ratio || ""),
          asset_ids: Array.isArray(item.asset_ids)
            ? item.asset_ids.map(String)
            : [],
        }));
      if (!assignments.length) {
        const ratios = Object.entries(product.available_by_ratio || {})
          .sort((left, right) => Number(right[1]) - Number(left[1]));
        const targetRatio = String(ratios[0]?.[0] || "3:4");
        const assetIds = (product.outputs || [])
          .filter((output) => output.target_ratio === targetRatio)
          .slice(0, Number(data.slot_image_max || 9))
          .map((output) => String(output.asset_id));
        assignments = [{
          slot_id: `${productId}-slot-1`,
          product_id: productId,
          target_ratio: targetRatio,
          asset_ids: assetIds,
        }];
      }
      stateByProduct.set(productId, assignments);
    });
    const persist = (notify = false) => {
      writeJsonListControl(
        "slot_assignments",
        [...stateByProduct.values()].flat(),
        { notify },
      );
    };
    const overview = element("div", "slot-board-overview");
    overview.append(
      element("strong", "", `第四阶段确认输出 ${products.reduce((sum, product) => sum + (product.outputs || []).length, 0)} 张`),
      element("span", "", `每个坑位 ${data.slot_image_min || 3}–${data.slot_image_max || 9} 张，坑位内只能使用一种比例。`),
      element("span", "", `已阻止 ${data.blocked_outputs?.length || 0} 个超限或无效输出。`),
    );
    content.appendChild(overview);
    const board = element("div", "slot-board-products");
    content.appendChild(board);

    const draw = () => {
      board.replaceChildren();
      products.forEach((product) => {
        const productId = String(product.product_id || "");
        const section = element("section", "slot-product");
        const heading = element("div", "slot-product-heading");
        heading.append(
          element("strong", "", `商品 ${productId}`),
          element("span", "", `3:4 ${product.available_by_ratio?.["3:4"] || 0} 张 · 1:1 ${product.available_by_ratio?.["1:1"] || 0} 张`),
        );
        const add = element("button", "button-secondary", "添加坑位");
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
          });
          persist(true);
          draw();
        });
        heading.appendChild(add);
        section.appendChild(heading);
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
          remove.disabled = stateByProduct.get(productId).length === 1;
          const count = element("span", "slot-count");
          controls.append(slotId, ratio, count, remove);
          slot.appendChild(controls);
          const grid = element("div", "slot-asset-grid");
          slot.appendChild(grid);
          slots.appendChild(slot);

          const drawAssets = () => {
            grid.replaceChildren();
            const outputs = (product.outputs || []).filter(
              (output) => output.target_ratio === assignment.target_ratio,
            );
            assignment.asset_ids = assignment.asset_ids.filter((assetId) =>
              outputs.some((output) => String(output.asset_id) === String(assetId))
            );
            outputs.forEach((output) => {
              const card = element("label", "slot-asset-card");
              const image = document.createElement("img");
              image.loading = "lazy";
              image.alt = `${productId} ${assignment.slot_id} 候选图`;
              image.src = apiPath(
                `/stages/slots_copy/assets/${encodeURIComponent(output.asset_id)}`,
              );
              const checkbox = document.createElement("input");
              checkbox.type = "checkbox";
              checkbox.checked = assignment.asset_ids.includes(String(output.asset_id));
              checkbox.disabled = (
                !checkbox.checked
                && assignment.asset_ids.length >= Number(data.slot_image_max || 9)
              );
              checkbox.addEventListener("change", () => {
                const assetId = String(output.asset_id);
                if (checkbox.checked) assignment.asset_ids.push(assetId);
                else assignment.asset_ids = assignment.asset_ids.filter(
                  (value) => value !== assetId,
                );
                persist(true);
                drawAssets();
              });
              card.append(
                image,
                checkbox,
                element("span", "", `${output.kind === "crop" ? "裁剪" : "原图"} · ${(Number(output.output_size_bytes || 0) / 1048576).toFixed(2)}MB`),
              );
              grid.appendChild(card);
            });
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
            assignment.asset_ids = [];
            persist(true);
            drawAssets();
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
  }

  function renderStageResult(schemaComponent) {
    const view = UiState.resultView(uiState);
    (resultRenderers[schemaComponent] || []).forEach((rendererName) => {
      renderResult(rendererName, view);
    });
    if (schemaComponent === "asset_match_gallery") {
      renderFolderOwnershipReview(view);
      renderAssetMatchGallery(view);
    }
    if (schemaComponent === "image_review") renderImageReview(view);
    if (schemaComponent === "slots_copy_editor") renderSlotBoard(view);
    if (schemaComponent === "inspection_matrix") renderInspectionMatrix(view);
  }

  async function loadStage() {
    const requestedStageId = currentStageId;
    if (!sessionId) {
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
      revision = payload.state.revision;
      revisionLabel.textContent = String(revision);
      uiState = UiState.receiveStage(uiState, {
        stageId: requestedStageId,
        status: payload.state.status,
        result: payload.result,
        submission: payload.submission,
      });
      if (payload.input) hydrateForm(activeForm(), payload.input.values);
      renderStatus();
      renderSubmission();
      renderStageResult(stages.get(requestedStageId).component);
    } catch (error) {
      if (requestedStageId !== currentStageId) return;
      actionMessage.textContent = error.message;
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
    const form = activeForm();
    if (!form) {
      persistenceInFlight = false;
      return;
    }
    clearFieldErrors(form);
    let values;
    try {
      values = serializeForm(form);
    } catch (error) {
      actionMessage.textContent = error.message;
      persistenceInFlight = false;
      return;
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
      const payload = await fetchJson(apiPath(`/stages/${requestedStageId}${stageActions[mode]}`), {
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
          status: "ready_for_agent",
          result: null,
          submission: { created_at: payload.created_at },
        });
        renderStatus();
        renderSubmission();
        renderStageResult(stages.get(requestedStageId).component);
        actionMessage.textContent = "交接已持久化，正在等待 Agent 接收。";
        await loadRecoveryInstruction(requestedStageId);
      } else {
        revision = UiState.persistedRevision(payload);
        const preservesReviewContext = [
          "completeness",
          "asset_matching",
          "image_review",
          "slots_copy",
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
          : "草稿已保存；不会创建 Agent 交接。";
      }
      revisionLabel.textContent = String(revision);
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
      const pausedSubmission = pendingPersistenceMode === "submit";
      pendingPersistenceMode = null;
      actionMessage.textContent = pausedSubmission
        ? `保存失败，排队的提交已暂停：${error.message}`
        : error.message;
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
    actionMessage.textContent = "正在撤回尚未被 Agent 认领的提交…";
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
    recoveryButton.textContent = "已复制恢复指令";
    window.setTimeout(() => { recoveryButton.textContent = "复制恢复指令"; }, 1800);
  }

  async function pollStage() {
    if (document.hidden || !sessionId) return;
    const requestedStageId = currentStageId;
    try {
      const [stageState, sessionPayload] = await Promise.all([
        fetchJson(apiPath(`/stages/${requestedStageId}/status`)),
        fetchJson(apiPath()),
      ]);
      if (requestedStageId !== currentStageId) return;
      const priorRevision = revision;
      const priorStatus = uiState.serverStatus;
      revision = stageState.revision;
      revisionLabel.textContent = String(revision);
      const heartbeat = sessionPayload.session.last_agent_heartbeat;
      applySessionSnapshot(sessionPayload.session);
      uiState = UiState.receiveStatus(uiState, stageState.status, heartbeat);
      renderStatus();
      const connection = UiState.connectionView(uiState, Date.now());
      connectionLabel.textContent = connection.connectionLabel;
      offlinePanel.hidden = connection.connectionLabel === "Agent 已连接";
      if (priorRevision !== revision || priorStatus !== stageState.status) await loadStage();
    } catch (error) {
      offlinePanel.hidden = false;
      connectionLabel.textContent = "Agent 状态暂不可用";
    }
  }

  function activateStage(stageId) {
    if (!stages.has(stageId)) return;
    stageGeneration += 1;
    window.clearTimeout(autoSaveTimer);
    pendingPersistenceMode = null;
    currentStageId = stageId;
    uiState = UiState.switchStage(uiState, stageId);
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
    actionMessage.textContent = "填写完成后可保存草稿，或提交给 Agent。";
    renderStatus();
    renderSubmission();
    renderStageResult(stage.component);
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
  panels.forEach((panel) => {
    panel.addEventListener("input", (event) => {
      if (!event.target?.getAttribute?.("name")) return;
      if (panel.dataset.stagePanel === currentStageId) {
        localEditVersion += 1;
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
  async function bootstrap() {
    let initialStage = currentStageId;
    let warning = null;
    if (sessionId) {
      try {
        const payload = await fetchJson(apiPath());
        applySessionSnapshot(payload.session);
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
