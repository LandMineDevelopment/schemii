// Own the optimistic request/confirmation ordering. Presentation and workspace
// commits remain with the application; a matching response updates metadata
// without replacing the already mounted optimistic detail surface.
export async function performDesignHistoryMove({
  state, direction, operation, applyPreview, requestMove, applyMutation, restorePreview,
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
    if (delta.length) {
      const preview = await applyPreview(delta);
      presentation = preview.presentation;
      previewApplied = true;
    }
    const mutation = await requestMove(direction, workspaceId, { expectedDesignRevision }, {
      signal: operation.signal,
    });
    if (!isCurrent()) return null;
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
