// Own the optimistic request/confirmation ordering. Presentation and workspace
// commits remain with the application; a matching response updates metadata
// without replacing the already mounted optimistic detail surface.
export async function performDesignHistoryMove({
  state, direction, operation, applyPreview, requestMove, applyMutation, restorePreview,
  deferPreview = false,
}) {
  const workspaceId = state.activeWorkspace.id;
  const expectedDesignRevision = state.design.revision;
  const rollback = {
    design: state.design,
    layout: state.designLayout,
    history: state.designHistory,
    activeLayer: state.activeLayer,
    selectedTableId: state.selectedTableId,
    selectedViewId: state.selectedViewId,
  };
  const action = state.designHistory?.[direction] || null;
  const delta = action?.delta || [];
  const isCurrent = () => operation.isCurrent() && state.activeWorkspace?.id === workspaceId;
  let previewApplied = false;
  let presentation = null;
  try {
    if (delta.length && !deferPreview) {
      const preview = await applyPreview(delta, { isCurrent });
      if (!isCurrent()) return null;
      presentation = preview.presentation;
      previewApplied = true;
    }
    const mutation = await requestMove(direction, workspaceId, { expectedDesignRevision }, {
      signal: operation.signal,
    });
    if (!isCurrent()) return null;
    if (delta.length && deferPreview) {
      try {
        const preview = await applyPreview(delta, { isCurrent, mutation });
        if (!isCurrent()) return null;
        presentation = preview.presentation;
        previewApplied = true;
      } catch (error) {
        if (!isCurrent()) return null;
        // The server already committed: recovery must install its confirmed
        // state, never roll back to the pre-request design.
        applyMutation(mutation, { cue: false, render: true });
        return { error };
      }
    }
    const previewMatches = previewApplied
      && JSON.stringify(state.design.content) === JSON.stringify(mutation.design.content);
    applyMutation(mutation, { cue: !previewMatches, render: !previewMatches });
    return { presentation, action };
  } catch (error) {
    if (!isCurrent()) return null;
    if (previewApplied) restorePreview(rollback);
    return { error };
  }
}
