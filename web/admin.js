const ADMIN_API = "/api/v1/admin";

const providerPresets = {
  deepseek: {
    label: "DeepSeek",
    baseUrl: "https://api.deepseek.com",
    model: "deepseek-flash",
  },
  openai: {
    label: "OpenAI",
    baseUrl: "https://api.openai.com/v1",
    model: "gpt-4.1-mini",
  },
  custom: {
    label: "其他兼容服务",
    baseUrl: "",
    model: "",
  },
  rules: {
    label: "纯规则模式",
    baseUrl: "",
    model: "",
  },
};

const state = {
  config: null,
  logs: [],
  filter: "all",
  appealFilter: "active",
  appeals: [],
  appealOffset: 0,
  toastTimer: null,
};

const elements = {
  dashboard: document.querySelector("#admin-dashboard"),
  loginPanel: document.querySelector("#admin-login"),
  loginForm: document.querySelector("#admin-login-form"),
  loginError: document.querySelector("#admin-login-error"),
  loginButton: document.querySelector("#admin-login-button"),
  logoutButton: document.querySelector("#admin-logout"),
  form: document.querySelector("#ai-config-form"),
  apiFields: document.querySelector("#api-fields"),
  apiKey: document.querySelector("#api-key"),
  keyHelp: document.querySelector("#key-help"),
  keyBadge: document.querySelector("#saved-key-badge"),
  model: document.querySelector("#model-name"),
  baseUrl: document.querySelector("#base-url"),
  fallback: document.querySelector("#fallback-enabled"),
  advanced: document.querySelector("#advanced-settings"),
  configError: document.querySelector("#config-error"),
  configResult: document.querySelector("#config-result"),
  saveButton: document.querySelector("#save-config"),
  testButton: document.querySelector("#test-connection"),
  toggleKey: document.querySelector("#toggle-key-visibility"),
  serviceSummary: document.querySelector("#service-summary"),
  serviceStatus: document.querySelector("#service-status-label"),
  serviceProvider: document.querySelector("#service-provider-label"),
  metricUsers: document.querySelector("#metric-users"),
  metricRequests: document.querySelector("#metric-requests"),
  metricCompleted: document.querySelector("#metric-completed"),
  metricPending: document.querySelector("#metric-pending"),
  fallbackNote: document.querySelector("#fallback-note"),
  logList: document.querySelector("#log-list"),
  appealList: document.querySelector("#admin-appeal-list"),
  refreshAppeals: document.querySelector("#refresh-appeals"),
  loadMoreAppeals: document.querySelector("#load-more-appeals"),
  refreshLogs: document.querySelector("#refresh-logs"),
  toast: document.querySelector("#admin-toast"),
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatApiError(payload, fallback) {
  if (!payload) return fallback;
  if (typeof payload.detail === "string") return payload.detail;
  if (Array.isArray(payload.detail)) {
    return payload.detail.map((item) => item.msg || "填写内容有误").join("；");
  }
  return fallback;
}

async function adminApi(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body) headers.set("Content-Type", "application/json");
  const response = await fetch(`${ADMIN_API}${path}`, { ...options, headers, credentials: "same-origin" });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401 && options.method !== "POST") showLocked();
    throw new Error(formatApiError(payload, `请求没有完成（${response.status}）`));
  }
  return payload;
}

function showLocked() {
  elements.dashboard.classList.add("is-hidden");
  elements.loginPanel.classList.remove("is-hidden");
  elements.logoutButton.classList.add("is-hidden");
  elements.loginForm.reset();
}

function showDashboard(requiresLogin) {
  elements.loginPanel.classList.add("is-hidden");
  elements.dashboard.classList.remove("is-hidden");
  elements.logoutButton.classList.toggle("is-hidden", !requiresLogin);
}

async function loadDashboard() {
  await Promise.all([loadConfig(), loadOverview(), loadLogs(), loadAppeals()]);
}

function renderAppeals() {
  if (!state.appeals.length) {
    elements.appealList.innerHTML = `<div class="admin-empty">目前没有符合这个状态的学号申诉。</div>`;
    return;
  }
  const statusLabels = {
    agent_review: "AI 正在初审申诉材料",
    awaiting_owner: "原账号 24 小时举证中",
    owner_review: "AI 正在初审原账号材料",
    manual_review: "等待人工决定归属",
    claimant_rejected: "申诉材料未通过",
    owner_confirmed: "已保留原账号",
    transferred: "已交接给申诉人",
  };
  elements.appealList.innerHTML = state.appeals.map((item) => `<article class="admin-appeal-card">
    <div class="appeal-card-heading"><strong>学号 ${escapeHtml(item.student_id)}</strong><time datetime="${escapeHtml(item.created_at)}">${escapeHtml(formatTime(item.created_at))}</time></div>
    <p><b>联系申请人：</b><span>${escapeHtml(item.contact)}</span></p>
    ${item.owner_contact ? `<p><b>联系原账号：</b><span>${escapeHtml(item.owner_contact)}</span></p>` : ""}
    ${item.description ? `<p><b>补充说明：</b>${escapeHtml(item.description)}</p>` : ""}
    <p><b>当前进度：</b>${escapeHtml(statusLabels[item.status] || item.status)}</p>
    ${item.owner_deadline ? `<p><b>原账号截止：</b>${escapeHtml(formatTime(item.owner_deadline))}</p>` : ""}
    ${item.resolution_note ? `<p><b>处理说明：</b>${escapeHtml(item.resolution_note)}</p>` : ""}
    <div class="appeal-materials">
      <a class="button button-quiet button-small" href="/api/v1/admin/student-id-appeals/${escapeHtml(item.id)}/card/claimant" target="_blank" rel="noopener">查看申诉人学生卡</a>
      ${item.owner_agent_review && Object.keys(item.owner_agent_review).length ? `<a class="button button-quiet button-small" href="/api/v1/admin/student-id-appeals/${escapeHtml(item.id)}/card/owner" target="_blank" rel="noopener">查看原账号学生卡</a>` : ""}
    </div>
    <div class="appeal-card-footer"><span>${escapeHtml(statusLabels[item.status] || item.status)}</span>
    ${["agent_review", "awaiting_owner", "owner_review", "manual_review"].includes(item.status) ? `<span class="appeal-resolution-actions"><button class="button button-quiet button-small" type="button" data-resolve-appeal="${escapeHtml(item.id)}" data-decision="keep_owner">保留原账号</button><button class="button button-primary button-small" type="button" data-resolve-appeal="${escapeHtml(item.id)}" data-decision="transfer_to_claimant">交给申诉人</button></span>` : ""}</div>
  </article>`).join("");
}

async function loadAppeals(more = false) {
  if (!more) {
    state.appeals = [];
    state.appealOffset = 0;
    elements.appealList.innerHTML = `<div class="admin-loading">正在读取申诉…</div>`;
  }
  try {
    const page = await adminApi(`/student-id-appeals?status=${state.appealFilter}&offset=${state.appealOffset}&limit=30`);
    state.appeals.push(...page);
    state.appealOffset += page.length;
    renderAppeals();
    elements.loadMoreAppeals.classList.toggle("is-hidden", page.length < 30);
  } catch (error) {
    elements.appealList.innerHTML = `<div class="admin-empty">申诉暂时无法读取：${escapeHtml(error.message)}</div>`;
    elements.loadMoreAppeals.classList.add("is-hidden");
  }
}

async function loginAdmin(event) {
  event.preventDefault();
  elements.loginError.classList.add("is-hidden");
  setButtonLoading(elements.loginButton, true, "正在验证…");
  try {
    await adminApi("/session", {
      method: "POST",
      body: JSON.stringify({ password: elements.loginForm.querySelector("[name=password]").value }),
    });
    elements.loginForm.reset();
    showDashboard(true);
    await loadDashboard();
  } catch (error) {
    elements.loginError.textContent = error.message;
    elements.loginError.classList.remove("is-hidden");
  } finally {
    setButtonLoading(elements.loginButton, false);
  }
}

function setButtonLoading(button, loading, text) {
  if (loading) {
    button.dataset.originalText = button.textContent.trim();
    button.textContent = text;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
  } else {
    button.textContent = button.dataset.originalText || button.textContent;
    button.disabled = false;
    button.removeAttribute("aria-busy");
  }
}

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.add("is-visible");
  window.clearTimeout(state.toastTimer);
  state.toastTimer = window.setTimeout(() => elements.toast.classList.remove("is-visible"), 3000);
}

function showError(message) {
  elements.configError.textContent = message;
  elements.configError.classList.remove("is-hidden");
}

function clearMessages() {
  elements.configError.textContent = "";
  elements.configError.classList.add("is-hidden");
  elements.configResult.textContent = "";
  elements.configResult.classList.add("is-hidden");
  elements.configResult.classList.remove("is-attention");
}

function showConfigResult(title, message, attention = false) {
  elements.configResult.innerHTML = `<strong>${escapeHtml(title)}</strong><br />${escapeHtml(message)}`;
  elements.configResult.classList.remove("is-hidden");
  elements.configResult.classList.toggle("is-attention", attention);
}

function selectedVendor() {
  return elements.form.querySelector("input[name='vendor']:checked")?.value || "rules";
}

function updateProviderFields(vendor, useStoredValues = false) {
  const rulesMode = vendor === "rules";
  elements.apiFields.classList.toggle("is-disabled", rulesMode);
  elements.testButton.textContent = rulesMode ? "检查备用规则" : "先测试连接";
  if (rulesMode) return;

  const storedVendor = state.config?.saved_vendor;
  const canUseStored = useStoredValues || vendor === storedVendor;
  if (canUseStored && state.config) {
    elements.model.value = state.config.model || providerPresets[vendor].model;
    elements.baseUrl.value = state.config.base_url || providerPresets[vendor].baseUrl;
  } else {
    elements.model.value = providerPresets[vendor].model;
    elements.baseUrl.value = providerPresets[vendor].baseUrl;
  }
  elements.advanced.open = vendor === "custom";

  const savedForVendor = state.config?.key_configured && vendor === storedVendor;
  elements.apiKey.placeholder = savedForVendor
    ? "留空会继续使用已保存的 Key"
    : `填写 ${providerPresets[vendor].label} 的 API Key`;
  elements.keyHelp.textContent = savedForVendor
    ? "已经保存过密钥。留空不会删除或替换它；页面不会读取出原文。"
    : "密钥保存在服务端，页面和运行记录都不会显示原文。";
}

function populateConfig(config) {
  state.config = config;
  const radio = elements.form.querySelector(`input[name='vendor'][value='${config.selected_provider}']`);
  if (radio) radio.checked = true;
  elements.fallback.checked = config.fallback_enabled;
  elements.apiKey.value = "";
  updateProviderFields(config.selected_provider, true);

  elements.keyBadge.textContent = config.key_configured ? "密钥已安全保存" : "还没有保存密钥";
  elements.keyBadge.classList.toggle("is-ready", config.key_configured);
}

async function loadConfig() {
  const config = await adminApi("/config");
  populateConfig(config);
}

function renderOverview(overview) {
  elements.metricUsers.textContent = overview.registered_users;
  elements.metricRequests.textContent = overview.match_requests;
  elements.metricCompleted.textContent = overview.completed_runs;
  elements.metricPending.textContent = overview.pending_invitations;
  elements.serviceStatus.textContent = overview.service_status_label;
  elements.serviceProvider.textContent = overview.model
    ? `${overview.provider_label} · ${overview.model}`
    : overview.provider_label;
  elements.serviceSummary.classList.remove("is-loading");
  elements.serviceSummary.classList.toggle("is-attention", overview.service_status === "attention");
  elements.fallbackNote.textContent = overview.fallback_runs
    ? `${overview.fallback_runs} 次匹配由备用规则自动接管`
    : "尚未发生需要备用规则接管的情况";
}

async function loadOverview() {
  renderOverview(await adminApi("/overview"));
}

function formPayload() {
  const apiKey = elements.apiKey.value.trim();
  return {
    vendor: selectedVendor(),
    api_key: apiKey || null,
    base_url: elements.baseUrl.value.trim(),
    model: elements.model.value.trim(),
    fallback_enabled: elements.fallback.checked,
  };
}

async function saveConfig(event) {
  event.preventDefault();
  clearMessages();
  setButtonLoading(elements.saveButton, true, "正在保存…");
  try {
    const config = await adminApi("/config", {
      method: "PUT",
      body: JSON.stringify(formPayload()),
    });
    populateConfig(config);
    showConfigResult("设置已生效", config.status_message);
    showToast("AI 服务设置已保存");
    await Promise.all([loadOverview(), loadLogs()]);
  } catch (error) {
    showError(error.message);
  } finally {
    setButtonLoading(elements.saveButton, false);
  }
}

async function testConnection() {
  clearMessages();
  setButtonLoading(elements.testButton, true, "正在测试…");
  try {
    const result = await adminApi("/config/test", {
      method: "POST",
      body: JSON.stringify(formPayload()),
    });
    const timing = result.elapsed_ms ? `（约 ${result.elapsed_ms} 毫秒）` : "";
    showConfigResult(result.title, `${result.message}${timing}`, !result.ok);
    showToast(result.ok ? "连接测试成功" : "连接测试需要检查");
    await loadLogs();
  } catch (error) {
    showError(error.message);
  } finally {
    setButtonLoading(elements.testButton, false);
  }
}

function formatTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatDuration(milliseconds) {
  if (milliseconds === null || milliseconds === undefined) return "";
  if (milliseconds < 1000) return `${milliseconds} 毫秒`;
  return `${(milliseconds / 1000).toFixed(1)} 秒`;
}

function technicalDetails(info) {
  const entries = Object.entries(info || {});
  if (!entries.length) return "";
  const rows = entries
    .map(([key, value]) => `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd>`)
    .join("");
  return `<details class="technical-details"><summary>查看技术信息</summary><dl>${rows}</dl></details>`;
}

function readableMessage(message) {
  const text = String(message || "");
  if (text.length <= 180) {
    return `<p class="log-message">${escapeHtml(text)}</p>`;
  }
  return `<p class="log-message">${escapeHtml(text.slice(0, 180))}…</p>
    <details class="summary-details">
      <summary>查看完整匹配说明</summary>
      <p>${escapeHtml(text)}</p>
    </details>`;
}

function renderLogs() {
  const visible = state.logs.filter((item) => state.filter === "all" || item.kind === state.filter);
  if (!visible.length) {
    elements.logList.innerHTML = `<div class="admin-empty">这里还没有对应记录。发起一次匹配或测试 AI 连接后，就会看到清楚的过程说明。</div>`;
    return;
  }
  elements.logList.innerHTML = visible
    .map((item) => {
      const statusClass = `is-${item.status}`;
      const steps = (item.steps || []).length
        ? `<ol class="log-steps">${item.steps
            .map(
              (step) =>
                `<li>${escapeHtml(step.title)}${step.detail ? `<small>${escapeHtml(step.detail)}</small>` : ""}</li>`,
            )
            .join("")}</ol>`
        : "";
      const duration = formatDuration(item.duration_ms);
      return `<article class="log-item" data-kind="${escapeHtml(item.kind)}">
        <div class="log-meta">
          <time datetime="${escapeHtml(item.created_at)}">${escapeHtml(formatTime(item.created_at))}</time>
          ${duration ? `<small>用时 ${escapeHtml(duration)}</small>` : ""}
          <span class="log-status ${statusClass}">${escapeHtml(item.status_label)}</span>
        </div>
        <div class="log-body">
          <h3>${escapeHtml(item.title)}</h3>
          ${item.context ? `<p class="log-context">${escapeHtml(item.context)}</p>` : ""}
          ${readableMessage(item.message)}
          ${steps}
          ${technicalDetails(item.technical_info)}
        </div>
      </article>`;
    })
    .join("");
}

async function loadLogs() {
  try {
    state.logs = await adminApi("/logs?limit=40");
    renderLogs();
  } catch (error) {
    elements.logList.innerHTML = `<div class="admin-empty">暂时无法读取运行记录。${escapeHtml(error.message)}</div>`;
  }
}

function bindEvents() {
  elements.loginForm.addEventListener("submit", loginAdmin);
  elements.logoutButton.addEventListener("click", async () => {
    try {
      await adminApi("/session", { method: "DELETE" });
    } finally {
      showLocked();
    }
  });
  elements.form.addEventListener("submit", saveConfig);
  elements.testButton.addEventListener("click", testConnection);
  elements.refreshLogs.addEventListener("click", async () => {
    setButtonLoading(elements.refreshLogs, true, "正在刷新…");
    await Promise.all([loadOverview(), loadLogs()]);
    setButtonLoading(elements.refreshLogs, false);
  });
  elements.refreshAppeals.addEventListener("click", async () => {
    setButtonLoading(elements.refreshAppeals, true, "正在刷新…");
    await loadAppeals();
    setButtonLoading(elements.refreshAppeals, false);
  });
  elements.loadMoreAppeals.addEventListener("click", () => loadAppeals(true));
  elements.appealList.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-resolve-appeal]");
    if (!button) return;
    const decision = button.dataset.decision;
    const action = decision === "keep_owner" ? "保留原账号" : "把学号交给申诉人";
    if (!window.confirm(`确认${action}？这个决定会结束冻结，且不能在页面中撤回。`)) return;
    setButtonLoading(button, true, "正在更新…");
    try {
      await adminApi(`/student-id-appeals/${button.dataset.resolveAppeal}/resolve`, {
        method: "POST",
        body: JSON.stringify({decision}),
      });
      showToast(`已${action}`);
      await loadAppeals();
    } catch (error) { showToast(error.message); setButtonLoading(button, false); }
  });
  document.querySelectorAll("[data-appeal-filter]").forEach((button) => button.addEventListener("click", () => {
    state.appealFilter = button.dataset.appealFilter;
    document.querySelectorAll("[data-appeal-filter]").forEach((item) => item.classList.toggle("is-active", item === button));
    loadAppeals();
  }));
  elements.toggleKey.addEventListener("click", () => {
    const showing = elements.apiKey.type === "text";
    elements.apiKey.type = showing ? "password" : "text";
    elements.toggleKey.textContent = showing ? "显示" : "隐藏";
    elements.apiKey.focus();
  });
  elements.form.querySelectorAll("input[name='vendor']").forEach((radio) => {
    radio.addEventListener("change", () => {
      clearMessages();
      updateProviderFields(radio.value);
    });
  });
  document.querySelectorAll(".log-filter").forEach((button) => {
    button.addEventListener("click", () => {
      state.filter = button.dataset.filter;
      document.querySelectorAll(".log-filter").forEach((item) => {
        item.classList.toggle("is-active", item === button);
      });
      renderLogs();
    });
  });
}

async function initialize() {
  bindEvents();
  window.setInterval(() => {
    if (!document.hidden && elements.loginPanel.classList.contains("is-hidden")) {
      Promise.all([loadOverview(), loadLogs(), loadAppeals()]).catch(() => {});
    }
  }, 30000);
  try {
    const session = await adminApi("/session");
    showDashboard(session.requires_login);
    await loadDashboard();
  } catch (error) {
    if (!elements.loginPanel.classList.contains("is-hidden")) return;
    showError(`后台暂时无法读取设置：${error.message}`);
    elements.serviceStatus.textContent = "后台未连接";
    elements.serviceProvider.textContent = "请稍后重试";
    elements.serviceSummary.classList.add("is-attention");
  }
}

initialize();
