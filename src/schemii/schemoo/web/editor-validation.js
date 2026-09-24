let nextErrorId = 0;

export function validationKey(control, key) {
  control.dataset.validationKey = key;
  return control;
}

export function clearEditorValidation(body, summary) {
  summary.textContent = "";
  for (const control of body.querySelectorAll("[data-validation-error-id]")) {
    const id = control.dataset.validationErrorId;
    const describedBy = (control.getAttribute("aria-describedby") || "").split(/\s+/).filter(value => value && value !== id);
    if (describedBy.length) control.setAttribute("aria-describedby", describedBy.join(" "));
    else control.removeAttribute("aria-describedby");
    control.removeAttribute("aria-invalid");
    delete control.dataset.validationErrorId;
  }
  for (const error of body.querySelectorAll(".mf-inline-error")) error.remove();
}

export function showEditorValidation(body, summary, issues, scrollBody = body) {
  clearEditorValidation(body, summary);
  if (!issues.length) return false;
  summary.textContent = [...new Set(issues.map(issue => issue.message))].join(" ");
  const controls = new Map();
  for (const issue of issues) {
    const root = [...body.querySelectorAll("[data-validation-key]")].find(candidate => candidate.dataset.validationKey === issue.key);
    if (!root) continue;
    const control = root.matches("input, select, textarea, button") ? root : root.querySelector("input, select, textarea, button");
    if (!control) continue;
    if (!controls.has(control)) controls.set(control, { root, messages: [] });
    if (!controls.get(control).messages.includes(issue.message)) controls.get(control).messages.push(issue.message);
  }
  const first = controls.keys().next().value;
  for (const [control, { root, messages }] of controls) {
    const error = document.createElement("p");
    error.className = "mf-inline-error";
    error.id = `mf-editor-error-${++nextErrorId}`;
    error.textContent = messages.join(" ");
    control.setAttribute("aria-invalid", "true");
    control.setAttribute("aria-describedby", [control.getAttribute("aria-describedby"), error.id].filter(Boolean).join(" "));
    control.dataset.validationErrorId = error.id;
    const wrapper = root.closest(".mf-label, .stack, .derived-keys") || root.parentElement;
    wrapper.append(error);
  }
  if (first) {
    for (let ancestor = first.closest("details"); ancestor && body.contains(ancestor); ancestor = ancestor.parentElement.closest("details")) ancestor.open = true;
    first.focus({ preventScroll: true });
    const area = scrollBody.getBoundingClientRect(), rect = first.getBoundingClientRect();
    if (rect.top < area.top + 16) scrollBody.scrollTop += rect.top - area.top - 16;
    else if (rect.bottom > area.bottom - 16) scrollBody.scrollTop += rect.bottom - area.bottom + 16;
  }
  return true;
}
