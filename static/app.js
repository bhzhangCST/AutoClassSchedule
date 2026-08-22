"use strict";

let appData = { state: null, meta: null };
let currentView = "overview";
let currentAssignmentClassId = null;
let currentAssignmentGrade = 1;
const assignmentDrafts = new Map();
let scheduleMode = "class";
let currentScheduleGrade = 1;
let pendingConfirm = null;

const pageNames = {
  overview: ["工作台", "工作概览"],
  settings: ["基础配置", "学校与班级"],
  teachers: ["资源配置", "教师与任课"],
  schedule: ["课表编排", "生成与查看课表"],
  rules: ["规则中心", "排课规则"],
};

const gradeNames = { 1: "一年级", 2: "二年级", 3: "三年级", 4: "四年级", 5: "五年级", 6: "六年级" };

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function api(path, options = {}) {
  const requestOptions = { credentials: "same-origin", ...options };
  if (requestOptions.body && !(requestOptions.body instanceof FormData)) {
    requestOptions.headers = { "Content-Type": "application/json", ...(requestOptions.headers || {}) };
  }
  const response = await fetch(path, requestOptions);
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await response.json() : await response.text();
  if (response.status === 401 && path !== "/api/auth/login") {
    showLogin();
    throw new Error("登录已失效，请重新登录");
  }
  if (!response.ok) {
    throw new Error(data?.detail || data || "请求失败");
  }
  return data;
}

function showLogin() {
  document.getElementById("login-screen").classList.remove("hidden");
  document.getElementById("app-shell").classList.add("hidden");
}

function showApp(username = "admin") {
  document.getElementById("login-screen").classList.add("hidden");
  document.getElementById("app-shell").classList.remove("hidden");
  document.getElementById("account-name").textContent = username;
}

function showToast(message, type = "success") {
  const container = document.getElementById("toast-container");
  const toast = document.createElement("div");
  toast.className = `toast ${type === "error" ? "error" : ""}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 3500);
}

function setLoading(button, loading, label) {
  if (!button) return;
  if (loading) {
    button.dataset.originalLabel = button.textContent;
    button.textContent = label || "处理中…";
    button.disabled = true;
  } else {
    button.textContent = button.dataset.originalLabel || button.textContent;
    button.disabled = false;
  }
}

async function loadState() {
  const data = await api("/api/state");
  appData = data;
  renderAll();
}

function applyStateResponse(data) {
  appData = { state: data.state, meta: data.meta };
  renderAll();
}

function switchView(view) {
  if (currentView === "teachers") captureCurrentAssignmentDraft();
  currentView = view;
  document.querySelectorAll("[data-view-panel]").forEach((panel) => panel.classList.toggle("active", panel.dataset.viewPanel === view));
  document.querySelectorAll(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
  document.getElementById("page-eyebrow").textContent = pageNames[view][0];
  document.getElementById("page-title").textContent = pageNames[view][1];
  document.getElementById("sidebar").classList.remove("open");
  document.getElementById("menu-button").setAttribute("aria-expanded", "false");
  if (view === "schedule") renderSchedule();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function subjectMap() {
  return Object.fromEntries(appData.meta.subjects.map((subject) => [subject.id, subject]));
}

function teacherMap() {
  return Object.fromEntries(appData.state.teachers.map((teacher) => [teacher.id, teacher]));
}

function configuredTeacherLoads() {
  const loads = Object.fromEntries(appData.state.teachers.map((teacher) => [teacher.id, 0]));
  for (const classItem of appData.state.classes) {
    const curriculum = appData.meta.curriculum[String(classItem.grade)];
    const assignments = appData.state.assignments[classItem.id] || {};
    for (const [subjectId, hours] of Object.entries(curriculum)) {
      const teacherId = assignments[subjectId];
      if (teacherId && teacherId in loads) loads[teacherId] += hours;
    }
  }
  return loads;
}

function assignmentStats() {
  let total = 0;
  let assigned = 0;
  for (const classItem of appData.state.classes) {
    const curriculum = appData.meta.curriculum[String(classItem.grade)];
    const assignments = appData.state.assignments[classItem.id] || {};
    for (const subjectId of Object.keys(curriculum)) {
      total += 1;
      if (assignments[subjectId]) assigned += 1;
    }
  }
  return { total, assigned };
}

function renderAll() {
  if (!appData.state || !appData.meta) return;
  document.getElementById("hero-school-name").textContent = appData.state.school_name;
  const storageStatus = document.getElementById("storage-status");
  if (storageStatus) {
    storageStatus.innerHTML = `<span class="status-dot"></span>${appData.meta.storage_backend === "upstash" ? "数据自动保存到云端" : "数据自动保存在本机"}`;
  }
  renderOverview();
  renderSettings();
  renderTeachers();
  renderAssignments();
  renderSchedule();
  renderRules();
}

function renderOverview() {
  const { state } = appData;
  const assignment = assignmentStats();
  const schedule = state.schedule;
  const metrics = [
    ["6", "覆盖年级", "一年级至六年级"],
    [String(state.classes.length), "当前班级", "按学校设置统计"],
    [String(state.teachers.length), "已录入教师", "用于任课与冲突检查"],
    [`${assignment.assigned}/${assignment.total}`, "任课关系", "已配置 / 应配置"],
  ];
  document.getElementById("overview-metrics").innerHTML = metrics.map(([value, label, note]) => `
    <article class="metric-card"><span class="metric-label">${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><span>${escapeHtml(note)}</span></article>
  `).join("");

  const checks = [
    [state.classes.length > 0, "班级结构", `已配置 ${state.classes.length} 个班`],
    [state.teachers.length > 0, "教师资料", state.teachers.length ? `已录入 ${state.teachers.length} 名教师` : "可先无教师生成基础课表"],
    [assignment.assigned === assignment.total, "任课关系", assignment.assigned === assignment.total ? "全部课程已分配教师" : `还有 ${assignment.total - assignment.assigned} 项未分配`],
    [Boolean(schedule?.success), "可用课表", schedule?.success ? "已生成并保存" : "等待生成"],
  ];
  document.getElementById("readiness-list").innerHTML = checks.map(([done, title, detail]) => `
    <div class="check-item ${done ? "done" : ""}"><span class="check-symbol">${done ? "✓" : "·"}</span><div><strong>${title}</strong><span>${detail}</span></div><span>${done ? "完成" : "待处理"}</span></div>
  `).join("");
  const complete = checks.filter((item) => item[0]).length;
  const readiness = document.getElementById("readiness-status");
  readiness.textContent = `${complete} / ${checks.length}`;
  readiness.classList.toggle("good", complete === checks.length);

  const latest = document.getElementById("latest-result");
  if (!schedule?.success) {
    latest.innerHTML = `<div class="empty-icon">表</div><h4>尚未生成课表</h4><p>完成必要配置后，进入“生成课表”开始编排。</p>`;
  } else {
    const rate = Math.round((schedule.quality?.morning_third_rate || 0) * 100);
    const time = new Date(schedule.generated_at).toLocaleString("zh-CN", { hour12: false });
    latest.innerHTML = `<div class="result-summary"><strong>${rate}%</strong><p>三至六年级上午第3节核心课比例</p><p class="muted">生成于 ${escapeHtml(time)} · 第${schedule.attempt}次尝试成功</p></div>
      <ul class="mini-warning-list">${(schedule.warnings || []).slice(0, 2).map((warning) => `<li>${escapeHtml(warning)}</li>`).join("") || "<li>未发现生成后警告</li>"}</ul>`;
  }
}

function renderSettings() {
  document.getElementById("school-name").value = appData.state.school_name;
  document.getElementById("grade-count-grid").innerHTML = Array.from({ length: 6 }, (_, index) => {
    const grade = index + 1;
    return `<div class="grade-count-field"><label for="grade-count-${grade}">${gradeNames[grade]}</label><input id="grade-count-${grade}" type="number" min="1" max="20" value="${appData.state.class_counts[String(grade)] || 6}" required></div>`;
  }).join("");
}

function renderTeachers(filter = document.getElementById("teacher-search")?.value || "") {
  const subjects = subjectMap();
  const classes = Object.fromEntries(appData.state.classes.map((item) => [item.id, item]));
  const loads = configuredTeacherLoads();
  const normalizedFilter = filter.trim().toLowerCase();
  const teachers = appData.state.teachers.filter((teacher) => teacher.name.toLowerCase().includes(normalizedFilter));
  document.getElementById("teacher-count-label").textContent = `共 ${appData.state.teachers.length} 名教师`;
  const body = document.getElementById("teacher-table-body");
  body.innerHTML = teachers.map((teacher) => `
    <tr>
      <td><span class="teacher-name">${escapeHtml(teacher.name)}</span></td>
      <td>${teacher.homeroom_class_id ? `<span class="status-pill good">${escapeHtml(classes[teacher.homeroom_class_id]?.name || "班级已移除")}</span>` : '<span class="muted">—</span>'}</td>
      <td><div class="subject-tags">${teacher.subject_ids.length ? teacher.subject_ids.map((id) => `<span class="subject-tag">${escapeHtml(subjects[id]?.short || id)}</span>`).join("") : '<span class="muted">未限定课程</span>'}</div></td>
      <td>${loads[teacher.id] || 0} 节</td>
      <td>${teacher.min_weekly_lessons ? `${teacher.min_weekly_lessons} 节` : "不检查"}</td>
      <td class="action-column"><div class="table-actions"><button class="tiny-button" data-edit-teacher="${teacher.id}">编辑</button><button class="tiny-button danger" data-delete-teacher="${teacher.id}">删除</button></div></td>
    </tr>
  `).join("");
  document.getElementById("teacher-empty").classList.toggle("hidden", appData.state.teachers.length > 0);
  body.closest(".responsive-table").classList.toggle("hidden", appData.state.teachers.length === 0);
  body.querySelectorAll("[data-edit-teacher]").forEach((button) => button.addEventListener("click", () => openTeacherDialog(button.dataset.editTeacher)));
  body.querySelectorAll("[data-delete-teacher]").forEach((button) => button.addEventListener("click", () => deleteTeacher(button.dataset.deleteTeacher)));
}

function renderTeacherSubjectOptions(selected = []) {
  document.getElementById("teacher-subject-options").innerHTML = appData.meta.subjects.map((subject) => `
    <label class="subject-option"><input type="checkbox" value="${subject.id}" ${selected.includes(subject.id) ? "checked" : ""}><span>${escapeHtml(subject.short)}</span></label>
  `).join("");
}

function renderTeacherHomeroomOptions(teacher = null) {
  const select = document.getElementById("teacher-homeroom-class");
  const occupied = new Map(
    appData.state.teachers
      .filter((item) => item.homeroom_class_id && item.id !== teacher?.id)
      .map((item) => [item.homeroom_class_id, item.name]),
  );
  select.innerHTML = `<option value="">不是班主任</option>${appData.state.classes.map((classItem) => {
    const owner = occupied.get(classItem.id);
    return `<option value="${classItem.id}" ${owner ? "disabled" : ""}>${escapeHtml(classItem.name)}${owner ? `（${escapeHtml(owner)}）` : ""}</option>`;
  }).join("")}`;
  select.value = teacher?.homeroom_class_id || "";
}

function openTeacherDialog(teacherId = null) {
  const teacher = teacherId ? appData.state.teachers.find((item) => item.id === teacherId) : null;
  document.getElementById("teacher-dialog-title").textContent = teacher ? "编辑教师" : "添加教师";
  document.getElementById("teacher-id").value = teacher?.id || "";
  document.getElementById("teacher-name").value = teacher?.name || "";
  document.getElementById("teacher-min-lessons").value = teacher?.min_weekly_lessons ?? 12;
  renderTeacherHomeroomOptions(teacher);
  renderTeacherSubjectOptions(teacher?.subject_ids || []);
  document.getElementById("teacher-dialog").showModal();
  document.getElementById("teacher-name").focus();
}

async function deleteTeacher(teacherId) {
  const teacher = appData.state.teachers.find((item) => item.id === teacherId);
  const confirmed = await confirmDialog("删除教师", `删除“${teacher?.name || "该教师"}”后，相关班级任课关系将变为未分配。`);
  if (!confirmed) return;
  try {
    const data = await api(`/api/teachers/${teacherId}`, { method: "DELETE" });
    applyStateResponse(data);
    showToast("教师已删除");
  } catch (error) {
    showToast(error.message, "error");
  }
}

function assignmentNamesFromState(classItem) {
  const assignments = appData.state.assignments[classItem.id] || {};
  const teachers = teacherMap();
  const curriculum = appData.meta.curriculum[String(classItem.grade)];
  return Object.fromEntries(
    Object.keys(curriculum).map((subjectId) => [subjectId, teachers[assignments[subjectId]]?.name || ""]),
  );
}

function assignmentValuesMatch(left, right, subjectIds) {
  return subjectIds.every((subjectId) => (left[subjectId] || "") === (right[subjectId] || ""));
}

function updateAssignmentDraftStatus() {
  const count = assignmentDrafts.size;
  const status = document.getElementById("assignment-draft-status");
  const button = document.getElementById("save-assignments-button");
  if (status) status.textContent = count ? `${count} 个班有未保存修改` : "暂无未保存修改";
  if (button) button.textContent = count ? `保存全部任课配置（${count}个班）` : "保存任课配置";
}

function captureCurrentAssignmentDraft() {
  if (!appData.state || !currentAssignmentClassId) return;
  const inputs = [...document.querySelectorAll("[data-assignment-subject]")];
  if (!inputs.length) return;
  const classItem = appData.state.classes.find((item) => item.id === currentAssignmentClassId);
  if (!classItem) return;
  const values = Object.fromEntries(inputs.map((input) => [input.dataset.assignmentSubject, input.value.trim()]));
  const baseValues = assignmentNamesFromState(classItem);
  const subjectIds = Object.keys(appData.meta.curriculum[String(classItem.grade)]);
  if (assignmentValuesMatch(values, baseValues, subjectIds)) {
    assignmentDrafts.delete(classItem.id);
  } else {
    assignmentDrafts.set(classItem.id, values);
  }
  updateAssignmentDraftStatus();
}

function renderAssignments() {
  const classes = appData.state.classes;
  const classIds = new Set(classes.map((item) => item.id));
  for (const classId of assignmentDrafts.keys()) {
    if (!classIds.has(classId)) assignmentDrafts.delete(classId);
  }
  if (!classes.length) {
    document.getElementById("assignment-grade-select").innerHTML = "";
    document.getElementById("assignment-class-select").innerHTML = "";
    document.getElementById("assignment-grid").innerHTML = "";
    updateAssignmentDraftStatus();
    return;
  }
  const grades = [...new Set(classes.map((item) => Number(item.grade)))].sort((left, right) => left - right);
  const currentClass = classes.find((item) => item.id === currentAssignmentClassId);
  if (currentClass) currentAssignmentGrade = Number(currentClass.grade);
  if (!grades.includes(currentAssignmentGrade)) currentAssignmentGrade = grades[0];

  const gradeSelect = document.getElementById("assignment-grade-select");
  gradeSelect.innerHTML = grades.map((grade) => `<option value="${grade}">${gradeNames[grade]}</option>`).join("");
  gradeSelect.value = String(currentAssignmentGrade);

  const gradeClasses = classes.filter((item) => Number(item.grade) === currentAssignmentGrade);
  if (!gradeClasses.some((item) => item.id === currentAssignmentClassId)) currentAssignmentClassId = gradeClasses[0]?.id || null;
  const classSelect = document.getElementById("assignment-class-select");
  classSelect.innerHTML = gradeClasses.map((item) => `<option value="${item.id}">${escapeHtml(item.name)}${assignmentDrafts.has(item.id) ? "（未保存）" : ""}</option>`).join("");
  classSelect.value = currentAssignmentClassId || "";

  const classItem = classes.find((item) => item.id === currentAssignmentClassId);
  if (!classItem) return;
  const curriculum = appData.meta.curriculum[String(classItem.grade)];
  const assignments = appData.state.assignments[classItem.id] || {};
  const displayAssignments = { ...(assignmentDrafts.get(classItem.id) || assignmentNamesFromState(classItem)) };
  const teachers = appData.state.teachers;
  const teachersById = teacherMap();
  displayAssignments.reading = displayAssignments.chinese || "";
  displayAssignments.meeting = teachersById[assignments.meeting]?.name || "";
  const subjects = subjectMap();
  const assignedCount = Object.keys(curriculum).filter((subjectId) => displayAssignments[subjectId]).length;
  document.getElementById("assignment-grade-label").textContent = gradeNames[classItem.grade];
  document.getElementById("assignment-class-name").textContent = classItem.name;
  document.getElementById("assignment-progress").textContent = `${assignedCount} / ${Object.keys(curriculum).length}`;
  document.getElementById("assignment-progress").classList.toggle("good", assignedCount === Object.keys(curriculum).length);
  document.getElementById("assignment-grid").innerHTML = Object.entries(curriculum).map(([subjectId, hours]) => {
    const subject = subjects[subjectId];
    const assignedTeacher = assignments[subjectId] || "";
    const eligible = teachers.filter((teacher) => teacher.subject_ids.length === 0 || teacher.subject_ids.includes(subjectId) || teacher.id === assignedTeacher);
    const assignedName = displayAssignments[subjectId] || "";
    const listId = `teacher-options-${subjectId}`;
    const lockedReason = subjectId === "reading" ? "随本班语文教师自动同步" : (subjectId === "meeting" ? "随本班班主任自动同步" : "");
    return `<div class="assignment-row ${lockedReason ? "locked-assignment" : ""}"><div class="assignment-subject"><span class="subject-color" style="background:${subject.color}"></span><div><strong>${escapeHtml(subject.name)}</strong><span>每周 ${hours} 节</span></div></div><div class="teacher-search-field"><input type="search" list="${listId}" value="${escapeHtml(assignedName)}" placeholder="${lockedReason ? "自动设置" : "搜索教师姓名"}" aria-label="${escapeHtml(subject.name)}任课教师" data-assignment-subject="${subjectId}" data-eligible-teachers="${eligible.map((teacher) => teacher.id).join(",")}" ${lockedReason ? "disabled" : ""}><datalist id="${listId}">${eligible.map((teacher) => `<option value="${escapeHtml(teacher.name)}"></option>`).join("")}</datalist><span>${lockedReason || `${eligible.length} 名可选教师`}</span></div></div>`;
  }).join("");
  updateAssignmentDraftStatus();
}

function renderGenerationMessage(result = appData.state.schedule) {
  const container = document.getElementById("generation-message");
  if (!result) {
    container.innerHTML = "";
    return;
  }
  if (!result.success) {
    container.innerHTML = `<div class="message-panel error"><h4>本次未能生成课表</h4><ul>${(result.errors || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>`;
    return;
  }
  const warnings = result.warnings || [];
  const rate = Math.round((result.quality?.morning_third_rate || 0) * 100);
  container.innerHTML = `<div class="message-panel ${warnings.length ? "warning" : "success"}"><h4>课表生成成功 · 上午第3节核心课比例 ${rate}%</h4>${warnings.length ? `<details class="warning-details"><summary>查看 ${warnings.length} 条生成说明</summary><ul>${warnings.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></details>` : "<span>所有已配置的硬性约束均已满足，未发现生成后警告。</span>"}</div>`;
}

function renderSchedule() {
  if (!appData.state) return;
  const schedule = appData.state.schedule;
  const exportButton = document.getElementById("export-button");
  const teacherExportButton = document.getElementById("teacher-export-button");
  exportButton.classList.toggle("disabled", !schedule?.success);
  teacherExportButton.classList.toggle("disabled", !schedule?.success);
  teacherExportButton.disabled = !schedule?.success;
  renderExportOptions();
  renderGenerationMessage(schedule);
  document.querySelectorAll("[data-schedule-mode]").forEach((button) => button.classList.toggle("active", button.dataset.scheduleMode === scheduleMode));

  const gradeFilter = document.getElementById("schedule-grade-filter");
  const gradeSelect = document.getElementById("schedule-grade-select");
  const grades = [...new Set(appData.state.classes.map((item) => Number(item.grade)))].sort((left, right) => left - right);
  if (!grades.includes(currentScheduleGrade)) currentScheduleGrade = grades[0] || 1;
  gradeSelect.innerHTML = grades.map((grade) => `<option value="${grade}">${gradeNames[grade]}</option>`).join("");
  gradeSelect.value = String(currentScheduleGrade);
  gradeFilter.classList.toggle("hidden", scheduleMode !== "class");
  document.getElementById("schedule-entity-label").textContent = scheduleMode === "class" ? "班级" : "教师";
  document.getElementById("print-button").textContent = scheduleMode === "class" ? "打印当前班级课表" : "打印当前教师课表";

  const selector = document.getElementById("schedule-entity-select");
  const entities = scheduleMode === "class"
    ? appData.state.classes.filter((item) => Number(item.grade) === currentScheduleGrade)
    : appData.state.teachers;
  const previous = selector.value;
  selector.innerHTML = entities.length ? entities.map((item) => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join("") : '<option value="">暂无可选数据</option>';
  if (entities.some((item) => item.id === previous)) selector.value = previous;
  renderTimetable();
}

function renderExportOptions() {
  const select = document.getElementById("export-grade-select");
  const previous = select.value;
  const grades = [...new Set(appData.state.classes.map((item) => Number(item.grade)))].sort((left, right) => left - right);
  select.innerHTML = `<option value="">全部年级</option>${grades.map((grade) => `<option value="${grade}">${gradeNames[grade]}</option>`).join("")}`;
  if (["", ...grades.map(String)].includes(previous)) select.value = previous;
  updateExportLink();
}

function updateExportLink() {
  const grade = document.getElementById("export-grade-select").value;
  document.getElementById("export-button").href = `/api/export/schedule.pdf${grade ? `?grade=${grade}` : ""}`;
}

function renderTeacherExportFilters() {
  const grades = [...new Set(appData.state.classes.map((item) => Number(item.grade)))].sort((left, right) => left - right);
  const gradeSelect = document.getElementById("teacher-export-grade");
  const previousGrade = Number(gradeSelect.value) || currentScheduleGrade;
  gradeSelect.innerHTML = grades.map((grade) => `<option value="${grade}">${gradeNames[grade]}</option>`).join("");
  gradeSelect.value = String(grades.includes(previousGrade) ? previousGrade : grades[0]);
  const teacherInput = document.getElementById("teacher-export-teacher");
  const availableTeachers = scheduledTeachers();
  document.getElementById("teacher-export-teacher-options").innerHTML = availableTeachers
    .map((teacher) => `<option value="${escapeHtml(teacher.name)}"></option>`)
    .join("");
  if (!availableTeachers.some((teacher) => teacher.name === teacherInput.value)) teacherInput.value = "";
  updateTeacherExportFields();
}

function scheduledTeachers() {
  const schedule = appData.state.schedule;
  if (!schedule?.success) return [];
  const teacherIds = new Set(
    Object.values(schedule.lessons || {}).flatMap((classLessons) => (
      Object.values(classLessons).map((lesson) => lesson?.teacher_id).filter(Boolean)
    )),
  );
  return appData.state.teachers.filter((teacher) => teacherIds.has(teacher.id));
}

function updateTeacherExportFields() {
  const scope = document.getElementById("teacher-export-scope").value;
  document.getElementById("teacher-export-grade-field").classList.toggle("hidden", scope !== "grade");
  document.getElementById("teacher-export-teacher-field").classList.toggle("hidden", scope !== "teacher");
  document.getElementById("submit-teacher-export").textContent = scope === "teacher" ? "下载教师 PDF" : "下载 PDF 压缩包";
}

function lessonForTeacher(schedule, teacherId, slot) {
  for (const classItem of appData.state.classes) {
    const lesson = schedule.lessons[classItem.id]?.[slot];
    if (lesson?.teacher_id === teacherId) return { ...lesson, class_name: classItem.name };
  }
  return null;
}

function buildPrintTimetable() {
  const schedule = appData.state.schedule;
  if (!schedule?.success) {
    showToast("请先生成课表", "error");
    return false;
  }
  const entityId = document.getElementById("schedule-entity-select").value;
  const classItem = scheduleMode === "class" ? appData.state.classes.find((item) => item.id === entityId) : null;
  const teacher = scheduleMode === "teacher" ? appData.state.teachers.find((item) => item.id === entityId) : null;
  if (!classItem && !teacher) {
    showToast("请选择需要打印的班级或教师", "error");
    return false;
  }

  const subjects = subjectMap();
  const teachers = teacherMap();
  document.getElementById("print-title").textContent = scheduleMode === "class"
    ? `${appData.state.school_name}${classItem.name}课程表`
    : `${appData.state.school_name}${teacher.name}课程表`;
  const printNote = document.getElementById("print-note");
  printNote.textContent = scheduleMode === "class" ? "" : `教师：${teacher.name}`;
  printNote.classList.toggle("hidden", scheduleMode === "class");

  let html = `<thead><tr><th class="print-session-column">时段</th><th class="print-period-column">节次</th>${appData.meta.days.map((day) => `<th>${escapeHtml(day.name)}</th>`).join("")}</tr></thead><tbody>`;
  for (const [index, period] of appData.meta.periods.entries()) {
    html += "<tr>";
    if (index === 0) html += '<th rowspan="4" class="print-session">上午</th>';
    if (index === 4) html += '<th rowspan="3" class="print-session">下午</th>';
    html += `<th class="print-period">第${period.order}节</th>`;
    for (const day of appData.meta.days) {
      const slot = `${day.id}-${period.id}`;
      const unavailable = scheduleMode === "class" && Number(classItem.grade) <= 2 && period.id === "am4";
      if (unavailable) {
        html += '<td class="print-empty">—</td>';
        continue;
      }
      const lesson = scheduleMode === "class" ? schedule.lessons[entityId]?.[slot] : lessonForTeacher(schedule, entityId, slot);
      if (!lesson) {
        html += "<td></td>";
        continue;
      }
      const subject = subjects[lesson.subject_id];
      const primary = scheduleMode === "class" ? subject.name : lesson.class_name;
      const secondary = scheduleMode === "class" ? (teachers[lesson.teacher_id]?.name || "") : subject.name;
      html += `<td><strong>${escapeHtml(primary)}</strong>${secondary ? `<span>${escapeHtml(secondary)}</span>` : ""}</td>`;
    }
    html += "</tr>";
  }
  document.getElementById("print-timetable").innerHTML = `${html}</tbody>`;
  return true;
}

function printCurrentSchedule() {
  if (!buildPrintTimetable()) return;
  document.getElementById("print-sheet").setAttribute("aria-hidden", "false");
  requestAnimationFrame(() => window.print());
}

function renderTimetable() {
  const schedule = appData.state.schedule;
  const table = document.getElementById("timetable");
  const title = document.getElementById("timetable-title");
  const subtitle = document.getElementById("timetable-subtitle");
  const qualityBadge = document.getElementById("quality-badge");
  if (!schedule?.success) {
    title.textContent = "尚未生成";
    subtitle.textContent = "课表预览";
    qualityBadge.innerHTML = "";
    table.innerHTML = `<tbody><tr><td class="empty-state"><div class="empty-icon">表</div><h4>等待生成课表</h4><p>点击右上角“生成新课表”开始。</p></td></tr></tbody>`;
    return;
  }

  const entityId = document.getElementById("schedule-entity-select").value;
  const subjects = subjectMap();
  const teachers = teacherMap();
  const classItem = scheduleMode === "class" ? appData.state.classes.find((item) => item.id === entityId) : null;
  const teacher = scheduleMode === "teacher" ? appData.state.teachers.find((item) => item.id === entityId) : null;
  title.textContent = classItem?.name || teacher?.name || "请选择";
  subtitle.textContent = scheduleMode === "class" ? "班级周课程表" : "教师周课程表";
  const rate = Math.round((schedule.quality?.morning_third_rate || 0) * 100);
  qualityBadge.innerHTML = `<span class="quality-pill">核心课优先率 ${rate}%</span>`;

  let html = `<thead><tr><th class="period-cell">节次</th>${appData.meta.days.map((day) => `<th>${day.name}</th>`).join("")}</tr></thead><tbody>`;
  for (const period of appData.meta.periods) {
    html += `<tr><td class="period-cell">${period.name}</td>`;
    for (const day of appData.meta.days) {
      const slot = `${day.id}-${period.id}`;
      const unavailable = scheduleMode === "class" && classItem?.grade <= 2 && period.id === "am4";
      if (unavailable) {
        html += '<td class="unavailable-cell">—</td>';
        continue;
      }
      const lesson = scheduleMode === "class" ? schedule.lessons[entityId]?.[slot] : lessonForTeacher(schedule, entityId, slot);
      if (!lesson) {
        html += "<td></td>";
        continue;
      }
      const subject = subjects[lesson.subject_id];
      const secondary = scheduleMode === "class" ? (teachers[lesson.teacher_id]?.name || "未分配教师") : lesson.class_name;
      html += `<td><div class="lesson-card" style="--subject-color:${subject.color}"><strong>${escapeHtml(subject.name)}</strong><span>${escapeHtml(secondary)}</span></div></td>`;
    }
    html += "</tr>";
  }
  table.innerHTML = `${html}</tbody>`;
}

function renderRules() {
  document.getElementById("rule-grid").innerHTML = appData.meta.rules.map((rule, index) => `
    <article class="rule-card"><div class="rule-card-top"><span class="rule-number">${String(index + 1).padStart(2, "0")}</span><span class="rule-level ${rule.level}">${rule.level === "hard" ? "硬性约束" : "柔性目标"}</span></div><h4>${escapeHtml(rule.title)}</h4><p>${escapeHtml(rule.detail)}</p></article>
  `).join("");
  const subjects = appData.meta.subjects;
  const header = `<thead><tr><th>年级</th>${subjects.map((subject) => `<th>${escapeHtml(subject.short)}</th>`).join("")}<th>总课时</th></tr></thead>`;
  const body = Array.from({ length: 6 }, (_, index) => {
    const grade = index + 1;
    const curriculum = appData.meta.curriculum[String(grade)];
    const total = Object.values(curriculum).reduce((sum, value) => sum + value, 0);
    return `<tr><td>${gradeNames[grade]}</td>${subjects.map((subject) => `<td>${curriculum[subject.id] || "—"}</td>`).join("")}<td><strong>${total}</strong></td></tr>`;
  }).join("");
  document.getElementById("curriculum-table").innerHTML = `${header}<tbody>${body}</tbody>`;
}

function confirmDialog(title, message) {
  const dialog = document.getElementById("confirm-dialog");
  document.getElementById("confirm-title").textContent = title;
  document.getElementById("confirm-message").textContent = message;
  dialog.returnValue = "cancel";
  dialog.showModal();
  return new Promise((resolve) => {
    pendingConfirm = resolve;
  });
}

document.getElementById("confirm-dialog").addEventListener("close", (event) => {
  if (pendingConfirm) {
    pendingConfirm(event.target.returnValue === "confirm");
    pendingConfirm = null;
  }
});

document.getElementById("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = document.getElementById("login-button");
  const errorElement = document.getElementById("login-error");
  errorElement.textContent = "";
  setLoading(button, true, "正在登录…");
  try {
    const data = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username: document.getElementById("login-username").value, password: document.getElementById("login-password").value }),
    });
    showApp(data.username);
    await loadState();
  } catch (error) {
    errorElement.textContent = error.message;
  } finally {
    setLoading(button, false);
  }
});

document.getElementById("logout-button").addEventListener("click", async () => {
  await api("/api/auth/logout", { method: "POST" });
  showLogin();
  document.getElementById("login-password").value = "";
});

document.querySelectorAll(".nav-item").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
document.querySelectorAll("[data-jump]").forEach((button) => button.addEventListener("click", (event) => {
  event.preventDefault();
  switchView(button.dataset.jump);
}));
document.getElementById("menu-button").addEventListener("click", (event) => {
  const menu = document.getElementById("sidebar");
  const open = menu.classList.toggle("open");
  event.currentTarget.setAttribute("aria-expanded", String(open));
});

document.getElementById("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  setLoading(button, true, "保存中…");
  try {
    const counts = Object.fromEntries(Array.from({ length: 6 }, (_, index) => [String(index + 1), Number(document.getElementById(`grade-count-${index + 1}`).value)]));
    const data = await api("/api/settings", { method: "PUT", body: JSON.stringify({ school_name: document.getElementById("school-name").value, class_counts: counts }) });
    applyStateResponse(data);
    showToast("学校与班级设置已保存");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setLoading(button, false);
  }
});

document.getElementById("add-teacher-button").addEventListener("click", () => openTeacherDialog());
document.getElementById("import-teachers-button").addEventListener("click", () => {
  document.getElementById("teacher-import-form").reset();
  document.getElementById("teacher-import-result").classList.add("hidden");
  document.getElementById("teacher-import-dialog").showModal();
});
document.getElementById("teacher-search").addEventListener("input", (event) => renderTeachers(event.target.value));
document.getElementById("close-teacher-dialog").addEventListener("click", () => document.getElementById("teacher-dialog").close());
document.getElementById("cancel-teacher-dialog").addEventListener("click", () => document.getElementById("teacher-dialog").close());
document.getElementById("close-teacher-import-dialog").addEventListener("click", () => document.getElementById("teacher-import-dialog").close());
document.getElementById("cancel-teacher-import-dialog").addEventListener("click", () => document.getElementById("teacher-import-dialog").close());
document.getElementById("teacher-import-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = document.getElementById("submit-teacher-import");
  const result = document.getElementById("teacher-import-result");
  const file = document.getElementById("teacher-import-file").files[0];
  if (!file) return;
  const body = new FormData();
  body.append("file", file);
  setLoading(button, true, "正在校验…");
  result.classList.add("hidden");
  try {
    const data = await api("/api/teachers/import", { method: "POST", body });
    applyStateResponse(data);
    document.getElementById("teacher-import-dialog").close();
    const summary = data.import;
    showToast(`已处理 ${summary.total} 名教师：新增 ${summary.created}，更新 ${summary.updated}，任课 ${summary.assigned || 0} 项，班主任 ${summary.homerooms || 0} 项${summary.renamed ? `，同名区分 ${summary.renamed}` : ""}`);
  } catch (error) {
    result.textContent = error.message;
    result.classList.remove("hidden");
  } finally {
    setLoading(button, false);
  }
});
document.getElementById("teacher-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = document.getElementById("save-teacher-button");
  setLoading(button, true, "保存中…");
  const teacherId = document.getElementById("teacher-id").value;
  const payload = {
    name: document.getElementById("teacher-name").value,
    min_weekly_lessons: Number(document.getElementById("teacher-min-lessons").value),
    subject_ids: [...document.querySelectorAll("#teacher-subject-options input:checked")].map((input) => input.value),
    homeroom_class_id: document.getElementById("teacher-homeroom-class").value || null,
  };
  try {
    const data = await api(teacherId ? `/api/teachers/${teacherId}` : "/api/teachers", { method: teacherId ? "PUT" : "POST", body: JSON.stringify(payload) });
    document.getElementById("teacher-dialog").close();
    applyStateResponse(data);
    showToast(teacherId ? "教师资料已更新" : "教师已添加");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setLoading(button, false);
  }
});

document.getElementById("assignment-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  captureCurrentAssignmentDraft();
  if (!assignmentDrafts.size) {
    showToast("没有需要保存的任课修改");
    return;
  }
  setLoading(button, true, "保存中…");
  const teachersByName = new Map(appData.state.teachers.map((teacher) => [teacher.name, teacher]));
  try {
    const classes = {};
    for (const [classId, draft] of assignmentDrafts.entries()) {
      const classItem = appData.state.classes.find((item) => item.id === classId);
      if (!classItem) continue;
      const curriculum = appData.meta.curriculum[String(classItem.grade)];
      const savedAssignments = appData.state.assignments[classId] || {};
      const assignments = {};
      for (const subjectId of Object.keys(curriculum)) {
        const name = (draft[subjectId] || "").trim();
        const teacher = name ? teachersByName.get(name) : null;
        if (name && !teacher) throw new Error(`${classItem.name}：未找到教师“${name}”，请从搜索建议中选择`);
        const fixedTeacher = subjectId === "reading" || subjectId === "meeting";
        const eligible = teacher && (
          teacher.subject_ids.length === 0
          || teacher.subject_ids.includes(subjectId)
          || savedAssignments[subjectId] === teacher.id
        );
        if (teacher && !fixedTeacher && !eligible) throw new Error(`${classItem.name}：${teacher.name}未设置为该科目的可任教师`);
        assignments[subjectId] = teacher?.id || null;
      }
      classes[classId] = assignments;
    }
    const data = await api("/api/assignments", { method: "PUT", body: JSON.stringify({ classes }) });
    const savedCount = data.updated_classes;
    assignmentDrafts.clear();
    applyStateResponse(data);
    showToast(`已保存 ${savedCount} 个班的任课配置`);
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setLoading(button, false);
    updateAssignmentDraftStatus();
  }
});

document.getElementById("assignment-grade-select").addEventListener("change", (event) => {
  captureCurrentAssignmentDraft();
  currentAssignmentGrade = Number(event.target.value);
  currentAssignmentClassId = null;
  renderAssignments();
});
document.getElementById("assignment-class-select").addEventListener("change", (event) => {
  captureCurrentAssignmentDraft();
  currentAssignmentClassId = event.target.value;
  renderAssignments();
});
document.getElementById("assignment-form").addEventListener("input", (event) => {
  if (!event.target.matches("[data-assignment-subject]")) return;
  if (event.target.dataset.assignmentSubject === "chinese") {
    const readingInput = document.querySelector('[data-assignment-subject="reading"]');
    if (readingInput) readingInput.value = event.target.value;
  }
  captureCurrentAssignmentDraft();
});
window.addEventListener("beforeunload", (event) => {
  if (!assignmentDrafts.size) return;
  event.preventDefault();
  event.returnValue = "";
});

document.getElementById("generate-button").addEventListener("click", async () => {
  const button = document.getElementById("generate-button");
  const seedValue = document.getElementById("schedule-seed").value;
  setLoading(button, true, "正在排课…");
  document.getElementById("generation-message").innerHTML = '<div class="message-panel success"><h4>正在搜索可行课表</h4><span>会尝试不同组合，请稍候…</span></div>';
  try {
    const data = await api("/api/schedule/generate", {
      method: "POST",
      body: JSON.stringify({ seed: seedValue ? Number(seedValue) : null, attempts: Number(document.getElementById("schedule-attempts").value || 80) }),
    });
    appData = { state: data.state, meta: data.meta };
    renderAll();
    renderGenerationMessage(data.result);
    showToast(data.result.success ? "课表生成成功" : "未找到可行课表", data.result.success ? "success" : "error");
  } catch (error) {
    showToast(error.message, "error");
    renderGenerationMessage({ success: false, errors: [error.message] });
  } finally {
    setLoading(button, false);
  }
});

document.querySelectorAll("[data-schedule-mode]").forEach((button) => button.addEventListener("click", () => {
  scheduleMode = button.dataset.scheduleMode;
  renderSchedule();
}));
document.getElementById("schedule-grade-select").addEventListener("change", (event) => {
  currentScheduleGrade = Number(event.target.value);
  renderSchedule();
});
document.getElementById("export-grade-select").addEventListener("change", updateExportLink);
document.getElementById("schedule-entity-select").addEventListener("change", renderTimetable);
document.getElementById("print-button").addEventListener("click", printCurrentSchedule);
window.addEventListener("afterprint", () => document.getElementById("print-sheet").setAttribute("aria-hidden", "true"));

document.getElementById("teacher-export-button").addEventListener("click", () => {
  if (!appData.state.schedule?.success) return;
  renderTeacherExportFilters();
  document.getElementById("teacher-export-dialog").showModal();
});
document.getElementById("close-teacher-export-dialog").addEventListener("click", () => document.getElementById("teacher-export-dialog").close());
document.getElementById("cancel-teacher-export-dialog").addEventListener("click", () => document.getElementById("teacher-export-dialog").close());
document.getElementById("teacher-export-scope").addEventListener("change", updateTeacherExportFields);
document.getElementById("teacher-export-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const scope = document.getElementById("teacher-export-scope").value;
  if (scope === "teacher") {
    const teacherName = document.getElementById("teacher-export-teacher").value.trim();
    const teacher = scheduledTeachers().find((item) => item.name === teacherName);
    if (!teacher) {
      showToast("请从搜索建议中选择一名已排课教师", "error");
      return;
    }
    document.getElementById("teacher-export-dialog").close();
    window.location.assign(`/api/export/teacher.pdf?teacher_id=${encodeURIComponent(teacher.id)}`);
    return;
  }
  const params = new URLSearchParams();
  if (scope === "grade") params.set("grade", document.getElementById("teacher-export-grade").value);
  document.getElementById("teacher-export-dialog").close();
  window.location.assign(`/api/export/teachers.zip${params.size ? `?${params}` : ""}`);
});

async function bootstrap() {
  try {
    const me = await api("/api/auth/me");
    if (!me.authenticated) throw new Error("尚未登录");
    showApp(me.username);
    await loadState();
  } catch {
    showLogin();
  }
}

bootstrap();
