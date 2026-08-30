$ErrorActionPreference = "Stop"
if ($env:OS -ne "Windows_NT") { throw "PowerShell legacy volume adoption tests require Windows." }

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

function Write-CredentialSet([string]$Directory) {
    New-Item -ItemType Directory -Force -Path $Directory | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $Directory "instance"), "schemii`n")
    foreach ($name in @("metadata_bootstrap_password", "metadata_migration_password", "metadata_schemii_password", "metadata_schemer_password", "opencode_password")) {
        [System.IO.File]::WriteAllText((Join-Path $Directory $name), ("a" * 32) + "`n")
    }
}

function Invoke-TestScript([string]$Script, [string[]]$Arguments, [string]$Output) {
    & pwsh -NoProfile -ExecutionPolicy Bypass -File $Script @Arguments *> $Output
    return $LASTEXITCODE
}

$root = Join-Path $env:RUNNER_TEMP ("schemii-powershell-legacy-adoption-" + [Guid]::NewGuid().ToString("N"))
$bin = Join-Path $root "bin"
New-Item -ItemType Directory -Path $bin | Out-Null
$fakeDocker = @'
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$CommandArguments)
$ErrorActionPreference = "Stop"
$line = $CommandArguments -join " "
Add-Content -LiteralPath $env:SCHEMII_TEST_DOCKER_LOG -Value $line
if ($CommandArguments.Count -ge 5 -and $CommandArguments[0] -ceq "volume" -and $CommandArguments[1] -ceq "inspect" -and $CommandArguments[2] -ceq "--format") {
    $format = $CommandArguments[3]
    $volume = $CommandArguments[4]
    $logical = $volume.Substring("schemii_".Length)
    if ($format.StartsWith("{{.Name}}|")) {
        $generation = if ($env:SCHEMII_TEST_REPLACED) { $env:SCHEMII_TEST_REPLACED } else { "0" }
        Write-Output "$volume|2026-08-24T00:00:0${generation}Z|local|C:\ProgramData\docker\volumes\$volume\_data-$generation|local|null"
        exit 0
    }
    if ($format.Contains("com.docker.compose.volume")) {
        if ($format.Contains("{{.Name}}")) { Write-Output "||$volume" }
        elseif ($logical -in @("schemii-config", "schemii-schemas")) { Write-Output "|" }
        else { Write-Output "schemii|$logical" }
        exit 0
    }
}
if ($line -eq "info" -or $line -eq "compose version") { exit 0 }
if ($line -eq "volume inspect schemii_schemii-metadata-postgres") { exit 0 }
if ($line -eq "volume ls -q") { Write-Output "schemii_schemii-config"; Write-Output "schemii_schemii-schemas"; exit 0 }
if ($line -eq "ps -aq --filter label=com.docker.compose.project=schemii") {
    if (-not $env:SCHEMII_TEST_NO_CONTAINERS -and $env:SCHEMII_TEST_WITNESS_MODE -cne "no-witness") { Write-Output "witness" }
    exit 0
}
if ($line -eq "ps -aq") {
    if ($env:SCHEMII_TEST_NO_CONTAINERS) { exit 0 }
    if ($env:SCHEMII_TEST_WITNESS_MODE -ceq "no-witness") { exit 0 }
    Write-Output "witness"
    if ($env:SCHEMII_TEST_WITNESS_MODE -ceq "foreign") { Write-Output "foreign" }
    exit 0
}
if ($line -eq "inspect --format {{.State.Running}} witness") { Write-Output "false"; exit 0 }
if ($CommandArguments[0] -ceq "inspect" -and $CommandArguments[2].Contains(".Mounts")) {
    if ($CommandArguments[-1] -ceq "witness") {
        Write-Output "volume|schemii_schemii-config|/data/config"
        Write-Output "volume|schemii_schemii-schemas|/data/schemas"
    }
    else { Write-Output "volume|schemii_schemii-config|/data/config" }
    exit 0
}
if ($CommandArguments[0] -ceq "inspect" -and $CommandArguments[2].Contains("com.docker.compose.project.working_dir")) {
    if ($CommandArguments[-1] -ceq "foreign") { Write-Output "foreign|schemii|$($env:SCHEMII_TEST_REPOSITORY)"; exit 0 }
    switch -CaseSensitive ($env:SCHEMII_TEST_WITNESS_MODE) {
        "wrong-project" { Write-Output "other|schemii|$($env:SCHEMII_TEST_REPOSITORY)" }
        "wrong-repository" { Write-Output "schemii|schemii|C:\other-repository" }
        default { Write-Output "schemii|schemii|$($env:SCHEMII_TEST_REPOSITORY)" }
    }
    exit 0
}
if ($line.StartsWith("image inspect ")) { if ($env:SCHEMII_TEST_NO_CONTAINERS) { exit 71 }; exit 0 }
exit 0
'@
[System.IO.File]::WriteAllText((Join-Path $bin "docker.ps1"), $fakeDocker)
[System.IO.File]::WriteAllText((Join-Path $bin "docker.cmd"), "@pwsh -NoProfile -File `"%~dp0docker.ps1`" %*`r`n")

$oldPath = $env:PATH
try {
    $env:PATH = "$bin;$oldPath"
    $env:SCHEMII_INSTANCE = "schemii"
    $env:SCHEMII_TEST_REPOSITORY = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

    foreach ($witnessMode in @("foreign", "wrong-project", "wrong-repository", "no-witness")) {
        $caseRoot = Join-Path $root $witnessMode
        $credentials = Join-Path $caseRoot "credentials"
        New-Item -ItemType Directory -Path $caseRoot | Out-Null
        Protect-TestOwnerDirectory $caseRoot
        Write-CredentialSet $credentials
        $env:SCHEMII_CREDENTIAL_DIR = $credentials
        $env:SCHEMII_TEST_DOCKER_LOG = Join-Path $caseRoot "docker.log"
        $env:SCHEMII_TEST_WITNESS_MODE = $witnessMode
        $status = Invoke-TestScript (Join-Path $PSScriptRoot "../start.ps1") @("-Mode", "legacy-volume-adopt", "-ConfirmInstance", "ADOPT:schemii", "-NoOpen") (Join-Path $caseRoot "output.log")
        Assert-True ($status -ne 0) "Unsafe legacy witness unexpectedly succeeded: $witnessMode"
        Assert-True (-not (Test-Path -LiteralPath (Join-Path $credentials "legacy-volume-adoptions.v1"))) "Failed adoption published evidence: $witnessMode"
    }

    Remove-Item Env:SCHEMII_TEST_WITNESS_MODE -ErrorAction SilentlyContinue
    $caseRoot = Join-Path $root "lifecycle"
    $credentials = Join-Path $caseRoot "credentials"
    New-Item -ItemType Directory -Path $caseRoot | Out-Null
    Protect-TestOwnerDirectory $caseRoot
    Write-CredentialSet $credentials
    $env:SCHEMII_CREDENTIAL_DIR = $credentials
    $env:SCHEMII_TEST_DOCKER_LOG = Join-Path $caseRoot "docker.log"

    $status = Invoke-TestScript (Join-Path $PSScriptRoot "../start.ps1") @("-Mode", "instance-backup", "-Path", (Join-Path $caseRoot "missing"), "-NoOpen") (Join-Path $caseRoot "missing.log")
    Assert-True ($status -ne 0) "Recovery accepted missing legacy adoption evidence."
    Assert-True ([System.IO.File]::ReadAllText((Join-Path $caseRoot "missing.log")).Contains("lacks unchanged adoption evidence")) "Missing-evidence failure was not explicit."

    $status = Invoke-TestScript (Join-Path $PSScriptRoot "../start.ps1") @("-Mode", "legacy-volume-adopt", "-ConfirmInstance", "ADOPT:schemii", "-NoOpen") (Join-Path $caseRoot "adopt.log")
    Assert-True ($status -eq 0) "PowerShell legacy adoption failed."
    $adoptionDirectory = Join-Path $credentials "legacy-volume-adoptions.v1"
    Assert-True (@(Get-ChildItem -LiteralPath $adoptionDirectory -Force).Count -eq 2) "Adoption did not publish exactly two manifests."

    $env:SCHEMII_TEST_NO_CONTAINERS = "1"
    $status = Invoke-TestScript (Join-Path $PSScriptRoot "../start.ps1") @("-Mode", "instance-backup", "-Path", (Join-Path $caseRoot "recreated"), "-NoOpen") (Join-Path $caseRoot "recreated.log")
    Assert-True ($status -eq 71) "Recreated-container recovery did not trust unchanged durable evidence."
    Remove-Item Env:SCHEMII_TEST_NO_CONTAINERS

    $env:SCHEMII_TEST_REPLACED = "1"
    $status = Invoke-TestScript (Join-Path $PSScriptRoot "../start.ps1") @("-Mode", "instance-backup", "-Path", (Join-Path $caseRoot "replaced"), "-NoOpen") (Join-Path $caseRoot "replaced.log")
    Assert-True ($status -ne 0) "Recovery accepted a replaced legacy volume."
    Remove-Item Env:SCHEMII_TEST_REPLACED

    $schemasManifest = Join-Path $adoptionDirectory "schemii-schemas.manifest"
    $missingManifest = "$schemasManifest.missing"
    Move-Item -LiteralPath $schemasManifest -Destination $missingManifest
    $status = Invoke-TestScript (Join-Path $PSScriptRoot "../start.ps1") @("-Mode", "instance-backup", "-Path", (Join-Path $caseRoot "partial"), "-NoOpen") (Join-Path $caseRoot "partial.log")
    Assert-True ($status -ne 0) "Recovery accepted incomplete legacy evidence."
    Move-Item -LiteralPath $missingManifest -Destination $schemasManifest

    Add-Content -LiteralPath (Join-Path $adoptionDirectory "schemii-config.manifest") -Value "tampered=true"
    $status = Invoke-TestScript (Join-Path $PSScriptRoot "../start.ps1") @("-Mode", "instance-backup", "-Path", (Join-Path $caseRoot "tampered"), "-NoOpen") (Join-Path $caseRoot "tampered.log")
    Assert-True ($status -ne 0) "Recovery accepted a tampered legacy manifest."

    foreach ($tampered in @($false, $true)) {
        $name = if ($tampered) { "uninstall-tampered" } else { "uninstall-valid" }
        $uninstallRoot = Join-Path $root $name
        $repository = Join-Path $uninstallRoot "repository"
        New-Item -ItemType Directory -Path (Join-Path $repository "src/schemii") -Force | Out-Null
        Protect-TestOwnerDirectory $uninstallRoot
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot "../start.ps1") -Destination (Join-Path $repository "start.ps1")
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot "../uninstall.ps1") -Destination (Join-Path $repository "uninstall.ps1")
        [System.IO.File]::WriteAllText((Join-Path $repository "compose.yaml"), "services: {}`n")
        $uninstallCredentials = Join-Path $uninstallRoot "credentials"
        Write-CredentialSet $uninstallCredentials
        $env:SCHEMII_CREDENTIAL_DIR = $uninstallCredentials
        $env:SCHEMII_TEST_REPOSITORY = $repository
        $env:SCHEMII_TEST_DOCKER_LOG = Join-Path $uninstallRoot "docker.log"
        $status = Invoke-TestScript (Join-Path $repository "start.ps1") @("-Mode", "legacy-volume-adopt", "-ConfirmInstance", "ADOPT:schemii", "-NoOpen") (Join-Path $uninstallRoot "adopt.log")
        Assert-True ($status -eq 0) "Uninstall fixture adoption failed."
        if ($tampered) { Add-Content -LiteralPath (Join-Path $uninstallCredentials "legacy-volume-adoptions.v1/schemii-config.manifest") -Value "tampered=true" }
        $env:SCHEMII_TEST_NO_CONTAINERS = "1"
        $status = Invoke-TestScript (Join-Path $repository "uninstall.ps1") @("-Yes") (Join-Path $uninstallRoot "uninstall.log")
        Assert-True ($status -eq 0) "PowerShell uninstall fixture failed."
        $calls = [System.IO.File]::ReadAllText((Join-Path $uninstallRoot "docker.log"))
        if ($tampered) {
            Assert-True (-not $calls.Contains("volume rm schemii_schemii-config")) "Uninstall removed a volume with tampered evidence."
            Assert-True (Test-Path -LiteralPath $uninstallCredentials) "Uninstall removed credentials with tampered evidence."
        }
        else {
            Assert-True ($calls.Contains("volume rm schemii_schemii-config") -and $calls.Contains("volume rm schemii_schemii-schemas")) "Uninstall did not remove attested legacy volumes."
            Assert-True (-not (Test-Path -LiteralPath $uninstallCredentials)) "Uninstall retained credentials for an attested instance."
        }
        Remove-Item Env:SCHEMII_TEST_NO_CONTAINERS
    }
    Write-Host "PowerShell legacy volume adoption mock tests passed"
}
finally {
    $env:PATH = $oldPath
    foreach ($name in @("SCHEMII_INSTANCE", "SCHEMII_CREDENTIAL_DIR", "SCHEMII_TEST_DOCKER_LOG", "SCHEMII_TEST_REPOSITORY", "SCHEMII_TEST_WITNESS_MODE", "SCHEMII_TEST_NO_CONTAINERS", "SCHEMII_TEST_REPLACED")) {
        Remove-Item "Env:$name" -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $root) { Remove-Item -LiteralPath $root -Recurse -Force }
}
