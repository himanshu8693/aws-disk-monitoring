"""
tests/test_alarm_rule_generation.py

Tests the composite alarm rule generation logic without requiring AWS credentials.
Run with: python3 tests/test_alarm_rule_generation.py
"""

import sys
import json


def build_composite_rule(hosts_with_metadata, region):
    """
    Python equivalent of the Ansible Jinja2 namespace+for-loop in disk_alerting/tasks/main.yml.
    Produces the alarm_rule string for a given region.
    This tests the logic itself, not the Ansible YAML syntax.
    """
    parts = []
    for hostname, meta in hosts_with_metadata.items():
        if meta.get("ansible_aws_ssm_region") == region:
            alarm_name = f'ALARM("disk-critical-{meta["account_id"]}-{hostname}")'
            parts.append(alarm_name)
    return " OR ".join(parts)


def test_single_instance_single_region():
    hosts = {
        "i-0abc123": {"account_id": "111111111111", "ansible_aws_ssm_region": "us-east-1"},
    }
    rule = build_composite_rule(hosts, "us-east-1")
    assert rule == 'ALARM("disk-critical-111111111111-i-0abc123")'
    print("PASS: single instance, single region")


def test_multiple_instances_same_account():
    hosts = {
        "i-0aaa": {"account_id": "111111111111", "ansible_aws_ssm_region": "us-east-1"},
        "i-0bbb": {"account_id": "111111111111", "ansible_aws_ssm_region": "us-east-1"},
    }
    rule = build_composite_rule(hosts, "us-east-1")
    assert 'ALARM("disk-critical-111111111111-i-0aaa")' in rule
    assert 'ALARM("disk-critical-111111111111-i-0bbb")' in rule
    assert " OR " in rule
    print("PASS: multiple instances, same account")


def test_multiple_accounts_same_region():
    """
    Two accounts, both with instances in us-east-1.
    The composite alarm must include instances from both accounts.
    Alarm names include account_id to prevent collision.
    """
    hosts = {
        "i-0aaa": {"account_id": "111111111111", "ansible_aws_ssm_region": "us-east-1"},
        "i-0bbb": {"account_id": "222222222222", "ansible_aws_ssm_region": "us-east-1"},
    }
    rule = build_composite_rule(hosts, "us-east-1")
    assert 'ALARM("disk-critical-111111111111-i-0aaa")' in rule
    assert 'ALARM("disk-critical-222222222222-i-0bbb")' in rule
    print("PASS: multiple accounts in same region — alarm names include account_id (no collision)")


def test_region_filtering():
    """
    Instances in different regions must produce separate composite alarms.
    eu-west-1 composite must not contain us-east-1 instance alarms.
    """
    hosts = {
        "i-0aaa": {"account_id": "111111111111", "ansible_aws_ssm_region": "us-east-1"},
        "i-0ccc": {"account_id": "222222222222", "ansible_aws_ssm_region": "eu-west-1"},
    }
    us_rule = build_composite_rule(hosts, "us-east-1")
    eu_rule = build_composite_rule(hosts, "eu-west-1")

    assert "i-0aaa" in us_rule
    assert "i-0ccc" not in us_rule
    assert "i-0ccc" in eu_rule
    assert "i-0aaa" not in eu_rule
    print("PASS: region filtering — composite alarms are scoped per-region")


def test_empty_region():
    """If no instances exist in a region, the rule is empty. The Ansible task skips in that case."""
    hosts = {
        "i-0aaa": {"account_id": "111111111111", "ansible_aws_ssm_region": "us-east-1"},
    }
    rule = build_composite_rule(hosts, "ap-southeast-1")
    assert rule == ""
    print("PASS: empty rule for region with no instances")


def test_alarm_name_format():
    """Alarm names follow the pattern disk-critical-<account_id>-<instance_id>."""
    hosts = {
        "i-0abc123def456": {"account_id": "123456789012", "ansible_aws_ssm_region": "us-east-1"},
    }
    rule = build_composite_rule(hosts, "us-east-1")
    assert 'ALARM("disk-critical-123456789012-i-0abc123def456")' in rule
    print("PASS: alarm name format is disk-critical-<account_id>-<instance_id>")


def test_metric_normalization():
    """Both Linux and Windows must emit DiskUsedPercent — verify config files."""
    import os
    base = os.path.join(os.path.dirname(__file__), "..")

    linux_template = open(
        os.path.join(base, "roles/cloudwatch_agent/templates/amazon-cloudwatch-agent.json.j2")
    ).read()
    assert '"DiskUsedPercent"' in linux_template, "Linux template must contain DiskUsedPercent"
    assert '"disk_used_percent"' in linux_template, "Linux source metric must be disk_used_percent"

    windows_script = open(
        os.path.join(base, "roles/cloudwatch_agent/files/emit-disk-metrics.ps1")
    ).read()
    assert "DiskUsedPercent" in windows_script, "Windows script must emit DiskUsedPercent"

    print("PASS: both Linux (CW Agent rename) and Windows (PowerShell) emit DiskUsedPercent")


def test_no_static_credentials():
    """Check that no AWS access keys appear in any source file."""
    import subprocess, os
    base = os.path.join(os.path.dirname(__file__), "..")
    result = subprocess.run(
        ["grep", "-r", "--include=*.yml", "--include=*.yaml", "--include=*.tf", "--include=*.py", "--include=*.ps1", "--include=*.j2", "--include=*.md", "AKIA", "roles/", "playbooks/", "inventory/", "terraform/"],
        capture_output=True, text=True, cwd=base,
    )
    assert result.stdout.strip() == "", f"Static key found: {result.stdout}"
    print("PASS: no static AKIA credentials in source files")


def test_inventory_uses_placement_region():
    """Verify inventory files use placement.region, not a hardcoded region string."""
    import os, glob
    base = os.path.join(os.path.dirname(__file__), "..")
    inventory_files = glob.glob(os.path.join(base, "inventory/accounts/*.yml"))
    assert len(inventory_files) > 0, "No inventory files found"
    for path in inventory_files:
        content = open(path).read()
        assert "ansible_aws_ssm_region: placement.region" in content, (
            f"{path}: ansible_aws_ssm_region must use placement.region, not a hardcoded value"
        )
    print(f"PASS: {len(inventory_files)} inventory file(s) use placement.region (not hardcoded)")


def test_correct_ansible_module_namespace():
    """All cloudwatch_metric_alarm calls must use amazon.aws, not community.aws."""
    import subprocess, os
    base = os.path.join(os.path.dirname(__file__), "..")
    result = subprocess.run(
        ["grep", "-r", "community.aws.cloudwatch_metric_alarm", "roles/", "playbooks/"],
        capture_output=True, text=True, cwd=base,
    )
    assert result.stdout.strip() == "", (
        f"Deprecated community.aws.cloudwatch_metric_alarm found: {result.stdout}"
    )
    print("PASS: no community.aws.cloudwatch_metric_alarm — all use amazon.aws")


def test_cw_agent_config_renders_valid_json():
    """Render the Linux CloudWatch Agent config template and verify it is valid JSON."""
    import json as _json
    from jinja2 import Environment
    import os

    base = os.path.join(os.path.dirname(__file__), "..")
    env = Environment()
    env.filters["to_json"] = _json.dumps

    template_path = os.path.join(
        base, "roles/cloudwatch_agent/templates/amazon-cloudwatch-agent.json.j2"
    )
    tmpl = env.from_string(open(template_path).read())
    rendered = tmpl.render(
        cloudwatch_metrics_collection_interval_seconds=60,
        cloudwatch_disk_monitor_paths=["/", "/data"],
        cloudwatch_namespace="DiskMonitoring",
        account_id="111111111111",
        account_name="acquired-co-a",
    )
    doc = _json.loads(rendered)
    measurements = [m["rename"] for m in doc["metrics"]["metrics_collected"]["disk"]["measurement"]]
    assert "DiskUsedPercent" in measurements
    assert doc["metrics"]["append_dimensions"]["Platform"] == "Linux"
    print("PASS: Linux CW Agent config renders valid JSON with DiskUsedPercent")


def test_treat_missing_data_breaching():
    """Critical alarms must use treat_missing_data: breaching."""
    import os
    base = os.path.join(os.path.dirname(__file__), "..")
    content = open(os.path.join(base, "roles/disk_alerting/tasks/main.yml")).read()
    assert "treat_missing_data: breaching" in content
    print("PASS: treat_missing_data: breaching is set on critical alarms")


def test_ssm_bootstrap_uses_explicit_sts():
    """ssm_bootstrap must assume role explicitly (not rely on default env credentials)."""
    import os
    base = os.path.join(os.path.dirname(__file__), "..")
    content = open(os.path.join(base, "roles/ssm_bootstrap/tasks/main.yml")).read()
    assert "sts assume-role" in content
    assert "AWS_ACCESS_KEY_ID" in content
    assert "no_log: true" in content
    print("PASS: ssm_bootstrap uses explicit STS AssumeRole with no_log")



def test_cross_account_alarm_uses_metrics_account_id():
    """
    Per-instance alarms must express the source account via the metrics[].account_id
    field (MetricDataQuery level), NOT as an 'AccountId' metric dimension.

    This is the correct CloudWatch cross-account alarm mechanism when alarms
    in a monitoring account target OAM-sourced metrics from member accounts.
    """
    import os
    base = os.path.join(os.path.dirname(__file__), "..")
    content = open(os.path.join(base, "roles/disk_alerting/tasks/main.yml")).read()

    # Correct: account_id must appear inside the metrics[] block
    assert "account_id: \"{{ hostvars[item]['account_id'] }}\"" in content, \
        "disk_alerting must set account_id inside the metrics[] MetricDataQuery block"

    # Correct: must use metrics: (MetricDataQuery), not bare metric:/namespace:/statistic:
    assert "metrics:" in content, \
        "disk_alerting must use the metrics[] parameter for cross-account alarms"

    # Wrong: AccountId must NOT appear as a dimensions entry in alarm tasks
    import re
    # Look for the pattern: dimensions block containing AccountId
    # This would be wrong — AccountId should not be an alarm metric dimension
    bad_pattern = re.search(
        r'dimensions\s*:\s*\n(\s*-\s*name\s*:.*\n\s*value\s*:.*\n)*\s*-\s*name\s*:\s*AccountId',
        content
    )
    assert bad_pattern is None, \
        "AccountId must not appear as a metric dimension in alarm task dimensions block"

    print("PASS: cross-account alarm uses metrics[].account_id (not AccountId dimension)")


def test_ssm_connection_plugin_canonical_namespace():
    """
    Both inventory files must use the canonical amazon.aws.aws_ssm connection plugin,
    not the legacy community.aws.aws_ssm name.
    """
    import os, glob
    base = os.path.join(os.path.dirname(__file__), "..")
    inventory_files = glob.glob(os.path.join(base, "inventory/accounts/*.yml"))
    assert len(inventory_files) > 0, "No inventory files found"

    for path in inventory_files:
        content = open(path).read()
        assert "amazon.aws.aws_ssm" in content, \
            f"{path}: ansible_connection must use amazon.aws.aws_ssm"
        assert "community.aws.aws_ssm" not in content, \
            f"{path}: legacy community.aws.aws_ssm found — must be amazon.aws.aws_ssm"

    # Also confirm no stale community.aws.aws_ssm in docs
    doc_files = glob.glob(os.path.join(base, "docs/*.md"))
    for path in doc_files:
        content = open(path).read()
        assert "community.aws.aws_ssm" not in content, \
            f"{path}: stale community.aws.aws_ssm reference found in documentation"

    print(f"PASS: all {len(inventory_files)} inventory file(s) use amazon.aws.aws_ssm")


if __name__ == "__main__":
    tests = [
        test_single_instance_single_region,
        test_multiple_instances_same_account,
        test_multiple_accounts_same_region,
        test_region_filtering,
        test_empty_region,
        test_alarm_name_format,
        test_metric_normalization,
        test_no_static_credentials,
        test_inventory_uses_placement_region,
        test_correct_ansible_module_namespace,
        test_cw_agent_config_renders_valid_json,
        test_treat_missing_data_breaching,
        test_ssm_bootstrap_uses_explicit_sts,
        test_cross_account_alarm_uses_metrics_account_id,
        test_ssm_connection_plugin_canonical_namespace,
    ]

    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"FAIL: {t.__name__}: {e}")
            failed += 1

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
