# emit-disk-metrics.ps1
# Runs as a scheduled task every minute. Calculates DiskUsedPercent for each
# logical disk and puts it as a custom CloudWatch metric.
# This gives Windows the same metric name (DiskUsedPercent) as Linux, so alarms
# and dashboards work identically across both platforms.
#
# Collection is platform-specific; telemetry semantics are platform-independent.

param(
    [string]$Namespace = "DiskMonitoring",
    [string]$Region    = (Invoke-RestMethod -Uri "http://169.254.169.254/latest/meta-data/placement/region" -TimeoutSec 3),
    [string]$InstanceId = (Invoke-RestMethod -Uri "http://169.254.169.254/latest/meta-data/instance-id" -TimeoutSec 3),
    [string]$AccountId  = (Invoke-RestMethod -Uri "http://169.254.169.254/latest/meta-data/identity-credentials/ec2/info" -TimeoutSec 3 | ConvertFrom-Json).AccountId,
    [string]$AccountName = $env:ACCOUNT_NAME
)

$disks = Get-WmiObject Win32_LogicalDisk -Filter "DriveType=3"

foreach ($disk in $disks) {
    if ($disk.Size -le 0) { continue }

    $usedPercent = [math]::Round((($disk.Size - $disk.FreeSpace) / $disk.Size) * 100, 2)
    $freeBytes   = $disk.FreeSpace
    $usedBytes   = $disk.Size - $disk.FreeSpace
    $totalBytes  = $disk.Size
    $mountPoint  = $disk.DeviceID

    $dimensions = @(
        @{ Name = "InstanceId";  Value = $InstanceId },
        @{ Name = "AccountId";   Value = $AccountId },
        @{ Name = "AccountName"; Value = if ($AccountName) { $AccountName } else { "unknown" } },
        @{ Name = "Platform";    Value = "Windows" },
        @{ Name = "MountPoint";  Value = $mountPoint }
    )

    $metrics = @(
        @{ MetricName = "DiskUsedPercent";   Value = $usedPercent; Unit = "Percent" },
        @{ MetricName = "DiskFreeBytes";     Value = $freeBytes;   Unit = "Bytes" },
        @{ MetricName = "DiskUsedBytes";     Value = $usedBytes;   Unit = "Bytes" },
        @{ MetricName = "DiskCapacityBytes"; Value = $totalBytes;  Unit = "Bytes" }
    ) | ForEach-Object {
        $m = $_
        @{
            MetricName = $m.MetricName
            Value      = $m.Value
            Unit       = $m.Unit
            Dimensions = $dimensions
        }
    }

    aws cloudwatch put-metric-data `
        --namespace $Namespace `
        --region    $Region `
        --metric-data (ConvertTo-Json $metrics -Compress)
}
