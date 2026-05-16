# Test execution telemetry

Auto-regenerated weekly by `.github/workflows/test-telemetry.yml` and
`.github/workflows/mutation.yml`. All files in this directory are
**informational** — none of them is a CI gate; promotion is an operator
decision after telemetry shows stable readings.

## Contents

- `dashboard.md` — the rolling 7-day test health report (slow tests, flaky
  candidates, P99 outliers). Refreshed every Monday 06:30 UTC.
- `mutation-YYYY-MM-DD.md` — per-week mutation-testing run output. The
  mutation workflow opens a tracking issue (label `tech-debt-tests`) when
  any module is below the Phase 4 exit gate (60% killed).

## Reading the dashboard

- **Slow tests (top 30 by P99)** — anything with P99 > 30s is also called
  out separately under "Slow-tier warnings". Move it to the heavy tier
  (`@pytest.mark.heavy`) if it does not fit the fast-tier budget.
- **Flaky candidates** — failure rate > 1% over the last 7 days. The
  `flaky-tracker.yml` workflow auto-opens a tracking issue; the
  `.claude/rules/testing.md` quarantine workflow then governs the 14-day
  fix + 21-day delete SLO.

## Where the data comes from

`scripts/lib/test_telemetry.py` walks every `junit-*` artifact uploaded
by any workflow in the last 7 days (via the GitHub Actions REST API,
filtered by `artifact.created_at`), reads every `testcase` row, and
aggregates per-test stats. There is no persistent DB; the rolling
window is the artifact retention period (90 days for `ubuntu-latest`
public-repo runs).

## Adding a new workflow to telemetry

The aggregator picks up any artifact whose name starts with `junit-`.
A new workflow only has to ship its JUnit XML under that prefix:

```yaml
- name: Upload JUnit
  if: always()
  uses: actions/upload-artifact@<pinned-sha>
  with:
    name: junit-<workflow-name>-${{ github.run_id }}
    path: <path-to-junit.xml>
```

No code change to `scripts/lib/test_telemetry.py` required.
