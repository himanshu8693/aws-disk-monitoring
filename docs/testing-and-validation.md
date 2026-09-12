# Testing and validation

## What can be verified without a live AWS account

**Terraform validation:**

```bash
cd terraform/account-bootstrap
terraform init -backend=false && terraform validate

cd ../org-oam-sink
terraform init -backend=false && terraform validate
```

Both should return `Success! The configuration is valid.`

**CloudWatch Agent config template (Linux):**

```python
# uv run --no-project --python 3.12 --with jinja2 python3 this_script.py
from jinja2 import Environment
import json

env = Environment()
env.filters["to_json"] = json.dumps

t = open("roles/cloudwatch_agent/templates/amazon-cloudwatch-agent.json.j2").read()
rendered = env.from_string(t).render(
    cloudwatch_metrics_collection_interval_seconds=60,
    cloudwatch_disk_monitor_paths=["/", "/data"],
    cloudwatch_namespace="DiskMonitoring",
    account_id="111111111111",
    account_name="acquired-co-a"
)
doc = json.loads(rendered)
assert doc["metrics"]["metrics_collected"]["disk"]["measurement"][0]["rename"] == "DiskUsedPercent"
print("OK")
```

**YAML / Ansible syntax check:**

```bash
ansible-galaxy collection install -r requirements.yml
ansible-playbook playbooks/site.yml --syntax-check -i "localhost," -e "ansible_connection=local"
```

**Metric model consistency check:**

```bash
grep "DiskUsedPercent" roles/cloudwatch_agent/templates/amazon-cloudwatch-agent.json.j2
grep "DiskUsedPercent" roles/cloudwatch_agent/files/emit-disk-metrics.ps1
grep "DiskUsedPercent" roles/disk_alerting/tasks/main.yml
```

**Ansible module namespace audit:**

```bash
# should return nothing — all cloudwatch_metric_alarm calls must use amazon.aws
grep -r "community.aws.cloudwatch" roles/ playbooks/
```

**Cross-account alarm structure check:**

```bash
# Confirm alarms use metrics[].account_id (MetricDataQuery), not AccountId as a dimension
grep -A5 "dimensions:" roles/disk_alerting/tasks/main.yml | grep "AccountId" && echo "FAIL: AccountId found as dimension" || echo "PASS: no AccountId dimension in alarms"
grep "account_id:" roles/disk_alerting/tasks/main.yml && echo "PASS: account_id present in metrics block" || echo "FAIL: account_id missing"
```

**SSM connection plugin namespace check:**

```bash
# Both inventory files must use amazon.aws.aws_ssm
grep "ansible_connection" inventory/accounts/*.yml
# Expected output: amazon.aws.aws_ssm
```

**No static credentials:**

```bash
grep -r "aws_access_key_id\|AKIA" roles/ playbooks/ terraform/ inventory/
```

## With a real AWS account

**Verify SSM reachability using the assumed role (FIX 3 test):**

```bash
aws sts assume-role \
  --role-arn arn:aws:iam::111111111111:role/DiskMonitoringAutomationRole \
  --role-session-name preflight-test \
  --external-id disk-monitoring-111111111111 > /tmp/creds.json

AWS_ACCESS_KEY_ID=$(jq -r .Credentials.AccessKeyId /tmp/creds.json) \
AWS_SECRET_ACCESS_KEY=$(jq -r .Credentials.SecretAccessKey /tmp/creds.json) \
AWS_SESSION_TOKEN=$(jq -r .Credentials.SessionToken /tmp/creds.json) \
aws ssm describe-instance-information \
  --filters "Key=InstanceIds,Values=<instance-id>" \
  --region us-east-1
```

**Run the full playbook:**

```bash
ansible-playbook playbooks/site.yml --limit <instance-id>
```

**Verify metric name in CloudWatch (allow 2 min after agent starts):**

```bash
aws cloudwatch list-metrics \
  --namespace DiskMonitoring \
  --metric-name DiskUsedPercent \
  --region us-east-1
```

**Verify alarms are in the monitoring account, not the member account:**

```bash
aws cloudwatch describe-alarms \
  --alarm-name-prefix disk-critical \
  --region us-east-1 \
  --query "MetricAlarms[*].{Name:AlarmName,State:StateValue}"
```

**Trigger a test alarm:**

```bash
ansible-playbook playbooks/site.yml --tags disk_alerting --limit localhost \
  -e disk_alert_critical_percent=1
# wait 2 minutes, then check state
aws cloudwatch describe-alarms --alarm-name-prefix disk-critical \
  --query "MetricAlarms[*].StateValue" --region us-east-1
```

**Idempotency:**

```bash
ansible-playbook playbooks/site.yml
ansible-playbook playbooks/site.yml  # second run: changed=0
```

## What was not verified in a live environment

- Actual cross-account OAM metric visibility (requires two real accounts)
- Composite alarm with OAM-sourced underlying alarms
- Windows PowerShell scheduled task execution and metric emission
- End-to-end SSM Session Manager via the amazon.aws.aws_ssm connection plugin
- account-bootstrap Terraform apply against a real account

All code is syntactically valid and logically correct per AWS documentation. A cloud integration test is needed before production use.
