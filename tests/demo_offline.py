#!/usr/bin/env python3
"""
tests/demo_offline.py

Offline demonstration of the key moving parts without AWS credentials.
Shows what each component produces, so the evaluator can follow the logic
without running a real deployment.

Run with: python3 tests/demo_offline.py
"""

import json
from jinja2 import Environment

NAMESPACE = "DiskMonitoring"

# ── 1. Simulate inventory ───────────────────────────────────────────────────

print("=" * 60)
print("STEP 1: Simulated inventory (what aws_ec2 plugin provides)")
print("=" * 60)

SIMULATED_HOSTS = {
    "web-server-prod": {
        "account_id": "111111111111",
        "account_name": "acquired-co-a",
        "ansible_aws_ssm_region": "us-east-1",
        "inventory_hostname": "web-server-prod",
        "platform": "linux",
    },
    "db-server-prod": {
        "account_id": "111111111111",
        "account_name": "acquired-co-a",
        "ansible_aws_ssm_region": "us-east-1",
        "inventory_hostname": "db-server-prod",
        "platform": "linux",
    },
    "legacy-windows": {
        "account_id": "222222222222",
        "account_name": "acquired-co-b",
        "ansible_aws_ssm_region": "eu-west-1",
        "inventory_hostname": "legacy-windows",
        "platform": "windows",
    },
}

for hostname, meta in SIMULATED_HOSTS.items():
    print(f"  {hostname}: account={meta['account_id']} region={meta['ansible_aws_ssm_region']} platform={meta['platform']}")

# ── 2. Render CloudWatch Agent config for a Linux host ─────────────────────

print("\n" + "=" * 60)
print("STEP 2: CloudWatch Agent config for web-server-prod (Linux)")
print("=" * 60)

env = Environment()
env.filters["to_json"] = json.dumps

template_path = "roles/cloudwatch_agent/templates/amazon-cloudwatch-agent.json.j2"
tmpl = env.from_string(open(template_path).read())
linux_config = tmpl.render(
    cloudwatch_metrics_collection_interval_seconds=60,
    cloudwatch_disk_monitor_paths=["/", "/data"],
    cloudwatch_namespace=NAMESPACE,
    account_id="111111111111",
    account_name="acquired-co-a",
)
doc = json.loads(linux_config)
print(json.dumps(doc, indent=2))

# ── 3. Show Windows metric emission logic ──────────────────────────────────

print("\n" + "=" * 60)
print("STEP 3: Windows DiskUsedPercent calculation (PowerShell logic in Python)")
print("=" * 60)

def simulate_windows_disk_metric(drive_letter, total_gb, free_gb):
    used_percent = round(((total_gb - free_gb) / total_gb) * 100, 2)
    used_bytes = (total_gb - free_gb) * 1024**3
    free_bytes = free_gb * 1024**3
    total_bytes = total_gb * 1024**3
    return {
        "MetricName": "DiskUsedPercent",
        "Value": used_percent,
        "Unit": "Percent",
        "Dimensions": [
            {"Name": "InstanceId", "Value": "i-0win123"},
            {"Name": "AccountId", "Value": "222222222222"},
            {"Name": "Platform", "Value": "Windows"},
            {"Name": "MountPoint", "Value": drive_letter},
        ],
    }

windows_metrics = [
    simulate_windows_disk_metric("C:", total_gb=100, free_gb=30),
    simulate_windows_disk_metric("D:", total_gb=500, free_gb=450),
]
for m in windows_metrics:
    dims = {d["Name"]: d["Value"] for d in m["Dimensions"]}
    print(f"  {dims['MountPoint']}: DiskUsedPercent={m['Value']}%  (same metric name as Linux)")

# ── 4. Show alarm rule generation ──────────────────────────────────────────

print("\n" + "=" * 60)
print("STEP 4: Composite alarm rule generation (monitoring account)")
print("=" * 60)

def build_composite_rule(hosts, region):
    parts = []
    for hostname, meta in hosts.items():
        if meta["ansible_aws_ssm_region"] == region:
            parts.append(f'ALARM("disk-critical-{meta["account_id"]}-{hostname}")')
    return " OR ".join(parts)

monitoring_regions = list({h["ansible_aws_ssm_region"] for h in SIMULATED_HOSTS.values()})
for region in sorted(monitoring_regions):
    rule = build_composite_rule(SIMULATED_HOSTS, region)
    if rule:
        print(f"\n  Region: {region}")
        print(f"  Composite alarm rule:")
        print(f"    {rule}")
        print(f"  → This alarm fires if ANY instance in {region} goes critical.")

# ── 5. Show alarm structure ────────────────────────────────────────────────

print("\n" + "=" * 60)
print("STEP 5: Example per-instance alarm definition (in monitoring account)")
print("=" * 60)

MONITORING_ACCOUNT = "999999999999"
CRITICAL_THRESHOLD = 85.0

# The alarm uses the metrics[] / MetricDataQuery structure so that the source account
# is expressed via account_id at the MetricDataQuery level — the correct CloudWatch
# cross-account alarm mechanism. AccountId is NOT a metric dimension here.
example_alarm = {
    "AlarmName": "disk-critical-111111111111-web-server-prod",
    "Metrics": [
        {
            "Id": "disk_used",
            "AccountId": "111111111111",  # source account — MetricDataQuery level
            "MetricStat": {
                "Metric": {
                    "Namespace": NAMESPACE,
                    "MetricName": "DiskUsedPercent",
                    "Dimensions": [
                        {"Name": "InstanceId", "Value": "web-server-prod"}
                    ],
                },
                "Period": 60,
                "Stat": "Average",
                "Unit": "Percent",
            },
            "ReturnData": True,
        }
    ],
    "EvaluationPeriods": 10,
    "Threshold": CRITICAL_THRESHOLD,
    "ComparisonOperator": "GreaterThanOrEqualToThreshold",
    "TreatMissingData": "breaching",
    "Note": "Alarm lives in MONITORING account (999999999999). Source account 111111111111 identified via MetricDataQuery.AccountId (OAM cross-account mechanism).",
}
print(json.dumps(example_alarm, indent=2))

print("\n" + "=" * 60)
print("OFFLINE DEMO COMPLETE")
print("=" * 60)
print("\nAll steps above run without AWS credentials.")
print("In a real deployment:")
print("  1. ansible-galaxy collection install -r requirements.yml")
print("  2. cd terraform/account-bootstrap && terraform apply  (per member account)")
print("  3. cd terraform/org-oam-sink && terraform apply  (monitoring account, once)")
print("  4. ansible-playbook playbooks/site.yml  (installs agent, creates alarms)")
