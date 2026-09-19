const API_ROOT = "/api/v1";
const TOKEN_KEY = "ligong_dazi_access_token";

const state = {
  token: localStorage.getItem(TOKEN_KEY),
  user: null,
  preview: null,
  selectedUsers: new Set(),
  selectedActivity: null,
  runningTimer: null,
  toastTimer: null,
};

const elements = {
  authView: document.querySelector("#auth-view"),
  appView: document.querySelector("#app-view"),
  mainNav: document.querySelector("#main-nav"),
  accountArea: document.querySelector("#account-area"),
  accountName: document.querySelector("#account-name"),
  authError: document.querySelector("#auth-error"),
  loginForm: document.querySelector("#login-form"),
  registerForm: document.querySelector("#register-form"),
  matchForm: document.querySelector("#match-form"),
  matchSubmit: document.querySelector("#match-submit"),
  resultEmpty: document.querySelector("#result-empty"),
  resultRunning: document.querySelector("#result-running"),
  resultContent: document.querySelector("#result-content"),
  resultSuccess: document.querySelector("#result-success"),
  matchError: document.querySelector("#match-error"),
  runningTitle: document.querySelector("#running-title"),
  progressBar: document.querySelector("#progress-bar"),
  resultSummary: document.querySelector("#result-summary"),
  agentTrace: document.querySelector("#agent-trace"),
  personalizationReport: document.querySelector("#personalization-report"),
  candidateList: document.querySelector("#candidate-list"),
  confirmBar: document.querySelector("#confirm-bar"),
  selectionSummary: document.querySelector("#selection-summary"),
  confirmButton: document.querySelector("#confirm-match-button"),
  successTitle: document.querySelector("#success-title"),
  successCopy: document.querySelector("#success-copy"),
  invitationList: document.querySelector("#invitation-list"),
  inviteBadge: document.querySelector("#invite-badge"),
  activityList: document.querySelector("#activity-list"),
  profileForm: document.querySelector("#profile-form"),
  profileCredit: document.querySelector("#profile-credit"),
  profileStatus: document.querySelector("#profile-status"),
  agentModePill: document.querySelector("#agent-mode-pill"),
  toast: document.querySelector("#toast"),
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
    return payload.detail
      .map((item) => item.msg || "输入内容有误")
      .filter(Boolean)
      .join("；");
  }
  return fallback;
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.token) headers.set("Authorization", `Bearer ${state.token}`);
  if (options.body && !(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${API_ROOT}${path}`, { ...options, headers });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith("/auth/")) {
      logout(false);
    }
    throw new Error(formatApiError(payload, `请求失败（${response.status}）`));
  }
  return payload;
}

function setButtonLoading(button, loading, loadingText) {
  if (loading) {
    button.dataset.originalText = button.textContent.trim();
    button.textContent = loadingText;
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
  state.toastTimer = window.setTimeout(() => {
    elements.toast.classList.remove("is-visible");
  }, 2800);
}

function showInlineError(element, message) {
  element.textContent = message;
  element.classList.remove("is-hidden");
}

function hideInlineError(element) {
  element.textContent = "";
  element.classList.add("is-hidden");
}

function switchAuthPanel(panel) {
  const loginActive = panel === "login";
  document.querySelector("#login-panel").classList.toggle("is-hidden", !loginActive);
  document.querySelector("#register-panel").classList.toggle("is-hidden", loginActive);
  document.querySelector("#login-tab").classList.toggle("is-active", loginActive);
  document.querySelector("#register-tab").classList.toggle("is-active", !loginActive);
  document.querySelector("#login-tab").setAttribute("aria-selected", String(loginActive));
  document.querySelector("#register-tab").setAttribute("aria-selected", String(!loginActive));
  hideInlineError(elements.authError);
}

function showAuthenticatedShell() {
  elements.authView.classList.add("is-hidden");
  elements.appView.classList.remove("is-hidden");
  elements.mainNav.classList.remove("is-hidden");
  elements.accountArea.classList.remove("is-hidden");
  elements.accountName.textContent = state.user.display_name;
  populateProfileForm();
  loadInvitations(true);
}

function showAuthShell() {
  elements.authView.classList.remove("is-hidden");
  elements.appView.classList.add("is-hidden");
  elements.mainNav.classList.add("is-hidden");
  elements.accountArea.classList.add("is-hidden");
}

function logout(showMessage = true) {
  state.token = null;
  state.user = null;
  state.preview = null;
  state.selectedUsers.clear();
  state.selectedActivity = null;
  localStorage.removeItem(TOKEN_KEY);
  showAuthShell();
  switchAuthPanel("login");
  if (showMessage) showToast("已安全退出");
}

async function loadCurrentUser() {
  state.user = await api("/users/me");
  showAuthenticatedShell();
}

async function handleLogin(event) {
  event.preventDefault();
  hideInlineError(elements.authError);
  const button = event.submitter;
  setButtonLoading(button, true, "正在登录…");
  const form = new FormData(elements.loginForm);
  try {
    const result = await api("/auth/token", {
      method: "POST",
      body: JSON.stringify({ email: form.get("email"), password: form.get("password") }),
    });
    state.token = result.access_token;
    localStorage.setItem(TOKEN_KEY, state.token);
    await loadCurrentUser();
    switchTab("match");
  } catch (error) {
    showInlineError(elements.authError, error.message);
  } finally {
    setButtonLoading(button, false);
  }
}

async function handleRegister(event) {
  event.preventDefault();
  hideInlineError(elements.authError);
  const button = event.submitter;
  setButtonLoading(button, true, "正在创建账号…");
  const form = new FormData(elements.registerForm);
  try {
    const result = await api("/auth/register", {
      method: "POST",
      body: JSON.stringify({
        display_name: form.get("display_name"),
        email: form.get("email"),
        password: form.get("password"),
        university: "南京理工大学",
      }),
    });
    state.token = result.access_token;
    localStorage.setItem(TOKEN_KEY, state.token);
    await loadCurrentUser();
    switchTab("profile");
    elements.profileStatus.textContent = "先补充兴趣和常去地点，匹配会更准确。";
  } catch (error) {
    showInlineError(elements.authError, error.message);
  } finally {
    setButtonLoading(button, false);
  }
}

function switchTab(tabName) {
  document.querySelectorAll(".nav-button[data-tab]").forEach((button) => {
    const active = button.dataset.tab === tabName;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-current", active ? "page" : "false");
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.toggle("is-hidden", panel.dataset.panel !== tabName);
  });
  if (tabName === "invitations") loadInvitations();
  if (tabName === "activities") loadActivities();
  if (tabName === "profile") populateProfileForm();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function toLocalInputValue(date) {
  const offset = date.getTimezoneOffset();
  return new Date(date.getTime() - offset * 60_000).toISOString().slice(0, 16);
}

function initializeDates() {
  const now = new Date();
  const start = new Date(now.getTime() + 2 * 60 * 60 * 1000);
  start.setMinutes(start.getMinutes() < 30 ? 30 : 60, 0, 0);
  const end = new Date(start.getTime() + 2 * 60 * 60 * 1000);
  document.querySelector("#starts-at").value = toLocalInputValue(start);
  document.querySelector("#ends-at").value = toLocalInputValue(end);
}

function setResultView(view) {
  elements.resultEmpty.classList.toggle("is-hidden", view !== "empty");
  elements.resultRunning.classList.toggle("is-hidden", view !== "running");
  elements.resultContent.classList.toggle("is-hidden", view !== "content");
  elements.resultSuccess.classList.toggle("is-hidden", view !== "success");
  elements.confirmBar.classList.toggle("is-hidden", view !== "content");
}

function startRunningProgress() {
  const stepIds = ["step-activities", "step-users", "step-score"];
  const titles = ["正在查询可加入的活动", "正在寻找时间合适的用户", "正在计算可解释匹配度"];
  let index = 0;
  stepIds.forEach((id) => document.querySelector(`#${id}`).classList.remove("is-current", "is-done"));
  document.querySelector(`#${stepIds[0]}`).classList.add("is-current");
  elements.runningTitle.textContent = titles[0];
  elements.progressBar.style.width = "24%";
  window.clearInterval(state.runningTimer);
  state.runningTimer = window.setInterval(() => {
    if (index >= 2) return;
    document.querySelector(`#${stepIds[index]}`).classList.remove("is-current");
    document.querySelector(`#${stepIds[index]}`).classList.add("is-done");
    index += 1;
    document.querySelector(`#${stepIds[index]}`).classList.add("is-current");
    elements.runningTitle.textContent = titles[index];
    elements.progressBar.style.width = `${54 + index * 22}%`;
  }, 650);
}

function finishRunningProgress() {
  window.clearInterval(state.runningTimer);
  document.querySelectorAll(".agent-steps li").forEach((item) => {
    item.classList.remove("is-current");
    item.classList.add("is-done");
  });
  elements.progressBar.style.width = "100%";
}

function getCategory(form) {
  const custom = String(form.get("custom_category") || "").trim();
  return custom || form.get("category");
}

async function handleMatch(event) {
  event.preventDefault();
  hideInlineError(elements.matchError);
  state.selectedUsers.clear();
  state.selectedActivity = null;
  setResultView("running");
  startRunningProgress();
  setButtonLoading(elements.matchSubmit, true, "Agent 正在匹配…");
  const form = new FormData(elements.matchForm);
  const payload = {
    category: getCategory(form),
    starts_at: new Date(form.get("starts_at")).toISOString(),
    ends_at: new Date(form.get("ends_at")).toISOString(),
    location: String(form.get("location")).trim(),
    people_needed: Number(form.get("people_needed")),
    title: String(form.get("title") || "").trim() || null,
    personal_requirement: String(form.get("personal_requirement") || "").trim() || null,
  };
  try {
    const preview = await api("/matches/preview", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    const run = await api(`/agent-runs/${preview.agent_run_id}`).catch(() => null);
    finishRunningProgress();
    state.preview = { ...preview, requestedCount: payload.people_needed, run };
    renderMatchResult();
    window.setTimeout(() => setResultView("content"), 180);
  } catch (error) {
    window.clearInterval(state.runningTimer);
    setResultView("empty");
    showInlineError(elements.matchError, error.message);
  } finally {
    setButtonLoading(elements.matchSubmit, false);
  }
}

function renderAgentTrace() {
  const toolLabels = {
    search_activities: ["查询已有活动", "检查同类活动与空余名额"],
    search_users: ["寻找可用用户", "过滤冲突、拉黑与低信用候选"],
    calculate_match: ["计算匹配度", "按七项权重生成解释"],
  };
  const trace = (state.preview.run?.trace || []).filter((item) => item.tool && toolLabels[item.tool]);
  const uniqueTrace = [...new Map(trace.map((item) => [item.tool, item])).values()];
  elements.agentTrace.innerHTML = uniqueTrace
    .map((item) => {
      const [title, detail] = toolLabels[item.tool];
      const count = Number.isFinite(item.result_count) ? ` · ${item.result_count} 条结果` : "";
      return `<div class="trace-item"><strong>${escapeHtml(title)}</strong>${escapeHtml(detail + count)}</div>`;
    })
    .join("");
}

function renderPersonalization() {
  const report = state.preview.personalization;
  const notices = [];
  if (report.applied.length) {
    notices.push(`<div class="notice"><strong>已采用：</strong>${escapeHtml(report.applied.join("；"))}</div>`);
  }
  if (report.ignored_for_safety.length) {
    notices.push(
      `<div class="notice notice-warning"><strong>未用于排序：</strong>${escapeHtml(report.ignored_for_safety.join("；"))}</div>`,
    );
  }
  if (report.unresolved.length) {
    notices.push(
      `<div class="notice notice-warning"><strong>暂未识别：</strong>${escapeHtml(report.unresolved.join("；"))}</div>`,
    );
  }
  elements.personalizationReport.innerHTML = notices.join("");
}

function formatDate(value) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function userCandidateCopy(candidate) {
  const user = candidate.user;
  const meta = [user.department, user.grade_year ? `${user.grade_year} 年级` : null, `信用 ${user.credit_score}`]
    .filter(Boolean)
    .join(" · ");
  return {
    title: user.display_name,
    kind: "候选搭子",
    meta,
  };
}

function activityCandidateCopy(candidate) {
  const activity = candidate.activity;
  return {
    title: activity.title,
    kind: "已有活动",
    meta: `${formatDate(activity.starts_at)} · ${activity.location} · ${activity.participant_count}/${activity.capacity} 人`,
  };
}

function renderCandidates() {
  const candidates = state.preview.candidates || [];
  if (!candidates.length) {
    elements.candidateList.innerHTML = `
      <div class="empty-list">
        <div><strong>暂时没有合适候选</strong><br />可以调整时间、地点或个性化要求后重试。</div>
      </div>`;
    elements.confirmBar.classList.add("is-hidden");
    return;
  }
  elements.candidateList.innerHTML = candidates
    .map((candidate) => {
      const copy = candidate.candidate_type === "user" ? userCandidateCopy(candidate) : activityCandidateCopy(candidate);
      const inputType = candidate.candidate_type === "activity" ? "radio" : "checkbox";
      const inputName = candidate.candidate_type === "activity" ? "activity-candidate" : "user-candidate";
      const reasons = candidate.explanation
        .slice(0, 4)
        .map((reason) => `<span>${escapeHtml(reason)}</span>`)
        .join("");
      return `
        <label class="candidate-card" data-type="${candidate.candidate_type}" data-id="${escapeHtml(candidate.candidate_id)}">
          <input type="${inputType}" name="${inputName}" value="${escapeHtml(candidate.candidate_id)}" />
          <div class="candidate-main">
            <div class="candidate-title-row">
              <strong>${escapeHtml(copy.title)}</strong>
              <span class="candidate-kind">${escapeHtml(copy.kind)}</span>
            </div>
            <p class="candidate-meta">${escapeHtml(copy.meta)}</p>
            <div class="candidate-reasons">${reasons}</div>
          </div>
          <div class="candidate-score">${escapeHtml(candidate.score)}<small>匹配分</small></div>
        </label>`;
    })
    .join("");
}

function renderMatchResult() {
  elements.resultSummary.textContent = state.preview.summary;
  elements.agentModePill.lastChild.textContent =
    state.preview.agent_mode === "openai_compatible" ? " 模型 Agent" : " 规则 Agent";
  renderAgentTrace();
  renderPersonalization();
  renderCandidates();
  updateSelectionSummary();
}

function updateSelectionSummary() {
  document.querySelectorAll(".candidate-card").forEach((card) => {
    const selected =
      card.dataset.type === "activity"
        ? state.selectedActivity === card.dataset.id
        : state.selectedUsers.has(card.dataset.id);
    card.classList.toggle("is-selected", selected);
    card.querySelector("input").checked = selected;
  });
  if (state.selectedActivity) {
    elements.selectionSummary.textContent = "已选择加入 1 个已有活动";
    elements.confirmButton.textContent = "确认加入活动";
    elements.confirmButton.disabled = false;
  } else if (state.selectedUsers.size) {
    elements.selectionSummary.textContent = `已选择 ${state.selectedUsers.size} 位搭子`;
    elements.confirmButton.textContent = "确认创建并邀请";
    elements.confirmButton.disabled = false;
  } else {
    elements.selectionSummary.textContent = "尚未选择候选";
    elements.confirmButton.textContent = "确认并执行";
    elements.confirmButton.disabled = true;
  }
}

function handleCandidateSelection(event) {
  const card = event.target.closest(".candidate-card");
  if (!card) return;
  const candidateId = card.dataset.id;
  if (card.dataset.type === "activity") {
    state.selectedActivity = state.selectedActivity === candidateId ? null : candidateId;
    state.selectedUsers.clear();
  } else {
    state.selectedActivity = null;
    if (state.selectedUsers.has(candidateId)) {
      state.selectedUsers.delete(candidateId);
    } else if (state.selectedUsers.size < state.preview.requestedCount) {
      state.selectedUsers.add(candidateId);
    } else {
      showToast(`本次最多选择 ${state.preview.requestedCount} 位搭子`);
    }
  }
  updateSelectionSummary();
}

async function confirmMatch() {
  if (!state.preview || (!state.selectedActivity && !state.selectedUsers.size)) return;
  hideInlineError(elements.matchError);
  setButtonLoading(elements.confirmButton, true, "正在执行…");
  try {
    const result = await api(`/matches/${state.preview.match_request_id}/confirm`, {
      method: "POST",
      body: JSON.stringify({
        candidate_user_ids: [...state.selectedUsers],
        existing_activity_id: state.selectedActivity,
      }),
    });
    elements.successTitle.textContent = result.status === "joined" ? "已加入活动" : "搭子局已创建";
    elements.successCopy.textContent = result.invitations.length
      ? `“${result.activity.title}”已创建，并向 ${result.invitations.length} 位候选发送邀请。`
      : `你已加入“${result.activity.title}”，可以在活动页查看安排。`;
    setResultView("success");
    loadInvitations(true);
  } catch (error) {
    showInlineError(elements.matchError, error.message);
  } finally {
    setButtonLoading(elements.confirmButton, false);
  }
}

function resetMatchResult() {
  state.preview = null;
  state.selectedUsers.clear();
  state.selectedActivity = null;
  hideInlineError(elements.matchError);
  setResultView("empty");
  elements.matchForm.querySelector("textarea[name='personal_requirement']").focus();
}

function invitationStatusLabel(status) {
  const labels = {
    pending: ["待处理", ""],
    accepted: ["已接受", "is-success"],
    rejected: ["已拒绝", "is-muted"],
    expired: ["已过期", "is-muted"],
  };
  return labels[status] || [status, "is-muted"];
}

async function loadInvitations(silent = false) {
  if (!state.token) return;
  if (!silent) elements.invitationList.innerHTML = `<div class="empty-list">正在读取邀请…</div>`;
  try {
    const invitations = await api("/invitations");
    const pendingCount = invitations.filter((item) => item.status === "pending").length;
    elements.inviteBadge.textContent = String(pendingCount);
    elements.inviteBadge.classList.toggle("is-hidden", pendingCount === 0);
    renderInvitations(invitations);
  } catch (error) {
    if (!silent) elements.invitationList.innerHTML = `<div class="empty-list">${escapeHtml(error.message)}</div>`;
  }
}

function renderInvitations(invitations) {
  if (!invitations.length) {
    elements.invitationList.innerHTML = `<div class="empty-list">目前没有收到邀请。发起一次匹配，或切换另一个演示账号试试。</div>`;
    return;
  }
  elements.invitationList.innerHTML = invitations
    .map((invitation) => {
      const [statusText, statusClass] = invitationStatusLabel(invitation.status);
      const actions =
        invitation.status === "pending"
          ? `<div class="list-card-actions">
              <button class="button button-quiet" type="button" data-invitation-id="${escapeHtml(invitation.id)}" data-decision="rejected">拒绝</button>
              <button class="button button-primary" type="button" data-invitation-id="${escapeHtml(invitation.id)}" data-decision="accepted">接受邀请</button>
            </div>`
          : `<span class="status-label ${statusClass}">${escapeHtml(statusText)}</span>`;
      return `<article class="list-card">
        <div>
          <div class="candidate-title-row">
            <h2>${escapeHtml(invitation.activity.title)}</h2>
            <span class="status-label ${statusClass}">${escapeHtml(statusText)}</span>
          </div>
          <p>${escapeHtml(invitation.inviter.display_name)} 邀请你 · ${escapeHtml(formatDate(invitation.activity.starts_at))}</p>
          <p>${escapeHtml(invitation.activity.location)} · ${escapeHtml(invitation.activity.category)}</p>
        </div>
        ${actions}
      </article>`;
    })
    .join("");
}

async function respondInvitation(event) {
  const button = event.target.closest("button[data-invitation-id]");
  if (!button) return;
  setButtonLoading(button, true, button.dataset.decision === "accepted" ? "正在接受…" : "正在拒绝…");
  try {
    await api(`/invitations/${button.dataset.invitationId}/respond`, {
      method: "POST",
      body: JSON.stringify({ decision: button.dataset.decision }),
    });
    showToast(button.dataset.decision === "accepted" ? "已接受邀请" : "已拒绝邀请");
    await loadInvitations();
  } catch (error) {
    showToast(error.message);
    setButtonLoading(button, false);
  }
}

async function loadActivities() {
  elements.activityList.innerHTML = `<div class="empty-list">正在读取活动…</div>`;
  try {
    const activities = await api("/activities?limit=50");
    renderActivities(activities);
  } catch (error) {
    elements.activityList.innerHTML = `<div class="empty-list">${escapeHtml(error.message)}</div>`;
  }
}

function renderActivities(activities) {
  if (!activities.length) {
    elements.activityList.innerHTML = `<div class="empty-list">还没有活动。回到“找搭子”创建第一个搭子局。</div>`;
    return;
  }
  elements.activityList.innerHTML = activities
    .map((activity) => {
      const formed = activity.status === "formed";
      return `<article class="list-card">
        <div>
          <div class="candidate-title-row">
            <h2>${escapeHtml(activity.title)}</h2>
            <span class="status-label ${formed ? "is-success" : ""}">${formed ? "已成局" : "招募中"}</span>
          </div>
          <p>${escapeHtml(formatDate(activity.starts_at))} · ${escapeHtml(activity.location)}</p>
          <p>${escapeHtml(activity.category)} · ${activity.participant_count}/${activity.capacity} 人</p>
        </div>
      </article>`;
    })
    .join("");
}

function populateProfileForm() {
  if (!state.user) return;
  const form = elements.profileForm.elements;
  form.display_name.value = state.user.display_name || "";
  form.campus.value = state.user.campus || "";
  form.department.value = state.user.department || "";
  form.grade_year.value = state.user.grade_year || "";
  form.bio.value = state.user.bio || "";
  form.interests.value = (state.user.interests || []).join("，");
  form.preferred_locations.value = (state.user.preferred_locations || []).join("，");
  form.social_style.value = state.user.social_style || "balanced";
  form.preferred_group_min.value = state.user.preferred_group_min || 2;
  form.preferred_group_max.value = state.user.preferred_group_max || 6;
  elements.profileCredit.textContent = String(state.user.credit_score);
}

function splitList(value) {
  return String(value || "")
    .split(/[，,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

async function saveProfile(event) {
  event.preventDefault();
  const button = event.submitter;
  setButtonLoading(button, true, "正在保存…");
  elements.profileStatus.textContent = "";
  const form = new FormData(elements.profileForm);
  const payload = {
    display_name: String(form.get("display_name")).trim(),
    campus: String(form.get("campus") || "").trim() || null,
    department: String(form.get("department") || "").trim() || null,
    grade_year: form.get("grade_year") ? Number(form.get("grade_year")) : null,
    bio: String(form.get("bio") || "").trim() || null,
    interests: splitList(form.get("interests")),
    preferred_locations: splitList(form.get("preferred_locations")),
    social_style: form.get("social_style"),
    preferred_group_min: Number(form.get("preferred_group_min")),
    preferred_group_max: Number(form.get("preferred_group_max")),
  };
  try {
    state.user = await api("/users/me", { method: "PATCH", body: JSON.stringify(payload) });
    elements.accountName.textContent = state.user.display_name;
    elements.profileCredit.textContent = String(state.user.credit_score);
    elements.profileStatus.textContent = "画像已保存";
    showToast("画像已更新，下一次匹配会使用新信息");
  } catch (error) {
    elements.profileStatus.textContent = error.message;
  } finally {
    setButtonLoading(button, false);
  }
}

function bindEvents() {
  document.querySelector("#login-tab").addEventListener("click", () => switchAuthPanel("login"));
  document.querySelector("#register-tab").addEventListener("click", () => switchAuthPanel("register"));
  elements.loginForm.addEventListener("submit", handleLogin);
  elements.registerForm.addEventListener("submit", handleRegister);
  document.querySelector("#logout-button").addEventListener("click", () => logout());

  document.querySelectorAll(".demo-account").forEach((button) => {
    button.addEventListener("click", () => {
      elements.loginForm.elements.email.value = button.dataset.email;
      elements.loginForm.elements.password.value = "demo-password-123";
      elements.loginForm.elements.password.focus();
    });
  });

  document.querySelectorAll(".nav-button[data-tab]").forEach((button) => {
    button.addEventListener("click", () => switchTab(button.dataset.tab));
  });
  document.querySelectorAll("[data-go-tab]").forEach((button) => {
    button.addEventListener("click", () => switchTab(button.dataset.goTab));
  });
  document.querySelectorAll("[data-refresh]").forEach((button) => {
    button.addEventListener("click", () => {
      if (button.dataset.refresh === "invitations") loadInvitations();
      if (button.dataset.refresh === "activities") loadActivities();
    });
  });

  elements.matchForm.addEventListener("submit", handleMatch);
  document.querySelector("#new-match-button").addEventListener("click", resetMatchResult);
  document.querySelector("#start-another-button").addEventListener("click", resetMatchResult);
  elements.candidateList.addEventListener("click", handleCandidateSelection);
  elements.confirmButton.addEventListener("click", confirmMatch);
  elements.invitationList.addEventListener("click", respondInvitation);
  elements.profileForm.addEventListener("submit", saveProfile);

  document.querySelector("input[name='custom_category']").addEventListener("input", (event) => {
    if (event.target.value.trim()) {
      document.querySelectorAll("input[name='category']").forEach((input) => {
        input.checked = false;
      });
    }
  });
  document.querySelectorAll("input[name='category']").forEach((input) => {
    input.addEventListener("change", () => {
      document.querySelector("input[name='custom_category']").value = "";
    });
  });
}

async function initialize() {
  bindEvents();
  initializeDates();
  elements.confirmButton.disabled = true;
  if (!state.token) {
    showAuthShell();
    return;
  }
  try {
    await loadCurrentUser();
  } catch {
    logout(false);
  }
}

initialize();
