# 启动本地 Web UI。
#
# 用这个脚本而不是直接 python -m src.api.app，是因为日志被重定向到文件时
# 需要 UTF-8 编码，否则中文和颜色码会变成乱码。
#
# 用法：
#   .\run_web.ps1                  # http://127.0.0.1:8765
#   .\run_web.ps1 --port 9000
#   .\run_web.ps1 --no-access-log  # 只保留启动日志，不刷每条请求

param(
    [switch]$NoAccessLog,
    [int]$Port = 8765,
    [string]$Host = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"

$arguments = @("-m", "src.api.app", "--host", $Host, "--port", "$Port")
if ($NoAccessLog) { $arguments += "--no-access-log" }

Write-Host "启动 Web UI: http://${Host}:${Port}" -ForegroundColor Cyan
python @arguments
