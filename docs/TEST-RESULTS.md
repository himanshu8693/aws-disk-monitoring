# Test Results

**No real AWS accounts were used.** All validation is local.

---

## Environment

| Item | Value |
|---|---|
| OS | macOS 15 (Darwin 25.6.0, arm64) |
| Python | 3.14.5 |
| pytest | 9.1.1 |
| Terraform | v1.16.1 |

---

## Validation matrix

| What | Method | Result |
|---|---|---|
| Composite alarm rule logic (7 cases) | Python unit tests | ✅ PASS |
| Metric normalization — DiskUsedPercent on both platforms | Static analysis | ✅ PASS |
| Cross-account alarm uses `metrics[].account_id` | Static analysis | ✅ PASS |
| No `AccountId` as alarm metric dimension | Static analysis | ✅ PASS |
| `treat_missing_data: breaching` on critical alarms | Static analysis | ✅ PASS |
| `ssm_bootstrap` uses explicit STS AssumeRole + `no_log` | Static analysis | ✅ PASS |
| Inventory files use `amazon.aws.aws_ssm` (not legacy name) | Static analysis | ✅ PASS |
| Inventory files use `placement.region` (not hardcoded) | Static analysis | ✅ PASS |
| All alarms use `amazon.aws.cloudwatch_metric_alarm` | Static analysis | ✅ PASS |
| Linux CW Agent config renders valid JSON | Jinja2 render + parse | ✅ PASS |
| No static AWS credentials in any source file | grep scan | ✅ PASS |
| Terraform `account-bootstrap` | `terraform validate` | ✅ PASS |
| Terraform `org-oam-sink` | `terraform validate` | ✅ PASS |
| End-to-end alarm flow | Offline Python demo | ✅ PASS |
| Real SSM connectivity | — | ❌ NOT LIVE-TESTED |
| CloudWatch Agent install on real EC2 | — | ❌ NOT LIVE-TESTED |
| OAM cross-account metric visibility | — | ❌ NOT LIVE-TESTED |
| CloudWatch alarm creation in real account | — | ❌ NOT LIVE-TESTED |
| Windows scheduled task on real EC2 | — | ❌ NOT LIVE-TESTED |

---

## Test run

```bash
source .venv/bin/activate
python3 -m pytest tests/test_alarm_rule_generation.py -v
```

```
collected 15 items

tests/test_alarm_rule_generation.py::test_single_instance_single_region PASSED
tests/test_alarm_rule_generation.py::test_multiple_instances_same_account PASSED
tests/test_alarm_rule_generation.py::test_multiple_accounts_same_region PASSED
tests/test_alarm_rule_generation.py::test_region_filtering PASSED
tests/test_alarm_rule_generation.py::test_empty_region PASSED
tests/test_alarm_rule_generation.py::test_alarm_name_format PASSED
tests/test_alarm_rule_generation.py::test_metric_normalization PASSED
tests/test_alarm_rule_generation.py::test_no_static_credentials PASSED
tests/test_alarm_rule_generation.py::test_inventory_uses_placement_region PASSED
tests/test_alarm_rule_generation.py::test_correct_ansible_module_namespace PASSED
tests/test_alarm_rule_generation.py::test_cw_agent_config_renders_valid_json PASSED
tests/test_alarm_rule_generation.py::test_treat_missing_data_breaching PASSED
tests/test_alarm_rule_generation.py::test_ssm_bootstrap_uses_explicit_sts PASSED
tests/test_alarm_rule_generation.py::test_cross_account_alarm_uses_metrics_account_id PASSED
tests/test_alarm_rule_generation.py::test_ssm_connection_plugin_canonical_namespace PASSED

15 passed in 0.07s
```
