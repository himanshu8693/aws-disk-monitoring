# Architectural Decisions

Each decision is documented as: what I chose, why, what I considered instead, and the trade-off.

---

## 1. AWS over Azure/GCP

**Why:** My production experience is primarily on AWS, which matters for an assessment. Beyond that, AWS had exactly the two native services I needed — SSM Session Manager for credential-free management access and CloudWatch OAM for cross-account metric visibility — both without adding a third-party tool.

**Alternative considered:** Azure (AMA + Azure Monitor + Azure Lighthouse) or GCP (Ops Agent + Cloud Monitoring). Both have equivalent capabilities.

**Trade-off:** Azure Lighthouse offers more granular delegated administration control. GCP workload identity federation is cleaner than STS for some use cases. AWS was chosen for familiarity and because both key services (SSM, OAM) are available without additional cost beyond standard usage.

---

## 2. Ansible for configuration, not for telemetry collection

**Why:** Ansible is already in use at the customer. It's good at idempotent configuration — installing the CloudWatch Agent, deploying the Windows PowerShell script, and creating CloudWatch alarms. I deliberately did not use Ansible to poll disk usage on a schedule.

**Alternative considered:** Using Ansible with a cron job to run `df` on every host and send results to a central store.

**Trade-off:** The Ansible polling approach works for small fleets but fails at scale. Opening an SSM session against every VM every minute to run `df` is slow, expensive, and adds operational noise. Using the CloudWatch Agent to push metrics means Ansible is only involved at setup and drift-correction time — it's never in the critical path for continuous monitoring.

---

## 3. SSM Session Manager over SSH

**Why:** Acquired companies have inconsistent SSH key management. Distributing and rotating SSH key pairs across multiple accounts with different processes is a reliability problem — something always gets missed. SSM gives a single IAM-based control plane: one `sts:AssumeRole` call gives Ansible access to all instances in that account, with a full CloudTrail audit trail and zero open inbound ports.

**Alternative considered:** SSH with a bastion host or EC2 Instance Connect.

**Trade-off:** SSM Session Manager requires the SSM Agent to be running and the instance to have outbound HTTPS access to the SSM endpoint. If an instance loses network access or the agent crashes, Ansible cannot reach it. That's a recoverable failure (the ssm_bootstrap role detects it clearly) and acceptable for configuration-time operations. For continuous monitoring, the CloudWatch Agent pushes metrics independently of SSM.

---

## 4. CloudWatch Agent (push model) over Ansible polling

**Why:** Continuous disk monitoring requires a consistent stream of data every 60 seconds from every instance. Implementing that with Ansible would mean scheduling a playbook run against every host every minute — impractical at scale and brittle. The CloudWatch Agent runs on every instance, pushes metrics directly to CloudWatch, and does not depend on Ansible being available.

**Alternative considered:** A cron job running `df` and shipping results to S3/DynamoDB, or a custom collection script.

**Trade-off:** Requires the CloudWatch Agent to be installed and running on every instance. The agent is managed by the `cloudwatch_agent` Ansible role, with nightly drift detection to catch any instances that have been modified or newly provisioned. The agent is well-maintained by AWS and runs on both Linux and Windows.

---

## 5. CloudWatch over Prometheus/Datadog

**Why:** The assignment brief asked to avoid a third-party monitoring platform unless justified. CloudWatch is the native metrics store for EC2 — instances can push metrics there without any additional infrastructure. Running a Prometheus server (with Thanos or Cortex for multi-account federation) would add servers to operate and is more complexity than the problem needs.

**Alternative considered:** Prometheus with remote_write to a central store; Datadog.

**Trade-off:** CloudWatch has a less capable query language than PromQL and Datadog's query system. Dashboards are workable but not as flexible. The cost model (per-metric, per-alarm) is predictable but can grow significantly at large fleet sizes. If the customer already runs Datadog, ingesting CloudWatch metrics there via the Datadog AWS integration would be a reasonable next step — this design does not prevent that.

---

## 6. OAM for cross-account visibility

**Why:** CloudWatch OAM (Observability Access Manager) is the native AWS mechanism for reading metrics across accounts. A member account creates an OAM link pointing at the monitoring account's sink, and the monitoring account immediately has read-only access to that account's metrics. No data copying, no custom ETL, no aggregation database to operate.

**Alternative considered:** Streaming metrics via Kinesis Data Firehose to a central account; building a Lambda-based metric aggregation pipeline.

**Trade-off:** OAM is regional — each region needs its own link. OAM also does not allow the monitoring account to write into member accounts, which is a security benefit but means alarms must be created in the monitoring account (not the member account). This is handled correctly in the `disk_alerting` role.

---

## 7. Cross-account IAM roles over hardcoded credentials

**Why:** No static AWS access keys exist anywhere in this system. The Ansible control node authenticates via IAM Identity Center (short-lived STS token), then calls `sts:AssumeRole` into a purpose-built role in each member account. The ExternalId condition on the trust policy prevents confused-deputy attacks.

**Alternative considered:** IAM users with access keys in a secrets manager; per-account IAM users with cross-account policies.

**Trade-off:** Requires IAM Identity Center (or an OIDC-federated CI role) to be configured. The assumed role has a 1-hour session limit, which is fine for one-off configuration runs and nightly drift detection. For long-running operations this would need to be considered.

---

## 8. Normalized metric name (DiskUsedPercent)

**Why:** The CloudWatch Agent on Linux natively collects `disk_used_percent` (used percentage). The Windows CloudWatch Agent collects `% Free Space` (the inverse). If alarms use `DiskUsedPercent >= 85%`, Windows alarms would never fire correctly because the metric values are inverted.

**Fix:** Linux: rename `disk_used_percent` to `DiskUsedPercent` in the agent config. Windows: deploy a PowerShell scheduled task that calculates used percentage and emits `DiskUsedPercent` via `aws cloudwatch put-metric-data`.

**Trade-off:** Windows metric emission requires the AWS CLI installed on the instance. This is common in managed environments but not guaranteed. The approach is transparent — the PowerShell script is deployed by Ansible and its logic is simple enough to audit. The alternative (creating alarms against the native Windows metric `% Free Space` with inverted threshold logic) is confusing to operate and easy to misconfigure.

---

## What I intentionally did NOT build

- **A custom aggregation database:** OAM handles cross-account metric centralization natively.
- **Lambda functions:** Nothing here requires event-driven compute beyond what CloudWatch handles.
- **Prometheus/Grafana stack:** More infrastructure to operate than the problem warrants.
- **Auto-remediation:** The brief asked for monitoring and alerting, not automatic disk cleanup. Auto-remediation should be a deliberate, audited action.
- **ML-based anomaly detection:** Simple percentage thresholds are explainable and auditable. ML thresholds would require training data and introduce false-positive risk that's harder to explain to an on-call engineer at 2am.
