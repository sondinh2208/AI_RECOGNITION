$ErrorActionPreference = 'Stop'

$threadId = '01a144ec-ed34-7f32-a8d1-c0588e338fec'
$expectedOffset = 26896743
$newOffset = 32525788
$expectedOrdinal = 2564
$codexDir = Join-Path $env:USERPROFILE '.codex'
$database = Join-Path $codexDir 'thread_history_1.sqlite'
$stateDatabase = Join-Path $codexDir 'state_5.sqlite'
$session = Join-Path $codexDir 'sessions\2026\10\16\rollout-2026-10-16T20-35-40-01a144ec-ed34-7f32-a8d1-c0588e338fec.jsonl'
$sqlite = 'C:\msys64\ucrt64\bin\sqlite3.exe'
$backupRoot = Join-Path 'D:\taixuong\AI_RECOGNITION\.codex-history-backup' (Get-Date -Format 'yyyyMMdd-HHmmss')
$logFile = 'D:\taixuong\AI_RECOGNITION\codex-history-repair.log'

function Write-RepairLog([string]$message) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $message" | Add-Content -LiteralPath $logFile -Encoding UTF8
}

Write-RepairLog 'Repair v2 armed; waiting for ChatGPT/Codex and IDE Codex processes to close.'

$deadline = (Get-Date).AddMinutes(30)
while ((Get-Date) -lt $deadline) {
    $active = Get-Process -Name 'ChatGPT', 'codex' -ErrorAction SilentlyContinue
    if (-not $active) { break }
    Start-Sleep -Seconds 2
}

if (Get-Process -Name 'ChatGPT', 'codex' -ErrorAction SilentlyContinue) {
    Write-RepairLog 'Timed out after 30 minutes; no files were changed.'
    exit 2
}

Start-Sleep -Seconds 3

foreach ($required in @($database, $session, $sqlite)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Required file not found: $required" }
}

$sessionLength = (Get-Item -LiteralPath $session).Length
if ($sessionLength -lt $newOffset) {
    Write-RepairLog "Safety check failed; session is only $sessionLength bytes, expected at least $newOffset. No files changed."
    exit 3
}

New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
foreach ($source in @($database, $stateDatabase, $session, (Join-Path $codexDir 'session_index.jsonl'))) {
    if (Test-Path -LiteralPath $source) { Copy-Item -LiteralPath $source -Destination $backupRoot }
}
foreach ($base in @($database, $stateDatabase)) {
    foreach ($suffix in @('-wal', '-shm')) {
        $sidecar = "$base$suffix"
        if (Test-Path -LiteralPath $sidecar) { Copy-Item -LiteralPath $sidecar -Destination $backupRoot }
    }
}

$before = & $sqlite -readonly $database "SELECT next_rollout_byte_offset || '|' || next_rollout_ordinal FROM thread_history_projection_state WHERE thread_id='$threadId';"
Write-RepairLog "Projection state before repair: $before"

if ($before -ne "$expectedOffset|$expectedOrdinal") {
    Write-RepairLog "Safety check failed; expected $expectedOffset|$expectedOrdinal. Backup retained, database unchanged."
    exit 4
}

$sql = @"
BEGIN IMMEDIATE;
UPDATE thread_history_projection_state
SET next_rollout_byte_offset = $newOffset
WHERE thread_id = '$threadId'
  AND next_rollout_byte_offset = $expectedOffset
  AND next_rollout_ordinal = $expectedOrdinal;
COMMIT;
PRAGMA wal_checkpoint(TRUNCATE);
"@

& $sqlite $database $sql
if ($LASTEXITCODE -ne 0) { throw "sqlite3 exited with code $LASTEXITCODE" }

$after = & $sqlite -readonly $database "SELECT next_rollout_byte_offset || '|' || next_rollout_ordinal FROM thread_history_projection_state WHERE thread_id='$threadId';"
if ($after -ne "$newOffset|$expectedOrdinal") { throw "Repair verification failed; current state: $after" }

Write-RepairLog "Repair completed. Projection state is now $after. Backup: $backupRoot"
