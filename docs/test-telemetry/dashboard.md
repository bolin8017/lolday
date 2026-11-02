# Test execution telemetry dashboard

_Last updated: 2026-11-02 (regenerated weekly by `.github/workflows/test-telemetry.yml`)._

Total tests tracked: **17**.

## Slow tests (top 30 by P99)

| Test | P50 (s) | P95 (s) | P99 (s) | Runs |
| --- | ---: | ---: | ---: | ---: |
| `tests.heavy.mlflow.test_real_mlflow_lifecycle::test_full_run_lifecycle` | 30.68 | 33.64 | 34.51 | 7 |
| `tests.heavy.mlflow.test_acl_real_multi_user::test_mlflow_admin_unscoped_search_sees_all` | 1.40 | 32.05 | 33.25 | 7 |
| `tests.heavy.mlflow.test_acl_real_multi_user::test_mlflow_user_filter_restricts_to_owner` | 20.79 | 28.46 | 28.64 | 7 |
| `tests.heavy.postgres.test_migrations_real_pg::test_each_revision_round_trips` | 4.88 | 17.50 | 19.41 | 7 |
| `tests.heavy.postgres.test_jobs_concurrent_submit::test_concurrent_submit_assigns_distinct_primary_keys` | 10.85 | 17.94 | 18.38 | 7 |
| `tests.heavy.postgres.test_audit_log_durability::test_audit_log_jsonb_roundtrip` | 0.72 | 15.63 | 17.55 | 7 |
| `tests.heavy.postgres.test_audit_log_durability::test_audit_log_concurrent_writes_both_persist` | 1.07 | 15.88 | 16.20 | 7 |
| `tests.heavy.auth.test_concurrent_get_or_create::test_concurrent_get_or_create_resolves_to_single_user` | 1.98 | 12.72 | 15.85 | 7 |
| `tests.heavy.postgres.test_migrations_real_pg::test_downgrade_fully_reverts_schema` | 0.88 | 11.81 | 15.37 | 7 |
| `tests.heavy.postgres.test_migrations_real_pg::test_upgrade_to_head_then_downgrade_to_base` | 1.01 | 13.71 | 15.01 | 7 |
| `tests.heavy.postgres.test_jobs_concurrent_submit::test_concurrent_submit_preserves_submitted_at_order` | 0.47 | 13.73 | 14.70 | 7 |
| `tests.heavy.postgres.test_smoke::test_real_pg_session_returns_one` | 7.74 | 12.46 | 13.90 | 7 |
| `tests.heavy.postgres.test_migrations_real_pg::test_upgrade_head_is_idempotent` | 0.49 | 8.25 | 10.58 | 7 |
| `tests.heavy.postgres.test_audit_log_durability::test_audit_log_rollback_takes_row_with_it` | 0.76 | 7.38 | 9.19 | 7 |
| `tests.heavy.auth.test_jwks_reflector::test_jwks_client_cache_holds_back_to_back` | 0.48 | 0.85 | 0.88 | 7 |
| `tests.heavy.auth.test_jwks_reflector::test_jwks_client_refreshes_after_explicit_invalidation` | 0.61 | 0.65 | 0.66 | 7 |
| `tests.heavy.auth.test_jwks_reflector::test_jwks_client_verifies_signed_jwt_against_reflector` | 0.35 | 0.49 | 0.52 | 7 |

## Flaky candidates (failure rate > 1%)

None this week. ✓

## Slow-tier warnings (P99 > 30s)

- `tests.heavy.mlflow.test_real_mlflow_lifecycle::test_full_run_lifecycle` — P99 = 34.5s
- `tests.heavy.mlflow.test_acl_real_multi_user::test_mlflow_admin_unscoped_search_sees_all` — P99 = 33.2s
