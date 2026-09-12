# Run this once in the central monitoring account.
# Creates the OAM sink that all member accounts link to, plus the SNS topic
# that the composite alarm uses to notify on-call.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

variable "allowed_account_ids" {
  description = "Account IDs that are allowed to create OAM links to this sink."
  type        = list(string)
}

variable "alert_email" {
  description = "Email to subscribe to the disk alert SNS topic (optional)."
  type        = string
  default     = ""
}

locals {
  tags = {
    Project = "disk-monitoring"
    Role    = "monitoring"
  }
}

resource "aws_oam_sink" "main" {
  name = "disk-monitoring-sink"
  tags = local.tags
}

resource "aws_oam_sink_policy" "main" {
  sink_identifier = aws_oam_sink.main.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        AWS = [for id in var.allowed_account_ids : "arn:aws:iam::${id}:root"]
      }
      Action   = "oam:CreateLink"
      Resource = "*"
      Condition = {
        "ForAllValues:StringEquals" = {
          "oam:ResourceTypes" = ["AWS::CloudWatch::Metric", "AWS::Logs::LogGroup"]
        }
      }
    }]
  })
}

resource "aws_sns_topic" "alerts" {
  name         = "disk-monitoring-alerts"
  display_name = "Disk Monitoring Alerts"
  tags         = local.tags
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alert_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

output "oam_sink_arn" {
  description = "Pass this to account-bootstrap as monitoring_account_sink_arn."
  value       = aws_oam_sink.main.arn
}

output "sns_topic_arn" {
  value = aws_sns_topic.alerts.arn
}
