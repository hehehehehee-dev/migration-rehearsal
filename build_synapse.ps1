param(
    [string]$Endpoint = "http://127.0.0.1:11455/v1",
    [string]$Model = "qwen2.5-coder-1.5b",
    [string]$Provider = "custom"
)
$ErrorActionPreference = 'Stop'
$project = $PSScriptRoot
$stage = Join-Path $project '.tooling\codegen'
$shim = Join-Path $project '.tooling\node_modules\.bin\synapse.cmd'
$toolingPython = Join-Path $project '.tooling\python'
if (!(Test-Path -LiteralPath $shim)) {
    throw 'Install the pinned Synapse CLI with npm install --prefix .tooling @2ndbrainlabs-ai/synapse-cli@0.1.9'
}
New-Item -ItemType Directory -Path $stage -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $project 'rehearsal.py'),(Join-Path $project 'requirements.txt') -Destination $stage
if (Test-Path -LiteralPath $toolingPython) {
    $env:PYTHONPATH = "$toolingPython;$toolingPython/win32;$toolingPython/win32/lib;$toolingPython/pywin32_system32;$env:PYTHONPATH"
}
$env:SYNAPSE_TELEMETRY = '0'
$intent = @"
Generate a synchronous MCP tool named rehearse_database_migration with one parameter plan_json: str. Import these six functions from rehearsal. The Python body follows, with each vertical bar marking a new line:
ctx = validate_request(plan_json)
ctx = create_test_snapshot(ctx)
ctx = capture_baseline(ctx)
ctx = execute_migration(ctx)
ctx = verify_postconditions(ctx)
return build_evidence_report(ctx)
Always call all six. No other calls. The imports field is a list of Python import statements. The env_vars field is an empty list; validate_request checks approval.
"@
Push-Location -LiteralPath $stage
try {
    & $shim build --discover --local --custom --query ($intent -replace '\r?\n', ' | ') --provider $Provider --llm-base-url $Endpoint --model $Model --max-time 4
    if ($LASTEXITCODE -ne 0) {
        throw "Synapse failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}
