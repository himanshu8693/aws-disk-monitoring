# Architecture

## 1. Account topology

```mermaid
graph TD
    subgraph AUTO["🔧 Automation Account"]
        SSO["IAM Identity Center"]
        ANSIBLE["Ansible Control Node"]
        SSO --> ANSIBLE
    end

    subgraph ACCA["🏢 Member Account A  ·  111111111111"]
        subgraph R_USE1["us-east-1"]
            EC2A1["EC2 Linux"] & EC2A2["EC2 Windows"] --> CWA1["CloudWatch\nDiskMonitoring"]
        end
        subgraph R_USW2["us-west-2"]
            EC2A3["EC2 Linux"] --> CWA2["CloudWatch\nDiskMonitoring"]
        end
    end

    subgraph ACCB["🏢 Member Account B  ·  222222222222"]
        subgraph R_EUW1["eu-west-1"]
            EC2B1["EC2 Linux"] --> CWB1["CloudWatch\nDiskMonitoring"]
        end
    end

    subgraph MON["📊 Monitoring Account  ·  999999999999"]
        subgraph MON_USE1["us-east-1"]
            SINK1["OAM Sink"] --> ALM1["Alarms + SNS"]
        end
        subgraph MON_USW2["us-west-2"]
            SINK2["OAM Sink"] --> ALM2["Alarms + SNS"]
        end
        subgraph MON_EUW1["eu-west-1"]
            SINK3["OAM Sink"] --> ALM3["Alarms + SNS"]
        end
    end

    ANSIBLE -->|"sts:AssumeRole + ExternalId\nSSM Session"| ACCA & ACCB
    ANSIBLE -->|"Creates alarms"| MON
    CWA1 -->|"OAM link"| SINK1
    CWA2 -->|"OAM link"| SINK2
    CWB1 -->|"OAM link"| SINK3
    ALM1 & ALM2 & ALM3 --> ONCALL["📟 On-call"]

    classDef account fill:#1a2332,color:#e8edf2,stroke:#4a90d9,stroke-width:2px
    classDef mon fill:#1c1c3a,color:#bc8cff,stroke:#8957e5,stroke-width:1px
    classDef ec2l fill:#0f3d1f,color:#56d364,stroke:#3fb950,stroke-width:1px
    classDef ec2w fill:#2d1a00,color:#f0883e,stroke:#d29922,stroke-width:1px
    classDef auto fill:#0d2137,color:#79c0ff,stroke:#388bfd,stroke-width:2px

    class AUTO auto
    class ACCA,ACCB account
    class MON mon
    class EC2A1,EC2A3,EC2B1 ec2l
    class EC2A2 ec2w
```

---

## 2. End-to-end disk monitoring flow

```mermaid
flowchart TD
    subgraph INST["EC2 Instance  (member account)"]
        LINUX["CloudWatch Agent\nreads disk_used_percent\nrenames → DiskUsedPercent"]
        WIN["PowerShell Scheduled Task\ncalculates Size-FreeSpace / Size\nemits DiskUsedPercent"]
    end

    subgraph CWM["CloudWatch  (member account)"]
        NS["Namespace: DiskMonitoring\nDiskUsedPercent · DiskFreeBytes\nDiskUsedBytes · DiskCapacityBytes\nDimensions: InstanceId · AccountId\nPlatform · AccountName"]
    end

    LINUX -->|"PutMetricData every 60s"| NS
    WIN   -->|"PutMetricData every 60s"| NS

    subgraph OAM["OAM  (read-only bridge)"]
        LINK["OAM Link\nmember account → monitoring sink\nresource: AWS::CloudWatch::Metric"]
    end

    NS -->|"no data copy"| LINK

    subgraph MONA["Monitoring Account"]
        PA["Per-instance alarm\ndisk-critical-acct-instanceid\nmetrics account_id at MetricDataQuery level\nDiskUsedPercent >= 85%\nevaluation_periods: 10 x 60s\ntreat_missing_data: breaching"]
        CA["Regional composite alarm\nfleet-disk-critical-region\nALARM disk-critical-A OR ALARM disk-critical-B\none page for N breaching instances"]
        SNS2["SNS Topic"]
        PA -->|"any instance breaching"| CA --> SNS2
    end

    LINK --> PA
    SNS2 --> ALERT["📟 On-call"]

    style INST  fill:#0f3d1f,stroke:#3fb950,color:#56d364
    style CWM   fill:#0d2137,stroke:#388bfd,color:#79c0ff
    style OAM   fill:#1a2332,stroke:#4a90d9,color:#e8edf2
    style MONA  fill:#1c1c3a,stroke:#8957e5,color:#bc8cff
    style ALERT fill:#2d1a00,stroke:#d29922,color:#ffa657
```

**Why `account_id` is at the `MetricDataQuery` level, not a metric dimension:**  
CloudWatch cross-account alarms route the metric lookup via `metrics[].account_id` in the alarm definition. Setting `AccountId` as a metric dimension has no effect on alarm routing — the alarm would look for the metric in the monitoring account's own namespace and never find it.

**Why alarms live in the monitoring account:**  
CloudWatch composite alarms can only reference alarms in the same account and region. Per-instance alarms must therefore also live in the monitoring account, not in member accounts.

---

## 3. New account onboarding

```mermaid
flowchart LR
    T["terraform apply\naccount-bootstrap\n\nCreates:\nDiskMonitoringAutomationRole\nDiskMonitoringInstanceRole\nInstance profile\nS3 SSM bucket\nOAM link to monitoring sink"]

    I["Add inventory file\ninventory/accounts/new-account.aws_ec2.yml\n\nSets: assume_role_arn\nregions · account_id\nansible_connection: amazon.aws.aws_ssm"]

    R["ansible-playbook site.yml\n\nPlay 1: ssm_bootstrap\nverify SSM reachability\n\nPlay 2: cloudwatch_agent\ninstall agent or PS task\n\nPlay 3: disk_alerting\ncreate alarms in monitoring account"]

    DONE["All running EC2s monitored\nAlarms in monitoring account\nSNS notifications active"]

    T --> I --> R --> DONE

    style T    fill:#0d2137,stroke:#388bfd,color:#79c0ff
    style I    fill:#0f3d1f,stroke:#3fb950,color:#56d364
    style R    fill:#1c1c3a,stroke:#8957e5,color:#bc8cff
    style DONE fill:#1a3a1a,stroke:#3fb950,color:#7ee787
```

---

## IAM trust boundaries

| Identity | Can do | Cannot do |
|---|---|---|
| Automation account | AssumeRole into member accounts, start SSM sessions, read/write SSM S3 bucket | Create/delete resources, access data, call any other service |
| Member account instance | Push metrics to CloudWatch, register with SSM | Access other accounts, modify IAM, read secrets |
| Monitoring account | Read cross-account metrics via OAM, manage alarms and SNS | AssumeRole into member accounts, modify member-account resources |

`DiskMonitoringAutomationRole` trust policy requires `ExternalId: disk-monitoring-<account-id>` — prevents confused-deputy attacks where a shared automation account is tricked into assuming roles it was not intended to manage.
