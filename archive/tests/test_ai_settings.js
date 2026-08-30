const assert = require("node:assert/strict");
const fs = require("node:fs");

const shared = fs.readFileSync("src/schemii/shared_web/ai-assistant.js", "utf8");
const diagnostics = fs.readFileSync("src/schemii/shared_web/error-diagnostics.js", "utf8");
const styles = fs.readFileSync("src/schemii/shared_web/ai-assistant.css", "utf8");
const schemii = fs.readFileSync("src/schemii/web/app.js", "utf8");
const schemiiHtml = fs.readFileSync("src/schemii/web/index.html", "utf8");
const schemer = fs.readFileSync("src/schemii/schemer_web/app.js", "utf8");
const schemerHtml = fs.readFileSync("src/schemii/schemer_web/index.html", "utf8");

assert.match(shared, /request\("\/api\/ai\/settings", \{ method: "GET" \}\)/, "settings must load only from the local settings endpoint");
assert.match(shared, /request\("\/api\/ai\/settings", \{ method: "PUT", body: JSON\.stringify\(\{ expectedRevision: settings\.revision, policy: nextPolicy \}\)/, "settings saves must carry the optimistic revision");
assert.match(shared, /error\.code === "policy_changed"[\s\S]*changed in another tab[\s\S]*refreshed/, "revision conflicts must refresh with stale-tab guidance");
for (const mode of ["disabled", "every_action", "once_per_chat", "automatic"]) assert.match(shared, new RegExp(`${mode}:`), `mode ${mode} must be rendered`);
for (const bound of ["rowsDisclosed", "rowsWritten", "pagesInspected", "rawStatements", "operationTimeoutMs", "agentConcurrency"]) assert.ok(shared.includes(`["${bound}"`), `${bound} must be editable`);
assert.match(shared, /form\.elements\[name\]\.value === "" \? null/, "blank bounds must round-trip as null");
assert.match(shared, /Inherit PostgreSQL/, "null operation timeout must be labeled as inherited PostgreSQL policy");
assert.match(shared, /Transport pagination, response-size limits, and process ceilings still apply separately/, "user bounds must be distinguished from application ceilings");
assert.match(shared, /Configured:.*effective:.*non-relaxable safety floor/, "narrowed automatic modes must explain configured and effective values");
assert.match(shared, /Object\.entries\(settings\.capabilities/, "only server-returned product-supported capabilities may be rendered");
assert.doesNotMatch(shared, /POSTGRES_CAPABILITIES|APP_RESOURCE_CAPABILITIES/, "the browser must not invent unsupported product capabilities");

assert.match(shared, /function hasLocalSettingsAction[\s\S]*allowedLocalErrorAction/, "AI rendering must use the shared action allowlist");
assert.match(diagnostics, /Object\.keys\(action\)\.sort\(\)\.join\(","\) !== "path,type"[\s\S]*open_local_settings[\s\S]*\/api\/ai\/settings/, "local settings navigation must require the exact allowlisted action object");
assert.match(diagnostics, /\["capability_unavailable", "application_limitation"\]\.includes/, "only structured policy errors may expose local settings guidance");
assert.match(shared, /Required capability:/, "structured errors must name the exact missing capability");
assert.match(shared, /function operationError[\s\S]*error\.code = payload\.code[\s\S]*error\.payload = \{ error: payload \}/, "failed operation receipts must preserve structured policy error details");
assert.match(schemii, /if \(!aiAssistant\.renderError\(error\)\) detailAiActionError/, "Schemii proposal failures must use the shared structured error renderer");
assert.doesNotMatch(shared + diagnostics, /location\s*=|location\.href|window\.location/, "model and server action objects must not navigate the page");

assert.match(shared, /function invalidatePendingProposals[\s\S]*\.ai-action-card:not\(\.applied\)[\s\S]*button\.disabled = true[\s\S]*Revoked because AI permissions or limits changed/, "successful saves must visibly revoke pending proposal cards");
assert.match(shared, /state\.settings = updated; onPolicyChange\(updated\); invalidatePendingProposals\(\); renderSettings\(\)/, "successful saves must refresh effective policy before invalidating proposals");
const policyFormStart = shared.indexOf('form.dataset.aiPolicyForm = ""');
const saveHandler = shared.slice(shared.indexOf('form.addEventListener("submit"', policyFormStart), shared.indexOf("function renderSettings"));
assert.doesNotMatch(saveHandler, /messages|sendMessage|\/messages/, "saving settings must not send a model prompt or erase chat history");
assert.match(shared, /Provider credentials[\s\S]*stored separately from permissions/, "provider authentication must be visibly separated from policy");
assert.match(shared, /providerSection\.append\(providerHead, providerBody\)/, "provider controls must remain in their own settings section");
assert.match(shared, /async function loadStatus\(shouldRenderSettings = false\)[\s\S]*if \(shouldRenderSettings \|\| settingsDialog\.open\) renderSettings\(\)/, "status loading must not shadow the settings renderer and leave the modal empty");

const schemiiSession = schemii.slice(schemii.indexOf("buildSessionPayload:"), schemii.indexOf("parseSession:", schemii.indexOf("buildSessionPayload:")));
assert.doesNotMatch(schemiiSession, /approvals|automatic|every_action|once_per_chat/, "Schemii sessions may not submit browser-owned modes");
assert.match(schemii, /syncSchemiiAiPolicy[\s\S]*effectiveMode !== "disabled"[\s\S]*targetCapabilities/, "Schemii sessions must narrow effective policy by target availability");
const schemerSession = schemer.slice(schemer.indexOf("buildSessionPayload:"), schemer.indexOf("parseSession:", schemer.indexOf("buildSessionPayload:")));
assert.doesNotMatch(schemerSession, /configuredMode|effectiveMode|approvals/, "Schemer sessions may submit disclosure and target context but no authority modes");
assert.match(schemerHtml, />Disclosure<select[\s\S]*Disclosure can narrow server-owned permissions but cannot grant capabilities/, "Schemer tiers must be presented as disclosure narrowing, not authority");
assert.doesNotMatch(schemiiHtml, /ai-schema-permission|ai-data-read-permission|ai-write-permission/, "legacy Schemii capability checkboxes must be removed");

for (const html of [schemiiHtml, schemerHtml]) {
  assert.match(html, /data-ai-settings-close[^>]*aria-label=/, "settings close control needs an accessible name");
  assert.match(html, /data-ai-settings-status/, "settings status needs a dedicated live surface");
}
assert.match(shared, /settingsDialog\.addEventListener\("cancel"[\s\S]*closeSettings/, "Escape must close settings through focus restoration");
assert.match(shared, /settingsReturnFocus[\s\S]*target\?\.isConnected[\s\S]*target\.focus/, "settings must restore keyboard focus");
assert.match(shared, /function setNestedDialogOpen[\s\S]*root\.inert = open[\s\S]*root\.setAttribute\("aria-hidden"/, "a nested AI dialog must own focus and hide the outer assistant from assistive technology");
assert.match(shared, /function trapPanelFocus[\s\S]*event\.key !== "Tab"[\s\S]*first[\s\S]*last/, "the AI panel must trap keyboard focus while it owns the modal layer");
assert.match(shared, /if \(panelModal\) root\.addEventListener\("keydown", trapPanelFocus\)/, "only modal AI panels may trap keyboard focus");
assert.match(shared, /event\.key !== "Escape"[\s\S]*settingsDialog\.open \|\| historyDialog\.open \|\| state\.busy[\s\S]*!panelModal && !root\.contains\(document\.activeElement\)[\s\S]*stopPropagation/, "Escape from a non-modal workspace must not close both AI and an underlying surface");
assert.match(schemiiHtml, /class="ai-panel"[^>]*role="complementary"[^>]*aria-labelledby=/, "Schemii AI must be an accessible non-modal companion");
assert.doesNotMatch(schemiiHtml.match(/<aside class="ai-panel"[^>]*>/)?.[0] || "", /aria-modal/, "Schemii AI must not claim false modal semantics");
assert.match(schemerHtml, /class="ai-panel"[^>]*role="dialog"[^>]*aria-modal="true"[^>]*aria-labelledby=/, "Schemer may retain modal drawer semantics");
assert.match(shared, /aria-describedby/, "bound inputs must reference their descriptions");
assert.match(styles, /@media \(max-width: 540px\)[\s\S]*\.ai-policy-capability, \.ai-policy-limits label \{ grid-template-columns: 1fr/, "policy settings must remain usable on mobile");
assert.match(styles, /#ai-settings-dialog \{[^}]*overflow: hidden[\s\S]*\.ai-settings-body \{[^}]*overflow-y: auto/, "AI settings must have one body scroller instead of nested dialog scrollbars");
assert.match(styles, /\.ai-policy-form \{[^}]*grid-template-columns: minmax\(0, 1fr\)[^}]*padding: 0/, "AI policy layout must override the generic two-column dialog form");
assert.match(styles, /\.ai-policy-form fieldset \{[^}]*min-width: 0[^}]*grid-column: 1/, "AI policy fieldsets must stack without overlap or clipping");
assert.match(styles, /@media \(prefers-reduced-motion: reduce\)/, "shared AI UI must retain reduced-motion behavior");

console.log("Shared AI permissions and limits UI contracts passed");
