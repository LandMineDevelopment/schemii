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
