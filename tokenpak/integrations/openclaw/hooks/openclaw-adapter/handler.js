// SPDX-License-Identifier: Apache-2.0
//
// openclaw-adapter handler.js — Path C session-binding hook.
//
// Per OAS-02 (initiative 2026-04-28-openclaw-adapter-session-binding) and
// PHASE-A-MEMO.md: OpenClaw v2026.3.23-2 has no outbound-request mutation
// hook surface. Instead, this hook subscribes to message-level events and
// writes the active session UUID to ~/.openclaw/sessions/active.json. The
// tokenpak proxy reads that file when traffic arrives with
// User-Agent: openclaw* and uses the UUID as journal.db.session_id.
//
// Safeguards (Kevin's non-negotiables, 2026-04-28):
//   1. Atomic file write: tmp.<pid>.<ts> → fs.renameSync to final
//   2. JSON schema validation: UUID regex check before write
//   3. File permissions 0600 (owner-only) immediately after rename
//   4. Stale-TTL: handler does NOT enforce; OAS-11 proxy reader does
//   5. Malformed/missing fallback: validation fail → silent no-op
//   6. Multi-session race documented (last write wins; OK for typical
//      single-active-Telegram-conversation pattern)
//   7. Telemetry attribution_source flag: set by proxy (OAS-11), not here
//
// Hook event subscriptions:
//   - message:received — PRIMARY (fires before LLM dispatch)
//   - message:sent     — SECONDARY (keeps last_event_ts fresh)
//
// Author: tokenpak <hello@tokenpak.ai>

const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");

const PLUGIN_NAME = "[openclaw-adapter]";
const SCHEMA_VERSION = "1.0";
const TARGET_DIR = path.join(os.homedir(), ".openclaw", "sessions");
const TARGET_FILE = path.join(TARGET_DIR, "active.json");
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

function writeActiveJson(sessionUuid, eventType) {
  try {
    if (typeof sessionUuid !== "string" || !UUID_RE.test(sessionUuid)) {
      // Sanity check failed — refuse to write
      return;
    }
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
    fs.renameSync(tmpFile, TARGET_FILE); // atomic on same filesystem
    fs.chmodSync(TARGET_FILE, 0o600);

    // Cleanup stray tmp files from prior crashes (older than 60s)
    for (const f of fs.readdirSync(TARGET_DIR)) {
      if (f.startsWith("active.json.tmp.")) {
        try {
          const stat = fs.statSync(path.join(TARGET_DIR, f));
          if (Date.now() - stat.mtimeMs > 60_000) {
            fs.unlinkSync(path.join(TARGET_DIR, f));
          }
        } catch (_) {
          // ignore — file may have been cleaned by another instance
        }
      }
    }
  } catch (e) {
    console.warn(`${PLUGIN_NAME} active.json write failed:`, e?.message);
  }
}

/**
 * OpenClaw managed-hook handler. Subscribes to message:received and
 * message:sent events; writes the active session UUID to active.json
 * for the tokenpak proxy to consume.
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
