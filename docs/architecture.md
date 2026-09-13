# Architecture

## 1. Account topology

```mermaid
graph TD
    subgraph ORG["☁️  AWS Organization"]

        subgraph AUTO["🔧 Automation Account"]
            SSO["IAM Identity Center\n(no static keys)"]
            ANSIBLE["Ansible Control Node\nsite.yml playbook"]
            SSO --> ANSIBLE
        end

        subgraph MON["📊 Monitoring Account  ·  999999999999"]
            subgraph MON_USE1["us-east-1"]
                SINK1["OAM Sink"]
                ALARMS1["CloudWatch Alarms\nper-instance + composite"]
                SNS1["SNS Topic\ndisk-monitoring-alerts"]
                SINK1 --> ALARMS1 --> SNS1
            end
            subgraph MON_USW2["us-west-2"]
                SINK2["OAM Sink"]
                ALARMS2["CloudWatch Alarms"]
                SNS2["SNS Topic"]
                SINK2 --> ALARMS2 --> SNS2
            end
            subgraph MON_EUW1["eu-west-1"]
                SINK3["OAM Sink"]
                ALARMS3["CloudWatch Alarms"]
                SNS3["SNS Topic"]
                SINK3 --> ALARMS3 --> SNS3
            end
            DASH["CloudWatch Dashboard\n(cross-region)"]
        end

        subgraph ACCA["🏢 Member Account A  ·  111111111111"]
            subgraph ACCA_USE1["us-east-1"]
                EC2A1["EC2 Linux\nDiskMonitoringInstanceProfile"]
                EC2A2["EC2 Windows\nDiskMonitoringInstanceProfile"]
                CWA1["CloudWatch\nDiskMonitoring namespace"]
                EC2A1 -->|"PutMetricData\nevery 60s"| CWA1
                EC2A2 -->|"PutMetricData\nPowerShell task"| CWA1
            end
            subgraph ACCA_USW2["us-west-2"]
                EC2A3["EC2 Linux"]
                CWA2["CloudWatch"]
                EC2A3 -->|"PutMetricData"| CWA2
            end
        end

        subgraph ACCB["🏢 Member Account B  ·  222222222222"]
            subgraph ACCB_EUW1["eu-west-1"]
                EC2B1["EC2 Linux\nDiskMonitoringInstanceProfile"]
                CWB1["CloudWatch\nDiskMonitoring namespace"]
                EC2B1 -->|"PutMetricData\nevery 60s"| CWB1
            end
        end

    end

    %% Ansible → member accounts
    ANSIBLE -->|"sts:AssumeRole\n+ ExternalId"| ACCA
    ANSIBLE -->|"sts:AssumeRole\n+ ExternalId"| ACCB
    ANSIBLE -->|"Creates alarms\n& SNS topics"| MON

    %% OAM links
    CWA1 -->|"OAM link\nread-only"| SINK1
    CWA2 -->|"OAM link\nread-only"| SINK2
    CWB1 -->|"OAM link\nread-only"| SINK3

    %% Notifications
    SNS1 -->|"Alert"| ONCALL["📟 On-call\nSlack / PagerDuty"]
    SNS2 --> ONCALL
    SNS3 --> ONCALL

    classDef account fill:#1a2332,color:#e8edf2,stroke:#4a90d9,stroke-width:2px
    classDef service fill:#0d2137,color:#79c0ff,stroke:#388bfd,stroke-width:1px
    classDef ec2linux fill:#0f3d1f,color:#56d364,stroke:#3fb950,stroke-width:1px
    classDef ec2win fill:#2d1a00,color:#f0883e,stroke:#d29922,stroke-width:1px
    classDef monitoring fill:#1c1c3a,color:#bc8cff,stroke:#8957e5,stroke-width:1px
    classDef external fill:#2d1a00,color:#ffa657,stroke:#d29922,stroke-width:1px

    class AUTO,MON,ACCA,ACCB account
    class SSO,ANSIBLE,CWA1,CWA2,CWB1 service
    class EC2A1,EC2A3,EC2B1 ec2linux
    class EC2A2 ec2win
    class SINK1,SINK2,SINK3,ALARMS1,ALARMS2,ALARMS3,SNS1,SNS2,SNS3,DASH monitoring
    class ONCALL external
```

---

## 2. Metric collection — Linux vs Windows

```mermaid
flowchart LR
    subgraph LINUX["🐧 Linux EC2"]
        direction TB
        OS_L["OS disk stats\n/proc/mounts"]
        CWA["CloudWatch Agent\namazon-cloudwatch-agent"]
        CFG["agent config\n.json.j2 template\n─────────────\nrename:\n  disk_used_percent\n  → DiskUsedPercent"]
        OS_L --> CWA --> CFG
    end

    subgraph WINDOWS["🪟 Windows EC2"]
        direction TB
        WMI["Win32_LogicalDisk\nWMI object"]
        PS["emit-disk-metrics.ps1\nScheduled Task · SYSTEM\nevery 1 minute"]
        CALC["used% =\n(Size − FreeSpace)\n ─────────────── × 100\n      Size"]
        WMI --> PS --> CALC
    end

    subgraph METRIC["📈 CloudWatch — DiskMonitoring namespace"]
        direction TB
        M1["DiskUsedPercent  (Percent)"]
        M2["DiskFreeBytes    (Bytes)"]
        M3["DiskUsedBytes    (Bytes)"]
        M4["DiskCapacityBytes(Bytes)"]
        DIMS["Dimensions on every point:\nInstanceId · AccountId\nAccountName · Platform\nMountPoint"]
    end

    CFG  -->|"PutMetricData\nevery 60s"| M1
    CALC -->|"aws cloudwatch\nput-metric-data"| M1
    CFG  --> M2 & M3 & M4
    CALC --> M2 & M3 & M4
    M1 & M2 & M3 & M4 --- DIMS

    style LINUX   fill:#0f3d1f,stroke:#3fb950,color:#56d364
    style WINDOWS fill:#2d1a00,stroke:#d29922,color:#f0883e
    style METRIC  fill:#0d2137,stroke:#388bfd,color:#79c0ff
```

---

## 3. Cross-account alarm chain

```mermaid
flowchart TD
    subgraph SRC["Member Account (source)"]
        AGENT["CloudWatch Agent /\nPowerShell task"]
        CWM["CloudWatch Metrics\nDiskMonitoring::DiskUsedPercent\n─────────────────────────\nPeriod: 60s  ·  Stat: Average"]
        AGENT -->|PutMetricData| CWM
    end

    subgraph LINK["OAM  (read-only bridge)"]
        OAMLINK["OAM Link\naccount 111… → monitoring sink\n─────────────────────────\nResource type:\nAWS::CloudWatch::Metric"]
    end

    CWM -->|"read-only\n(no data copy)"| OAMLINK

    subgraph MON["Monitoring Account"]
        subgraph INST["Per-instance alarm  ×N"]
            PA["alarm: disk-critical-111…-i-0abc\n─────────────────────────────\nmetrics[].account_id: 111…\nDiskUsedPercent ≥ 85%\nevaluation_periods: 10  ×  60s\ntreat_missing_data: breaching"]
        end
        subgraph COMP["Regional composite alarm  ×1 per region"]
            CA["alarm: fleet-disk-critical-us-east-1\n─────────────────────────────────\nrule: ALARM(\"disk-critical-111-i-0abc\")\n   OR ALARM(\"disk-critical-111-i-0def\")\n   OR ALARM(\"disk-critical-222-i-0xyz\")"]
        end
        SNS_T["SNS Topic\ndisk-monitoring-alerts"]
        PA -->|"any instance\nin ALARM state"| CA
        CA -->|"1 notification\nfor N instances"| SNS_T
    end

    OAMLINK --> PA

    ALERT["📟 On-call\nSlack / PagerDuty / Email"]
    SNS_T --> ALERT

    note1["⚠️  account_id is set at\nthe MetricDataQuery level\n(metrics[].account_id)\nnot as a metric dimension"]

    style SRC   fill:#0f3d1f,stroke:#3fb950,color:#56d364
    style LINK  fill:#1a2332,stroke:#4a90d9,color:#e8edf2
    style MON   fill:#1c1c3a,stroke:#8957e5,color:#bc8cff
    style ALERT fill:#2d1a00,stroke:#d29922,color:#ffa657
    style note1 fill:#2d2208,stroke:#d29922,color:#e3b341,font-size:11px
```

---

## 4. Ansible role sequence

```mermaid
sequenceDiagram
    autonumber
    participant OPS as Operator / CI
    participant CTL as Ansible Control Node
    participant STS as AWS STS
    participant SSM as AWS SSM
    participant EC2 as EC2 Instance
    participant CW  as CloudWatch (member)
    participant OAM as OAM Link
    participant CWMON as CloudWatch (monitoring)

    OPS->>CTL: ansible-playbook site.yml

    Note over CTL,STS: Play 1 — ssm_bootstrap (pre-flight)
    CTL->>STS: AssumeRole DiskMonitoringAutomationRole<br/>(ExternalId, 900s session)
    STS-->>CTL: Temporary credentials
    CTL->>SSM: DescribeInstanceInformation
    SSM-->>CTL: PingStatus = Online ✓

    Note over CTL,EC2: Play 2 — cloudwatch_agent (config)
    CTL->>EC2: SSM Session (amazon.aws.aws_ssm)<br/>no SSH, no open inbound ports
    EC2-->>CTL: Connection established
    CTL->>EC2: Install CloudWatch Agent (Linux RPM/DEB)<br/>or deploy emit-disk-metrics.ps1 (Windows)
    CTL->>EC2: Render agent config template → /opt/aws/.../config.json
    CTL->>EC2: Start / restart agent service

    Note over EC2,CW: Continuous — every 60 seconds (Ansible not involved)
    loop Every 60s
        EC2->>CW: PutMetricData: DiskUsedPercent, DiskFreeBytes…
    end

    Note over CW,CWMON: Always-on — OAM makes metrics readable in monitoring account
    CW-->>OAM: metrics visible via OAM link
    OAM-->>CWMON: read-only metric access

    Note over CTL,CWMON: Play 3 — disk_alerting (control-plane only, no EC2 connection)
    CTL->>CWMON: CreateAlarm: disk-critical-111-i-0abc<br/>(metrics[].account_id = 111…)
    CTL->>CWMON: CreateAlarm: disk-warning-111-i-0abc
    CTL->>CWMON: CreateAlarm: fleet-disk-critical-us-east-1<br/>(composite rule)
    CTL->>CWMON: CreateTopic: disk-monitoring-alerts
```

---

## 5. New account onboarding

```mermaid
flowchart LR
    subgraph TF["Step 1 — Terraform  (once per account)"]
        T1["terraform apply\naccount-bootstrap/"]
        T2["Creates:\n✦ DiskMonitoringAutomationRole\n  (trust: automation account + ExternalId)\n✦ DiskMonitoringInstanceRole\n  + instance profile\n✦ S3 SSM transfer bucket\n  (KMS, 7-day lifecycle)\n✦ OAM link → monitoring sink"]
        T1 --> T2
    end

    subgraph INV["Step 2 — Inventory file  (one file, ~10 lines)"]
        I1["inventory/accounts/\nnew-account.aws_ec2.yml"]
        I2["Sets:\n· assume_role_arn\n· regions\n· account_id / account_name\n· ansible_connection:\n  amazon.aws.aws_ssm"]
        I1 --> I2
    end

    subgraph RUN["Step 3 — Ansible run"]
        R1["ansible-playbook\nplaybooks/site.yml"]
        R2["ssm_bootstrap\n→ verify reachability"]
        R3["cloudwatch_agent\n→ install + configure"]
        R4["disk_alerting\n→ create alarms"]
        R1 --> R2 --> R3 --> R4
    end

    subgraph DONE["✅ Outcome"]
        D1["All running EC2s monitored\nAlarms in monitoring account\nOne SNS notification channel"]
    end

    TF --> INV --> RUN --> DONE

    style TF   fill:#0d2137,stroke:#388bfd,color:#79c0ff
    style INV  fill:#0f3d1f,stroke:#3fb950,color:#56d364
    style RUN  fill:#1c1c3a,stroke:#8957e5,color:#bc8cff
    style DONE fill:#1a3a1a,stroke:#3fb950,color:#7ee787
```

---

## 6. IAM trust model

```mermaid
graph LR
    subgraph AUTO_ACC["Automation Account"]
        OIDC["OIDC / IAM Identity Center\nShort-lived STS token"]
        ANSIBLE2["Ansible\ncontrol node"]
        OIDC --> ANSIBLE2
    end

    subgraph MEMBER["Member Account  (per acquired company)"]
        AUTO_ROLE["DiskMonitoringAutomationRole\n─────────────────────────────\nTrust: automation account root\nCondition: ExternalId =\n  disk-monitoring-{account-id}\n─────────────────────────────\nPerms: ec2:Describe*\n  ssm:StartSession\n  ssm:DescribeInstanceInformation\n  s3:PutObject (SSM bucket only)"]
        INST_ROLE["DiskMonitoringInstanceRole\n(EC2 instance profile)\n─────────────────────────────\nAmazonSSMManagedInstanceCore\nCloudWatchAgentServerPolicy"]
        S3["S3 SSM bucket\nKMS-encrypted\n7-day lifecycle"]
        AUTO_ROLE --> S3
    end

    ANSIBLE2 -->|"sts:AssumeRole\n+ ExternalId"| AUTO_ROLE
    AUTO_ROLE -->|"SSM Session\nvia instance profile"| INST_ROLE

    subgraph MON_ACC["Monitoring Account"]
        MON_ROLE["Monitoring identity\n(separate role)\n─────────────────────────\ncloudwatch:PutMetricAlarm\ncloudwatch:DescribeAlarms\nsns:CreateTopic"]
    end

    ANSIBLE2 -->|"sts:AssumeRole"| MON_ROLE

    subgraph BLOCK["🔒 Security properties"]
        B1["No static credentials\nanywhere in the system"]
        B2["ExternalId prevents\nconfused-deputy attack"]
        B3["Monitoring account has\nzero trust into member accounts\n(OAM is read-only data plane)"]
        B4["Zero inbound ports on EC2\nSSM uses outbound HTTPS 443"]
    end

    style AUTO_ACC fill:#0d2137,stroke:#388bfd,color:#79c0ff
    style MEMBER   fill:#0f3d1f,stroke:#3fb950,color:#56d364
    style MON_ACC  fill:#1c1c3a,stroke:#8957e5,color:#bc8cff
    style BLOCK    fill:#2d1a00,stroke:#d29922,color:#e3b341
```

---

## Why alarms live in the monitoring account

CloudWatch composite alarms can only reference alarms in the **same account and region**. Placing per-instance alarms in member accounts and the composite alarm in the monitoring account is therefore invalid — the composite could not reference them cross-account.

The correct model: per-instance alarms **and** composite alarms both live in the monitoring account, watching metrics that OAM has made readable there. Member accounts only run the CloudWatch Agent; they hold no alarms managed by this system.

## Multi-region design

OAM is regional. Each (source-account × source-region) pair needs its own OAM link. The `account-bootstrap` Terraform module creates one OAM link per region. The `disk_alerting` role creates alarms in the monitoring account in the region matching each instance's `ansible_aws_ssm_region`.

**Constraint:** An alarm in `eu-west-1` cannot reference a metric in `us-east-1`. Each region is independently alarmed. A single CloudWatch dashboard can aggregate the cross-region view for operators.

## Ansible's role in this architecture

Ansible is an **onboarding and drift-correction tool**, not a telemetry collection engine.

| Ansible does | Ansible does NOT do |
|---|---|
| Install CloudWatch Agent (or PS task) | Poll disk usage every minute |
| Create CloudWatch alarms | Collect or forward metrics |
| Verify SSM reachability pre-flight | Run continuously on instances |
| Correct configuration drift (nightly) | Replace the agent push model |

This distinction matters at scale: Ansible polling 5,000 instances every minute would be impractical. The agent-push model scales to tens of thousands of instances with zero Ansible involvement after initial setup.
