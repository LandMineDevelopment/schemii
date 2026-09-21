import { requestJson } from "#common/http.js";
import { element } from "#common/dom.js";

// Dependency review is advisory; DELETE checks again atomically on the server.
export function confirmModelDeletion(model) {
  const previousFocus = document.activeElement;
  const path = `/api/v1/schemoo/models/${encodeURIComponent(model.id)}`;
  const controller = new AbortController();
  const dialog = element("dialog", { className: "ui-dialog ui-confirmation model-deletion", attrs: { "aria-label": "Delete model?" } });
  const status = element("p", { text: "Checking dependent dashboards…", attrs: { role: "status" } });
  const dependents = element("div");
  const error = element("p", { className: "ui-confirmation__error", attrs: { role: "alert" } });
  const cancel = element("button", { type: "button", className: "ui-button", text: "Cancel", attrs: { autofocus: "" } });
  const confirm = element("button", { type: "button", className: "ui-button ui-confirmation__submit", text: "Delete model" });
  confirm.disabled = true;
  const body = element("div", { className: "ui-confirmation__body" }, [
    element("p", { text: `Are you sure you want to delete “${model.name}”?` }),
    element("p", { className: "hint", text: "This permanently deletes the saved model, its layout, and saved previews. Your PostgreSQL tables and data are not changed. This cannot be undone." }),
    status, dependents, error,
  ]);
  dialog.append(element("header", { className: "ui-dialog__head" }, [element("h2", { text: "Delete model?" })]),
    body, element("footer", { className: "ui-dialog__actions" }, [cancel, confirm]));
  let pending = false, succeeded = false;

  function showDependencies(dashboards) {
    confirm.disabled = true;
    cancel.textContent = "Close";
    status.textContent = `${dashboards.length} saved dashboard${dashboards.length === 1 ? " depends" : "s depend"} on this model. Deletion is blocked.`;
    dependents.replaceChildren(
      element("ul", { className: "model-deletion__dashboards", attrs: { "aria-label": "Dependent dashboards" } }, dashboards.map(dashboard =>
        element("li", {}, [element("a", { text: dashboard.name, attrs: {
          href: `/schemer?dashboard=${encodeURIComponent(dashboard.id)}`, target: "_blank", rel: "noopener",
          "aria-label": `${dashboard.name} (opens in a new tab)`,
        } })]))),
      element("p", { className: "hint", text: "Review these dashboards in Schemer (links open a new tab). Delete them there only if you no longer need them, then reopen this dialog. Saved dashboards cannot be moved to another model." }),
    );
  }

  return new Promise(resolve => {
    dialog.addEventListener("cancel", event => { if (pending) event.preventDefault(); });
    dialog.addEventListener("close", () => {
      controller.abort(); dialog.remove();
      if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true });
      resolve(succeeded);
    }, { once: true });
    cancel.onclick = () => { if (!pending) dialog.close(); };
    confirm.onclick = async () => {
      if (pending || confirm.disabled) return;
      pending = true; cancel.disabled = true; confirm.disabled = true;
      confirm.textContent = "Deleting…"; dialog.setAttribute("aria-busy", "true");
      try {
        await requestJson(`${path}?expected_revision=${model.revision}`, { method: "DELETE" });
        succeeded = true; dialog.close();
      } catch (failure) {
        if (failure.code === "model_in_use" && failure.details.dashboards?.length) {
          showDependencies(failure.details.dashboards);
          error.textContent = "A dashboard now depends on this model. Nothing was deleted.";
        } else if (failure.code === "model_revision_conflict") {
          error.textContent = "This model changed after it was loaded. Close this dialog and refresh the model list to review the latest revision before deleting.";
        } else {
          error.textContent = `${failure.message} Close this dialog and refresh to review the model’s current state before trying again.`;
        }
        cancel.textContent = "Close";
        confirm.textContent = "Delete model";
      } finally {
        pending = false; cancel.disabled = false; dialog.removeAttribute("aria-busy");
        if (!succeeded) cancel.focus();
      }
    };
    document.body.append(dialog); dialog.showModal(); cancel.focus();
    requestJson(`${path}/dependencies`, { signal: controller.signal }).then(({ dashboards }) => {
      if (!dialog.open) return;
      if (dashboards.length) showDependencies(dashboards);
      else { status.textContent = "No saved dashboards depend on this model."; confirm.disabled = false; }
    }).catch(failure => {
      if (!dialog.open) return;
      status.textContent = "Dashboard dependencies could not be checked.";
      error.textContent = `${failure.message} Close and reopen this dialog to try again. Deletion remains disabled.`;
      cancel.textContent = "Close";
    });
  });
}
