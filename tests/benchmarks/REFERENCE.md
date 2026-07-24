# TokenPak health benchmark reference profile

Profile ID: `tokenpak-health-reference-v1`

This profile governs absolute V11 `/health` latency and capacity verdicts. A
run that does not satisfy every requirement below is still useful diagnostic
evidence, but its receipt must be labeled informational and must not report an
absolute pass.

## Workload contract

- Traffic is loopback-only and never contacts a model provider.
- A fresh subject process is used for every measured run.
- O3a/O3b, O6a/O6b, warm-up, and V11 retain separate vectors and verdicts.
- O3b ends at the completion of the first valid `/health` response and records
  both launch-to-first-health and listener-to-first-health durations. It does
  not use the V11 readiness barrier.
- V11 begins only after three actively requested valid health responses at
  least 50 ms apart; elapsed sleep is not readiness evidence.
- The warm-up is 20 requests at open-loop 25 requests/s.
- V11 is exactly 500 requests at open-loop 100 requests/s with 20 workers and
  a five-second per-request timeout.
- V11 requires p50 below 15 ms, p99 below 500 ms, zero request errors, at
  least 85 completed requests/s, and zero measured-phase listener drops or
  overflows. These thresholds are invariant across environments.
- O3a/O3b use a 30-second observation window. A timeout is retained as a
  right-censored subject outcome, not replaced or discarded.
- At least five runs per artifact are executed in alternating control/candidate
  order.

## Qualifying environment

- Linux on `x86_64`/`amd64`.
- CPython 3.12 for both controller and subject.
- At least four dedicated logical CPUs in one fixed affinity set.
- At least 8 GiB RAM.
- A content-addressed runtime/container image identity is supplied and retained.
- The runner, suite file, reference file, subject source/tree/distribution, and
  dependency inventory are hash-bound in every receipt. Every non-metadata
  wheel payload member must also match the declared Git commit byte-for-byte,
  and a separately hash-bound build-provenance receipt must name the exact
  artifact hash, source commit, source tree, and wheel payload-manifest hash.
- No CPU steal, CPU throttling, swap-in, or swap-out activity occurs during a
  measured run.
- Host competing load is bounded by the release-validation environment owner;
  process and load snapshots are retained for independent review.
- The load generator meets the 10 ms p99 and 50 ms maximum submit-lag limits.
- All required system/process telemetry and listener counters are available.

The actual CPU model, host image digest, kernel, virtualization/container
state, affinity, power mode, dependency versions, and competing processes are
recorded on every run. Shared CI runners normally do not qualify; they run the
contract/adversarial checks and may produce informational measurements only.

## Machine-readable profile

The governed runner parses this block and binds this file's SHA-256 into every
receipt.

```json tokenpak-health-reference-profile
{
  "profile_id": "tokenpak-health-reference-v1",
  "os": "Linux",
  "machines": ["x86_64", "amd64"],
  "python_major_minor": "3.12",
  "minimum_affinity_cpus": 4,
  "minimum_memory_bytes": 8589934592,
  "loopback_host": "127.0.0.1",
  "fixed_affinity_required": true,
  "runtime_image_digest_required": true,
  "maximum_steal_delta_jiffies": 0,
  "maximum_swap_io_delta_pages": 0,
  "maximum_throttle_delta": 0,
  "minimum_telemetry_samples": 2,
  "submit_lag_p99_ceiling_ms": 10.0,
  "submit_lag_maximum_ceiling_ms": 50.0,
  "v11": {
    "warmup_requests": 20,
    "warmup_rps": 25.0,
    "measured_requests": 500,
    "measured_rps": 100.0,
    "workers": 20,
    "request_timeout_s": 5.0,
    "p50_ceiling_ms": 15.0,
    "p99_ceiling_ms": 500.0,
    "minimum_throughput_rps": 85.0,
    "maximum_request_errors": 0,
    "maximum_listener_drops": 0,
    "maximum_listener_overflows": 0
  }
}
```
