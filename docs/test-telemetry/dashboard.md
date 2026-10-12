# Test execution telemetry dashboard

_Last updated: 2026-10-12 (regenerated weekly by `.github/workflows/test-telemetry.yml`)._

Total tests tracked: **17**.

## Slow tests (top 30 by P99)

| Test | P50 (s) | P95 (s) | P99 (s) | Runs |
| --- | ---: | ---: | ---: | ---: |
| `tests.heavy.mlflow.test_real_mlflow_lifecycle::test_full_run_lifecycle` | 28.64 | 33.91 | 34.49 | 7 |
| `tests.heavy.mlflow.test_acl_real_multi_user::test_mlflow_user_filter_restricts_to_owner` | 2.20 | 31.69 | 32.60 | 7 |
| `tests.heavy.mlflow.test_acl_real_multi_user::test_mlflow_admin_unscoped_search_sees_all` | 22.08 | 31.45 | 32.02 | 7 |
| `tests.heavy.postgres.test_migrations_real_pg::test_each_revision_round_trips` | 1.39 | 13.53 | 17.61 | 7 |
| `tests.heavy.postgres.test_audit_log_durability::test_audit_log_concurrent_writes_both_persist` | 0.84 | 16.06 | 16.45 | 7 |
| `tests.heavy.postgres.test_jobs_concurrent_submit::test_concurrent_submit_assigns_distinct_primary_keys` | 1.33 | 13.57 | 15.18 | 7 |
| `tests.heavy.auth.test_concurrent_get_or_create::test_concurrent_get_or_create_resolves_to_single_user` | 1.59 | 13.73 | 15.16 | 7 |
| `tests.heavy.postgres.test_migrations_real_pg::test_upgrade_to_head_then_downgrade_to_base` | 0.94 | 11.06 | 14.20 | 7 |
| `tests.heavy.postgres.test_migrations_real_pg::test_upgrade_head_is_idempotent` | 2.09 | 12.41 | 12.82 | 7 |
| `tests.heavy.postgres.test_jobs_concurrent_submit::test_concurrent_submit_preserves_submitted_at_order` | 3.70 | 12.62 | 12.75 | 7 |
| `tests.heavy.postgres.test_migrations_real_pg::test_downgrade_fully_reverts_schema` | 0.53 | 12.10 | 12.45 | 7 |
| `tests.heavy.postgres.test_audit_log_durability::test_audit_log_jsonb_roundtrip` | 0.77 | 9.75 | 12.07 | 7 |
| `tests.heavy.postgres.test_audit_log_durability::test_audit_log_rollback_takes_row_with_it` | 0.71 | 11.73 | 11.98 | 7 |
| `tests.heavy.postgres.test_smoke::test_real_pg_session_returns_one` | 5.78 | 10.37 | 10.70 | 7 |
| `tests.heavy.auth.test_jwks_reflector::test_jwks_client_verifies_signed_jwt_against_reflector` | 0.44 | 1.79 | 2.22 | 7 |
| `tests.heavy.auth.test_jwks_reflector::test_jwks_client_cache_holds_back_to_back` | 0.62 | 1.53 | 1.83 | 7 |
| `tests.heavy.auth.test_jwks_reflector::test_jwks_client_refreshes_after_explicit_invalidation` | 0.52 | 0.78 | 0.83 | 7 |

## Flaky candidates (failure rate > 1%)

None this week. ✓

## Slow-tier warnings (P99 > 30s)

- `tests.heavy.mlflow.test_real_mlflow_lifecycle::test_full_run_lifecycle` — P99 = 34.5s
- `tests.heavy.mlflow.test_acl_real_multi_user::test_mlflow_user_filter_restricts_to_owner` — P99 = 32.6s
- `tests.heavy.mlflow.test_acl_real_multi_user::test_mlflow_admin_unscoped_search_sees_all` — P99 = 32.0s
