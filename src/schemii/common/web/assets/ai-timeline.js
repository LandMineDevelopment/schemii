/** Keep a turn's tracker after its prompt, before tools and assistant responses.
 * Older transcripts without turn IDs use their latest user message as the anchor.
 * Return existing nodes so moving a tracker never restarts its animation.
 */
export function placeTurnActivity(nodes, activity, { turnId } = {}) {
  const ordered = nodes.filter(node => node !== activity);
  const belongs = node => !turnId || node.dataset?.messageTurnId === turnId;
  const hasTurn = turnId && ordered.some(node => node.dataset?.messageTurnId === turnId);
  let anchor = ordered.findLastIndex(node => node.dataset?.messageRole === "user" && (!hasTurn || belongs(node)));
  if (anchor < 0 && hasTurn) anchor = ordered.findIndex(belongs) - 1;
  ordered.splice(anchor + 1, 0, activity);
  return ordered;
}
