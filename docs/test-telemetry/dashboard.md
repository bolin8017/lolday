# Test execution telemetry dashboard

_Last updated: 2026-11-09 (regenerated weekly by `.github/workflows/test-telemetry.yml`)._

Total tests tracked: **17**.

## Slow tests (top 30 by P99)

| Test | P50 (s) | P95 (s) | P99 (s) | Runs |
| --- | ---: | ---: | ---: | ---: |
| `tests.heavy.mlflow.test_real_mlflow_lifecycle::test_full_run_lifecycle` | 25.17 | 34.83 | 36.19 | 7 |
| `tests.heavy.mlflow.test_acl_real_multi_user::test_mlflow_admin_unscoped_search_sees_all` | 28.44 | 35.71 | 35.93 | 7 |
| `tests.heavy.mlflow.test_acl_real_multi_user::test_mlflow_user_filter_restricts_to_owner` | 1.11 | 21.34 | 27.94 | 7 |
| `tests.heavy.postgres.test_migrations_real_pg::test_each_revision_round_trips` | 1.58 | 17.74 | 18.94 | 7 |
| `tests.heavy.postgres.test_audit_log_durability::test_audit_log_concurrent_writes_both_persist` | 0.90 | 17.31 | 17.56 | 7 |
| `tests.heavy.postgres.test_jobs_concurrent_submit::test_concurrent_submit_preserves_submitted_at_order` | 3.20 | 14.07 | 16.16 | 7 |
| `tests.heavy.postgres.test_migrations_real_pg::test_downgrade_fully_reverts_schema` | 0.67 | 12.13 | 15.87 | 7 |
| `tests.heavy.postgres.test_jobs_concurrent_submit::test_concurrent_submit_assigns_distinct_primary_keys` | 1.66 | 12.70 | 15.65 | 7 |
| `tests.heavy.postgres.test_migrations_real_pg::test_upgrade_head_is_idempotent` | 2.69 | 14.32 | 15.62 | 7 |
| `tests.heavy.auth.test_concurrent_get_or_create::test_concurrent_get_or_create_resolves_to_single_user` | 7.91 | 13.72 | 14.41 | 7 |
| `tests.heavy.postgres.test_audit_log_durability::test_audit_log_rollback_takes_row_with_it` | 7.95 | 12.48 | 12.59 | 7 |
| `tests.heavy.postgres.test_migrations_real_pg::test_upgrade_to_head_then_downgrade_to_base` | 0.58 | 9.56 | 11.93 | 7 |
| `tests.heavy.postgres.test_smoke::test_real_pg_session_returns_one` | 1.40 | 7.32 | 8.76 | 7 |
| `tests.heavy.postgres.test_audit_log_durability::test_audit_log_jsonb_roundtrip` | 0.59 | 2.35 | 2.53 | 7 |
| `tests.heavy.auth.test_jwks_reflector::test_jwks_client_verifies_signed_jwt_against_reflector` | 0.55 | 2.05 | 2.47 | 7 |
| `tests.heavy.auth.test_jwks_reflector::test_jwks_client_refreshes_after_explicit_invalidation` | 0.45 | 2.25 | 2.47 | 7 |
| `tests.heavy.auth.test_jwks_reflector::test_jwks_client_cache_holds_back_to_back` | 0.34 | 1.42 | 1.72 | 7 |

## Flaky candidates (failure rate > 1%)

None this week. ✓

## Slow-tier warnings (P99 > 30s)

- `tests.heavy.mlflow.test_real_mlflow_lifecycle::test_full_run_lifecycle` — P99 = 36.2s
- `tests.heavy.mlflow.test_acl_real_multi_user::test_mlflow_admin_unscoped_search_sees_all` — P99 = 35.9s
