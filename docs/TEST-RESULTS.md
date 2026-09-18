# Test Results

---

## Environment

### Local (control machine)

| Item | Value |
|---|---|
| OS | macOS 15 (Darwin 25.6.0, arm64) |
| Python | 3.12.13 |
| ansible-core | 2.16.19 |
| amazon.aws collection | 11.4.0 |
| community.aws collection | 9.3.0 |
| pytest | 9.1.1 |
| Terraform | v1.16.1 |

### Live AWS target

| Item | Value |
|---|---|
| AWS Account | 111111111111 |
| Region | us-east-1 |
| EC2 Instance | i-0aaabbbccc111ue1 (`acquired-co-a-prod-01`) |
| AMI / OS | Amazon Linux 2 |
| Instance type | t3.micro |
| SSM Agent | 3.3.4624.0 |
| Python on target | 3.8 (installed via `amazon-linux-extras`) |
| Connection | `amazon.aws.aws_ssm` via S3 bucket `disk-monitoring-ssm-acquired-co-a-ue1` |

---

## Unit test suite — 15/15 passing

```
tests/test_alarm_rule_generation.py::test_single_instance_single_region        PASSED
tests/test_alarm_rule_generation.py::test_multiple_instances_same_account       PASSED
tests/test_alarm_rule_generation.py::test_multiple_accounts_same_region         PASSED
tests/test_alarm_rule_generation.py::test_region_filtering                      PASSED
tests/test_alarm_rule_generation.py::test_empty_region                          PASSED
tests/test_alarm_rule_generation.py::test_alarm_name_format                     PASSED
tests/test_alarm_rule_generation.py::test_metric_normalization                  PASSED
tests/test_alarm_rule_generation.py::test_no_static_credentials                 PASSED
tests/test_alarm_rule_generation.py::test_inventory_uses_placement_region       PASSED
tests/test_alarm_rule_generation.py::test_correct_ansible_module_namespace      PASSED
tests/test_alarm_rule_generation.py::test_cw_agent_config_renders_valid_json    PASSED
tests/test_alarm_rule_generation.py::test_treat_missing_data_breaching          PASSED
tests/test_alarm_rule_generation.py::test_ssm_bootstrap_uses_explicit_sts       PASSED
tests/test_alarm_rule_generation.py::test_cross_account_alarm_uses_metrics_account_id  PASSED
tests/test_alarm_rule_generation.py::test_ssm_connection_plugin_canonical_namespace    PASSED

15 passed in 0.06s
```

---

## Live playbook run — `ansible-playbook playbooks/site.yml`

**Run date:** 2026-09-18  
**Result:** All 3 plays passed, 0 failures, 0 unreachable

```
PLAY RECAP
localhost      : ok=6    changed=3    failed=0    skipped=2    rescued=0    ignored=0
acquired-co-a-prod-01  : ok=13   changed=1    failed=0    skipped=7    rescued=0    ignored=0
```

### Play 1 — ssm_bootstrap  ✅ PASS

| Task | Result |
|---|---|
| Assume role (cross-account) | SKIPPED — single-account mode |
| Check SSM status (single-account) | OK — `PingStatus=Online, Platform=Linux, AgentVersion=3.3.4624.0` |
| Parse SSM response | OK |
| Fail if not reachable | SKIPPED — instance is reachable |
| Report SSM status | OK |

### Play 2 — cloudwatch_agent  ✅ PASS

| Task | Result |
|---|---|
| Gathering Facts | OK — `ansible_os_family=Amazon, architecture=x86_64` |
| Load Windows vars | SKIPPED |
| Set arch and package manager | OK — `cwa_arch=amd64, cwa_pkg_mgr=rpm` |
| Set OS path segment | OK — `cwa_os_path=amazon_linux` |
| Set download URL | OK — `https://amazoncloudwatch-agent.s3.amazonaws.com/amazon_linux/amd64/latest/amazon-cloudwatch-agent.rpm` |
| Download CloudWatch Agent | OK — 69 MB, HTTP 200 (cached 304 on re-runs) |
| Install (rpm) | CHANGED (first run) / OK (idempotent re-run) — `amazon-cloudwatch-agent-1.300072.0b1766-1` |
| Render agent config | CHANGED — written to `/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json` |
| Apply config and start agent | OK — `Configuration validation second phase succeeded` |
| Ensure service running/enabled | OK — `ActiveState=active, SubState=running, UnitFileState=enabled` |

### Play 3 — disk_alerting  ✅ PASS

| Task | Result |
|---|---|
| Collect unique regions | OK — `["us-east-1"]` |
| Ensure SNS topic | OK — `arn:aws:sns:us-east-1:111111111111:disk-monitoring-alerts` |
| Critical alarm (cross-account path) | CHANGED — created |
| Warning alarm (cross-account path) | CHANGED — created |
| Build composite rule | OK — `ALARM("disk-critical-111111111111-acquired-co-a-prod-01")` |
| Create composite alarm (AWS CLI) | CHANGED — created |

---

## AWS resources confirmed post-run

```
aws cloudwatch describe-alarms --region us-east-1
```

| Alarm name | Type | State | Threshold | Operator |
|---|---|---|---|---|
| `disk-critical-111111111111-acquired-co-a-prod-01` | Metric | ALARM* | 85% | GreaterThanOrEqualToThreshold |
| `disk-warning-111111111111-acquired-co-a-prod-01` | Metric | ALARM* | 75% | GreaterThanOrEqualToThreshold |
| `fleet-disk-critical-us-east-1` | Composite | ALARM* | — | `ALARM(disk-critical-…)` |

\* State=ALARM is expected: `treat_missing_data=breaching` fires when no datapoints
have arrived yet (CloudWatch agent has not sent its first metric batch).
Once the agent sends disk metrics, alarm state will reflect actual disk usage.

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
| Real SSM connectivity (PingStatus=Online) | Live AWS run | ✅ PASS |
| CloudWatch Agent install on real EC2 (Amazon Linux 2) | Live AWS run | ✅ PASS |
| CloudWatch Agent config applied and service running | Live AWS run | ✅ PASS |
| CloudWatch metric alarms created in real account | Live AWS run | ✅ PASS |
| Composite alarm created in real account | Live AWS run | ✅ PASS |
| OAM cross-account metric visibility | — | ⚠️ SINGLE-ACCOUNT only (no second account available) |
| Windows scheduled task on real EC2 | — | ⚠️ NOT TESTED (no Windows instance available) |
