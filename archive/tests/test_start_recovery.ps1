$ErrorActionPreference = "Stop"
if ($env:OS -ne "Windows_NT") { throw "PowerShell recovery orchestration tests require Windows." }

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

function Protect-TestOwnerDirectory([string]$Directory) {
    $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    $security = [System.Security.AccessControl.DirectorySecurity]::new()
    $security.SetOwner($sid)
    $security.SetAccessRuleProtection($true, $false)
    $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
        $sid,
        [System.Security.AccessControl.FileSystemRights]::FullControl,
        [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [System.Security.AccessControl.InheritanceFlags]::ObjectInherit,
        [System.Security.AccessControl.PropagationFlags]::None,
        [System.Security.AccessControl.AccessControlType]::Allow
    )
    [void]$security.AddAccessRule($rule)
    Set-Acl -LiteralPath $Directory -AclObject $security
}

function Get-AclSnapshot([string]$Root) {
    $snapshot = @{}
    foreach ($item in @((Get-Item -LiteralPath $Root)) + @(Get-ChildItem -LiteralPath $Root -Force -Recurse)) {
        $relative = [System.IO.Path]::GetRelativePath($Root, $item.FullName)
        $snapshot[$relative] = (Get-Acl -LiteralPath $item.FullName).Sddl
    }
    return $snapshot
}

function Assert-AclSnapshot([hashtable]$Expected, [string]$Root) {
    $actual = Get-AclSnapshot $Root
    Assert-True ($actual.Count -eq $Expected.Count) "Restore source contents changed."
    foreach ($name in $Expected.Keys) {
        Assert-True ($actual.ContainsKey($name) -and $actual[$name] -ceq $Expected[$name]) "Restore source ACL changed: $name"
    }
}

function Write-CredentialSet([string]$Directory, [string]$Project, [string]$Value) {
    New-Item -ItemType Directory -Force -Path $Directory | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $Directory "instance"), "$Project`n")
    foreach ($name in @("metadata_bootstrap_password", "metadata_migration_password", "metadata_schemii_password", "metadata_schemer_password", "opencode_password")) {
        [System.IO.File]::WriteAllText((Join-Path $Directory $name), ($Value * 32) + "`n")
    }
}

$root = Join-Path $env:RUNNER_TEMP ("schemii-powershell-recovery-" + [Guid]::NewGuid().ToString("N"))
$bin = Join-Path $root "bin"
New-Item -ItemType Directory -Path $bin | Out-Null
$fakeDocker = @'
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$CommandArguments)
$ErrorActionPreference = "Stop"
$line = $CommandArguments -join " "
Add-Content -LiteralPath $env:SCHEMII_TEST_DOCKER_LOG -Value $line
$stateFile = $env:SCHEMII_TEST_RECOVERY_STATE_FILE
if ($line -match 'metadata-recovery state$') {
    if (Test-Path -LiteralPath $stateFile) { Get-Content -LiteralPath $stateFile } else { Write-Output "none" }
    exit 0
}
if ($line -match 'metadata-recovery restore$') { [System.IO.File]::WriteAllText($stateFile, "rollback-required`n") }
if ($line -match 'metadata-recovery commit$') {
    if ($env:SCHEMII_TEST_DOCKER_FAILURE_KIND -eq "commit-before-publish") { exit 71 }
    [System.IO.File]::WriteAllText($stateFile, "committed-cleanup-required`n")
}
if ($line -match 'metadata-recovery finalize-commit$') {
    if ($env:SCHEMII_TEST_DOCKER_FAILURE_KIND -eq "finalize-after-publish") { exit 71 }
    Remove-Item -LiteralPath $stateFile -Force -ErrorAction SilentlyContinue
    exit 0
}
if ($line -match 'metadata-recovery rollback$') { Remove-Item -LiteralPath $stateFile -Force -ErrorAction SilentlyContinue }
if ($env:SCHEMII_TEST_DOCKER_FAIL_MATCH -and $line.Contains($env:SCHEMII_TEST_DOCKER_FAIL_MATCH)) { exit 71 }
if ($line -eq "info" -or $line -eq "compose version") { exit 0 }
if ($line -eq "ps -aq --filter label=com.docker.compose.project=$($env:SCHEMII_INSTANCE)") {
    if ($env:SCHEMII_TEST_DOCKER_FAILURE_KIND -eq "stopped-ps") { exit 71 }
    if ($env:SCHEMII_TEST_DOCKER_FAILURE_KIND -eq "stopped-inspect") { Write-Output "stopped-container" }
    exit 0
}
if ($line -eq "inspect --format {{.State.Running}} stopped-container") {
    if ($env:SCHEMII_TEST_DOCKER_FAILURE_KIND -eq "stopped-inspect") { exit 71 }
    Write-Output "false"
    exit 0
}
if ($line.StartsWith("volume inspect --format ")) {
    $name = $CommandArguments[-1]
    $logical = $name.Substring($env:SCHEMII_INSTANCE.Length + 1)
    Write-Output "$($env:SCHEMII_INSTANCE)|$logical"
    exit 0
}
if ($line.StartsWith("volume inspect ")) { exit 0 }
if ($line -match '^compose .* ps -q metadata-postgres$') { Write-Output "metadata-container"; exit 0 }
if ($line -match '^ps -a?q ') { exit 0 }
exit 0
'@
[System.IO.File]::WriteAllText((Join-Path $bin "docker.ps1"), $fakeDocker)
[System.IO.File]::WriteAllText((Join-Path $bin "docker.cmd"), "@pwsh -NoProfile -File `"%~dp0docker.ps1`" %*`r`n")

try {
    foreach ($failure in @($null, "metadata-migrate", "stop metadata-container", "stopped-ps", "stopped-inspect", "commit-before-publish", "commit-after-publish", "finalize-after-publish")) {
        $caseName = if (-not $failure) { "success" } elseif ($failure -eq "metadata-migrate") { "rollback" } else { $failure.Replace(" ", "-") }
        $project = "schemii-pwsh-$caseName"
        $caseRoot = Join-Path $root $caseName
        $credentials = Join-Path $caseRoot "credentials"
        $backupRoot = Join-Path $caseRoot "backup"
        $source = Join-Path $backupRoot $project
        $credentialSource = Join-Path $source "credentials"
        $log = Join-Path $caseRoot "docker.log"
        New-Item -ItemType Directory -Path $caseRoot | Out-Null
        Protect-TestOwnerDirectory $caseRoot
        Write-CredentialSet $credentials $project "a"
        Write-CredentialSet $credentialSource $project "b"
        [System.IO.File]::WriteAllText((Join-Path $source "instance"), "$project`n")
        foreach ($name in @("format", "release-version", "metadata-version", "checksums.sha256", "complete", "metadata.dump", "schemii-config.tar.gz", "schemii-schemas.tar.gz", "schemer-dashboards.tar.gz")) {
            [System.IO.File]::WriteAllText((Join-Path $source $name), "$name`n")
        }
        $sourceAcl = Get-AclSnapshot $source

        $oldPath = $env:PATH
        $env:PATH = "$bin;$oldPath"
        $env:SCHEMII_INSTANCE = $project
        $env:SCHEMII_CREDENTIAL_DIR = $credentials
        $env:SCHEMII_TEST_DOCKER_LOG = $log
        $env:SCHEMII_TEST_RECOVERY_STATE_FILE = Join-Path $caseRoot "recovery.state"
        $env:SCHEMII_TEST_DOCKER_FAIL_MATCH = if ($failure -in @("stopped-ps", "stopped-inspect")) { $null } elseif ($failure -in @("commit-before-publish", "commit-after-publish")) { "metadata-recovery commit" } elseif ($failure -eq "finalize-after-publish") { "metadata-recovery finalize-commit" } else { $failure }
        $env:SCHEMII_TEST_DOCKER_FAILURE_KIND = $failure
        if (-not $failure) {
            $env:SCHEMII_IMAGE = "schemii:immutable"
            $env:SCHEMII_METADATA_IMAGE = "schemii-metadata-postgres:immutable"
        }
        try {
            & pwsh -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "../start.ps1") `
                -Mode instance-restore -Path $backupRoot -ConfirmInstance "RESTORE:$project" -NoOpen *> (Join-Path $caseRoot "output.log")
            $status = $LASTEXITCODE
        }
        finally {
            $env:PATH = $oldPath
            Remove-Item Env:SCHEMII_TEST_DOCKER_FAIL_MATCH -ErrorAction SilentlyContinue
            Remove-Item Env:SCHEMII_TEST_DOCKER_FAILURE_KIND -ErrorAction SilentlyContinue
        }

        if ($failure) { Assert-True ($status -ne 0) "Injected recovery failure unexpectedly succeeded: $failure" }
        else { Assert-True ($status -eq 0) "PowerShell coordinated restore failed." }
        Assert-AclSnapshot $sourceAcl $source
        if ($failure -in @("stop metadata-container", "commit-after-publish")) {
            Assert-True (Test-Path -LiteralPath (Join-Path $credentials ".credential-transaction")) "Stop failure discarded credential recovery evidence."
        }
        else {
            Assert-True (-not (Test-Path -LiteralPath (Join-Path $credentials ".credential-transaction"))) "Credential transaction was not cleaned."
        }
        Assert-True (@(Get-ChildItem -LiteralPath $credentials -Directory -Filter ".restore-source.*").Count -eq 0) "Protected restore staging was not cleaned."

        $calls = [System.IO.File]::ReadAllText($log)
        $stageIndex = $calls.IndexOf("metadata-recovery stage-verification")
        $backupVerifyIndex = $calls.IndexOf("application-recovery-verify backup /transaction/verification")
        $clearIndex = $calls.IndexOf("metadata-recovery clear-verification")
        $restoreIndex = $calls.IndexOf("metadata-recovery restore")
        $migrateIndex = $calls.IndexOf("metadata-migrate")
        if ($failure -notin @("stopped-ps", "stopped-inspect")) {
            Assert-True ($stageIndex -ge 0 -and $stageIndex -lt $backupVerifyIndex -and $backupVerifyIndex -lt $clearIndex -and $clearIndex -lt $restoreIndex -and $restoreIndex -lt $migrateIndex) "PowerShell recovery validation/restore order changed."
        }
        if ($failure -eq "metadata-migrate") {
            $rollbackIndex = $calls.IndexOf("metadata-recovery rollback")
            Assert-True ($rollbackIndex -gt $migrateIndex) "PowerShell recovery did not roll back after the injected failure."
            Assert-True (-not $calls.Contains("metadata-recovery commit")) "Failed recovery attempted to commit."
        }
        elseif ($failure -eq "stop metadata-container") {
            Assert-True (-not $calls.Contains("metadata-recovery commit")) "Stop failure attempted to commit recovery."
            Assert-True (-not ([System.IO.File]::ReadAllText((Join-Path $caseRoot "output.log")).Contains("Coordinated restore completed"))) "Stop failure reported recovery success."
        }
        elseif ($failure -in @("stopped-ps", "stopped-inspect")) {
            Assert-True (-not $calls.Contains("metadata-recovery restore")) "Fail-closed stopped-instance check reached restore."
        }
        elseif ($failure -eq "commit-before-publish") {
            Assert-True ($calls.Contains("metadata-recovery rollback")) "Pre-publication commit failure did not roll back."
            Assert-True (-not $calls.Contains("metadata-recovery finalize-commit")) "Pre-publication commit failure finalized recovery."
        }
        elseif ($failure -in @("commit-after-publish", "finalize-after-publish")) {
            Assert-True (-not $calls.Contains("metadata-recovery rollback")) "Published commit failure attempted rollback."
            Assert-True (-not ([System.IO.File]::ReadAllText((Join-Path $caseRoot "output.log")).Contains("Coordinated restore completed"))) "Published commit failure reported recovery success."

            $env:PATH = "$bin;$oldPath"
            $env:SCHEMII_TEST_DOCKER_FAIL_MATCH = $null
            $env:SCHEMII_TEST_DOCKER_FAILURE_KIND = $null
            try {
                & pwsh -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "../start.ps1") `
                    -Mode instance-restore -Path $backupRoot -ConfirmInstance "RESTORE:$project" -NoOpen *> (Join-Path $caseRoot "restart-output.log")
                $restartStatus = $LASTEXITCODE
            }
            finally { $env:PATH = $oldPath }
            Assert-True ($restartStatus -eq 0) "Published commit cleanup did not complete on restart."
            $calls = [System.IO.File]::ReadAllText($log)
            Assert-True ([regex]::Matches($calls, '(?m)metadata-recovery restore\r?$').Count -eq 1) "Restart replayed a committed restore."
            Assert-True (-not $calls.Contains("metadata-recovery rollback")) "Restart rolled back a committed restore."
            Assert-True ($calls.Contains("metadata-recovery finalize-commit")) "Restart did not finalize committed recovery."
            Assert-True (-not (Test-Path -LiteralPath (Join-Path $credentials ".credential-transaction"))) "Restart retained rollback credential evidence."
            Assert-True (-not (Test-Path -LiteralPath (Join-Path $credentials ".credential-transaction-committed"))) "Restart retained committed credential cleanup evidence."
        }
        else {
            $securityIndex = $calls.IndexOf("metadata-recovery verify-metadata")
            $commitIndex = $calls.IndexOf("metadata-recovery commit")
            Assert-True ($securityIndex -gt $migrateIndex -and $commitIndex -gt $securityIndex) "PowerShell recovery verification/commit order changed."
            Assert-True ($calls.Contains("image inspect schemii:immutable schemii-metadata-postgres:immutable")) "Immutable recovery did not inspect the selected images."
            Assert-True (-not $calls.Contains("build metadata-postgres schemii")) "Immutable recovery rebuilt selected images."
        }
    }
    Write-Host "PowerShell coordinated recovery mock tests passed"
}
finally {
    if (Test-Path -LiteralPath $root) { Remove-Item -LiteralPath $root -Recurse -Force }
}
