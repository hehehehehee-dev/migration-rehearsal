param(
    [string]$Endpoint = "http://127.0.0.1:11455/v1",
    [string]$Model = "qwen2.5-coder-7b",
    [string]$Provider = "custom",
    [ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9-]*$')]
    [string]$RunName = ('run-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8))
)
$ErrorActionPreference = 'Stop'
$project = $PSScriptRoot
$runs = Join-Path $project '.tooling\codegen-natural'
$stage = Join-Path $runs $RunName
$shim = Join-Path $project '.tooling\node_modules\.bin\synapse.cmd'
$toolingPython = Join-Path $project '.tooling\python'
if (!(Test-Path -LiteralPath $shim)) {
    throw 'Install the pinned Synapse CLI with npm install --prefix .tooling @2ndbrainlabs-ai/synapse-cli@0.1.9'
}
New-Item -ItemType Directory -Path $runs -Force | Out-Null
New-Item -ItemType Directory -Path $stage | Out-Null
Copy-Item -LiteralPath (Join-Path $project 'rehearsal.py'),(Join-Path $project 'playlist.py'),(Join-Path $project 'mcp_plan.py'),(Join-Path $project 'requirements.txt') -Destination $stage
if (Test-Path -LiteralPath $toolingPython) {
    $env:PYTHONPATH = "$toolingPython;$toolingPython/win32;$toolingPython/win32/lib;$toolingPython/pywin32_system32;$env:PYTHONPATH"
}
$env:SYNAPSE_TELEMETRY = '0'
$intent = 'Build one release-review tool named rehearse_database_migration. After checking approval for the typed plan, use a disposable snapshot and record its original state before attempting the change. Check the result and give the reviewer the final evidence report. Use the common workflow for both price and playlist changes, including the shared verifier. Pass the full return value of each existing phase to the next and let the phases carry any errors through to the report. Keep the implementation straight-line. Its input is plan, with the existing MigrationPlan type from mcp_plan. Include valid Python imports for the input type and the selected functions.'
$intent | Set-Content -LiteralPath (Join-Path $stage 'intent.txt') -Encoding utf8
Write-Output "Build directory .tooling/codegen-natural/$RunName"
Push-Location -LiteralPath $stage
try {
    & $shim build --discover --local --custom --query $intent --provider $Provider --llm-base-url $Endpoint --model $Model --max-time 6 2>&1 |
        Tee-Object -FilePath (Join-Path $stage 'synapse-cli.log')
    if ($LASTEXITCODE -ne 0) {
        throw "Synapse failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}
