# Failure Modes & Mitigations

This document catalogues the realistic failure scenarios for this solution, their blast radius, how they are detected, and what the mitigation or recovery path is.

The goal is not to claim the system is fault-free — it is to demonstrate that every known failure has been thought through.

---

## 1. SSM Agent unreachable

**Scenario:** An EC2 instance has SSM Agent stopped, misconfigured, or has a network path issue (missing VPC endpoint or NAT gateway).

**Blast radius:** Ansible cannot connect to that host. The playbook run fails for that host but continues for others (Ansible's default `any_errors_fatal: false`).

**Detection:**
- Ansible play output shows `UNREACHABLE` for the host.
- SSM Fleet Manager in the AWS Console shows the instance as "Connection Lost".

**Mitigation:**
- IAM instance profile must include `AmazonSSMManagedInstanceCore`. The `ssm_bootstrap` role validates this exists before attempting a connection.
- VPC endpoints (`ssm`, `ssmmessages`, `ec2messages`) are provisioned by the `account-bootstrap` Terraform module so instances without internet access can still reach SSM.
- Recovery: restart the SSM Agent (`sudo systemctl restart amazon-ssm-agent`) or re-run the `ssm_bootstrap` role via the AWS Systems Manager Run Command console as a break-glass path.

---

## 2. STS AssumeRole failure

**Scenario:** The Ansible controller cannot assume the cross-account role — expired credentials, wrong external ID, or the trust policy was not yet applied.

**Blast radius:** All tasks in `ssm_bootstrap` that target that account fail. No configuration is pushed to those instances.

**Detection:**
- Task output shows `An error occurred (AccessDenied) when calling the AssumeRole operation`.
- The role exits early; no credentials are injected into subsequent tasks.

**Mitigation:**
- The `ssm_bootstrap` role uses `no_log: true` on the STS call to avoid leaking credentials in logs, but the error message itself is surfaced clearly.
- The `ssm_bootstrap_external_id_prefix` variable must match what was set in Terraform. Both are in version control so drift is visible in a PR diff.
- Recovery: verify the trust policy on the target role in the member account (`aws iam get-role`) and confirm the external ID matches `group_vars/all.yml`.

---

## 3. CloudWatch Agent fails to start

**Scenario:** The CloudWatch Agent process crashes or fails to start after installation — typically due to a malformed configuration JSON or a permissions issue on the log/metric namespace.

**Blast radius:** No disk metrics are emitted from that instance. The per-instance alarm transitions to `INSUFFICIENT_DATA` after the configured evaluation period (default: 3 consecutive 5-minute periods = 15 minutes).

**Detection:**
- `INSUFFICIENT_DATA` state on the individual `DiskUsedPercent` alarm in CloudWatch.
- On the instance: `sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl -m ec2 -a status` returns `stopped`.
- Agent logs at `/opt/aws/amazon-cloudwatch-agent/logs/amazon-cloudwatch-agent.log`.

**Mitigation:**
- The `cloudwatch_agent` role uses a handler that restarts the agent after any configuration change and then waits for the `status` to return `running` before marking the task complete.
- The `treat_missing_data: breaching` flag on alarms means a dead agent is treated as a breach, not as silence. This is intentional — silent failures are worse than noisy ones.
- Recovery: re-run the `cloudwatch_agent` role (`ansible-playbook playbooks/site.yml --tags cloudwatch_agent`). The role is idempotent.

---

## 4. Windows PowerShell scheduled task fails silently

**Scenario:** The `emit-disk-metrics.ps1` scheduled task on Windows instances fails — due to execution policy, missing AWS CLI, or IAM permission gap — and stops emitting `DiskUsedPercent` metrics.

**Blast radius:** Windows disk metrics stop flowing. Same as above — alarms go to `INSUFFICIENT_DATA` → breaching.

**Detection:**
- CloudWatch shows no data points for `DiskUsedPercent` on Windows instances for > 15 minutes.
- On the instance: `Get-ScheduledTask -TaskName "EmitDiskMetrics" | Get-ScheduledTaskInfo` shows last result != 0.
- Event Viewer → Task Scheduler → Operational log shows the failure reason.

**Mitigation:**
- The script is deployed with `-ExecutionPolicy Bypass` in the scheduled task action to avoid execution policy issues.
- The instance profile (provisioned by Terraform) includes `cloudwatch:PutMetricData` on the `DiskMonitoring` namespace.
- Recovery: RDP or SSM Session to the instance, run the script manually to surface the error, fix the root cause, then re-run the Ansible `windows.yml` tasks to redeploy.

---

## 5. CloudWatch alarm in wrong state due to metric name mismatch

**Scenario:** Linux CW Agent config uses the `rename` field to emit `DiskUsedPercent` but if the template renders the wrong metric name (e.g., a Jinja2 variable is undefined), the alarm targets a metric that never receives data.

**Blast radius:** All Linux instance alarms show `INSUFFICIENT_DATA` indefinitely.

**Detection:**
- CloudWatch Metrics → `DiskMonitoring` namespace shows no `DiskUsedPercent` metric, or shows the raw agent metric name instead.
- `ansible --check` mode on the `cloudwatch_agent` role will render and print the config template without pushing it.

**Mitigation:**
- The template has a fixed string `"name": "DiskUsedPercent"` — not a variable — so the rename target cannot be incorrectly templated.
- Integration test: run `ansible-playbook playbooks/site.yml --check --diff` in CI to validate template rendering before any push.

---

## 6. Composite alarm rule references a deleted per-instance alarm

**Scenario:** An EC2 instance is terminated. The per-instance CloudWatch alarm is deleted (manually or via automation), but the composite alarm rule still references it.

**Blast radius:** The composite alarm enters `INSUFFICIENT_DATA` for the missing rule component. Depending on the `treat_missing_data` setting this may suppress or trigger the composite alarm incorrectly.

**Detection:**
- CloudWatch console shows the composite alarm in `INSUFFICIENT_DATA`.
- `aws cloudwatch describe-alarms --alarm-names <composite>` lists the rule; `describe-alarms` on the referenced per-instance alarm returns empty.

**Mitigation:**
- The `disk_alerting` role is designed to be re-run whenever the inventory changes. Re-running rebuilds the composite alarm rule from the current live inventory, dropping references to terminated instances.
- Operationally: tie the `disk_alerting` playbook run to any Auto Scaling scale-in event via EventBridge → Lambda → Ansible Tower/AWX job, or simply run it as a nightly cron.
- This is a known operational gap in the PoC. A production-grade solution would use a Lambda function triggered by EC2 state-change events to keep the composite alarm in sync.

---

## 7. OAM sink link not established between member and monitoring account

**Scenario:** The `org-oam-sink` Terraform module (monitoring account) is applied but the member account's `account-bootstrap` module has not yet been applied — or was applied in a different region.

**Blast radius:** Metrics from member accounts are not visible in the monitoring account. The `disk_alerting` role can still create alarms in the monitoring account, but those alarms will be in `INSUFFICIENT_DATA` because no metric data is flowing.

**Detection:**
- CloudWatch → Settings → OAM Links in the monitoring account shows no linked sources.
- `aws oam list-links --region <region>` in the member account returns empty.

**Mitigation:**
- Apply `account-bootstrap` in every member account/region pair before running the Ansible playbooks. The `README.md` bootstrap sequence documents this order explicitly.
- Recovery: apply the Terraform modules in the correct order (`org-oam-sink` first, then `account-bootstrap` per member account), then wait up to 5 minutes for OAM propagation.

---

## 8. Ansible inventory returns stale or no hosts

**Scenario:** The `aws_ec2` dynamic inventory plugin returns no hosts — due to expired credentials, wrong `regions` list, or all instances being stopped.

**Blast radius:** Playbook runs against zero hosts. No configuration is pushed, no alarms are created. The failure is silent unless `--list-hosts` is checked.

**Detection:**
- `ansible-inventory -i inventory/accounts/ --list` returns an empty host list or throws an auth error.
- The `disk_alerting` role produces no alarms, leaving the monitoring account empty.

**Mitigation:**
- Always run `ansible-inventory --list` and verify host count before a production playbook run.
- The dynamic inventory uses `include_filters` on `instance-state-name: running` — stopped instances are intentionally excluded. Document this expectation clearly in runbooks.
- If using Ansible Tower/AWX, inventory sync failures are surfaced as job failures with a non-zero exit code.

---

## 9. SNS topic not subscribed

**Scenario:** The SNS topic `disk-monitoring-alerts` exists and alarms are configured to publish to it, but no email or PagerDuty endpoint is subscribed.

**Blast radius:** Alarms fire correctly in CloudWatch, but no one is notified. Silent breach.

**Detection:**
- `aws sns list-subscriptions-by-topic --topic-arn <arn>` returns empty `Subscriptions`.
- CloudWatch alarm history shows `ALARM` transitions with no downstream delivery.

**Mitigation:**
- The `account-bootstrap` Terraform module creates the SNS topic. Subscriptions are intentionally left outside Terraform (email confirmation cannot be automated without a human click).
- Operationally: add a required step in the onboarding runbook — after `terraform apply`, subscribe at least one endpoint to the SNS topic and confirm the subscription before the system is considered live.

---

## 10. IAM permission boundary blocks `cloudwatch:PutMetricData`

**Scenario:** The organization has a Service Control Policy (SCP) or IAM permission boundary that restricts `cloudwatch:PutMetricData` to specific namespaces or conditions.

**Blast radius:** Windows metrics fail to emit. Linux CloudWatch Agent metrics may also fail if `cloudwatch:PutMetricData` is blocked entirely.

**Detection:**
- CloudTrail shows `AccessDenied` on `cloudwatch:PutMetricData` calls from the instance role.
- No metrics appear in the `DiskMonitoring` namespace.

**Mitigation:**
- The Terraform `account-bootstrap` module explicitly uses `"Resource": "*"` with a condition limiting the namespace to `DiskMonitoring`. If an SCP is more restrictive, the SCP takes precedence.
- Resolution: work with the AWS account owner to add a targeted SCP exception for this namespace, or adjust the solution namespace to match an already-permitted one.

---

## Summary Table

| # | Failure | Detection | Recoverable? | Auto-healing? |
|---|---------|-----------|--------------|---------------|
| 1 | SSM Agent unreachable | UNREACHABLE in Ansible / Fleet Manager | Yes | No — manual restart |
| 2 | STS AssumeRole failure | AccessDenied error in task output | Yes | No — fix trust policy |
| 3 | CW Agent not running | INSUFFICIENT_DATA → breaching alarm | Yes | Re-run playbook |
| 4 | Windows PS task silent failure | No metrics for 15 min | Yes | Re-run playbook |
| 5 | Metric name mismatch | No data in namespace | Yes | Fix template, re-run |
| 6 | Stale composite alarm rule | Composite in INSUFFICIENT_DATA | Yes | Re-run disk_alerting role |
| 7 | OAM link missing | No metrics in monitoring account | Yes | Apply Terraform in order |
| 8 | Empty Ansible inventory | Zero hosts in --list | Yes | Fix credentials/regions |
| 9 | SNS not subscribed | Alarms fire, no notification | Yes | Subscribe endpoint manually |
| 10 | SCP blocks PutMetricData | AccessDenied in CloudTrail | Yes | SCP exception needed |

All failures are recoverable. None require rebuilding the solution from scratch.
