$ErrorActionPreference = 'Stop'

$threadId = '01a144ec-ed34-7f32-a8d1-c0588e338fec'
$expectedOffset = 4518318
$oldOrdinal = 60
$newOrdinal = 61
$codexDir = Join-Path $env:USERPROFILE '.codex'
$database = Join-Path $codexDir 'thread_history_1.sqlite'
$session = Join-Path $codexDir 'sessions\2026\10\16\rollout-2026-10-16T20-35-40-01a144ec-ed34-7f32-a8d1-c0588e338fec.jsonl'
$sqlite = 'C:\msys64\ucrt64\bin\sqlite3.exe'
$backupRoot = Join-Path 'D:\taixuong\AI_RECOGNITION\.codex-history-backup' (Get-Date -Format 'yyyyMMdd-HHmmss')
$logFile = 'D:\taixuong\AI_RECOGNITION\codex-history-repair.log'

function Write-RepairLog([string]$message) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $message" | Add-Content -LiteralPath $logFile -Encoding UTF8
}

Write-RepairLog 'Repair helper armed; waiting for Codex and ChatGPT processes to close.'

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

if (-not (Test-Path -LiteralPath $database)) { throw "Database not found: $database" }
if (-not (Test-Path -LiteralPath $session)) { throw "Session file not found: $session" }
if (-not (Test-Path -LiteralPath $sqlite)) { throw "sqlite3 not found: $sqlite" }

New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
Copy-Item -LiteralPath $database -Destination $backupRoot
foreach ($suffix in @('-wal', '-shm')) {
    $sidecar = "$database$suffix"
    if (Test-Path -LiteralPath $sidecar) { Copy-Item -LiteralPath $sidecar -Destination $backupRoot }
}
Copy-Item -LiteralPath $session -Destination $backupRoot
$sessionIndex = Join-Path $codexDir 'session_index.jsonl'
if (Test-Path -LiteralPath $sessionIndex) { Copy-Item -LiteralPath $sessionIndex -Destination $backupRoot }

$before = & $sqlite -readonly $database "SELECT next_rollout_byte_offset || '|' || next_rollout_ordinal FROM thread_history_projection_state WHERE thread_id='$threadId';"
Write-RepairLog "Projection state before repair: $before"

if ($before -ne "$expectedOffset|$oldOrdinal") {
    Write-RepairLog "Safety check failed; expected $expectedOffset|$oldOrdinal. Backup retained, database unchanged."
    exit 3
}

$sql = @"
BEGIN IMMEDIATE;
UPDATE thread_history_projection_state
SET next_rollout_ordinal = $newOrdinal
WHERE thread_id = '$threadId'
  AND next_rollout_byte_offset = $expectedOffset
  AND next_rollout_ordinal = $oldOrdinal;
COMMIT;
PRAGMA wal_checkpoint(TRUNCATE);
"@

& $sqlite $database $sql
if ($LASTEXITCODE -ne 0) { throw "sqlite3 exited with code $LASTEXITCODE" }

$after = & $sqlite -readonly $database "SELECT next_rollout_byte_offset || '|' || next_rollout_ordinal FROM thread_history_projection_state WHERE thread_id='$threadId';"
if ($after -ne "$expectedOffset|$newOrdinal") { throw "Repair verification failed; current state: $after" }

Write-RepairLog "Repair completed. Projection state is now $after. Backup: $backupRoot"
