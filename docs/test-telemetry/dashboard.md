# Test execution telemetry dashboard

_Last updated: 2026-10-05 (regenerated weekly by `.github/workflows/test-telemetry.yml`)._

Total tests tracked: **17**.

## Slow tests (top 30 by P99)

| Test | P50 (s) | P95 (s) | P99 (s) | Runs |
| --- | ---: | ---: | ---: | ---: |
| `tests.heavy.mlflow.test_acl_real_multi_user::test_mlflow_admin_unscoped_search_sees_all` | 2.00 | 37.52 | 39.28 | 7 |
| `tests.heavy.mlflow.test_acl_real_multi_user::test_mlflow_user_filter_restricts_to_owner` | 24.38 | 31.73 | 31.81 | 7 |
| `tests.heavy.mlflow.test_real_mlflow_lifecycle::test_full_run_lifecycle` | 26.11 | 30.87 | 31.00 | 7 |
| `tests.heavy.postgres.test_migrations_real_pg::test_downgrade_fully_reverts_schema` | 0.89 | 16.47 | 18.83 | 7 |
| `tests.heavy.postgres.test_audit_log_durability::test_audit_log_jsonb_roundtrip` | 0.67 | 16.13 | 18.50 | 7 |
| `tests.heavy.auth.test_concurrent_get_or_create::test_concurrent_get_or_create_resolves_to_single_user` | 7.57 | 15.11 | 17.02 | 7 |
| `tests.heavy.postgres.test_migrations_real_pg::test_each_revision_round_trips` | 2.41 | 12.91 | 13.05 | 7 |
| `tests.heavy.postgres.test_audit_log_durability::test_audit_log_concurrent_writes_both_persist` | 2.98 | 11.29 | 11.62 | 7 |
| `tests.heavy.postgres.test_jobs_concurrent_submit::test_concurrent_submit_preserves_submitted_at_order` | 3.47 | 10.96 | 11.56 | 7 |
| `tests.heavy.postgres.test_audit_log_durability::test_audit_log_rollback_takes_row_with_it` | 0.67 | 10.95 | 11.19 | 7 |
| `tests.heavy.postgres.test_jobs_concurrent_submit::test_concurrent_submit_assigns_distinct_primary_keys` | 1.71 | 8.92 | 10.58 | 7 |
| `tests.heavy.postgres.test_migrations_real_pg::test_upgrade_head_is_idempotent` | 1.02 | 10.47 | 10.54 | 7 |
| `tests.heavy.postgres.test_smoke::test_real_pg_session_returns_one` | 0.95 | 8.01 | 8.08 | 7 |
| `tests.heavy.postgres.test_migrations_real_pg::test_upgrade_to_head_then_downgrade_to_base` | 0.72 | 2.91 | 3.52 | 7 |
| `tests.heavy.auth.test_jwks_reflector::test_jwks_client_cache_holds_back_to_back` | 0.94 | 1.41 | 1.48 | 7 |
| `tests.heavy.auth.test_jwks_reflector::test_jwks_client_refreshes_after_explicit_invalidation` | 0.47 | 1.31 | 1.48 | 7 |
| `tests.heavy.auth.test_jwks_reflector::test_jwks_client_verifies_signed_jwt_against_reflector` | 0.56 | 0.68 | 0.70 | 7 |

## Flaky candidates (failure rate > 1%)

None this week. ✓

## Slow-tier warnings (P99 > 30s)

- `tests.heavy.mlflow.test_acl_real_multi_user::test_mlflow_admin_unscoped_search_sees_all` — P99 = 39.3s
- `tests.heavy.mlflow.test_acl_real_multi_user::test_mlflow_user_filter_restricts_to_owner` — P99 = 31.8s
- `tests.heavy.mlflow.test_real_mlflow_lifecycle::test_full_run_lifecycle` — P99 = 31.0s
