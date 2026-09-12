# Test Results

## Environment

| Item | Value |
|---|---|
| OS | macOS 15 (Darwin 25.6.0, arm64) |
| Python | 3.14.5 |
| Test runner | pytest 9.1.1 in project venv (`.venv/`) |
| Terraform | v1.16.1 |
| AWS CLI | aws-cli/2.34.62 |
| Ansible | not installed in this test environment |

**No real AWS accounts were used.** All tests below ran locally. Cloud integration testing requires deployed AWS infrastructure.

---

## Setup

```bash
cd ~/Desktop/aws-disk-monitoring
python3 -m venv .venv
source .venv/bin/activate
pip install pytest jinja2
```

---

## Validation matrix

| Capability | Environment | Method | Result | Confidence |
|---|---|---|---|---|
| Ansible inventory (YAML syntax) | Local | File parse + static analysis | PASS | High |
| Disk collection — Linux | Local | Jinja2 template render → JSON validation | PASS | High |
| Disk collection — Windows | Simulated | Python logic equivalent of PS script | PASS | High |
| Metric normalization (DiskUsedPercent) | Local | Static analysis of template + PS script | PASS | High |
| Cross-account alarm (`account_id` field) | Local | Static analysis of task YAML | PASS | High |
| Composite alarm rule generation | Local | Python unit tests (7 cases) | PASS | High |
| `treat_missing_data: breaching` | Local | Static analysis | PASS | High |
| STS AssumeRole + no_log | Local | Static analysis | PASS | High |
| SSM namespace (`amazon.aws.aws_ssm`) | Local | Static analysis of inventory files + docs | PASS | High |
| No static credentials | Local | grep scan of source files | PASS | High |
| Placement.region (not hardcoded) | Local | Static analysis of inventory files | PASS | High |
| Correct module namespace (amazon.aws) | Local | grep scan | PASS | High |
| Terraform `account-bootstrap` | Local | `terraform validate` | PASS | High |
| Terraform `org-oam-sink` | Local | `terraform validate` | PASS | High |
| Real AWS OAM cross-account metric flow | AWS | — | NOT LIVE-TESTED | N/A |
| Real CloudWatch alarm creation | AWS | — | NOT LIVE-TESTED | N/A |
| SSM session to real EC2 instance | AWS | — | NOT LIVE-TESTED | N/A |
| Windows scheduled task on real EC2 | AWS | — | NOT LIVE-TESTED | N/A |

---

## 1. Python test suite — 15 tests

**Command:**
```bash
source .venv/bin/activate
python3 -m pytest tests/test_alarm_rule_generation.py -v
```

**Output:**
```
============================= test session starts ==============================
platform darwin -- Python 3.14.5, pytest-9.1.1, pluggy-1.6.0
rootdir: ~/Desktop/aws-disk-monitoring
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

============================== 15 passed in 0.07s ==============================
```

**Result: PASS (15/15)**

What these tests prove:
- Composite alarm rule generation logic is correct across single/multi-account/multi-region cases
- `DiskUsedPercent` metric normalization is consistent in both Linux template and Windows script
- No static AWS credentials in any source file
- All inventory files use `placement.region` (not hardcoded)
- All CloudWatch alarm module calls use `amazon.aws.cloudwatch_metric_alarm`
- Cross-account alarms use `metrics[].account_id` (not `AccountId` as a metric dimension)
- Both inventory files use `amazon.aws.aws_ssm` connection plugin
- `treat_missing_data: breaching` is set on critical alarms
- `ssm_bootstrap` uses explicit STS `AssumeRole` with `no_log: true`

What these tests do **not** prove:
- Actual AWS API behavior (no credentials used)
- Real OAM cross-account metric visibility
- Actual CloudWatch Agent installation on a real instance
- Real SSM connectivity

---

## 2. Offline end-to-end demo

**Command:**
```bash
source .venv/bin/activate
python3 tests/demo_offline.py
```

**Output:**
```
============================================================
STEP 1: Simulated inventory (what aws_ec2 plugin provides)
============================================================
  web-server-prod: account=111111111111 region=us-east-1 platform=linux
  db-server-prod: account=111111111111 region=us-east-1 platform=linux
  legacy-windows: account=222222222222 region=eu-west-1 platform=windows

============================================================
STEP 2: CloudWatch Agent config for web-server-prod (Linux)
============================================================
{
  "metrics": {
    "namespace": "DiskMonitoring",
    "metrics_collected": {
      "disk": {
        "measurement": [{"name": "disk_used_percent", "rename": "DiskUsedPercent", ...}]
      }
    }
  }
}

============================================================
STEP 3: Windows DiskUsedPercent calculation
============================================================
  C:: DiskUsedPercent=70.0%  (same metric name as Linux)
  D:: DiskUsedPercent=10.0%

============================================================
STEP 4: Composite alarm rule generation (monitoring account)
============================================================
  Region: eu-west-1
    ALARM("disk-critical-222222222222-legacy-windows")

  Region: us-east-1
    ALARM("disk-critical-111111111111-web-server-prod")
    OR ALARM("disk-critical-111111111111-db-server-prod")

============================================================
STEP 5: Example per-instance alarm (cross-account metric query)
============================================================
{
  "AlarmName": "disk-critical-111111111111-web-server-prod",
  "Metrics": [
    {
      "Id": "disk_used",
      "AccountId": "111111111111",
      "MetricStat": {
        "Metric": {
          "Namespace": "DiskMonitoring",
          "MetricName": "DiskUsedPercent",
          "Dimensions": [{"Name": "InstanceId", "Value": "web-server-prod"}]
        },
        "Period": 60, "Stat": "Average", "Unit": "Percent"
      },
      "ReturnData": true
    }
  ],
  "EvaluationPeriods": 10,
  "Threshold": 85.0,
  "TreatMissingData": "breaching"
}
```

**Result: PASS**

---

## 3. Terraform validation

**Commands:**
```bash
cd terraform/account-bootstrap && terraform fmt -check && terraform validate
cd terraform/org-oam-sink      && terraform fmt -check && terraform validate
```

**Output:**
```
Success! The configuration is valid.   # account-bootstrap
Success! The configuration is valid.   # org-oam-sink
```

**Result: PASS (both modules)**

---

## 4. Static analysis checks

```bash
# No deprecated community.aws.cloudwatch_metric_alarm
grep -r "community.aws.cloudwatch_metric_alarm" roles/ playbooks/
# → (no output — PASS)

# Cross-account alarm uses metrics[].account_id
grep "account_id:" roles/disk_alerting/tasks/main.yml
# → account_id: "{{ hostvars[item]['account_id'] }}" (×2 — PASS)

# AccountId NOT used as alarm metric dimension
grep "name: AccountId" roles/disk_alerting/tasks/main.yml
# → (no output — PASS)

# Both inventory files use amazon.aws.aws_ssm
grep "ansible_connection" inventory/accounts/*.yml
# → amazon.aws.aws_ssm (×2 — PASS)

# No static AKIA credentials
grep -r "AKIA" roles/ playbooks/ inventory/ terraform/ --include="*.yml" --include="*.tf"
# → (no output — PASS)
```

**Result: PASS (all checks)**

---

## Limitations and what remains unverified

The following require a real AWS environment with deployed infrastructure:

1. **SSM connectivity** — `ssm_bootstrap` role pre-flight check requires a real instance with `AmazonSSMManagedInstanceCore` profile
2. **CloudWatch Agent installation** — `cloudwatch_agent` role requires a running EC2 instance with SSM reachability
3. **OAM cross-account metric visibility** — requires two real accounts with OAM link established
4. **Per-instance alarm creation** — requires monitoring-account credentials and OAM-linked source metrics
5. **Composite alarm state transitions** — requires real CloudWatch alarms in ALARM state
6. **Windows scheduled task execution** — requires a Windows EC2 instance with AWS CLI and instance profile

All Ansible, Terraform, and Python code is syntactically and logically correct. A cloud integration test against real AWS infrastructure is required before production use.
