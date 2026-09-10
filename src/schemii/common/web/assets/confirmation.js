import { element } from "./dom.js";

// Native modal focus containment and one-shot async confirmation are shared by
// destructive actions. An uncertain failure must be reviewed, not blindly retried.
export function confirmAction({ title, message, details, confirmLabel = "Delete", busyLabel = "Deleting…", onConfirm }) {
  const previousFocus = document.activeElement;
  const dialog = element("dialog", { className: "ui-dialog ui-confirmation", attrs: { "aria-label": title } });
  const error = element("p", { className: "ui-confirmation__error", attrs: { role: "alert" } });
  const cancel = element("button", { type: "button", className: "ui-button", text: "Cancel", attrs: { autofocus: "" } });
  const confirm = element("button", { type: "button", className: "ui-button ui-confirmation__submit", text: confirmLabel });
  const body = element("div", { className: "ui-confirmation__body" }, [element("p", { text: message }), element("p", { className: "hint", text: details }), error]);
  dialog.append(element("header", { className: "ui-dialog__head" }, [element("h2", { text: title })]), body, element("footer", { className: "ui-dialog__actions" }, [cancel, confirm]));
  let pending = false, succeeded = false;
  return new Promise(resolve => {
    dialog.addEventListener("cancel", event => { if (pending) event.preventDefault(); });
    dialog.addEventListener("close", () => {
      dialog.remove();
      if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true });
      resolve(succeeded);
    }, { once: true });
    cancel.onclick = () => { if (!pending) dialog.close(); };
    confirm.onclick = async () => {
      if (pending || confirm.disabled) return;
      pending = true; cancel.disabled = true; confirm.disabled = true;
      confirm.textContent = busyLabel; dialog.setAttribute("aria-busy", "true");
      try {
        await onConfirm();
        succeeded = true; dialog.close();
      } catch (failure) {
        error.textContent = failure.message || "The action could not be completed. Refresh and review its current state before trying again.";
        cancel.textContent = "Close";
        confirm.textContent = confirmLabel;
      } finally {
        pending = false; cancel.disabled = false; dialog.removeAttribute("aria-busy");
        if (!succeeded) cancel.focus();
      }
    };
    document.body.append(dialog); dialog.showModal(); cancel.focus();
  });
}
