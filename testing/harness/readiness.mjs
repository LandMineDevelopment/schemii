/**
 * A wave's pages must not enter their assigned products until the fleet-wide
 * logout/relogin proof has finished. Product pages can start authenticated
 * streams as soon as they load.
 */
export async function openProductsAfterIsolation(lanes, { parkLanes, proveIsolation, navigateLane }) {
  await parkLanes();
  await proveIsolation();
  for (const lane of lanes) await navigateLane(lane.id);
}

export function assertProductNavigationAllowed(target, isolationProven, baseURL) {
  const pathname = decodeURIComponent(new URL(target, baseURL).pathname);
  const productRoute = pathname === '/' || ['/schemoo', '/schemer'].some(route =>
    pathname === route || pathname.startsWith(`${route}/`));
  if (productRoute && !isolationProven) {
    throw new Error('Product navigation requires a completed isolation proof');
  }
}

export function assertLaneReadyToClaim(lane) {
  if (lane.status !== 'ready') throw new Error('Only a preflight-ready lane can be claimed.');
}

/**
 * A failed fleet-wide recovery invalidates every browser session touched by
 * that proof. Persist blocked states even if closing one browser reports an
 * error, so no peer can be claimed from a manifest that still says ready.
 */
export async function recoverFleetFailClosed({
  recover, lanes, affectedLaneIds, messageFor, closeFleet, invalidateLane, onFailure, persist,
}) {
  try {
    return await recover();
  } catch (error) {
    const message = messageFor(error);
    const ids = typeof affectedLaneIds === 'function' ? affectedLaneIds() : affectedLaneIds;
    const affected = new Set(ids);
    let cleanupError = null;
    try {
      await closeFleet();
    } catch (failure) {
      cleanupError = failure;
    }

    for (const lane of lanes) {
      if (!affected.has(lane.id)) continue;
      invalidateLane(lane);
      lane.status = 'blocked';
      lane.error = message;
    }
    onFailure({ message, cleanupError });
    await persist();
    throw new Error(message);
  }
}
