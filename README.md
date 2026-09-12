# Disk Utilization Monitoring Across AWS Accounts

**Assessment:** Lucidity Solutions Architect — Cloud Consultant case study
**Cloud provider chosen:** AWS

---

## Problem

A multi-account AWS environment grown through acquisitions needs early warning on disk space exhaustion across all EC2 instances (Linux and Windows). Ansible is already in the environment. The solution must avoid adding a third-party monitoring platform without clear justification.

---

## Architecture overview

```
AWS Organization
├── Automation account       — Ansible control node, IAM Identity Center
├── Monitoring account       — OAM sink, CloudWatch alarms, SNS, dashboards
├── Member account A (111)   — EC2 (Linux + Windows), us-east-1, us-west-2
└── Member account B (222)   — EC2 (Linux), eu-west-1

Flow:
  Ansible (Automation account)
      │
      ├─ STS AssumeRole ──→ Member account A / B
      │   (ExternalId, short-lived token)
      │
      ├─ SSM Session Manager ──→ EC2 instances
      │   (no SSH, no open inbound ports)
      │
      └─ Installs CloudWatch Agent (Linux)
         Deploys PowerShell scheduled task (Windows)
              │
              │ PutMetricData every 60s
              ▼
       CloudWatch (member account, per region)
              │
              │ OAM link (read-only)
              ▼
       CloudWatch (monitoring account)
              │
       Per-instance alarm: DiskUsedPercent >= 85%
              │
       Regional composite alarm: ANY instance critical
              │
       SNS → email / PagerDuty
```

---

## A. Access management

### How VMs are securely managed across multiple accounts

Zero static credentials anywhere. The credential chain:

```
IAM Identity Center (SSO) or OIDC CI role
  └─ Short-lived STS token (Automation account)
       └─ sts:AssumeRole → DiskMonitoringAutomationRole (each member account)
            ExternalId: disk-monitoring-<account-id>
            (prevents confused-deputy attacks)
            └─ SSM Session Manager → EC2 instance
                 (outbound HTTPS only, zero open inbound ports, CloudTrail logged)
```

**Why SSM instead of SSH:**  
SSH across acquired accounts means managing key pairs with no consistent process, varying security group rules, and no audit trail. SSM gives one IAM-based control plane, CloudTrail logging of every session, and zero inbound port requirements.

**Least privilege:**  
- The automation role can only describe EC2 instances, start SSM sessions, and read/write the SSM S3 transfer bucket. It cannot create or delete AWS resources.
- EC2 instances carry `AmazonSSMManagedInstanceCore` + `CloudWatchAgentServerPolicy`. Nothing else.

**Blast radius:**  
- Monitoring account compromised: read metrics, create/delete alarms. No member-account resource access.
- Automation account compromised: start SSM sessions on enrolled instances. No resource creation, no data access.

**Infrastructure:** All IAM roles, trust policies, and OAM links are provisioned by Terraform (`terraform/account-bootstrap/` per member account, `terraform/org-oam-sink/` once in the monitoring account).

---

## B. Data collection and aggregation

### How disk usage is collected

**Linux:**  
The CloudWatch Agent reads `disk_used_percent` from the OS and renames it `DiskUsedPercent` in the `DiskMonitoring` namespace. Configuration template: `roles/cloudwatch_agent/templates/amazon-cloudwatch-agent.json.j2`.

**Windows:**  
The Windows CloudWatch Agent natively emits `% Free Space` (inverted). Instead of inverted alarm logic, a PowerShell scheduled task (`roles/cloudwatch_agent/files/emit-disk-metrics.ps1`) runs every minute, calculates `used% = (size - free) / size × 100`, and calls `aws cloudwatch put-metric-data` to emit `DiskUsedPercent` — the same metric name as Linux.

**Normalized metric contract (both platforms):**

| Metric | Namespace | Dimensions |
|---|---|---|
| `DiskUsedPercent` | `DiskMonitoring` | `InstanceId` |
| `DiskFreeBytes` | `DiskMonitoring` | `InstanceId` |
| `DiskUsedBytes` | `DiskMonitoring` | `InstanceId` |
| `DiskCapacityBytes` | `DiskMonitoring` | `InstanceId` |

### How data is centralized

CloudWatch OAM (Observability Access Manager) links each member account to the monitoring account's sink. After linking, the monitoring account can query metrics from all member accounts as if they were local — no data copying, no aggregation pipeline.

**Why OAM instead of a custom pipeline:**  
OAM is the native AWS mechanism. A custom pipeline (Lambda + Kinesis + DynamoDB) would add infrastructure to operate, introduce additional failure modes, and provide no benefit over the native read-only access that OAM gives.

**Alarm placement:** All per-instance alarms and regional composite alarms live in the monitoring account. This is required — CloudWatch composite alarms can only reference alarms in the same account and region.

**Per-instance alarm (cross-account metric query):**
```yaml
metrics:
  - id: disk_used
    account_id: "{{ hostvars[item]['account_id'] }}"   # source account
    metric_stat:
      metric:
        namespace: "{{ cloudwatch_namespace }}"
        metric_name: DiskUsedPercent
        dimensions:
          - name: InstanceId
            value: "{{ item }}"
      period: 60
      stat: Average
      unit: Percent
    return_data: true
comparison: ">="
threshold: 85.0
treat_missing_data: breaching
```

`treat_missing_data: breaching` means a stopped agent fires the alarm rather than staying silently green — the correct default for disk monitoring.

**Regional composite alarm:**  
One composite alarm per region aggregates all per-instance alarms: `ALARM("disk-critical-<account>-<instance>") OR ...`. If any instance in the region is critical, one page is sent rather than N individual pages.

---

## C. VM discovery and enrollment

### Dynamic inventory

The `amazon.aws.aws_ec2` plugin discovers all running EC2 instances at playbook run time. One inventory file per account in `inventory/accounts/`:

```yaml
plugin: amazon.aws.aws_ec2
assume_role_arn: arn:aws:iam::111111111111:role/DiskMonitoringAutomationRole
regions: [us-east-1, us-west-2]
filters:
  instance-state-name: running
compose:
  ansible_aws_ssm_region: placement.region   # actual region, not hardcoded
  ansible_connection: "'amazon.aws.aws_ssm'"
  account_id: "'111111111111'"
```

No static inventory files to maintain. No manual host lists.

### Enrollment process

```
New EC2 instance boots with DiskMonitoringInstanceProfile attached
         ↓
Next Ansible run (nightly or on-demand)
         ↓
aws_ec2 plugin discovers instance
         ↓
ssm_bootstrap role: verifies SSM reachability (PingStatus == Online)
         ↓
cloudwatch_agent role: installs/configures agent (idempotent)
         ↓
disk_alerting role: creates per-instance alarm in monitoring account
         ↓
Instance is monitored
```

**Idempotency:** Every role can run repeatedly. Re-running on an already-configured instance produces `changed=0`. This means the playbook can run nightly as a drift-detection sweep — any instance that had its config modified gets corrected automatically.

**New account onboarding:**
1. `terraform apply` in `terraform/account-bootstrap/` for the new account
2. Add one inventory file in `inventory/accounts/`
3. Run `ansible-playbook playbooks/site.yml`

---

## D. Scalability

| Dimension | PoC behaviour | Production path |
|---|---|---|
| New VMs | Discovered on next playbook run via dynamic inventory | Attach `DiskMonitoringInstanceProfile` at launch; nightly run enrolls |
| New accounts | Add one Terraform apply + one inventory file | EventBridge rule on account creation → pipeline |
| New regions | OAM link + alarms per region; each inventory file lists regions | Same pipeline adds region to existing account's config |
| 10 → 1,000 VMs | Per-instance alarms scale linearly; composite alarm rule grows | Switch to per-account composite alarms using Metric Math at ~200 instances/region |
| Alarm noise | Composite alarm fires once for any number of simultaneous breaches | Same design works at any scale |

**Agent push model scales independently of Ansible.** The CloudWatch Agent pushes metrics every 60 seconds with no Ansible involvement after setup. Ansible is only in the path at install-time and for nightly drift correction — not for continuous metric collection.

---

## Repo layout

```
ansible.cfg                         Ansible configuration
requirements.yml                    Collection dependencies (amazon.aws, community.aws, ansible.windows)
group_vars/all.yml                  Global variables (thresholds, namespace, SNS topic name)

inventory/accounts/                 One file per AWS account — dynamic EC2 discovery
  acquired-co-a.aws_ec2.yml
  acquired-co-b.aws_ec2.yml

playbooks/site.yml                  Main playbook: ssm_bootstrap → cloudwatch_agent → disk_alerting

roles/
  ssm_bootstrap/                    Pre-flight: verifies SSM reachability per-account via STS
  cloudwatch_agent/                 Installs CW Agent (Linux) or deploys PowerShell task (Windows)
  disk_alerting/                    Creates per-instance + composite alarms in monitoring account

terraform/
  account-bootstrap/                Per-account: IAM roles, instance profile, VPC endpoints, OAM link
  org-oam-sink/                     One-time: OAM sink + SNS in monitoring account

tests/
  test_alarm_rule_generation.py     15 offline tests: alarm logic, metric normalization, static analysis
  demo_offline.py                   End-to-end demo without AWS credentials

docs/
  architecture.md                   Full account topology and data flow diagrams
  ARCHITECTURAL-DECISIONS.md        8 key decisions with rationale and trade-offs
  FAILURE-MODES.md                  10 failure scenarios with detection and recovery paths
  TEST-RESULTS.md                   Actual test output and validation matrix
```

---

## Running the PoC locally

```bash
# Install Ansible collections
ansible-galaxy collection install -r requirements.yml

# Validate Terraform (no AWS credentials needed)
cd terraform/account-bootstrap && terraform validate
cd ../org-oam-sink && terraform validate

# Run offline tests (no AWS credentials needed)
python3 -m venv .venv && source .venv/bin/activate && pip install pytest jinja2
python3 -m pytest tests/test_alarm_rule_generation.py -v

# Run offline end-to-end demo
python3 tests/demo_offline.py

# Full playbook (requires real AWS accounts with Terraform modules applied)
ansible-playbook playbooks/site.yml
```

---

## PoC vs production

This repository demonstrates the core architecture and minimal working flow requested by the assessment. It is syntactically and logically correct. Production deployment would additionally require:

- Automated account onboarding (Organizations EventBridge → Terraform pipeline)
- SSM session logging to CloudWatch Logs
- Alarm cleanup for terminated instances (EventBridge on EC2 state-change)
- Per-workload threshold policies (not a single global 85%)
- SCP enforcing `DiskMonitoringInstanceProfile` at instance launch

**Not live-tested:** This code has not been run against real AWS accounts. All validation is local (tests, Terraform validate, offline demo). Cloud integration testing is required before production use.

---

## Key trade-offs

| Decision | Why | Alternative considered |
|---|---|---|
| AWS | Production familiarity; SSM + OAM available natively | Azure (AMA + Lighthouse), GCP (Ops Agent + Monitoring) |
| SSM over SSH | No key management, IAM-based, full audit trail, zero open ports | SSH with bastion or EC2 Instance Connect |
| CloudWatch Agent push over Ansible poll | Scales independently of Ansible; agent runs continuously | Cron-based `df` via Ansible — fails at scale |
| OAM over custom pipeline | Native, read-only, no infrastructure to operate | Lambda + Kinesis + DynamoDB — more complexity, more failure modes |
| Alarms in monitoring account | Required by CloudWatch — composite alarms must reference same-account alarms | Alarms in member accounts — composite aggregation not possible cross-account |
| Normalized `DiskUsedPercent` | Identical metric contract for Linux and Windows alarms | Separate alarm thresholds per platform — confusing to operate |

---

## Cost model

CloudWatch is not free. This design replaces a third-party monitoring bill with a CloudWatch bill:

- Custom metrics: ~$0.30/metric/month × 4 metrics × monitored mount paths × instances
- Alarms: ~$0.10/alarm/month × 2 per instance + 1 composite per region
- OAM: no separate charge; observed metrics count toward source-account metric ingestion
- SSM Session Manager: no per-session charge; S3 session log storage at standard rates

Controlling which mount points are monitored (suppressing tmpfs/devtmpfs/squashfs) is the main cost lever.
