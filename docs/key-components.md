# Key components

## Access management

Zero static credentials anywhere in the system.

**Credential chain:**
1. Ansible control node authenticates via IAM Identity Center (SSO) or an OIDC-federated CI role. Credentials are short-lived STS tokens.
2. The dynamic inventory and ssm_bootstrap both call `sts:AssumeRole` into `DiskMonitoringAutomationRole` in each member account, using the account ID from the inventory file. An ExternalId condition prevents confused-deputy attacks.
3. That role is scoped to: `ec2:Describe*`, `ssm:StartSession`, `ssm:SendCommand`, `ssm:GetCommandInvocation`, `ssm:DescribeInstanceInformation`, and S3 read/write on the SSM transfer bucket.
4. Instances have `DiskMonitoringInstanceRole` as their profile: `AmazonSSMManagedInstanceCore` + `CloudWatchAgentServerPolicy`. Nothing else.

The monitoring account accesses member metrics via OAM, which is read-only by design. It has no trust relationship with member accounts and cannot assume roles or modify resources there.

**Blast-radius analysis:** If the automation account is compromised, an attacker can start SSM sessions and describe EC2 instances across enrolled member accounts. They cannot create/modify/delete infrastructure, access data, or do anything beyond what the scoped role allows. If the monitoring account is compromised, an attacker can read metrics and create/delete alarms — no access to member-account resources.

## VM discovery and enrollment

**Discovery:** `amazon.aws.aws_ec2` dynamic inventory plugin, one config file per account in `inventory/accounts/`. Queries EC2 at run time. No static inventory to maintain.

**Enrollment — new VMs:** The `DiskMonitoringInstanceProfile` baked into the AMI/launch template means a VM is monitored from first boot. The `cloudwatch_agent` role runs nightly via the site.yml playbook as drift detection.

**Enrollment — acquired VMs (non-golden):** The nightly idempotent Ansible run covers this. Any instance without the correct agent config gets it applied on the next sweep.

**Enrollment — new accounts:** One `terraform apply` in `account-bootstrap` + one inventory file in `inventory/accounts/`. No other changes.

## Metric model (FIX 11)

The normalized telemetry contract is platform-independent. Collection is OS-specific internally; the external metric name and dimensions are the same for Linux and Windows.

**Namespace:** `DiskMonitoring`

**Metrics:**

| Metric | Unit | Source |
|---|---|---|
| `DiskUsedPercent` | Percent | Linux: CW Agent (renamed from `disk_used_percent`). Windows: PowerShell scheduled task. |
| `DiskFreeBytes` | Bytes | Linux: CW Agent. Windows: PowerShell. |
| `DiskUsedBytes` | Bytes | Linux: CW Agent. Windows: PowerShell. |
| `DiskCapacityBytes` | Bytes | Linux: CW Agent. Windows: PowerShell. |

**Dimensions on every emitted metric:**

| Dimension | Value | Cardinality note |
|---|---|---|
| `InstanceId` | EC2 instance ID | One per instance — acceptable |
| `AccountId` | 12-digit AWS account ID | Enrichment for dashboards and Metrics Insights queries |
| `AccountName` | Human tag from inventory | Low cardinality — useful for dashboards |
| `Platform` | `Linux` or `Windows` | Two values — no cardinality risk |

Note: `AccountId` above is a dimension on the **emitted metric** (CloudWatch Agent `append_dimensions` / PowerShell `put-metric-data`). It is not used as an alarm dimension. CloudWatch alarms that target cross-account metrics express the source account via the `account_id` field inside the `metrics[]` MetricDataQuery structure — see `roles/disk_alerting/tasks/main.yml`.

Avoid adding dimensions like `Application`, `Environment`, `Owner` etc. unless there is a concrete dashboard or alarm reason. Each additional dimension multiplies metric cardinality and custom metric cost.

## Threshold design (FIX 8)

The default 85% critical / 75% warning thresholds are a starting point, not a universal policy.

Production threshold tuning should consider:
- **Growth rate** — a disk growing at 10 GB/day needs earlier warning than one growing at 100 MB/day.
- **Remediation time** — if on-call response takes 4 hours, the alarm should fire with at least 4 hours of headroom.
- **Business criticality** — a database transaction log disk has a much lower tolerance than a temp directory.
- **False-positive tolerance** — log-spiky workloads may need a higher threshold or longer evaluation period to avoid noise.

A simple horizon calculation to discuss in interviews:

```
estimated_time_to_full = DiskFreeBytes / (bytes_consumed_last_hour * 24)
# if estimated_time_to_full < 24h → page
# if < 72h → warn
```

This doesn't require ML. It requires a second metric (rate of growth) which CloudWatch Metric Math can compute from the `DiskFreeBytes` time series.

## Cost considerations (FIX 6)

CloudWatch is not free. This design replaces a third-party monitoring platform with native AWS services, shifting the cost model rather than eliminating cost.

**Cost drivers:**
- **Custom metrics:** ~$0.30/metric/month (first 10,000 metrics). With 4 metrics × N mount paths × N instances, cardinality matters.
- **Alarms:** ~$0.10/alarm/month. With two alarms per instance plus one composite per region, cost scales linearly with fleet size.
- **Dashboards:** ~$3/dashboard/month after the free tier.
- **OAM:** No additional charge for the OAM link/sink mechanism itself, but the metrics being observed are still billable at the source account.
- **SSM sessions:** $0.00 for SSM Session Manager itself; S3 session log storage is billed at standard S3 rates.

**Cost vs alternative:** A third-party APM/monitoring platform for a fleet of this size would typically cost more than the CloudWatch bill. The advantage here is no additional operational infrastructure to run or scale.

**Mitigation:** Use `ignore_file_system_types` in the agent config to suppress metrics from tmpfs/devtmpfs/squashfs mounts — these add cardinality with no monitoring value.

## Failure modes (FIX 9)

| Failure | What detects it | What does NOT detect it | Recovery | Customer impact |
|---|---|---|---|---|
| CloudWatch Agent stopped | `treat_missing_data: breaching` on alarms → INSUFFICIENT_DATA triggers | Alarms with `missing = notBreaching` (default) | Nightly Ansible drift-detection re-applies role and restarts service | Monitoring blind spot until next Ansible run |
| SSM unavailable (agent crash, network) | `ssm_bootstrap` preflight fails with clear error | Ansible run succeeds but does nothing useful | Fix network/agent, re-run site.yml | Configuration drift not applied |
| Instance terminated | Metric stops arriving; alarm goes INSUFFICIENT_DATA | Nothing removes the alarm automatically | Re-run `disk_alerting` role to delete stale alarms (add a delete task for terminated instances) | Stale alarms in dashboard — noise |
| IAM role missing / misconfigured | STS AssumeRole fails in inventory lookup; instance not discovered | — | Re-run `account-bootstrap` Terraform | Account invisible to monitoring |
| New account onboarded without OAM link | Metrics not visible in monitoring account | OAM dashboard silently missing the account | `terraform apply` account-bootstrap creates the link | Monitoring blind spot for that account |
| CloudWatch PutMetricData rate limit | Agent silently drops metrics; data gap appears | Alarm won't fire immediately | Agent has built-in retry; persistent gaps mean fleet has grown past a single-region limit | Intermittent missing data |
| Alert storm (many instances critical at once) | Composite alarm fires once; individual alarms visible for drill-down | — | Composite alarm design prevents N pages for N instances | One page rather than N pages |
| Malformed metric / wrong dimensions | Alarm dimensions don't match metric — alarm stays INSUFFICIENT_DATA | — | Review agent config via ssm_bootstrap → cloudwatch_agent run | Alarm never fires even if disk is full |

## Security review (FIX 10)

**IAM trust policy:** The `DiskMonitoringAutomationRole` requires an ExternalId of `disk-monitoring-<account-id>`. This prevents a confused-deputy attack where a compromised third party tricks our automation account into assuming roles in accounts it wasn't intended to manage.

**Least privilege:** The automation role cannot create or delete resources. The instance profile cannot assume other roles, access secrets, or call any service outside SSM + CloudWatch.

**No static credentials:** IAM Identity Center (SSO) for human operators; OIDC federation for CI/CD. STS tokens are short-lived (900 seconds for the ssm_bootstrap preflight, session manager default for Ansible).

**No inbound ports:** Security groups on all monitored instances can have zero inbound rules. SSM communicates over HTTPS outbound (port 443) to the SSM endpoint.

**Public access:** S3 SSM transfer buckets have all public access blocked and use KMS encryption. There is no publicly accessible resource in this design.

**Monitoring account compromise:** Read access to metrics across the fleet. Can create or delete alarms, modify dashboards, and silence on-call notifications via SNS. Cannot access, modify, or delete resources in member accounts. Cannot pivot to member-account credentials — there is no trust relationship going the other way.

**Automation account compromise:** Can start SSM sessions on enrolled instances and run commands (within the scope of `ssm:SendCommand`). This is the highest-risk blast radius. Mitigations: use SSM Session Manager only (not `SendCommand` unless needed), log all sessions to CloudWatch Logs, enable SSM document signing.
