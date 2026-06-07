/**
 * OpenClaw Adapter — active.json writer (Path C).
 *
 * Subscribes to message:received (PRIMARY) and message:sent (keeps last_event_ts
 * fresh during long conversations). On each event with a sessionKey, atomically
 * writes ~/.openclaw/sessions/active.json so the tokenpak proxy can bind the
 * outbound LLM request (User-Agent: openclaw*) to the OpenClaw session UUID.
 *
 * Why Path C: OpenClaw v2026.3.23-2 has no outbound-request mutation hook
 * surface (PHASE-A-MEMO.md, 2026-04-28). The message-level hooks are the only
 * available rendezvous point; the proxy reads active.json on the receiving end
 * Validation, staleness, and TTL enforcement live in the proxy.
 *
 * Never throws — all error paths fall through to console.warn so the host
 * gateway is unaffected by file-system failures.
 *
 * Source of truth: ~/vault/01_PROJECTS/tokenpak/initiatives/
 *   2026-04-28-openclaw-adapter-session-binding/03-SPEC.md §Component 1.
 */

const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");

const PLUGIN_NAME = "[openclaw-adapter]";
const SCHEMA_VERSION = "1.0";
const TARGET_DIR = path.join(os.homedir(), ".openclaw", "sessions");
const TARGET_FILE = path.join(TARGET_DIR, "active.json");
const TMP_PREFIX = "active.json.tmp.";
const TMP_STALE_MS = 60_000;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

let eventCounter = 0;

function readPriorCount() {
  try {
    const prior = JSON.parse(fs.readFileSync(TARGET_FILE, "utf8"));
    return Number.isInteger(prior.event_count) ? prior.event_count : 0;
  } catch (_) {
    return 0;
  }
}

function cleanupStaleTmp() {
  try {
    for (const f of fs.readdirSync(TARGET_DIR)) {
      if (!f.startsWith(TMP_PREFIX)) continue;
      const full = path.join(TARGET_DIR, f);
      try {
        const stat = fs.statSync(full);
        if (Date.now() - stat.mtimeMs > TMP_STALE_MS) {
          fs.unlinkSync(full);
        }
      } catch (_) {
        // ignore individual stat/unlink failures; best-effort cleanup
      }
    }
  } catch (_) {
    // dir may not exist yet on first call; nothing to clean
  }
}

function writeActiveJson(sessionUuid, eventType) {
  try {
    if (typeof sessionUuid !== "string" || !UUID_RE.test(sessionUuid)) return;
    fs.mkdirSync(TARGET_DIR, { recursive: true, mode: 0o700 });
    if (eventCounter === 0) eventCounter = readPriorCount();
    eventCounter += 1;
    const payload = {
      schema_version: SCHEMA_VERSION,
      session_uuid: sessionUuid,
      last_event: eventType,
      last_event_ts: Math.floor(Date.now() / 1000),
      event_count: eventCounter,
      agent: process.env.OPENCLAW_AGENT_NAME || os.hostname(),
    };
    const tmpFile = `${TARGET_FILE}.tmp.${process.pid}.${Date.now()}`;
    fs.writeFileSync(tmpFile, JSON.stringify(payload, null, 2), { mode: 0o600 });
    fs.renameSync(tmpFile, TARGET_FILE);
    fs.chmodSync(TARGET_FILE, 0o600);
    cleanupStaleTmp();
  } catch (e) {
    console.warn(`${PLUGIN_NAME} active.json write failed:`, e?.message);
  }
}

/**
 * OpenClaw hook entrypoint. Filters to message:received / message:sent, then
 * delegates to writeActiveJson. Wrapped in try/catch so a malformed event can
 * never propagate.
 *
 * @param {{type?: string, action?: string, sessionKey?: string}} event
 * @returns {Promise<void>}
 */
const handler = async (event) => {
  try {
    const { type, action, sessionKey } = event || {};
    if (type !== "message") return;
    if (action !== "received" && action !== "sent") return;
    if (!sessionKey) return;
    writeActiveJson(sessionKey, `${type}:${action}`);
  } catch (e) {
    console.warn(`${PLUGIN_NAME} handler error:`, e?.message);
  }
};

module.exports = handler;
module.exports.default = handler;
