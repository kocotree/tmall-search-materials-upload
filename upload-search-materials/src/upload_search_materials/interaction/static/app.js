(() => {
  "use strict";

  const shell = document.querySelector("[data-component='AppShell']");
  if (!shell) return;

  const registry = JSON.parse(document.querySelector("#stage-registry").textContent);
  const stages = new Map(registry.map((stage) => [stage.id, stage]));
  const railButtons = [...document.querySelectorAll("[data-stage-id]")];
  const panels = [...document.querySelectorAll("[data-stage-panel]")];
  const saveButton = document.querySelector("[data-save-draft]");
  const submitButton = document.querySelector("[data-submit-stage]");
  const recoveryButton = document.querySelector("[data-copy-recovery]");
  const offlinePanel = document.querySelector("[data-offline-panel]");
  const statusBadge = document.querySelector("[data-current-status]");
  const handoffStatus = document.querySelector("[data-handoff-status]");
  const actionMessage = document.querySelector("[data-action-message]");
  const revisionLabel = document.querySelector("[data-revision]");
  const titleLabel = document.querySelector("[data-current-title]");
  const currentStageLabel = document.querySelector("[data-current-stage-label]");
  const lastSubmittedLabel = document.querySelector("[data-last-submitted]");
  const taskDirectoryLabel = document.querySelector("[data-task-directory]");

  let sessionId = shell.dataset.sessionId || "";
  let currentStageId = railButtons[0]?.dataset.stageId || "setup";
  let revision = 0;
  let latestRecoveryInstruction = "";
  let lastSubmittedAt = null;

  const statusCopy = {
    draft: "编辑中",
    ready_for_agent: "已提交，等待 Agent",
    processing: "Agent 处理中",
    needs_user_input: "补充后重新提交",
    blocked: "补充后重新提交",
    completed: "已完成",
  };
  const stageActions = { draft: "/draft", submit: "/submit" };
  const resultRenderers = {
    "setup_form": [],
    "inspection_matrix": ["InspectionMatrix"],
    "product_scope_table": ["ProductScopeTable"],
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
    taskDirectoryLabel.textContent = `${shell.dataset.runsRoot}\\${sessionId}`;
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

  function setStatus(status) {
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
    submitButton.disabled = ["ready_for_agent", "processing", "completed"].includes(status);
    saveButton.disabled = ["ready_for_agent", "processing", "completed"].includes(status);
    updateResultsRecovery(status);
  }

  function updateResultsRecovery(status) {
    const isResults = currentStageId === "results";
    const mayRecover = isResults && ["needs_user_input", "blocked"].includes(status);
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
    } else {
      saveButton.hidden = false;
      submitButton.hidden = false;
    }
  }

  function renderResult(componentName, result) {
    if (!result || typeof result !== "object") return;
    const module = document.querySelector(`[data-component="${CSS.escape(componentName)}"]`);
    const empty = module?.querySelector("[data-empty-state]");
    if (!module || !empty) return;

    const summary = document.createElement("div");
    summary.className = "result-data";
    const heading = document.createElement("strong");
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
    empty.replaceWith(summary);
  }

  function renderStageResult(schemaComponent, result) {
    (resultRenderers[schemaComponent] || []).forEach((rendererName) => {
      renderResult(rendererName, result);
    });
  }

  async function loadStage() {
    if (!sessionId) {
      revision = 0;
      revisionLabel.textContent = "0";
      setStatus("draft");
      return;
    }
    try {
      const payload = await fetchJson(apiPath(`/stages/${currentStageId}`));
      revision = payload.state.revision;
      revisionLabel.textContent = String(revision);
      setStatus(payload.state.status);
      const stage = stages.get(currentStageId);
      renderStageResult(stage.component, payload.result || payload.state.result);
    } catch (error) {
      actionMessage.textContent = error.message;
    }
  }

  async function persistStage(mode) {
    const form = activeForm();
    if (!form) return;
    clearFieldErrors(form);
    let values;
    try {
      values = serializeForm(form);
    } catch (error) {
      actionMessage.textContent = error.message;
      return;
    }

    saveButton.disabled = true;
    submitButton.disabled = true;
    actionMessage.textContent = mode === "draft" ? "正在保存草稿…" : "正在创建交接…";
    try {
      await ensureSession();
      const body = { values };
      if (mode === "submit") body.revision = revision + 1;
      const payload = await fetchJson(apiPath(`/stages/${currentStageId}${stageActions[mode]}`), {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (mode === "submit") {
        revision = payload.revision;
        lastSubmittedAt = new Date();
        lastSubmittedLabel.textContent = lastSubmittedAt.toLocaleString("zh-CN", { hour12: false });
        setStatus("ready_for_agent");
        actionMessage.textContent = "交接已持久化，正在等待 Agent 接收。";
        await loadRecoveryInstruction();
      } else {
        revision += 1;
        setStatus("draft");
        actionMessage.textContent = "草稿已保存；不会创建 Agent 交接。";
      }
      revisionLabel.textContent = String(revision);
    } catch (error) {
      showFieldErrors(form, error.fieldErrors || {});
      actionMessage.textContent = error.message;
      setStatus("draft");
    }
  }

  async function loadRecoveryInstruction() {
    if (!sessionId) return;
    try {
      const payload = await fetchJson(apiPath(`/stages/${currentStageId}/recovery`));
      latestRecoveryInstruction = payload.instruction;
      recoveryButton.disabled = false;
    } catch (error) {
      latestRecoveryInstruction = "";
      recoveryButton.disabled = true;
    }
  }

  async function copyRecoveryInstruction() {
    if (!latestRecoveryInstruction) await loadRecoveryInstruction();
    if (!latestRecoveryInstruction) return;
    await navigator.clipboard.writeText(latestRecoveryInstruction);
    recoveryButton.textContent = "已复制恢复指令";
    window.setTimeout(() => { recoveryButton.textContent = "复制恢复指令"; }, 1800);
  }

  async function pollStage() {
    if (document.hidden || !sessionId) return;
    try {
      const [stageState, sessionPayload] = await Promise.all([
        fetchJson(apiPath(`/stages/${currentStageId}/status`)),
        fetchJson(apiPath()),
      ]);
      revision = stageState.revision;
      revisionLabel.textContent = String(revision);
      setStatus(stageState.status);
      const heartbeat = sessionPayload.session.last_agent_heartbeat;
      const heartbeatAge = heartbeat ? Date.now() - Date.parse(heartbeat) : Infinity;
      const agentOnline = stageState.status === "processing" && heartbeatAge < 10000;
      offlinePanel.hidden = agentOnline;
      if (!agentOnline) {
        offlinePanel.querySelector(".offline-title strong").textContent = "Agent 未连接";
      }
    } catch (error) {
      offlinePanel.hidden = false;
    }
  }

  function activateStage(stageId) {
    if (!stages.has(stageId)) return;
    currentStageId = stageId;
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
    window.scrollTo({ top: 0, behavior: "smooth" });
    loadStage();
  }

  railButtons.forEach((button) => {
    button.addEventListener("click", () => activateStage(button.dataset.stageId));
  });
  panels.forEach((panel) => {
    panel.addEventListener("input", () => {
      if (panel.dataset.stagePanel === currentStageId) setStatus("draft");
    });
  });
  saveButton.addEventListener("click", () => persistStage("draft"));
  submitButton.addEventListener("click", () => persistStage("submit"));
  recoveryButton.addEventListener("click", copyRecoveryInstruction);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) pollStage();
  });

  setInterval(pollStage, 2000);
  activateStage(currentStageId);
  if (sessionId) loadRecoveryInstruction();
})();
