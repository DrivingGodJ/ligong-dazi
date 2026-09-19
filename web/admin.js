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
  toastTimer: null,
};

const elements = {
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
  const response = await fetch(`${ADMIN_API}${path}`, { ...options, headers });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(formatApiError(payload, `请求没有完成（${response.status}）`));
  }
  return payload;
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
    : "密钥只保存在运行服务的这台电脑上，页面和运行记录都不会显示原文。";
}

function populateConfig(config) {
  state.config = config;
  const radio = elements.form.querySelector(`input[name='vendor'][value='${config.selected_provider}']`);
  if (radio) radio.checked = true;
  elements.fallback.checked = config.fallback_enabled;
  elements.apiKey.value = "";
  updateProviderFields(config.selected_provider, true);

  elements.keyBadge.textContent = config.key_configured ? "密钥已保存在本机" : "还没有保存密钥";
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
  elements.form.addEventListener("submit", saveConfig);
  elements.testButton.addEventListener("click", testConnection);
  elements.refreshLogs.addEventListener("click", async () => {
    setButtonLoading(elements.refreshLogs, true, "正在刷新…");
    await Promise.all([loadOverview(), loadLogs()]);
    setButtonLoading(elements.refreshLogs, false);
  });
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
  try {
    await Promise.all([loadConfig(), loadOverview(), loadLogs()]);
  } catch (error) {
    showError(`后台暂时无法读取设置：${error.message}`);
    elements.serviceStatus.textContent = "后台未连接";
    elements.serviceProvider.textContent = "请确认本地服务正在运行";
    elements.serviceSummary.classList.add("is-attention");
  }
  window.setInterval(() => {
    if (!document.hidden) Promise.all([loadOverview(), loadLogs()]);
  }, 30000);
}

initialize();
