# Test execution telemetry dashboard

_Last updated: 2026-05-18 (regenerated weekly by `.github/workflows/test-telemetry.yml`)._

Total tests tracked: **990**.

## Slow tests (top 30 by P99)

| Test | P50 (s) | P95 (s) | P99 (s) | Runs |
| --- | ---: | ---: | ---: | ---: |
| `tests.contract.openapi.test_schemathesis_detectors::test_detectors_endpoints_match_schema` | 98.07 | 106.24 | 107.43 | 11 |
| `tests.contract.openapi.test_schemathesis_jobs::test_jobs_endpoints_match_schema` | 69.09 | 73.93 | 75.81 | 11 |
| `tests.heavy.mlflow.test_real_mlflow_lifecycle::test_full_run_lifecycle` | 29.03 | 39.57 | 40.79 | 5 |
| `tests.heavy.mlflow.test_acl_real_multi_user::test_mlflow_admin_unscoped_search_sees_all` | 25.09 | 36.30 | 37.41 | 5 |
| `tests.heavy.mlflow.test_acl_real_multi_user::test_mlflow_user_filter_restricts_to_owner` | 2.22 | 32.66 | 33.94 | 5 |
| `tests.heavy.postgres.test_jobs_concurrent_submit::test_concurrent_submit_assigns_distinct_primary_keys` | 1.62 | 12.44 | 12.64 | 5 |
| `tests.heavy.postgres.test_migrations_real_pg::test_upgrade_to_head_then_downgrade_to_base` | 0.52 | 11.26 | 11.88 | 5 |
| `tests.heavy.postgres.test_migrations_real_pg::test_upgrade_head_is_idempotent` | 0.74 | 11.50 | 11.75 | 5 |
| `tests.heavy.postgres.test_audit_log_durability::test_audit_log_jsonb_roundtrip` | 0.25 | 10.97 | 11.72 | 5 |
| `tests.contract.openapi.test_schemathesis_users_me::test_users_me_endpoints_match_schema` | 7.66 | 11.00 | 11.69 | 11 |
| `tests.heavy.postgres.test_audit_log_durability::test_audit_log_concurrent_writes_both_persist` | 0.68 | 10.27 | 10.27 | 5 |
| `tests.heavy.postgres.test_jobs_concurrent_submit::test_concurrent_submit_preserves_submitted_at_order` | 3.16 | 7.22 | 7.97 | 5 |
| `tests.heavy.postgres.test_audit_log_durability::test_audit_log_rollback_takes_row_with_it` | 0.72 | 6.68 | 7.81 | 5 |
| `tests.heavy.postgres.test_smoke::test_real_pg_session_returns_one` | 6.14 | 7.64 | 7.71 | 5 |
| `tests.contract.openapi.test_schemathesis_jobs::test_jobs_endpoints_match_schema[POST /api/v1/jobs]` | 6.80 | 7.41 | 7.58 | 11 |
| `tests.heavy.postgres.test_migrations_real_pg::test_downgrade_fully_reverts_schema` | 0.49 | 6.00 | 7.09 | 5 |
| `tests.contract.openapi.test_schemathesis_jobs::test_jobs_endpoints_match_schema[GET /api/v1/jobs]` | 5.42 | 5.94 | 6.24 | 11 |
| `tests.contract.openapi.test_schemathesis_users_me::test_users_me_endpoints_match_schema[PATCH /api/v1/users/me]` | 3.44 | 5.02 | 5.56 | 11 |
| `tests.contract.openapi.test_schemathesis_jobs::test_jobs_endpoints_match_schema[GET /api/v1/jobs/{job_id}/events]` | 4.86 | 5.29 | 5.32 | 11 |
| `tests.contract.openapi.test_schemathesis_detectors::test_detectors_endpoints_match_schema[GET /api/v1/detectors]` | 4.50 | 5.11 | 5.13 | 11 |
| `tests.contract.openapi.test_schemathesis_detectors::test_detectors_endpoints_match_schema[POST /api/v1/detectors/{detector_id}/builds]` | 3.97 | 4.86 | 4.96 | 11 |
| `tests.contract.openapi.test_schemathesis_detectors::test_detectors_endpoints_match_schema[POST /api/v1/detectors]` | 4.03 | 4.71 | 4.82 | 11 |
| `tests.contract.openapi.test_schemathesis_detectors::test_detectors_endpoints_match_schema[GET /api/v1/detectors/{detector_id}/builds]` | 3.77 | 4.52 | 4.69 | 11 |
| `tests.contract.openapi.test_schemathesis_detectors::test_detectors_endpoints_match_schema[PATCH /api/v1/detectors/{detector_id}]` | 3.84 | 4.58 | 4.63 | 11 |
| `tests.contract.openapi.test_schemathesis_jobs::test_jobs_endpoints_match_schema[PATCH /api/v1/jobs/{job_id}]` | 3.54 | 4.24 | 4.40 | 11 |
| `tests.contract.openapi.test_schemathesis_detectors::test_detectors_endpoints_match_schema[GET /api/v1/detectors/{detector_id}/versions/{tag}]` | 2.97 | 3.99 | 4.32 | 11 |
| `tests.contract.openapi.test_schemathesis_detectors::test_detectors_endpoints_match_schema[DELETE /api/v1/detectors/{detector_id}/versions/{tag}]` | 3.21 | 4.02 | 4.03 | 11 |
| `tests.contract.openapi.test_schemathesis_detectors::test_detectors_endpoints_match_schema[GET /api/v1/detectors/{detector_id}]` | 3.08 | 3.75 | 3.80 | 11 |
| `tests.contract.openapi.test_schemathesis_jobs::test_jobs_endpoints_match_schema[GET /api/v1/jobs/{job_id}/logs]` | 2.99 | 3.55 | 3.78 | 11 |
| `tests.contract.openapi.test_schemathesis_jobs::test_jobs_endpoints_match_schema[GET /api/v1/jobs/{job_id}/queue-position]` | 2.92 | 3.55 | 3.69 | 11 |

## Flaky candidates (failure rate > 1%)

None this week. ✓

## Slow-tier warnings (P99 > 30s)

- `tests.contract.openapi.test_schemathesis_detectors::test_detectors_endpoints_match_schema` — P99 = 107.4s
- `tests.contract.openapi.test_schemathesis_jobs::test_jobs_endpoints_match_schema` — P99 = 75.8s
- `tests.heavy.mlflow.test_real_mlflow_lifecycle::test_full_run_lifecycle` — P99 = 40.8s
- `tests.heavy.mlflow.test_acl_real_multi_user::test_mlflow_admin_unscoped_search_sees_all` — P99 = 37.4s
- `tests.heavy.mlflow.test_acl_real_multi_user::test_mlflow_user_filter_restricts_to_owner` — P99 = 33.9s
