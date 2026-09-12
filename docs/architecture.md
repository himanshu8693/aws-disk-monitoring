# Architecture

## Account layout

```
AWS Organizations
├── Management account  (billing root, no workloads)
├── Security OU
│   ├── Monitoring account  — OAM sink, alarms, dashboards, SNS
│   └── Automation account  — Ansible control node, IAM Identity Center
├── Acquired Co A OU
│   └── member account 111111111111  (us-east-1, us-west-2)
└── Acquired Co B OU
    └── member account 222222222222  (eu-west-1)
```

## Logical architecture

```
Automation account
  │
  ├─ IAM Identity Center (no static keys)
  │
  └─ Ansible control node
       │
       ├─ STS AssumeRole ──────────────────────────────────────┐
       │   (per-account, per-run, short-lived)                  │
       │                                                        │
       ├─ dynamic inventory (amazon.aws.aws_ec2)                │
       │   one file per account in inventory/accounts/          │
       │                                                        │
       └─ SSM Session Manager (amazon.aws.aws_ssm)           │
           (no SSH, no open inbound ports)                      │
                │                                               │
                ▼                                               ▼
        Member account A                              Member account B
        EC2 Linux / Windows                           EC2 Linux
        DiskMonitoringInstanceProfile                 DiskMonitoringInstanceProfile
                │                                               │
                │ CloudWatch Agent (Linux)                      │
                │ PowerShell task (Windows)                     │
                │ → PutMetricData                               │
                │                                               │
                ▼                                               ▼
        CloudWatch metrics                            CloudWatch metrics
        (DiskUsedPercent, etc.)                       (DiskUsedPercent, etc.)
                │                                               │
                │ OAM link (read-only, per region)              │ OAM link
                ▼                                               ▼
        ┌─────────────────────────────────────────────────────────┐
        │                  Monitoring account                      │
        │                                                          │
        │  us-east-1                us-west-2       eu-west-1     │
        │  ──────────               ──────────       ─────────    │
        │  OAM sink                 OAM sink         OAM sink     │
        │  per-instance alarms      per-instance     per-instance │
        │  composite alarm          composite        composite    │
        │  SNS topic                SNS topic        SNS topic    │
        │                                                          │
        │  CloudWatch Dashboard (cross-region widgets)            │
        └─────────────────────────────────────────────────────────┘
                          │
                          ▼
                 SNS → Slack / PagerDuty
```

## Data flow (metric to alert)

```
EC2 instance
    └─ [every 60s] CloudWatch Agent / PowerShell task
         └─ PutMetricData: DiskUsedPercent, DiskFreeBytes, DiskUsedBytes, DiskCapacityBytes
              └─ CloudWatch (member account, source region)
                   └─ OAM link → Monitoring account (same region)
                        └─ per-instance alarm: DiskUsedPercent >= 85%
                             └─ treat_missing_data: breaching (silent agent = alarm)
                                  └─ composite alarm: any instance critical in this region
                                       └─ SNS → on-call notification
```

## Why alarms live in the monitoring account

CloudWatch composite alarms must reference alarms in the **same account and region**. Placing per-instance alarms in the member account and the composite alarm in the monitoring account would be invalid — the composite alarm could not reference them.

The correct model: per-instance alarms AND composite alarms both live in the monitoring account, watching metrics that OAM has made readable there. Member accounts only run the CloudWatch Agent; they have no alarms managed by this system.

## Multi-region design

OAM is regional. Each combination of (source-account, source-region) needs its own OAM link. The monitoring account needs OAM sinks, alarms, and SNS topics in each region where source instances exist.

The `account-bootstrap` Terraform module creates one OAM link per region. The `disk_alerting` Ansible role creates alarms in the monitoring account in the region matching each instance's `ansible_aws_ssm_region`.

**Trade-off:** A single cross-region CloudWatch dashboard can aggregate the view. But for alerting, each region is independent — an alarm in eu-west-1 cannot reference a metric in us-east-1. This is an AWS constraint, not a design choice.

## Ansible's role in this architecture

Ansible is an **onboarding and configuration tool**, not a telemetry collection engine.

- It installs the CloudWatch Agent (or deploys the PowerShell scheduled task for Windows).
- It runs nightly as drift detection — any instance missing the correct config gets it applied.
- It creates CloudWatch alarms and SNS topics in the monitoring account.
- It does NOT poll disk usage on a schedule. That is the CloudWatch Agent's job.

This distinction matters at scale: Ansible polling 5,000 instances every minute would be impractical. The agent-push model scales to tens of thousands of instances with no Ansible involvement after initial setup.
