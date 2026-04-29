/**
 * Smoke test for openclaw-adapter handler.js (Path C, OAS-04).
 *
 * Zero-dependency Node test (built-ins only). Isolates filesystem to
 * os.tmpdir()/openclaw-adapter-test-<pid>/ via process.env.HOME override
 * BEFORE requiring handler.js so the module-load-time os.homedir() resolves
 * to the test sandbox.
 *
 * Each of Kevin's 6 Path C handler safeguards is exercised by at least one
 * case (atomicity, schema validation, perms, missing-key fallback, and the
 * non-target-event filter); plus happy path, monotonic event_count, and
 * throw resistance for the host gateway.
 *
 * Run: `node tests/test-active-json.js` — exit 0 on all-pass, 1 on any fail.
 */

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const TEST_HOME = path.join(os.tmpdir(), `openclaw-adapter-test-${process.pid}`);
process.env.HOME = TEST_HOME;
process.env.OPENCLAW_AGENT_NAME = "trix-test";

const handler = require(path.join(__dirname, "..", "handler.js"));

const ACTIVE = path.join(TEST_HOME, ".openclaw", "sessions", "active.json");
const VALID_UUID = "11111111-2222-3333-4444-555555555555";

function reset() {
  if (fs.existsSync(TEST_HOME)) fs.rmSync(TEST_HOME, { recursive: true, force: true });
}

const cases = [
  {
    name: "happy path — message:received writes well-formed active.json",
    run: async () => {
      reset();
      await handler({ type: "message", action: "received", sessionKey: VALID_UUID });
      assert.ok(fs.existsSync(ACTIVE), "active.json missing");
      const j = JSON.parse(fs.readFileSync(ACTIVE, "utf8"));
      assert.equal(j.schema_version, "1.0");
      assert.equal(j.session_uuid, VALID_UUID);
      assert.equal(j.last_event, "message:received");
      assert.equal(typeof j.last_event_ts, "number");
      assert.equal(j.agent, "trix-test");
      assert.ok(j.event_count >= 1);
    },
  },
  {
    name: "0600 perms — file is owner-only-readable",
    run: async () => {
      reset();
      await handler({ type: "message", action: "received", sessionKey: VALID_UUID });
      const stat = fs.statSync(ACTIVE);
      const mode = stat.mode & 0o777;
      assert.equal(mode, 0o600, `mode is ${mode.toString(8)}, expected 600`);
    },
  },
  {
    name: "schema validation — refuses non-UUID sessionKey",
    run: async () => {
      reset();
      await handler({ type: "message", action: "received", sessionKey: "not-a-uuid" });
      assert.ok(!fs.existsSync(ACTIVE), "active.json should NOT exist when sessionKey is invalid");
    },
  },
  {
    name: "non-message events — no write",
    run: async () => {
      reset();
      await handler({ type: "gateway", action: "startup", sessionKey: VALID_UUID });
      assert.ok(!fs.existsSync(ACTIVE), "active.json should NOT exist for gateway:startup");
    },
  },
  {
    name: "missing sessionKey — graceful no-op",
    run: async () => {
      reset();
      await handler({ type: "message", action: "received" });
      assert.ok(!fs.existsSync(ACTIVE), "active.json should NOT exist when sessionKey absent");
    },
  },
  {
    name: "atomic write — no .tmp.* files left behind on success",
    run: async () => {
      reset();
      await handler({ type: "message", action: "received", sessionKey: VALID_UUID });
      const dir = path.dirname(ACTIVE);
      const stray = fs.readdirSync(dir).filter((f) => f.startsWith("active.json.tmp."));
      assert.equal(stray.length, 0, `stray tmp files: ${stray.join(", ")}`);
    },
  },
  {
    name: "monotonic event_count — second write increments",
    run: async () => {
      reset();
      await handler({ type: "message", action: "received", sessionKey: VALID_UUID });
      const c1 = JSON.parse(fs.readFileSync(ACTIVE, "utf8")).event_count;
      await handler({ type: "message", action: "sent", sessionKey: VALID_UUID });
      const c2 = JSON.parse(fs.readFileSync(ACTIVE, "utf8")).event_count;
      assert.ok(c2 > c1, `expected c2 > c1, got c1=${c1} c2=${c2}`);
    },
  },
  {
    name: "throw resistance — handler doesn't propagate errors",
    run: async () => {
      reset();
      try {
        await handler(null);
        await handler(undefined);
        await handler({});
        await handler({ type: "message" });
      } catch (e) {
        assert.fail(`handler threw: ${e.message}`);
      }
    },
  },
];

(async () => {
  let pass = 0, fail = 0;
  for (const c of cases) {
    try {
      await c.run();
      console.log(`PASS  ${c.name}`);
      pass++;
    } catch (e) {
      console.log(`FAIL  ${c.name} — ${e.message}`);
      fail++;
    }
  }
  reset();
  console.log(`\n${pass} passed, ${fail} failed.`);
  process.exit(fail > 0 ? 1 : 0);
})();
