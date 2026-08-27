"use strict";

let appData = { state: null, meta: null };
let currentView = "overview";
let currentAssignmentClassId = null;
let currentAssignmentGrade = 1;
const assignmentDrafts = new Map();
let scheduleMode = "class";
let currentScheduleGrade = 1;
let scheduleSwapDraft = null;
let selectedSwapSlot = null;
let pendingConfirm = null;
let teacherAssignmentRanges = [];
let nextTeacherAssignmentRangeId = 1;

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
    const detail = data?.detail ?? data ?? "请求失败";
    const message = Array.isArray(detail) ? detail.join("；") : (typeof detail === "object" ? JSON.stringify(detail) : detail);
    throw new Error(message);
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

function allocationList(value, totalLessons = 0) {
  if (Array.isArray(value)) {
    return value
      .filter((item) => item?.teacher_id && Number(item.lessons) > 0)
      .map((item) => ({ teacher_id: item.teacher_id, lessons: Number(item.lessons) }));
  }
  return value ? [{ teacher_id: value, lessons: Number(totalLessons) }] : [];
}

function allocationTotal(value, totalLessons = 0) {
  return allocationList(value, totalLessons).reduce((sum, item) => sum + item.lessons, 0);
}

function highestLessonAllocation(value) {
  return (value || []).reduce((best, item) => (
    !best || Number(item.lessons) > Number(best.lessons) ? item : best
  ), null);
}

function configuredTeacherLoads() {
  const loads = Object.fromEntries(appData.state.teachers.map((teacher) => [teacher.id, 0]));
  for (const classItem of appData.state.classes) {
    const curriculum = appData.meta.curriculum[String(classItem.grade)];
    const assignments = appData.state.assignments[classItem.id] || {};
    for (const [subjectId, hours] of Object.entries(curriculum)) {
      for (const allocation of allocationList(assignments[subjectId], hours)) {
        if (allocation.teacher_id in loads) loads[allocation.teacher_id] += allocation.lessons;
      }
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
      if (allocationTotal(assignments[subjectId], curriculum[subjectId]) === curriculum[subjectId]) assigned += 1;
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
  const container = document.getElementById("teacher-subject-options");
  if (!container) return;
  container.innerHTML = appData.meta.subjects.map((subject) => `
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

function teacherRangeClasses(grade) {
  return appData.state.classes.filter((item) => Number(item.grade) === Number(grade));
}

function teacherRangeSubjects(grade) {
  const curriculum = appData.meta.curriculum[String(grade)] || {};
  const allowed = new Set(Object.keys(curriculum).filter((subjectId) => !["reading", "meeting"].includes(subjectId)));
  return appData.meta.subjects.filter((subject) => allowed.has(subject.id));
}

function newTeacherRange(values = {}) {
  const grade = Number(values.grade || appData.state.classes[0]?.grade || 1);
  const classes = teacherRangeClasses(grade);
  return {
    id: `teacher-range-${nextTeacherAssignmentRangeId++}`,
    grade,
    start_class_id: values.start_class_id || classes[0]?.id || "",
    end_class_id: values.end_class_id || values.start_class_id || classes[0]?.id || "",
    subject_id: values.subject_id || "",
    lessons: Number(values.lessons || 1),
  };
}

function teacherRangesFromState(teacherId) {
  const ranges = [];
  const grades = [...new Set(appData.state.classes.map((item) => Number(item.grade)))].sort((left, right) => left - right);
  for (const grade of grades) {
    const classes = teacherRangeClasses(grade);
    for (const subject of teacherRangeSubjects(grade)) {
      let rangeStart = null;
      let rangeEnd = null;
      let rangeShare = null;
      for (let index = 0; index <= classes.length; index += 1) {
        const classItem = classes[index];
        const hours = appData.meta.curriculum[String(grade)]?.[subject.id] || 0;
        const share = classItem
          ? allocationList(appData.state.assignments[classItem.id]?.[subject.id], hours).find((item) => item.teacher_id === teacherId)
          : null;
        if (share && rangeShare && share.lessons === rangeShare.lessons) {
          rangeEnd = index;
          continue;
        }
        if (rangeShare) {
          ranges.push(newTeacherRange({
            grade,
            start_class_id: classes[rangeStart].id,
            end_class_id: classes[rangeEnd].id,
            subject_id: subject.id,
            lessons: rangeShare.lessons,
          }));
        }
        rangeStart = share ? index : null;
        rangeEnd = share ? index : null;
        rangeShare = share || null;
      }
    }
  }
  return ranges;
}

function expandedTeacherAssignments() {
  const expanded = {};
  for (const range of teacherAssignmentRanges) {
    if (!range.subject_id) continue;
    const classes = teacherRangeClasses(range.grade);
    const startIndex = classes.findIndex((item) => item.id === range.start_class_id);
    const endIndex = classes.findIndex((item) => item.id === range.end_class_id);
    if (startIndex < 0 || endIndex < startIndex) continue;
    for (const classItem of classes.slice(startIndex, endIndex + 1)) {
      if (!expanded[classItem.id]) expanded[classItem.id] = {};
      const previous = expanded[classItem.id][range.subject_id];
      expanded[classItem.id][range.subject_id] = {
        lessons: Number(range.lessons || 0) + Number(previous?.lessons || 0),
      };
    }
  }
  return expanded;
}

function teacherAssignmentIssues(assignments, teacherId = "") {
  const issues = { over: 0, incomplete: 0, shared: 0 };
  for (const [classId, subjectMap] of Object.entries(assignments)) {
    const classItem = appData.state.classes.find((item) => item.id === classId);
    const curriculum = appData.meta.curriculum[String(classItem?.grade)] || {};
    for (const [subjectId, share] of Object.entries(subjectMap)) {
      const existing = allocationList(appData.state.assignments[classId]?.[subjectId], curriculum[subjectId]);
      const others = existing.filter((item) => item.teacher_id !== teacherId);
      const total = others.reduce((sum, item) => sum + item.lessons, 0) + Number(share.lessons || 0);
      if (others.length) issues.shared += 1;
      if (total > curriculum[subjectId]) issues.over += 1;
      else if (total > 0 && total < curriculum[subjectId]) issues.incomplete += 1;
    }
  }
  return issues;
}

function syncTeacherSubjectsFromRanges() {
  const selectedSubjects = new Set(teacherAssignmentRanges.map((range) => range.subject_id).filter(Boolean));
  document.querySelectorAll("#teacher-subject-options input").forEach((input) => {
    if (selectedSubjects.has(input.value)) input.checked = true;
  });
}

function updateTeacherRangeStatus() {
  const status = document.getElementById("teacher-range-status");
  const assignments = expandedTeacherAssignments();
  const classCount = Object.keys(assignments).length;
  const assignmentCount = Object.values(assignments).reduce((sum, subjectMap) => sum + Object.keys(subjectMap).length, 0);
  const issues = teacherAssignmentIssues(assignments, document.getElementById("teacher-id").value);
  status.classList.toggle("warning", issues.over > 0 || issues.incomplete > 0);
  if (!assignmentCount) {
    status.textContent = "尚未添加任课范围";
  } else if (issues.over) {
    status.textContent = `已选 ${classCount} 个班、${assignmentCount} 项任课；${issues.over} 项课时合计超出标准，无法保存`;
  } else if (issues.incomplete) {
    status.textContent = `已选 ${classCount} 个班、${assignmentCount} 项任课；${issues.incomplete} 项仍有未分配课时`;
  } else if (issues.shared) {
    status.textContent = `已选 ${classCount} 个班、${assignmentCount} 项任课；${issues.shared} 项由多位教师共同承担`;
  } else {
    status.textContent = `已选 ${classCount} 个班、${assignmentCount} 项任课`;
  }
}

function renderTeacherAssignmentRanges() {
  const container = document.getElementById("teacher-range-list");
  if (!teacherAssignmentRanges.length) {
    container.innerHTML = '<div class="teacher-range-empty">尚未配置任课班级。点击下方按钮，可按连续班级范围添加。</div>';
    updateTeacherRangeStatus();
    return;
  }
  const grades = [...new Set(appData.state.classes.map((item) => Number(item.grade)))].sort((left, right) => left - right);
  container.innerHTML = teacherAssignmentRanges.map((range) => {
    const classes = teacherRangeClasses(range.grade);
    const subjects = teacherRangeSubjects(range.grade);
    const startIndex = Math.max(0, classes.findIndex((item) => item.id === range.start_class_id));
    const endIndex = Math.max(startIndex, classes.findIndex((item) => item.id === range.end_class_id));
    range.start_class_id = classes[startIndex]?.id || "";
    range.end_class_id = classes[endIndex]?.id || range.start_class_id;
    if (!subjects.some((subject) => subject.id === range.subject_id)) range.subject_id = "";
    const maxLessons = range.subject_id ? Number(appData.meta.curriculum[String(range.grade)]?.[range.subject_id] || 1) : 35;
    range.lessons = Math.min(Math.max(1, Number(range.lessons || 1)), maxLessons);
    const classOptions = (selectedId, minimumIndex = 0) => classes.map((classItem, index) => `
      <option value="${classItem.id}" ${index < minimumIndex ? "disabled" : ""} ${classItem.id === selectedId ? "selected" : ""}>${escapeHtml(classItem.name)}</option>
    `).join("");
    return `<div class="teacher-range-row" data-teacher-range-id="${range.id}">
      <label class="teacher-range-field"><span>年级</span><select data-teacher-range-field="grade">${grades.map((grade) => `<option value="${grade}" ${grade === range.grade ? "selected" : ""}>${gradeNames[grade]}</option>`).join("")}</select></label>
      <label class="teacher-range-field"><span>起始班级</span><select data-teacher-range-field="start_class_id">${classOptions(range.start_class_id)}</select></label>
      <label class="teacher-range-field"><span>结束班级</span><select data-teacher-range-field="end_class_id">${classOptions(range.end_class_id, startIndex)}</select></label>
      <label class="teacher-range-field teacher-range-subject"><span>任教学科</span><select data-teacher-range-field="subject_id" required><option value="">选择科目</option>${subjects.map((subject) => `<option value="${subject.id}" ${subject.id === range.subject_id ? "selected" : ""}>${escapeHtml(subject.short)}</option>`).join("")}</select></label>
      <label class="teacher-range-field teacher-range-lessons"><span>每班节数</span><input data-teacher-range-field="lessons" type="number" min="1" max="${maxLessons}" value="${range.lessons}" required></label>
      <button class="tiny-button danger teacher-range-remove" type="button" data-remove-teacher-range="${range.id}">移除</button>
    </div>`;
  }).join("");
  syncTeacherSubjectsFromRanges();
  updateTeacherRangeStatus();
}

function openTeacherDialog(teacherId = null) {
  const teacher = teacherId ? appData.state.teachers.find((item) => item.id === teacherId) : null;
  document.getElementById("teacher-dialog-title").textContent = teacher ? "编辑教师" : "添加教师";
  document.getElementById("teacher-id").value = teacher?.id || "";
  document.getElementById("teacher-name").value = teacher?.name || "";
  document.getElementById("teacher-min-lessons").value = teacher?.min_weekly_lessons ?? 12;
  renderTeacherHomeroomOptions(teacher);
  teacherAssignmentRanges = teacher ? teacherRangesFromState(teacher.id) : [];
  renderTeacherAssignmentRanges();
  document.getElementById("teacher-dialog").showModal();
  document.getElementById("teacher-name").focus();
}

async function deleteTeacher(teacherId) {
  const teacher = appData.state.teachers.find((item) => item.id === teacherId);
  const confirmed = await confirmDialog("删除教师", `删除“${teacher?.name || "该教师"}”后，相关班级任课关系将变为未分配。`, "确认删除");
  if (!confirmed) return;
  try {
    const data = await api(`/api/teachers/${teacherId}`, { method: "DELETE" });
    applyStateResponse(data);
    showToast("教师已删除");
  } catch (error) {
    showToast(error.message, "error");
  }
}

function assignmentValuesFromState(classItem) {
  const assignments = appData.state.assignments[classItem.id] || {};
  const teachers = teacherMap();
  const curriculum = appData.meta.curriculum[String(classItem.grade)];
  return Object.fromEntries(Object.entries(curriculum).map(([subjectId, hours]) => [
    subjectId,
    allocationList(assignments[subjectId], hours).map((item) => ({ ...item, name: teachers[item.teacher_id]?.name || "" })),
  ]));
}

function comparableAssignmentValues(values, subjectIds) {
  return Object.fromEntries(subjectIds.map((subjectId) => [subjectId, (values[subjectId] || []).map((item) => ({
    teacher_id: item.teacher_id || "",
    name: item.name || "",
    lessons: Number(item.lessons || 0),
  }))]));
}

function assignmentValuesMatch(left, right, subjectIds) {
  return JSON.stringify(comparableAssignmentValues(left, subjectIds)) === JSON.stringify(comparableAssignmentValues(right, subjectIds));
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
  const classItem = appData.state.classes.find((item) => item.id === currentAssignmentClassId);
  if (!classItem) return;
  const subjectIds = Object.keys(appData.meta.curriculum[String(classItem.grade)]);
  const values = Object.fromEntries(subjectIds.map((subjectId) => [subjectId, []]));
  for (const row of document.querySelectorAll("[data-assignment-allocation-row]")) {
    const subjectId = row.dataset.assignmentSubject;
    const name = row.querySelector("[data-assignment-teacher]")?.value.trim() || "";
    if (!name || !values[subjectId]) continue;
    const teacher = appData.state.teachers.find((item) => item.name === name);
    values[subjectId].push({
      teacher_id: teacher?.id || "",
      name,
      lessons: Number(row.querySelector("[data-assignment-lessons]")?.value || 0),
    });
  }
  const baseValues = assignmentValuesFromState(classItem);
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
  const displayAssignments = assignmentDrafts.get(classItem.id) || assignmentValuesFromState(classItem);
  const teachers = appData.state.teachers;
  const teachersById = teacherMap();
  const subjects = subjectMap();
  const assignedCount = Object.entries(curriculum).filter(([subjectId, hours]) => (
    (displayAssignments[subjectId] || []).reduce((sum, item) => (item.name || item.teacher_id) ? sum + Number(item.lessons || 0) : sum, 0) === hours
  )).length;
  document.getElementById("assignment-grade-label").textContent = gradeNames[classItem.grade];
  document.getElementById("assignment-class-name").textContent = classItem.name;
  document.getElementById("assignment-progress").textContent = `${assignedCount} / ${Object.keys(curriculum).length}`;
  document.getElementById("assignment-progress").classList.toggle("good", assignedCount === Object.keys(curriculum).length);
  document.getElementById("assignment-grid").innerHTML = Object.entries(curriculum).map(([subjectId, hours]) => {
    const subject = subjects[subjectId];
    const locked = ["reading", "meeting"].includes(subjectId);
    let savedAllocations = allocationList(assignments[subjectId], hours).map((item) => ({ ...item, name: teachersById[item.teacher_id]?.name || "" }));
    if (subjectId === "reading") {
      const chineseAllocations = displayAssignments.chinese || [];
      const chineseTeacher = highestLessonAllocation(chineseAllocations);
      savedAllocations = chineseTeacher?.name ? [{ ...chineseTeacher, lessons: hours }] : [];
    }
    const sourceAllocations = locked ? savedAllocations : (displayAssignments[subjectId] || []);
    const shownAllocations = sourceAllocations.length ? sourceAllocations : (locked ? [] : [{ teacher_id: "", name: "", lessons: hours }]);
    const total = sourceAllocations.reduce((sum, item) => (item.name || item.teacher_id) ? sum + Number(item.lessons || 0) : sum, 0);
    const listId = `teacher-options-${subjectId}`;
    const lockedReason = subjectId === "reading" ? "随本班语文课时数最多的教师自动同步" : (subjectId === "meeting" ? "随本班班主任自动同步" : "");
    const totalClass = total > hours ? "over" : (total < hours ? "under" : "complete");
    return `<div class="assignment-row ${locked ? "locked-assignment" : ""}" data-assignment-subject-card="${subjectId}">
      <div class="assignment-subject"><span class="subject-color" style="background:${subject.color}"></span><div><strong>${escapeHtml(subject.name)}</strong><span>每周 ${hours} 节</span></div><span class="allocation-total ${totalClass}">${total} / ${hours}</span></div>
      <div class="assignment-allocations">${shownAllocations.map((allocation) => `<div class="assignment-allocation-row" data-assignment-allocation-row data-assignment-subject="${subjectId}">
        <div class="teacher-search-field"><input type="search" list="${listId}" value="${escapeHtml(allocation.name || teachersById[allocation.teacher_id]?.name || "")}" placeholder="${locked ? "未分配教师" : "搜索教师姓名"}" aria-label="${escapeHtml(subject.name)}任课教师" data-assignment-teacher ${locked ? "disabled" : ""}></div>
        <label class="allocation-lessons"><span>节数</span><input data-assignment-lessons type="number" min="1" max="${hours}" value="${Number(allocation.lessons || hours)}" ${locked ? "disabled" : ""}></label>
        ${locked ? "" : '<button class="tiny-button danger allocation-remove" type="button" data-remove-assignment-allocation>移除</button>'}
      </div>`).join("")}${locked ? `<span class="allocation-help">${lockedReason}</span>` : `<button class="allocation-add" type="button" data-add-assignment-allocation="${subjectId}">+ 添加共同任课教师</button>`}</div>
      <datalist id="${listId}">${teachers.map((teacher) => `<option value="${escapeHtml(teacher.name)}"></option>`).join("")}</datalist>
    </div>`;
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
  if (scheduleSwapDraft && (!schedule?.success || !appData.state.classes.some((item) => item.id === scheduleSwapDraft.classId))) {
    scheduleSwapDraft = null;
    selectedSwapSlot = null;
  }
  const editing = Boolean(scheduleSwapDraft);
  exportButton.classList.toggle("disabled", !schedule?.success || editing);
  teacherExportButton.classList.toggle("disabled", !schedule?.success || editing);
  exportButton.disabled = !schedule?.success || editing;
  teacherExportButton.disabled = !schedule?.success || editing;
  document.getElementById("generate-button").disabled = editing;
  renderClassExportFilters();
  renderGenerationMessage(schedule);
  document.querySelectorAll("[data-schedule-mode]").forEach((button) => {
    button.classList.toggle("active", button.dataset.scheduleMode === scheduleMode);
    button.disabled = editing;
  });

  const gradeFilter = document.getElementById("schedule-grade-filter");
  const gradeSelect = document.getElementById("schedule-grade-select");
  const grades = [...new Set(appData.state.classes.map((item) => Number(item.grade)))].sort((left, right) => left - right);
  if (!grades.includes(currentScheduleGrade)) currentScheduleGrade = grades[0] || 1;
  gradeSelect.innerHTML = grades.map((grade) => `<option value="${grade}">${gradeNames[grade]}</option>`).join("");
  gradeSelect.value = String(currentScheduleGrade);
  gradeSelect.disabled = editing;
  gradeFilter.classList.toggle("hidden", scheduleMode !== "class");
  document.getElementById("schedule-entity-label").textContent = scheduleMode === "class" ? "班级" : "教师";
  const printButton = document.getElementById("print-button");
  printButton.textContent = scheduleMode === "class" ? "打印当前班级课表" : "打印当前教师课表";
  printButton.disabled = !schedule?.success || editing;

  const selector = document.getElementById("schedule-entity-select");
  const entities = scheduleMode === "class"
    ? appData.state.classes.filter((item) => Number(item.grade) === currentScheduleGrade)
    : appData.state.teachers;
  const previous = selector.value;
  selector.innerHTML = entities.length ? entities.map((item) => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join("") : '<option value="">暂无可选数据</option>';
  if (entities.some((item) => item.id === previous)) selector.value = previous;
  if (editing) selector.value = scheduleSwapDraft.classId;
  selector.disabled = editing;
  const editButton = document.getElementById("schedule-edit-button");
  editButton.classList.toggle("hidden", scheduleMode !== "class" || editing);
  editButton.disabled = !schedule?.success || !selector.value;
  renderScheduleEditControls();
  renderTimetable();
}

function selectedExportFormat(name) {
  return document.querySelector(`input[name="${name}"]:checked`)?.value || "pdf";
}

function renderClassExportFilters() {
  const select = document.getElementById("class-export-grade");
  const previous = Number(select.value) || currentScheduleGrade;
  const grades = [...new Set(appData.state.classes.map((item) => Number(item.grade)))].sort((left, right) => left - right);
  select.innerHTML = grades.map((grade) => `<option value="${grade}">${gradeNames[grade]}</option>`).join("");
  select.value = String(grades.includes(previous) ? previous : grades[0]);
  updateClassExportFields();
}

function updateClassExportFields() {
  const scope = document.getElementById("class-export-scope").value;
  const format = selectedExportFormat("class-export-format").toUpperCase();
  document.getElementById("class-export-grade-field").classList.toggle("hidden", scope !== "grade");
  document.getElementById("submit-class-export").textContent = `导出 ${format}`;
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
  const format = selectedExportFormat("teacher-export-format");
  document.getElementById("teacher-export-grade-field").classList.toggle("hidden", scope !== "grade");
  document.getElementById("teacher-export-teacher-field").classList.toggle("hidden", scope !== "teacher");
  document.getElementById("submit-teacher-export").textContent = format === "xlsx"
    ? "导出 XLSX"
    : (scope === "teacher" ? "导出 PDF" : "导出 PDF 压缩包");
}

function slotLabel(slot) {
  const [dayId, periodId] = slot.split("-");
  const day = appData.meta.days.find((item) => item.id === dayId);
  const period = appData.meta.periods.find((item) => item.id === periodId);
  return `${day?.name || dayId}${period?.name || periodId}`;
}

function subjectAllowedInSlot(grade, subjectId, slot) {
  const [dayId, periodId] = slot.split("-");
  const coreSubjects = new Set(["chinese", "math", "english"]);
  if (["am1", "am2"].includes(periodId)) {
    if (!coreSubjects.has(subjectId)) return false;
    if (Number(grade) <= 2 && subjectId === "english") return false;
  }
  if (subjectId === "english" && Number(grade) >= 3 && dayId === "mon" && ["pm1", "pm2"].includes(periodId)) return false;
  if (subjectId === "chinese" && dayId === "tue" && ["pm1", "pm2"].includes(periodId)) return false;
  if (subjectId === "math" && dayId === "thu" && ["pm1", "pm2"].includes(periodId)) return false;
  return true;
}

function previewScheduleSwapConflicts() {
  if (!scheduleSwapDraft) return [];
  const classItem = appData.state.classes.find((item) => item.id === scheduleSwapDraft.classId);
  if (!classItem) return ["班级不存在"];
  const subjects = subjectMap();
  const teachers = teacherMap();
  const conflicts = [];
  for (const [slot, lesson] of Object.entries(scheduleSwapDraft.lessons)) {
    if (!subjectAllowedInSlot(classItem.grade, lesson.subject_id, slot)) {
      conflicts.push(`${subjects[lesson.subject_id]?.name || lesson.subject_id}不能安排在${slotLabel(slot)}`);
    }
    if (!lesson.teacher_id) continue;
    for (const otherClass of appData.state.classes) {
      if (otherClass.id === classItem.id) continue;
      if (appData.state.schedule.lessons[otherClass.id]?.[slot]?.teacher_id === lesson.teacher_id) {
        conflicts.push(`${teachers[lesson.teacher_id]?.name || "未知教师"}在${slotLabel(slot)}还要为${otherClass.name}上课`);
      }
    }
  }
  return [...new Set(conflicts)];
}

function renderScheduleEditControls() {
  const bar = document.getElementById("schedule-edit-bar");
  const feedback = document.getElementById("schedule-edit-feedback");
  const editing = Boolean(scheduleSwapDraft);
  bar.classList.toggle("hidden", !editing);
  if (!editing) {
    feedback.classList.add("hidden");
    feedback.classList.remove("conflict");
    return;
  }
  const swapCount = scheduleSwapDraft.swaps.length;
  document.getElementById("schedule-edit-status").textContent = selectedSwapSlot
    ? `已选择${slotLabel(selectedSwapSlot)}，再选择另一节课即可交换`
    : (swapCount ? `已暂存 ${swapCount} 次交换；可继续调整，最后统一保存` : "依次选择两节课进行交换；固定课程不可调整");
  document.getElementById("undo-schedule-swap").disabled = swapCount === 0;
  const conflicts = previewScheduleSwapConflicts();
  scheduleSwapDraft.conflicts = conflicts;
  document.getElementById("save-schedule-swaps").disabled = swapCount === 0 || conflicts.length > 0;
  feedback.classList.toggle("hidden", swapCount === 0);
  feedback.classList.toggle("conflict", conflicts.length > 0);
  feedback.innerHTML = conflicts.length
    ? `<strong>当前有 ${conflicts.length} 项冲突，请继续调整后再保存</strong><ul>${conflicts.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
    : "当前调整未发现教师时间或硬性排课规则冲突，可以保存。";
}

function startScheduleEdit() {
  const classId = document.getElementById("schedule-entity-select").value;
  const lessons = appData.state.schedule?.lessons?.[classId];
  if (!lessons) {
    showToast("请选择需要调整的班级", "error");
    return;
  }
  scheduleSwapDraft = { classId, lessons: JSON.parse(JSON.stringify(lessons)), swaps: [], conflicts: [] };
  selectedSwapSlot = null;
  renderSchedule();
}

function selectScheduleSwapSlot(slot) {
  if (!scheduleSwapDraft) return;
  if (!selectedSwapSlot) {
    selectedSwapSlot = slot;
    renderScheduleEditControls();
    renderTimetable();
    return;
  }
  if (selectedSwapSlot === slot) {
    selectedSwapSlot = null;
    renderScheduleEditControls();
    renderTimetable();
    return;
  }
  const fromSlot = selectedSwapSlot;
  const lessons = scheduleSwapDraft.lessons;
  [lessons[fromSlot], lessons[slot]] = [lessons[slot], lessons[fromSlot]];
  scheduleSwapDraft.swaps.push({ from_slot: fromSlot, to_slot: slot });
  selectedSwapSlot = null;
  renderScheduleEditControls();
  renderTimetable();
}

function undoScheduleSwap() {
  if (!scheduleSwapDraft?.swaps.length) return;
  const lastSwap = scheduleSwapDraft.swaps.pop();
  const lessons = scheduleSwapDraft.lessons;
  [lessons[lastSwap.from_slot], lessons[lastSwap.to_slot]] = [lessons[lastSwap.to_slot], lessons[lastSwap.from_slot]];
  selectedSwapSlot = null;
  renderScheduleEditControls();
  renderTimetable();
}

async function cancelScheduleEdit() {
  if (scheduleSwapDraft?.swaps.length) {
    const confirmed = await confirmDialog("放弃课表调整", "尚未保存的课程交换将全部撤销。当前已保存课表不会改变。", "放弃调整");
    if (!confirmed) return;
  }
  scheduleSwapDraft = null;
  selectedSwapSlot = null;
  renderSchedule();
}

async function saveScheduleSwaps() {
  if (!scheduleSwapDraft?.swaps.length) return;
  const button = document.getElementById("save-schedule-swaps");
  setLoading(button, true, "校验并保存…");
  try {
    const data = await api("/api/schedule/swaps", {
      method: "PUT",
      body: JSON.stringify({ class_id: scheduleSwapDraft.classId, swaps: scheduleSwapDraft.swaps }),
    });
    const count = data.updated_swaps;
    scheduleSwapDraft = null;
    selectedSwapSlot = null;
    applyStateResponse(data);
    showToast(`已保存 ${count} 次课程交换，教师课表已同步更新`);
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setLoading(button, false);
    if (scheduleSwapDraft) renderScheduleEditControls();
  }
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
    : `${appData.state.school_name}教师课程表`;
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
      const mainText = scheduleMode === "class" ? subject.name : lesson.class_name;
      const secondary = scheduleMode === "class" ? (teachers[lesson.teacher_id]?.name || "") : subject.name;
      html += `<td><strong>${escapeHtml(mainText)}</strong>${secondary ? `<span>${escapeHtml(secondary)}</span>` : ""}</td>`;
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
  const editing = Boolean(scheduleSwapDraft && scheduleMode === "class" && scheduleSwapDraft.classId === entityId);
  title.textContent = classItem?.name || teacher?.name || "请选择";
  subtitle.textContent = editing ? "正在调整 · 尚未保存" : (scheduleMode === "class" ? "班级周课程表" : "教师周课程表");
  const rate = Math.round((schedule.quality?.morning_third_rate || 0) * 100);
  qualityBadge.innerHTML = editing
    ? `<span class="quality-pill">${scheduleSwapDraft.swaps.length} 次交换待保存</span>`
    : `<span class="quality-pill">核心课优先率 ${rate}%</span>`;

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
      const classLessons = editing ? scheduleSwapDraft.lessons : schedule.lessons[entityId];
      const lesson = scheduleMode === "class" ? classLessons?.[slot] : lessonForTeacher(schedule, entityId, slot);
      if (!lesson) {
        html += "<td></td>";
        continue;
      }
      const subject = subjects[lesson.subject_id];
      const secondary = scheduleMode === "class" ? (teachers[lesson.teacher_id]?.name || "未分配教师") : lesson.class_name;
      const cardContent = `<strong>${escapeHtml(subject?.name || lesson.subject_id)}</strong><span>${escapeHtml(secondary)}</span>`;
      if (editing) {
        const fixed = Boolean(appData.meta.fixed_lessons[String(classItem.grade)]?.[slot] || lesson.fixed);
        html += fixed
          ? `<td><div class="lesson-card lesson-locked" style="--subject-color:${subject?.color || "#365f58"}" title="固定课程不能交换">${cardContent}</div></td>`
          : `<td><button class="lesson-card lesson-swap-target ${selectedSwapSlot === slot ? "selected" : ""}" style="--subject-color:${subject?.color || "#365f58"}" type="button" data-swap-slot="${slot}" aria-pressed="${selectedSwapSlot === slot}">${cardContent}</button></td>`;
      } else {
        html += `<td><div class="lesson-card" style="--subject-color:${subject?.color || "#365f58"}">${cardContent}</div></td>`;
      }
    }
    html += "</tr>";
  }
  table.innerHTML = `${html}</tbody>`;
  table.querySelectorAll("[data-swap-slot]").forEach((button) => {
    button.addEventListener("click", () => selectScheduleSwapSlot(button.dataset.swapSlot));
  });
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

function confirmDialog(title, message, confirmLabel = "确认") {
  const dialog = document.getElementById("confirm-dialog");
  document.getElementById("confirm-title").textContent = title;
  document.getElementById("confirm-message").textContent = message;
  document.getElementById("confirm-action").textContent = confirmLabel;
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
document.getElementById("add-teacher-range").addEventListener("click", () => {
  const range = newTeacherRange();
  teacherAssignmentRanges.push(range);
  renderTeacherAssignmentRanges();
  document.querySelector(`[data-teacher-range-id="${range.id}"] [data-teacher-range-field="subject_id"]`)?.focus();
});
document.getElementById("teacher-range-list").addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove-teacher-range]");
  if (!button) return;
  teacherAssignmentRanges = teacherAssignmentRanges.filter((range) => range.id !== button.dataset.removeTeacherRange);
  renderTeacherAssignmentRanges();
});
document.getElementById("teacher-range-list").addEventListener("change", (event) => {
  const field = event.target.dataset.teacherRangeField;
  const row = event.target.closest("[data-teacher-range-id]");
  const range = teacherAssignmentRanges.find((item) => item.id === row?.dataset.teacherRangeId);
  if (!field || !range) return;
  if (field === "grade") {
    range.grade = Number(event.target.value);
    const classes = teacherRangeClasses(range.grade);
    range.start_class_id = classes[0]?.id || "";
    range.end_class_id = classes[0]?.id || "";
    if (!teacherRangeSubjects(range.grade).some((subject) => subject.id === range.subject_id)) range.subject_id = "";
    range.lessons = 1;
  } else if (field === "lessons") {
    range.lessons = Number(event.target.value || 1);
  } else {
    range[field] = event.target.value;
    if (field === "subject_id" && range.subject_id) {
      range.lessons = Number(appData.meta.curriculum[String(range.grade)]?.[range.subject_id] || 1);
    }
    if (field === "start_class_id") {
      const classes = teacherRangeClasses(range.grade);
      const startIndex = classes.findIndex((item) => item.id === range.start_class_id);
      const endIndex = classes.findIndex((item) => item.id === range.end_class_id);
      if (endIndex < startIndex) range.end_class_id = range.start_class_id;
    }
  }
  renderTeacherAssignmentRanges();
});
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
    let data = await api("/api/teachers/import", { method: "POST", body });
    if (data.requires_confirmation) {
      const shown = data.shortages.slice(0, 8).map((item) => `${item.class_name}${item.subject_name}缺少${item.missing}节`).join("；");
      const suffix = data.shortages.length > 8 ? `；另有${data.shortages.length - 8}项` : "";
      const confirmed = await confirmDialog(
        "任课课时尚未分配完整",
        `${shown}${suffix}。这些课仍可排入课表，但教师将显示为未分配。确认后才会写入本次教师名单。`,
        "确认保留缺口并导入",
      );
      if (!confirmed) {
        result.textContent = "未导入。请修改表格中的任课节数后重新上传。";
        result.classList.remove("hidden");
        return;
      }
      const confirmedBody = new FormData();
      confirmedBody.append("file", file);
      data = await api("/api/teachers/import?allow_incomplete=true", { method: "POST", body: confirmedBody });
    }
    applyStateResponse(data);
    document.getElementById("teacher-import-dialog").close();
    const summary = data.import;
    showToast(`已处理 ${summary.total} 名教师：新增 ${summary.created}，更新 ${summary.updated}，任课 ${summary.assigned || 0} 项，班主任 ${summary.homerooms || 0} 项${summary.merged_rows ? `，合并同名行 ${summary.merged_rows}` : ""}`);
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
  const teacherId = document.getElementById("teacher-id").value;
  const teachingAssignments = expandedTeacherAssignments();
  const issues = teacherAssignmentIssues(teachingAssignments, teacherId);
  if (issues.over) {
    showToast(`有 ${issues.over} 项课程的教师课时合计超过课程标准，请调整后再保存`, "error");
    return;
  }
  if (issues.incomplete) {
    const confirmed = await confirmDialog(
      "任课课时尚未分配完整",
      `保存后仍有 ${issues.incomplete} 项课程存在未分配课时，这些课排课时不会显示教师。是否确认保存？`,
      "确认保留缺口",
    );
    if (!confirmed) return;
  }
  const rangeSubjectIds = Object.values(teachingAssignments).flatMap((subjectMap) => Object.keys(subjectMap));
  const payload = {
    name: document.getElementById("teacher-name").value,
    min_weekly_lessons: Number(document.getElementById("teacher-min-lessons").value),
    subject_ids: [...new Set(rangeSubjectIds)],
    homeroom_class_id: document.getElementById("teacher-homeroom-class").value || null,
    teaching_assignments: teachingAssignments,
  };
  setLoading(button, true, "保存中…");
  try {
    const data = await api(teacherId ? `/api/teachers/${teacherId}` : "/api/teachers", { method: teacherId ? "PUT" : "POST", body: JSON.stringify(payload) });
    document.getElementById("teacher-dialog").close();
    applyStateResponse(data);
    const assignmentCount = Object.values(teachingAssignments).reduce((sum, subjectMap) => sum + Object.keys(subjectMap).length, 0);
    showToast(`${teacherId ? "教师资料已更新" : "教师已添加"}${assignmentCount ? `，已配置 ${assignmentCount} 项任课` : ""}`);
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
  const teachersByName = new Map(appData.state.teachers.map((teacher) => [teacher.name, teacher]));
  try {
    const classes = {};
    let incompleteCount = 0;
    for (const [classId, draft] of assignmentDrafts.entries()) {
      const classItem = appData.state.classes.find((item) => item.id === classId);
      if (!classItem) continue;
      const curriculum = appData.meta.curriculum[String(classItem.grade)];
      const assignments = {};
      for (const subjectId of Object.keys(curriculum)) {
        const seenTeachers = new Set();
        assignments[subjectId] = (draft[subjectId] || []).map((allocation) => {
          const name = (allocation.name || "").trim();
          const teacher = name ? teachersByName.get(name) : null;
          if (!teacher) throw new Error(`${classItem.name}：未找到教师“${name}”，请从搜索建议中选择`);
          if (seenTeachers.has(teacher.id)) throw new Error(`${classItem.name}的同一课程不能重复选择${teacher.name}`);
          seenTeachers.add(teacher.id);
          const lessons = Number(allocation.lessons || 0);
          if (!Number.isInteger(lessons) || lessons < 1) throw new Error(`${classItem.name}：任课节数必须是正整数`);
          return { teacher_id: teacher.id, lessons };
        });
        const total = assignments[subjectId].reduce((sum, item) => sum + item.lessons, 0);
        if (total > curriculum[subjectId]) throw new Error(`${classItem.name}的${subjectMap()[subjectId]?.name || subjectId}合计${total}节，超过标准${curriculum[subjectId]}节`);
        if (total > 0 && total < curriculum[subjectId] && !["reading", "meeting"].includes(subjectId)) incompleteCount += 1;
      }
      classes[classId] = assignments;
    }
    if (incompleteCount) {
      const confirmed = await confirmDialog(
        "任课课时尚未分配完整",
        `共有 ${incompleteCount} 项课程只分配了部分课时；剩余课时仍会排课，但教师显示为未分配。是否确认保存？`,
        "确认保留缺口",
      );
      if (!confirmed) return;
    }
    setLoading(button, true, "保存中…");
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
  if (!event.target.matches("[data-assignment-teacher], [data-assignment-lessons]")) return;
  captureCurrentAssignmentDraft();
  const card = event.target.closest("[data-assignment-subject-card]");
  if (card) {
    const subjectId = card.dataset.assignmentSubjectCard;
    const classItem = appData.state.classes.find((item) => item.id === currentAssignmentClassId);
    const expected = appData.meta.curriculum[String(classItem.grade)][subjectId];
    const total = [...card.querySelectorAll("[data-assignment-lessons]")].reduce((sum, input) => (
      input.closest("[data-assignment-allocation-row]")?.querySelector("[data-assignment-teacher]")?.value.trim() ? sum + Number(input.value || 0) : sum
    ), 0);
    const badge = card.querySelector(".allocation-total");
    badge.textContent = `${total} / ${expected}`;
    badge.className = `allocation-total ${total > expected ? "over" : (total < expected ? "under" : "complete")}`;
  }
});
document.getElementById("assignment-grid").addEventListener("click", (event) => {
  const addButton = event.target.closest("[data-add-assignment-allocation]");
  const removeButton = event.target.closest("[data-remove-assignment-allocation]");
  if (!addButton && !removeButton) return;
  captureCurrentAssignmentDraft();
  const classItem = appData.state.classes.find((item) => item.id === currentAssignmentClassId);
  if (!classItem) return;
  const draft = assignmentDrafts.get(classItem.id) || assignmentValuesFromState(classItem);
  if (addButton) {
    const subjectId = addButton.dataset.addAssignmentAllocation;
    draft[subjectId] = [...(draft[subjectId] || []), { teacher_id: "", name: "", lessons: 1 }];
  } else {
    const row = removeButton.closest("[data-assignment-allocation-row]");
    const subjectId = row.dataset.assignmentSubject;
    const index = [...row.parentElement.querySelectorAll("[data-assignment-allocation-row]")].indexOf(row);
    draft[subjectId] = (draft[subjectId] || []).filter((_, itemIndex) => itemIndex !== index);
  }
  assignmentDrafts.set(classItem.id, draft);
  renderAssignments();
});
window.addEventListener("beforeunload", (event) => {
  if (!assignmentDrafts.size && !scheduleSwapDraft?.swaps.length) return;
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
document.getElementById("schedule-entity-select").addEventListener("change", renderTimetable);
document.getElementById("print-button").addEventListener("click", printCurrentSchedule);
document.getElementById("schedule-edit-button").addEventListener("click", startScheduleEdit);
document.getElementById("undo-schedule-swap").addEventListener("click", undoScheduleSwap);
document.getElementById("cancel-schedule-edit").addEventListener("click", cancelScheduleEdit);
document.getElementById("save-schedule-swaps").addEventListener("click", saveScheduleSwaps);
window.addEventListener("afterprint", () => document.getElementById("print-sheet").setAttribute("aria-hidden", "true"));

document.getElementById("export-button").addEventListener("click", () => {
  if (!appData.state.schedule?.success || scheduleSwapDraft) return;
  renderClassExportFilters();
  document.getElementById("class-export-dialog").showModal();
});
document.getElementById("close-class-export-dialog").addEventListener("click", () => document.getElementById("class-export-dialog").close());
document.getElementById("cancel-class-export-dialog").addEventListener("click", () => document.getElementById("class-export-dialog").close());
document.getElementById("class-export-scope").addEventListener("change", updateClassExportFields);
document.querySelectorAll('input[name="class-export-format"]').forEach((input) => input.addEventListener("change", updateClassExportFields));
document.getElementById("class-export-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const scope = document.getElementById("class-export-scope").value;
  const format = selectedExportFormat("class-export-format");
  const params = new URLSearchParams();
  if (scope === "grade") params.set("grade", document.getElementById("class-export-grade").value);
  document.getElementById("class-export-dialog").close();
  window.location.assign(`/api/export/schedule.${format}${params.size ? `?${params}` : ""}`);
});

document.getElementById("teacher-export-button").addEventListener("click", () => {
  if (!appData.state.schedule?.success || scheduleSwapDraft) return;
  renderTeacherExportFilters();
  document.getElementById("teacher-export-dialog").showModal();
});
document.getElementById("close-teacher-export-dialog").addEventListener("click", () => document.getElementById("teacher-export-dialog").close());
document.getElementById("cancel-teacher-export-dialog").addEventListener("click", () => document.getElementById("teacher-export-dialog").close());
document.getElementById("teacher-export-scope").addEventListener("change", updateTeacherExportFields);
document.querySelectorAll('input[name="teacher-export-format"]').forEach((input) => input.addEventListener("change", updateTeacherExportFields));
document.getElementById("teacher-export-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const scope = document.getElementById("teacher-export-scope").value;
  const format = selectedExportFormat("teacher-export-format");
  if (scope === "teacher") {
    const teacherName = document.getElementById("teacher-export-teacher").value.trim();
    const teacher = scheduledTeachers().find((item) => item.name === teacherName);
    if (!teacher) {
      showToast("请从搜索建议中选择一名已排课教师", "error");
      return;
    }
    document.getElementById("teacher-export-dialog").close();
    const endpoint = format === "pdf" ? "/api/export/teacher.pdf" : "/api/export/teachers.xlsx";
    window.location.assign(`${endpoint}?teacher_id=${encodeURIComponent(teacher.id)}`);
    return;
  }
  const params = new URLSearchParams();
  if (scope === "grade") params.set("grade", document.getElementById("teacher-export-grade").value);
  document.getElementById("teacher-export-dialog").close();
  const endpoint = format === "pdf" ? "/api/export/teachers.zip" : "/api/export/teachers.xlsx";
  window.location.assign(`${endpoint}${params.size ? `?${params}` : ""}`);
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
