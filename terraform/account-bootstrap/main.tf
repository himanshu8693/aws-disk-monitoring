# Run this once per member account when onboarding a new acquisition.
# It creates everything Ansible needs to reach the instances and everything
# the instances need to push metrics.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}


provider "aws" {
  region = var.aws_region
}

variable "automation_account_id" {
  description = "Account ID where Ansible runs (the automation account)."
  type        = string
}

variable "monitoring_account_sink_arn" {
  description = "OAM sink ARN from the monitoring account (output of org-oam-sink). Leave empty for single-account deployments — OAM links to the same account are not permitted by AWS."
  type        = string
  default     = ""
}

variable "account_name" {
  description = "Short name for this account, used in resource names (e.g. acme-corp)."
  type        = string
}

variable "aws_region" {
  description = "AWS region where regional resources are created."
  type        = string
  default     = "us-east-1"
}

locals {
  tags = {
    Project     = "disk-monitoring"
    AccountName = var.account_name
  }
}

data "aws_caller_identity" "current" {}

# Cross-account role — assumed by the Ansible control node in the automation account.
# Scoped to the minimum needed: describe instances, start SSM sessions, use the S3 transfer bucket.
data "aws_iam_policy_document" "automation_trust" {
  statement {
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${var.automation_account_id}:root"]
    }
    actions = ["sts:AssumeRole", "sts:SetSourceIdentity"]
    condition {
      test     = "StringEquals"
      variable = "sts:ExternalId"
      values   = ["disk-monitoring-${data.aws_caller_identity.current.account_id}"]
    }
  }
}

data "aws_iam_policy_document" "automation_perms" {
  statement {
    actions   = ["ec2:DescribeInstances", "ec2:DescribeTags", "ec2:DescribeRegions"]
    resources = ["*"]
  }
  statement {
    actions = [
      "ssm:StartSession",
      "ssm:SendCommand",
      "ssm:GetCommandInvocation",
      "ssm:DescribeInstanceInformation",
      "ssm:ListCommandInvocations",
      "ssm:TerminateSession",
    ]
    resources = ["*"]
  }
  statement {
    actions = ["s3:PutObject", "s3:GetObject", "s3:ListBucket", "s3:DeleteObject"]
    resources = [
      "arn:aws:s3:::disk-monitoring-ssm-${var.account_name}",
      "arn:aws:s3:::disk-monitoring-ssm-${var.account_name}/*",
    ]
  }
}

resource "aws_iam_role" "automation" {
  name               = "DiskMonitoringAutomationRole"
  assume_role_policy = data.aws_iam_policy_document.automation_trust.json
  tags               = local.tags
}

resource "aws_iam_role_policy" "automation" {
  name   = "DiskMonitoringAutomationPolicy"
  role   = aws_iam_role.automation.id
  policy = data.aws_iam_policy_document.automation_perms.json
}

# Instance profile — attached to every EC2 in this account.
# Lets the SSM agent register and lets the CloudWatch agent push metrics.
resource "aws_iam_role" "instance" {
  name = "DiskMonitoringInstanceRole"
  tags = local.tags

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "cw_agent" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"
}

resource "aws_iam_instance_profile" "instance" {
  name = "DiskMonitoringInstanceProfile"
  role = aws_iam_role.instance.name
  tags = local.tags
}

# S3 bucket used by the SSM session plugin to transfer files between the
# control node and the instance. 7-day expiry keeps it clean.
resource "aws_s3_bucket" "ssm" {
  bucket = "disk-monitoring-ssm-${var.account_name}"
  tags   = local.tags
}

resource "aws_s3_bucket_lifecycle_configuration" "ssm" {
  bucket = aws_s3_bucket.ssm.id
  rule {
    id     = "expire"
    status = "Enabled"
    expiration { days = 7 }
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "ssm" {
  bucket = aws_s3_bucket.ssm.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "ssm" {
  bucket                  = aws_s3_bucket.ssm.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# OAM link — gives the central monitoring account read-only access to metrics in this account.
# Skipped in single-account deployments: AWS prohibits a link whose sink is in the same account.
# The sink account ID is extracted from field 5 of the colon-delimited ARN.
locals {
  sink_account_id = var.monitoring_account_sink_arn != "" ? split(":", var.monitoring_account_sink_arn)[4] : ""
  create_oam_link = var.monitoring_account_sink_arn != "" && local.sink_account_id != data.aws_caller_identity.current.account_id
}

resource "aws_oam_link" "to_monitoring" {
  count           = local.create_oam_link ? 1 : 0
  label_template  = "$AccountName"
  resource_types  = ["AWS::CloudWatch::Metric", "AWS::Logs::LogGroup"]
  sink_identifier = var.monitoring_account_sink_arn
  tags            = local.tags
}

output "automation_role_arn" {
  description = "Use this ARN in the inventory file's assume_role_arn."
  value       = aws_iam_role.automation.arn
}

output "instance_profile_name" {
  description = "Attach this profile to all EC2 instances in this account."
  value       = aws_iam_instance_profile.instance.name
}

output "ssm_bucket" {
  description = "Set as ansible_aws_ssm_bucket_name in the inventory file."
  value       = aws_s3_bucket.ssm.bucket
}
