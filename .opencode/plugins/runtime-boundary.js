const COMMAND_BOUNDARY = String.raw`(?:^|(?:&&|\|\||[;|\n])\s*)`
const WRAPPERS = String.raw`(?:(?:sudo|nohup)\s+)*(?:env\s+)?(?:[A-Za-z_][A-Za-z0-9_]*=(?:"[^"]*"|'[^']*'|[^\s]+)\s+)*`

const DIRECT_DOCKER = new RegExp(
  String.raw`${COMMAND_BOUNDARY}${WRAPPERS}(?:\/usr(?:\/local)?\/bin\/)?docker(?:\s|$)`,
)
const DIRECT_UVICORN = new RegExp(
  String.raw`${COMMAND_BOUNDARY}${WRAPPERS}(?:[^\s/]+\/)*uvicorn(?:\s|$)`,
)
const PYTHON_SERVER = new RegExp(
  String.raw`${COMMAND_BOUNDARY}${WRAPPERS}(?:[^\s/]+\/)*python(?:3(?:\.\d+)?)?\s+-m\s+(?:uvicorn|http\.server)(?:\s|$)`,
)
const FASTAPI_SERVER = new RegExp(
  String.raw`${COMMAND_BOUNDARY}${WRAPPERS}(?:[^\s/]+\/)*fastapi\s+(?:dev|run)(?:\s|$)`,
)

export function runtimeBoundaryViolation(command) {
  if (typeof command !== "string") return null
  if (command.includes("/var/run/docker.sock") || /DOCKER_HOST\s*=\s*unix:/.test(command)) {
    return "Direct Docker socket access is forbidden in this repository."
  }
  if (DIRECT_DOCKER.test(command)) {
    return "Direct Docker commands are forbidden in this repository."
  }
  if (DIRECT_UVICORN.test(command) || PYTHON_SERVER.test(command) || FASTAPI_SERVER.test(command)) {
    return "Direct development servers are forbidden in this repository."
  }
  return null
}

export const RuntimeBoundary = async () => ({
  "tool.execute.before": async (input, output) => {
    if (input.tool !== "bash") return
    const violation = runtimeBoundaryViolation(output.args?.command)
    if (!violation) return
    throw new Error(`${violation} Use ./start.sh; if it fails, report that failure without substituting another runtime, port, or protocol.`)
  },
})

export default RuntimeBoundary
