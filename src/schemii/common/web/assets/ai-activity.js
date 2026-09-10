import { createIconButton } from "./ui.js";

/** One activity card for both assistants. Updates preserve animation and disclosure state. */
export function renderAiActivity(run, { existing, workingLabel = "Working with this workspace", onStop } = {}) {
  const card = existing || document.createElement("details");
  if (!existing) {
    card.open = true;
    const summary = document.createElement("summary");
    const dots = document.createElement("span"); dots.className = "ai-progress-grid"; dots.setAttribute("aria-hidden", "true");
    for (let index = 0; index < 25; index += 1) dots.append(document.createElement("i"));
    const title = document.createElement("strong"); title.className = "ai-run-title";
    const elapsed = document.createElement("time"); elapsed.className = "ai-run-time";
    summary.append(dots, title, elapsed);
    const steps = document.createElement("div"); steps.className = "ai-run-steps";
    card.append(summary, steps);
  }
  const running = run.state === "working";
  card.className = `ai-run ${run.state}`;
  const title = card.querySelector(".ai-run-title");
  title.textContent = running ? workingLabel : ({ waiting_approval: "Waiting for action approval", failed: "Turn failed", cancelled: "Turn stopped" }[run.state] || "Response ready");
  title.classList.toggle("shimmer", running);
  const started = typeof run.startedAt === "number" ? run.startedAt : Date.parse(run.startedAt);
  if (running) delete card.dataset.finishedAt;
  else if (!card.dataset.finishedAt) card.dataset.finishedAt = String(Date.now());
  const ended = run.finishedAt ? (typeof run.finishedAt === "number" ? run.finishedAt : Date.parse(run.finishedAt)) : Number(card.dataset.finishedAt || Date.now());
  card.querySelector(".ai-run-time").textContent = `${Number.isFinite(started) ? Math.max(0, Math.round((ended - started) / 1000)) : 0}s`;
  const stages = run.stages instanceof Map ? [...run.stages.values()] : [...(run.stages || [])];
  if (!stages.length) stages.push({ label: "Starting assistant", state: running ? "running" : run.state });
  const signature = JSON.stringify([stages, run.error, running && run.turnId]);
  if (card.dataset.stages !== signature) {
    card.dataset.stages = signature;
    const steps = card.querySelector(".ai-run-steps"), rows = [];
    for (const stage of stages) {
      const row = document.createElement("div"); row.className = `ai-run-step ${stage.state}`;
      const marker = document.createElement("span"); marker.className = "ai-run-step-marker";
      const copy = document.createElement("span"); copy.className = "ai-run-step-copy"; copy.textContent = stage.label;
      row.append(marker, copy); rows.push(row);
    }
    if (run.error) { const error = document.createElement("p"); error.className = "ai-run-error"; error.textContent = run.error; rows.push(error); }
    if (running && run.turnId) {
      const stop = createIconButton({ icon: "stop", label: "Stop assistant turn", className: "compact danger ai-run-stop" });
      stop.dataset.cancelTurn = run.turnId; if (onStop) stop.onclick = onStop; rows.push(stop);
    }
    steps.replaceChildren(...rows);
  }
  return card;
}
