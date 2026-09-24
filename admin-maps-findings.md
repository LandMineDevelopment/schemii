# Administration, account and diagnostics audit

Actual deployed browser audit, 2026-09-23. Desktop Chromium 1440×900; mobile touch Chromium 390×844; header additionally 320×740. Used authorized session without saving any user/role/database/credential mutation. Screenshot files prefixed admin- and maps- belong to this pass. Local working-tree account code is older than deployed admin; screenshots and observations describe deployed app.

## Confirmed issue A — Mobile diagnostics entry picker is positioned above the screen (P1)

Reproduce: open /system-map, /api-map or /db-map at 390×844; tap the Registered route/Gateway operation chooser beside Browse.

Actual: modal's title, search/filter controls, first entries and Close button are above the viewport. The visible portion begins midway down the entries and ends at y473, leaving lower half dark. A touch user cannot access search or the close button. Desktop positions correctly.

Evidence: maps-mobile-picker-viewport.png, maps-mobile-system-map-picker.png, maps-desktop-system-map-picker.png. Metrics in maps-detail-observations.json and admin-final-probes.json: modal x0 y-270.078 width390 height742.719; close button y-257; search y-190. Mobile computed transform matrix(1,0,0,1,0,-371.359), while top101.281 and bottom0. Shared ui.css sets translateY(-50%); mobile system-map.css bottom-sheet override fails to reset it.

Required fix: reset transform for the mobile bottom-sheet placement; keep header/search/Close in viewport and make only entries scroll. Respect visual viewport/keyboard height. Verify 320/390 mobile in all three lenses, opening, searching, selecting and closing, plus unchanged desktop centering.

## Confirmed issue B — Diagnostics typography is too small to read at normal zoom (P2)

Reproduce: /system-map desktop or phone; expand API then Explain this step; optionally Open status source. Also check Internal components lens.

Actual: essential route labels, explanations, metadata and source are drawn in 7–9px text despite ample desktop space. The phone repeats that scale. This makes route selection and understanding inputs/outputs need magnification. Native Browse select also truncates every long lens label (Request journey..., Database operat..., Internal compon...).

Evidence: maps-desktop-internals-expanded.png, maps-mobile-system-map.png, maps-mobile-source-code.png, maps-desktop-source-inspector.png. DOM measurements recorded route sublabels at 7px. CSS directly specifies 7px route descriptions and 9px entry titles. This is an observed usability/style issue, not an assertion that a specific WCAG minimum font-size rule exists.

Required fix: use the readable shared product body/control type scale for diagnostics, allocate enough width/vertical room, and collapse optional technical detail instead of shrinking all text. Make selected lens name readable. Verify at default zoom desktop/mobile and increased browser text size without clipped controls.

## Confirmed issue C — Account/admin mobile header squeezes brand into navigation (P2/P3)

Reproduce: authenticated /account or /admin at 390px and 320px.

Actual: six tiny 9px navigation controls wrap into two rows beside the brand. Flex shrinking gives the brand link a 50.17px box at 320px, although its icon+wordmark require ~97px. The Schemii wordmark extends to x113.36 while navigation starts x86.17, visibly running underneath the lower nav controls. At 390px the same overlap exists to x113 versus nav x99. This header differs from the compact app switcher used elsewhere.

Evidence: admin-small-mobile-header-viewport.png, admin-mobile-users.png, admin-mobile-account.png. Exact child rectangles in maps-source-observations.json; link fonts in admin-final-probes.json.

Required fix: prevent brand shrink; use the shared application switcher or an explicit mobile navigation menu with readable labels/touch targets, while preserving discoverable Account, Administration and Sign out. Verify 320/390px and desktop, all destinations keyboard/touch reachable.

## Coverage completed

Account password form. Admin Users list/search and Add user; existing user Account/Access/AI tabs; role list/new role/existing role Permissions/Members/AI; unsaved Add AI policy editor; empty Database profiles and Add database profile; AI connections shared Codex status and OpenCode Zen credential expansion; Diagnostics links. All at desktop/mobile. System map, API lens and database lens landing and chooser; request/API/internal/database lens expansion; Explain this step; actual source inspector. Native credential upload chooser was opened without selecting a file or changing the installed connection.

## Limitations and non-issues

No installation-owned database profiles exist in this environment, so editing populated profiles, connection testing and assigned-profile states could not be exercised without creating access configuration. No passwords, roles, grants, users, database profiles or provider connections saved/deleted. Fixed admin save bars cover a slice of the scrolling form in full-page screenshots; not reported as a blocking defect because scrolling makes the fields reachable. Editor screenshots can capture sticky elements at the current viewport position; this is separate from the confirmed map modal defect, which also exists in viewport-only screenshots and DOM geometry.
