const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "../web/index.html"), "utf8");
const registrationSource = source.slice(source.indexOf("function registrationInterestInputs("), source.indexOf("function openStudentAppeal("));

function node(tag = "div") {
  const classes = new Set();
  return {
    tag, children: [], listeners: {}, value: "", checked: false, textContent: "", dataset: {},
    classList: {
      add: (name) => classes.add(name), remove: (name) => classes.delete(name),
      toggle(name, active) { active ? classes.add(name) : classes.delete(name); },
      contains: (name) => classes.has(name),
    },
    append(...children) { children.forEach((child) => { child.parent = this; this.children.push(child); }); },
    querySelector(selector) {
      for (const child of this.children) {
        if (child.tag === selector) return child;
        const nested = child.querySelector(selector);
        if (nested) return nested;
      }
      return null;
    },
    remove() { this.parent.children = this.parent.children.filter((child) => child !== this); },
    addEventListener(name, callback) { this.listeners[name] = callback; },
    setAttribute(name, value) { this[name] = value; },
    focus() { this.focused = true; },
  };
}

function setup() {
  const grid = node(), draft = node("input"), error = node(), count = node(), add = node("button");
  for (const value of ["羽毛球", "跑步", "自习", "摄影", "电影", "吃饭"]) {
    const label = node("label"), input = node("input");
    Object.assign(input, { name: "interests", type: "checkbox", value });
    label.append(input);
    grid.append(label);
  }
  const descendants = (root) => root.children.flatMap((child) => [child, ...descendants(child)]);
  const inputs = () => descendants(grid).filter((item) => item.tag === "input");
  const form = node("form");
  form.querySelectorAll = (selector) => selector === 'input[name="interests"]'
    ? inputs() : grid.children.filter((item) => item.className === "registration-custom-interest");
  form.reset = () => { form.listeners.reset?.(); inputs().forEach((input) => { input.checked = false; }); draft.value = ""; };
  const nodes = {
    "#register-interest-choices": grid, "#register-custom-interest": draft,
    "#register-interest-error": error, "#register-interest-count": count, "#register-add-interest": add,
    ".onboarding-choices": { scrollIntoView() {} },
  };
  const authError = node(), appeal = node(), button = node("button");
  let apiCalls = 0;
  const context = vm.createContext({
    document: { querySelector: (selector) => nodes[selector], createElement: node },
    elements: { registerForm: form, authError, authAppealButton: appeal },
    FormData: class {
      getAll(name) { return name === "interests" ? inputs().filter((input) => input.checked).map((input) => input.value) : []; }
      get(name) { return { display_name: "新同学", student_id: "CUSTOM20260001", password: "password123", password_confirmation: "password123", campus: "南京", gender: "undisclosed", preferred_locations: "体育馆" }[name] || null; }
    },
    hideInlineError(element) { element.textContent = ""; element.classList.add("is-hidden"); },
    showInlineError(element, message) { element.textContent = message; element.classList.remove("is-hidden"); },
    validatePasswordConfirmation: () => true,
    setButtonLoading(element, busy) { element.disabled = busy; },
    refreshHumanChallenge() {}, showRegisterStep() {}, humanChallengePayload: () => ({}),
    splitList: (value) => value.split("，"),
    async api() { apiCalls += 1; throw new Error("暂时无法注册"); },
  });
  vm.runInContext(registrationSource, context);
  return { context, form, grid, draft, error, count, inputs, button, apiCalls: () => apiCalls };
}

test("custom activities use the original checkbox array, support deselection and removal", () => {
  const env = setup();
  env.draft.value = " 飞盘 ";
  assert.equal(env.context.addRegistrationInterest(), true);
  assert.equal(env.draft.value, "");
  const custom = env.inputs().at(-1);
  assert.equal(custom.value, "飞盘");
  assert.equal(custom.checked, true);
  assert.deepEqual(Array.from(env.context.registrationPayload().interests), ["飞盘"]);
  custom.checked = false;
  assert.deepEqual(Array.from(env.context.registrationPayload().interests), []);
  custom.checked = true;
  env.grid.children.at(-1).children.at(-1).listeners.click();
  assert.equal(env.inputs().length, 6);
  assert.equal(env.count.textContent, "已选择 0 项，最多 30 项。");
  assert.equal(env.draft.focused, true);
});

test("empty, duplicate, long and full-list errors preserve drafts and existing selections", () => {
  const env = setup();
  for (const [value, message] of [["  ", "先填写"], [" 羽毛球 ", "已经在上方"], ["自".repeat(51), "最多 50"]]) {
    env.draft.value = value;
    assert.equal(env.context.addRegistrationInterest(), false);
    assert.equal(env.draft.value, value);
    assert.match(env.error.textContent, new RegExp(message));
    assert.equal(env.inputs().length, 6);
  }
  env.draft.value = "🏃".repeat(50);
  assert.equal(env.context.addRegistrationInterest(), true);
  for (let index = 1; index < 30; index++) env.context.appendRegistrationInterest(`活动${index}`);
  env.draft.value = "第31项草稿";
  assert.equal(env.context.addRegistrationInterest(), false);
  assert.equal(env.draft.value, "第31项草稿");
  assert.equal(env.inputs().filter((input) => input.checked).length, 30);
});

test("normalization remains strip and exact deduplication; activity text cannot create HTML", () => {
  const env = setup();
  for (const value of ["FPV", "fpv", '"><svg onload=alert(1)>']) {
    env.draft.value = value;
    assert.equal(env.context.addRegistrationInterest(), true);
  }
  assert.deepEqual(Array.from(env.context.registrationPayload().interests), ["FPV", "fpv", '"><svg onload=alert(1)>']);
  const label = env.grid.children.at(-1).children[0];
  assert.equal(label.children[1].tag, "span");
  assert.equal(label.children[1].textContent, '"><svg onload=alert(1)>');
});

test("Enter adds without submitting, IME Enter is left alone and form reset clears custom rows", () => {
  const env = setup();
  env.context.initializeRegistrationInterests();
  let prevented = false;
  env.draft.value = "桌游";
  env.draft.listeners.keydown({ key: "Enter", isComposing: true, preventDefault() { prevented = true; } });
  assert.equal(env.inputs().length, 6);
  assert.equal(prevented, false);
  env.draft.listeners.keydown({ key: "Enter", isComposing: false, preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(env.inputs().length, 7);
  env.form.reset();
  assert.equal(env.inputs().length, 6);
  assert.equal(env.count.textContent, "已选择 0 项，最多 30 项。");
});

test("server safety cleanup replaces the submitted custom choice rather than retaining unsafe text", () => {
  const env = setup();
  env.context.appendRegistrationInterest("待清理的自定义词");
  env.context.appendRegistrationInterest("暂未选中的草稿活动");
  env.inputs().at(-1).checked = false;
  env.context.applyRegistrationInterests(["跑步", "清理后的活动", "清理后的活动", ""]);
  assert.deepEqual(Array.from(env.context.registrationPayload().interests), ["跑步", "清理后的活动"]);
  assert.equal(env.inputs().some((input) => input.value === "待清理的自定义词"), false);
  assert.equal(env.inputs().some((input) => input.value === "暂未选中的草稿活动" && !input.checked), true);
  assert.match(source, /form === elements\.registerForm && name === "interests" && Array\.isArray\(value\)[\s\S]*?applyRegistrationInterests\(value\)/);
});

test("finishing registration includes an unadded valid draft and failed API preserves the activity", async () => {
  const env = setup();
  env.draft.value = "滑板";
  await env.context.handleRegister({ preventDefault() {}, submitter: env.button });
  assert.equal(env.apiCalls(), 1);
  assert.deepEqual(Array.from(env.context.registrationPayload().interests), ["滑板"]);
  assert.equal(env.button.disabled, false);
  env.draft.value = "自".repeat(51);
  await env.context.handleRegister({ preventDefault() {}, submitter: env.button });
  assert.equal(env.apiCalls(), 1);
  assert.equal(env.draft.value.length, 51);
});

test("registration stays accessible and main action changes only its exact label", () => {
  assert.match(html, /for="register-custom-interest"/);
  assert.match(html, /id="register-add-interest"[^>]*type="button"/);
  assert.match(html, /id="register-interest-error"[^>]*role="alert"/);
  assert.match(html, /id="match-submit"[^>]*type="submit">\s*<span[^>]*>✦<\/span> 让 Agent 帮忙找搭子 \/ 创建活动/);
});
