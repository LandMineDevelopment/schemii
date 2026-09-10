import { element } from "./dom.js";

/** Shared name-and-submit dialog with focus containment and one pending request. */
export function namedAction({ title, initial = "", inputLabel = "Name", description = "", submitLabel = "Save",
  busyLabel = "Saving…", allowRetry = true, onSave }) {
  return new Promise(resolve => {
    const previous = document.activeElement;
    const dialog = element("dialog", { className: "ui-dialog ui-named-action", attrs: { "aria-label": title } });
    const form = element("form");
    const input = element("input", { attrs: { "aria-label": inputLabel, required: "", maxlength: "128", autocomplete: "off" } });
    input.value = initial;
    const error = element("p", { className: "ui-named-action__error", attrs: { role: "alert" } });
    const submit = element("button", { type: "submit", className: "ui-button primary", text: submitLabel });
    const cancel = element("button", { type: "button", className: "ui-button", text: "Cancel" });
    let saving = false, saved = false, failed = false;
    cancel.onclick = () => { if (!saving) dialog.close(); };
    dialog.addEventListener("cancel", event => { if (saving) event.preventDefault(); });
    dialog.addEventListener("close", () => { dialog.remove(); if (previous?.isConnected) previous.focus({ preventScroll: true }); resolve(saved); }, { once: true });
    form.onsubmit = async event => {
      event.preventDefault(); if (saving || (failed && !allowRetry) || !input.value.trim()) return;
      saving = true; submit.disabled = cancel.disabled = input.disabled = true;
      error.textContent = ""; submit.textContent = busyLabel; dialog.setAttribute("aria-busy", "true");
      try { await onSave(input.value.trim()); saved = true; dialog.close(); }
      catch (failure) { failed = true; error.textContent = failure.message || "The action could not be completed."; }
      finally {
        saving = false; cancel.disabled = false; submit.disabled = input.disabled = failed && !allowRetry;
        submit.textContent = submitLabel; dialog.removeAttribute("aria-busy");
        if (failed && !allowRetry) { cancel.textContent = "Close"; cancel.focus(); }
      }
    };
    form.append(element("h2", { text: title }), element("label", { className: "ui-named-action__label" }, [inputLabel, input]),
      element("p", { className: "hint", text: description }), error,
      element("div", { className: "ui-dialog__actions" }, [cancel, submit]));
    dialog.append(form); document.body.append(dialog); dialog.showModal(); input.focus(); input.select();
  });
}
