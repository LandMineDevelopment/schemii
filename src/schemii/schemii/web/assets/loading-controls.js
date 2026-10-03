function fitAvailable(state) {
  return Boolean(state.startupComplete && state.catalog && !state.catalogLoading);
}

export function bindCanvasFitControl({ button, canvas, getState, showToast, onFit }) {
  function updateAvailability() {
    button.disabled = !fitAvailable(getState());
  }

  button.addEventListener("click", () => {
    const state = getState();
    // Recheck admission as well as disabling the button: loading may have begun
    // since a click was queued or before the next header render.
    if (!fitAvailable(state)) return;
    if (!canvas.fit()) {
      showToast(state.catalog.source === "design"
        ? "No desired tables are available to fit."
        : "No live tables are available to fit.");
    } else onFit();
  });

  updateAvailability();
  return updateAvailability;
}
