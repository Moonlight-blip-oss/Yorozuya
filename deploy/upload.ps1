<#
  在 Windows 上把「服务器需要的那些文件」传到服务器（一条命令搞定）
  ---------------------------------------------------------------
  用法（在项目根目录开 PowerShell）：
      .\deploy\upload.ps1 -Server 1.2.3.4
      .\deploy\upload.ps1 -Server 1.2.3.4 -User ubuntu        # 非 root 用户
      .\deploy\upload.ps1 -Server 1.2.3.4 -Port 2222

  传的是：app/ yorozuya/ renderer/ icon/ deploy/ requirements-server.txt .dockerignore
  不传：appdata（本机数据）、Yorozuya*.exe、db_config.json（含你的库密码）、build/、.venv/
#>
param(
  [Parameter(Mandatory = $true)][string]$Server,
  [string]$User = "root",
  [int]$Port = 22,
  [string]$Dest = "/srv/yorozuya"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot          # 项目根
Set-Location $root

$items = @("app", "yorozuya", "renderer", "icon", "deploy", "requirements-server.txt", ".dockerignore")
foreach ($i in $items) {
  if (-not (Test-Path (Join-Path $root $i))) { throw "缺文件：$i（请在项目根运行本脚本）" }
}
if (-not (Get-Command scp -ErrorAction SilentlyContinue)) { throw "没找到 scp —— 请用 WinSCP 手动拖，或装 OpenSSH 客户端" }

$target = "${User}@${Server}"
Write-Host "==> 建目录 $Dest" -ForegroundColor Cyan
ssh -p $Port $target "mkdir -p '$Dest'"

Write-Host "==> 上传：$($items -join ', ')" -ForegroundColor Cyan
# -O：新版 scp 用旧协议，避免部分服务器上的 sftp 子系统问题
scp -O -P $Port -r @items "${target}:${Dest}/"

Write-Host ""
Write-Host "传完了。接下来在服务器上：" -ForegroundColor Green
Write-Host "  ssh -p $Port $target"
Write-Host "  cd $Dest && bash deploy/install.sh"
Write-Host ""
Write-Host "★ 不要传 db_config.json（里面有你本机的数据库密码）和 appdata/" -ForegroundColor Yellow
