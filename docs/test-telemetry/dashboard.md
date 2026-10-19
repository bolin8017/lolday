# Test execution telemetry dashboard

_Last updated: 2026-10-19 (regenerated weekly by `.github/workflows/test-telemetry.yml`)._

Total tests tracked: **17**.

## Slow tests (top 30 by P99)

| Test | P50 (s) | P95 (s) | P99 (s) | Runs |
| --- | ---: | ---: | ---: | ---: |
| `tests.heavy.mlflow.test_acl_real_multi_user::test_mlflow_user_filter_restricts_to_owner` | 1.41 | 35.81 | 35.89 | 7 |
| `tests.heavy.mlflow.test_acl_real_multi_user::test_mlflow_admin_unscoped_search_sees_all` | 27.49 | 31.31 | 31.43 | 7 |
| `tests.heavy.mlflow.test_real_mlflow_lifecycle::test_full_run_lifecycle` | 24.56 | 29.89 | 30.59 | 7 |
| `tests.heavy.postgres.test_jobs_concurrent_submit::test_concurrent_submit_preserves_submitted_at_order` | 0.98 | 11.62 | 12.19 | 7 |
| `tests.heavy.postgres.test_migrations_real_pg::test_upgrade_head_is_idempotent` | 0.81 | 11.94 | 12.13 | 7 |
| `tests.heavy.postgres.test_audit_log_durability::test_audit_log_jsonb_roundtrip` | 0.79 | 11.78 | 12.05 | 7 |
| `tests.heavy.postgres.test_audit_log_durability::test_audit_log_concurrent_writes_both_persist` | 0.76 | 8.97 | 11.74 | 7 |
| `tests.heavy.postgres.test_migrations_real_pg::test_each_revision_round_trips` | 1.48 | 9.21 | 11.74 | 7 |
| `tests.heavy.postgres.test_migrations_real_pg::test_downgrade_fully_reverts_schema` | 1.11 | 11.18 | 11.30 | 7 |
| `tests.heavy.auth.test_concurrent_get_or_create::test_concurrent_get_or_create_resolves_to_single_user` | 9.81 | 11.25 | 11.28 | 7 |
| `tests.heavy.postgres.test_audit_log_durability::test_audit_log_rollback_takes_row_with_it` | 0.74 | 10.78 | 11.12 | 7 |
| `tests.heavy.postgres.test_jobs_concurrent_submit::test_concurrent_submit_assigns_distinct_primary_keys` | 3.85 | 9.32 | 11.07 | 7 |
| `tests.heavy.postgres.test_migrations_real_pg::test_upgrade_to_head_then_downgrade_to_base` | 0.82 | 6.52 | 8.41 | 7 |
| `tests.heavy.postgres.test_smoke::test_real_pg_session_returns_one` | 0.83 | 5.61 | 7.17 | 7 |
| `tests.heavy.auth.test_jwks_reflector::test_jwks_client_refreshes_after_explicit_invalidation` | 0.46 | 0.94 | 1.04 | 7 |
| `tests.heavy.auth.test_jwks_reflector::test_jwks_client_cache_holds_back_to_back` | 0.45 | 0.80 | 0.85 | 7 |
| `tests.heavy.auth.test_jwks_reflector::test_jwks_client_verifies_signed_jwt_against_reflector` | 0.56 | 0.65 | 0.68 | 7 |

## Flaky candidates (failure rate > 1%)

None this week. ✓

## Slow-tier warnings (P99 > 30s)

- `tests.heavy.mlflow.test_acl_real_multi_user::test_mlflow_user_filter_restricts_to_owner` — P99 = 35.9s
- `tests.heavy.mlflow.test_acl_real_multi_user::test_mlflow_admin_unscoped_search_sees_all` — P99 = 31.4s
- `tests.heavy.mlflow.test_real_mlflow_lifecycle::test_full_run_lifecycle` — P99 = 30.6s
