param(
    [ValidateSet("ui", "docker-db", "ai", "ai-local-db", "ai-docker-db", "schemer", "schemer-ai", "credentials-backup", "credentials-restore", "credentials-rotate", "legacy-volume-adopt", "instance-backup", "instance-restore")]
    [string]$Mode = "ai-docker-db",
    [string]$Path,
    [string]$ConfirmInstance,
    [switch]$NoOpen,
    [switch]$Help
)

$ErrorActionPreference = "Stop"
$runningOnWindows = $env:OS -eq "Windows_NT"

if ($Help) {
    Write-Host "Usage: powershell -ExecutionPolicy Bypass -File .\start.ps1 [-Mode <mode>] [-NoOpen]"
    Write-Host ""
    Write-Host "Modes:"
    Write-Host "  ai-docker-db  Complete UI, tutorial PostgreSQL, and AI stack (default)"
    Write-Host "  ui            Local schema design only"
    Write-Host "  docker-db     UI and tutorial PostgreSQL without AI"
    Write-Host "  ai            UI and AI without included PostgreSQL"
    Write-Host "  ai-local-db   Linux host PostgreSQL with AI"
    Write-Host "  schemer       Schemii and Schemer with tutorial PostgreSQL, explicitly without AI"
    Write-Host "  schemer-ai    Schemii and Schemer with tutorial PostgreSQL and shared AI"
    Write-Host ""
    Write-Host "Credential lifecycle:"
    Write-Host "  -Mode credentials-backup -Path <directory>"
    Write-Host "  -Mode credentials-restore -Path <directory>"
    Write-Host "  -Mode credentials-rotate"
    Write-Host "  -Mode legacy-volume-adopt -ConfirmInstance ADOPT:<exact-instance-name>"
    Write-Host ""
    Write-Host "Coordinated Schemer recovery (all instance containers must be stopped):"
    Write-Host "  -Mode instance-backup -Path <directory>"
    Write-Host "  -Mode instance-restore -Path <directory> -ConfirmInstance RESTORE:<exact-instance-name>"
    Write-Host ""
    Write-Host "Uninstall: powershell -ExecutionPolicy Bypass -File .\uninstall.ps1"
    Write-Host "Setup help: https://github.com/LandMineDevelopment/schemii#install-docker"
    exit 0
}

$scriptDirectory = (Resolve-Path $PSScriptRoot).Path
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker was not found. Install and start Docker Desktop, reopen PowerShell, and see https://github.com/LandMineDevelopment/schemii#install-docker"
}

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker is installed, but the daemon is unavailable or access was denied. Start Docker Desktop, run 'docker info', and see https://github.com/LandMineDevelopment/schemii#docker-is-installed-but-unavailable"
}

docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose was not found. Update Docker Desktop or install Compose from https://docs.docker.com/compose/install/"
}
$project = $env:SCHEMII_INSTANCE
if (-not $project) {
    $legacyContainer = (& docker ps -aq --filter "label=com.docker.compose.project=schemii" --filter "label=com.docker.compose.service=schemii" | Select-Object -First 1)
    $legacyWorkingDirectory = if ($legacyContainer) { (& docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' $legacyContainer 2>$null) } else { "" }
    if ($legacyWorkingDirectory -eq $scriptDirectory) {
        $project = "schemii"
    }
    elseif (-not $legacyContainer) {
        docker volume inspect schemii_schemii-config *> $null
        $legacyConfig = $LASTEXITCODE -eq 0
        docker volume inspect schemii_schemii-schemas *> $null
        $legacySchemas = $LASTEXITCODE -eq 0
        if ($legacyConfig -and $legacySchemas) {
            throw "Legacy Schemii data volumes were found without a container that identifies their installation directory. Reuse them with `$env:SCHEMII_INSTANCE='schemii'; .\start.ps1 -Mode $Mode, or choose another unique instance name for a separate installation."
        }
        $sha = [System.Security.Cryptography.SHA256]::Create()
        try {
            $bytes = [System.Text.Encoding]::UTF8.GetBytes($scriptDirectory)
            $hash = $sha.ComputeHash($bytes)
            $instanceNumber = [BitConverter]::ToUInt32($hash, 0)
        }
        finally {
            $sha.Dispose()
        }
        $project = "schemii-$instanceNumber"
    }
    else {
        $sha = [System.Security.Cryptography.SHA256]::Create()
        try {
            $bytes = [System.Text.Encoding]::UTF8.GetBytes($scriptDirectory)
            $hash = $sha.ComputeHash($bytes)
            $instanceNumber = [BitConverter]::ToUInt32($hash, 0)
        }
        finally {
            $sha.Dispose()
        }
        $project = "schemii-$instanceNumber"
    }
}
if ($project -cnotmatch '^[a-z0-9][a-z0-9_-]*$') {
    throw "SCHEMII_INSTANCE must contain only lowercase letters, numbers, hyphens, or underscores."
}
$env:SCHEMII_INSTANCE = $project

$credentialRoot = if ($env:SCHEMII_CREDENTIAL_ROOT) { $env:SCHEMII_CREDENTIAL_ROOT } elseif ($runningOnWindows) { Join-Path $env:LOCALAPPDATA "Schemii\credentials" } else { Join-Path $HOME ".local/share/schemii/credentials" }
$credentialDirectory = if ($env:SCHEMII_CREDENTIAL_DIR) { $env:SCHEMII_CREDENTIAL_DIR } else { Join-Path $credentialRoot $project }
if (-not [System.IO.Path]::IsPathRooted($credentialDirectory)) { throw "SCHEMII_CREDENTIAL_DIR must be an absolute path." }
$credentialFiles = @("metadata_bootstrap_password", "metadata_migration_password", "metadata_schemii_password", "metadata_schemer_password", "opencode_password")
$credentialTransaction = Join-Path $credentialDirectory ".credential-transaction"
$credentialCommitCleanup = Join-Path $credentialDirectory ".credential-transaction-committed"
$legacyAdoptionDirectory = Join-Path $credentialDirectory "legacy-volume-adoptions.v1"
$restoreSourceStaging = $null
function Protect-CredentialPath([string]$Target, [bool]$Container) {
    $item = Get-Item -LiteralPath $Target -Force
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Credential paths must not be reparse points: $Target" }
    if (-not $runningOnWindows) {
        & chmod $(if ($Container) { "700" } else { "600" }) $Target
        if ($LASTEXITCODE -ne 0) { throw "Could not restrict credential permissions: $Target" }
        return
    }
    $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    if (-not $sid) { throw "Could not determine the current Windows user for credential ACLs." }
    $security = if ($Container) { [System.Security.AccessControl.DirectorySecurity]::new() } else { [System.Security.AccessControl.FileSecurity]::new() }
    $security.SetOwner($sid)
    $security.SetAccessRuleProtection($true, $false)
    $inheritance = if ($Container) { [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [System.Security.AccessControl.InheritanceFlags]::ObjectInherit } else { [System.Security.AccessControl.InheritanceFlags]::None }
    $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
        $sid,
        [System.Security.AccessControl.FileSystemRights]::FullControl,
        $inheritance,
        [System.Security.AccessControl.PropagationFlags]::None,
        [System.Security.AccessControl.AccessControlType]::Allow
    )
    [void]$security.AddAccessRule($rule)
    Set-Acl -LiteralPath $Target -AclObject $security
    $verified = Get-Acl -LiteralPath $Target
    $verifiedOwner = $verified.GetOwner([System.Security.Principal.SecurityIdentifier])
    $verifiedRules = @($verified.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))
    $invalidRule = @($verifiedRules | Where-Object {
        $_.IdentityReference -ne $sid -or $_.AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow -or $_.IsInherited -or
        ($_.FileSystemRights -band [System.Security.AccessControl.FileSystemRights]::FullControl) -ne [System.Security.AccessControl.FileSystemRights]::FullControl
    })
    if ($verified.AreAccessRulesProtected -ne $true -or $verifiedOwner -ne $sid -or $verifiedRules.Count -ne 1 -or $invalidRule.Count -ne 0) {
        throw "Credential ACL verification failed closed: $Target"
    }
}
function Protect-CredentialTree([string]$Directory) {
    Protect-CredentialPath $Directory $true
    $excluded = if ($Directory -ceq $credentialDirectory) { $legacyAdoptionDirectory } else { $null }
    $comparison = if ($runningOnWindows) { [System.StringComparison]::OrdinalIgnoreCase } else { [System.StringComparison]::Ordinal }
    foreach ($item in @(Get-ChildItem -LiteralPath $Directory -Force -Recurse)) {
        if ($excluded -and (
            [string]::Equals($item.FullName, $excluded, $comparison) -or
            $item.FullName.StartsWith($excluded + [System.IO.Path]::DirectorySeparatorChar, $comparison)
        )) { continue }
        Protect-CredentialPath $item.FullName $item.PSIsContainer
    }
}
function Test-OwnerOnlyPath([string]$Target, [bool]$Container) {
    try { $item = Get-Item -LiteralPath $Target -Force }
    catch { return $false }
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { return $false }
    if ($Container -and -not $item.PSIsContainer) { return $false }
    if (-not $Container -and $item.PSIsContainer) { return $false }
    if (-not $runningOnWindows) {
        $details = & stat -c '%u|%a' -- $Target 2>$null
        if ($LASTEXITCODE -ne 0) { $details = & stat -f '%u|%Lp' $Target 2>$null }
        $currentUser = & id -u
        return $LASTEXITCODE -eq 0 -and $details -ceq "$currentUser|$(if ($Container) { '700' } else { '600' })"
    }
    try {
        $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
        $acl = Get-Acl -LiteralPath $Target
        $owner = $acl.GetOwner([System.Security.Principal.SecurityIdentifier])
        $rules = @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))
        $invalid = @($rules | Where-Object {
            $_.IdentityReference -ne $sid -or $_.AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow -or $_.IsInherited -or
            ($_.FileSystemRights -band [System.Security.AccessControl.FileSystemRights]::FullControl) -ne [System.Security.AccessControl.FileSystemRights]::FullControl
        })
        return $acl.AreAccessRulesProtected -and $owner -eq $sid -and $rules.Count -eq 1 -and $invalid.Count -eq 0
    }
    catch { return $false }
}
$credentialParent = Split-Path -Parent $credentialDirectory
New-Item -ItemType Directory -Force -Path $credentialParent | Out-Null
$credentialLockPath = "${credentialDirectory}.lock"
$credentialLock = $null
$lockDeadline = [DateTime]::UtcNow.AddSeconds(60)
while (-not $credentialLock) {
    try {
        $credentialLock = [System.IO.File]::Open($credentialLockPath, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
    }
    catch [System.IO.IOException] {
        if ([DateTime]::UtcNow -ge $lockDeadline) { throw "Timed out waiting for another launcher credential operation for $project." }
        Start-Sleep -Seconds 1
    }
}
function Exit-CredentialLock {
    if ($script:credentialLock) {
        $script:credentialLock.Dispose()
        $script:credentialLock = $null
    }
}
try {
    Protect-CredentialPath $credentialLockPath $false
    function New-CredentialValue {
        $bytes = [byte[]]::new(32)
        [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
        return [Convert]::ToHexString($bytes).ToLowerInvariant()
    }
function Write-CredentialFile([string]$Target, [string]$Value) {
    if ($Value -cnotmatch '^[A-Za-z0-9_-]{16,256}$') { throw "Refusing to write an invalid credential." }
    [System.IO.File]::WriteAllText($Target, $Value + "`n", [System.Text.UTF8Encoding]::new($false))
    Protect-CredentialPath $Target $false
}
function Read-CredentialValue([string]$Target, [string]$Name) {
    if (-not (Test-Path -LiteralPath $Target -PathType Leaf)) { throw "$Name is missing." }
    $raw = [System.IO.File]::ReadAllText($Target, [System.Text.Encoding]::UTF8)
    if ($raw -cnotmatch '\A([A-Za-z0-9_-]{16,256})(?:\n)?\z') { throw "$Name must be one line containing 16-256 characters from [A-Za-z0-9_-] with an optional LF terminator." }
    return $Matches[1]
}
function Write-InstanceMarker([string]$Target, [string]$Value) {
    if (-not $Value -or $Value.Contains("`r") -or $Value.Contains("`n")) { throw "Invalid instance marker." }
    [System.IO.File]::WriteAllText($Target, $Value + "`n", [System.Text.UTF8Encoding]::new($false))
    Protect-CredentialPath $Target $false
}
function Read-InstanceMarker([string]$Target, [string]$Name) {
    if (-not (Test-Path -LiteralPath $Target -PathType Leaf)) { throw "$Name is missing." }
    $raw = [System.IO.File]::ReadAllText($Target, [System.Text.Encoding]::UTF8)
    if ($raw -cnotmatch '\A([^\r\n]+)(?:\r?\n)?\z') { throw "$Name must contain exactly one nonempty line." }
    return $Matches[1]
}
function Copy-ProtectedRestoreSource([string]$Source) {
    $staging = Join-Path $credentialDirectory (".restore-source." + [Guid]::NewGuid().ToString("N"))
    try {
        New-Item -ItemType Directory -Path $staging | Out-Null
        Protect-CredentialPath $staging $true
        foreach ($item in @(Get-ChildItem -LiteralPath $Source -Force)) {
            Copy-Item -LiteralPath $item.FullName -Destination $staging -Recurse -Force
        }
        Protect-CredentialTree $staging
        return $staging
    }
    catch {
        if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
        throw
    }
}
function Replace-CredentialFile([string]$Target, [string]$Value) {
    $temporary = Join-Path $credentialDirectory (".credential." + [Guid]::NewGuid().ToString("N"))
    try {
        Write-CredentialFile $temporary $Value
        # Preserve the file identity so existing Compose secret bind mounts
        # observe the update. The transaction retains both sets for recovery.
        [System.IO.File]::WriteAllBytes($Target, [System.IO.File]::ReadAllBytes($temporary))
        Protect-CredentialPath $Target $false
    }
    finally { if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force } }
}
if (Test-Path -LiteralPath $credentialDirectory) {
    if (-not (Test-OwnerOnlyPath $credentialDirectory $true)) { throw "Credential directory must be an owner-only non-reparse-point directory: $credentialDirectory" }
}
else { New-Item -ItemType Directory -Force -Path $credentialDirectory | Out-Null }
Protect-CredentialTree $credentialDirectory
$instanceFile = Join-Path $credentialDirectory "instance"
if (Test-Path $instanceFile) {
    if ((Read-InstanceMarker $instanceFile "Credential instance marker") -cne $project) { throw "Credential directory belongs to a different instance; refusing to use it." }
}
else { Write-InstanceMarker $instanceFile $project }

docker volume inspect "${project}_schemii-metadata-postgres" *> $null
$legacyMetadata = $LASTEXITCODE -eq 0
$migrationFile = Join-Path $credentialDirectory "metadata_migration_password"
if ($legacyMetadata -and -not (Test-Path $migrationFile)) {
    $legacyMetadataContainer = (& docker ps -aq --filter "label=com.docker.compose.project=$project" --filter "label=com.docker.compose.service=metadata-postgres" | Select-Object -First 1)
    $legacyValues = @{}
    if ($legacyMetadataContainer) {
        $details = (& docker inspect $legacyMetadataContainer | ConvertFrom-Json)[0]
        foreach ($item in $details.Config.Env) {
            $separator = $item.IndexOf("=")
            if ($separator -gt 0) { $legacyValues[$item.Substring(0, $separator)] = $item.Substring($separator + 1) }
        }
    }
    $defaults = @{
        metadata_bootstrap_password = if ($legacyValues.POSTGRES_PASSWORD) { $legacyValues.POSTGRES_PASSWORD } else { "schemii-metadata-bootstrap-local" }
        metadata_migration_password = if ($legacyValues.SCHEMII_METADATA_MIGRATION_PASSWORD) { $legacyValues.SCHEMII_METADATA_MIGRATION_PASSWORD } else { "schemii-metadata-migration-local" }
        metadata_schemii_password = if ($legacyValues.SCHEMII_METADATA_SCHEMII_PASSWORD) { $legacyValues.SCHEMII_METADATA_SCHEMII_PASSWORD } else { "schemii-metadata-runtime-local" }
        metadata_schemer_password = if ($legacyValues.SCHEMII_METADATA_SCHEMER_PASSWORD) { $legacyValues.SCHEMII_METADATA_SCHEMER_PASSWORD } else { "schemer-metadata-runtime-local" }
    }
    foreach ($name in $defaults.Keys) { Write-CredentialFile (Join-Path $credentialDirectory $name) $defaults[$name] }
    Write-Warning "Existing metadata volume ${project}_schemii-metadata-postgres was found without managed credentials. Historical credentials were preserved. Back them up; legacy rotation may first require the reviewed bootstrap-owned function. The volume was not reset."
}
foreach ($name in $credentialFiles) {
    $secretPath = Join-Path $credentialDirectory $name
    if (-not (Test-Path $secretPath)) { Write-CredentialFile $secretPath (New-CredentialValue) }
    else { Protect-CredentialPath $secretPath $false }
    [void](Read-CredentialValue $secretPath $name)
}
$env:SCHEMII_CREDENTIAL_DIR = $credentialDirectory

function Test-LegacyAdoptableVolume([string]$LogicalName) {
    return $project -ceq "schemii" -and $LogicalName -cin @("schemii-config", "schemii-schemas")
}
function Get-LegacyVolumeIdentity([string]$LogicalName) {
    $volume = "${project}_$LogicalName"
    $output = @(& docker volume inspect --format '{{.Name}}|{{.CreatedAt}}|{{.Driver}}|{{.Mountpoint}}|{{.Scope}}|{{json .Labels}}' $volume 2>$null)
    if ($LASTEXITCODE -ne 0 -or $output.Count -ne 1) { return $null }
    $parts = ([string]$output[0]) -split '\|', 6
    if ($parts.Count -ne 6) { return $null }
    $identity = [pscustomobject]@{
        Name = $parts[0]
        CreatedAt = $parts[1]
        Driver = $parts[2]
        Mountpoint = $parts[3]
        Scope = $parts[4]
        Labels = $parts[5]
    }
    if (
        $identity.Name -cne $volume -or -not $identity.CreatedAt -or
        $identity.Driver -cne "local" -or -not $identity.Mountpoint -or
        $identity.Scope -cne "local" -or $identity.Labels -cnotin @("null", "{}")
    ) { return $null }
    return $identity
}
function Get-LegacyManifestBody([string]$LogicalName, $Identity) {
    return (@(
        "format=schemii-legacy-volume-adoption-v1",
        "project=$project",
        "repository=$scriptDirectory",
        "logical=$LogicalName",
        "volume=$($Identity.Name)",
        "created-at=$($Identity.CreatedAt)",
        "driver=$($Identity.Driver)",
        "mountpoint=$($Identity.Mountpoint)",
        "scope=$($Identity.Scope)"
    ) -join "`n") + "`n"
}
function Test-LegacyAdoptionSet {
    if (-not (Test-LegacyAdoptableVolume "schemii-config")) { return $false }
    if (-not (Test-OwnerOnlyPath $legacyAdoptionDirectory $true)) { return $false }
    $items = @(Get-ChildItem -LiteralPath $legacyAdoptionDirectory -Force)
    if ($items.Count -ne 2) { return $false }
    $expectedNames = @("schemii-config.manifest", "schemii-schemas.manifest")
    foreach ($item in $items) {
        if ($item.Name -cnotin $expectedNames -or -not (Test-OwnerOnlyPath $item.FullName $false)) { return $false }
    }
    foreach ($logicalName in @("schemii-config", "schemii-schemas")) {
        $identity = Get-LegacyVolumeIdentity $logicalName
        if (-not $identity) { return $false }
        $manifest = Join-Path $legacyAdoptionDirectory "$logicalName.manifest"
        if (-not (Test-OwnerOnlyPath $manifest $false)) { return $false }
        try { $actual = [System.IO.File]::ReadAllText($manifest, [System.Text.Encoding]::UTF8) }
        catch { return $false }
        if ($actual -cne (Get-LegacyManifestBody $logicalName $identity)) { return $false }
    }
    return $true
}
function Test-RepositoryWorkingDirectory([string]$WorkingDirectory) {
    if (-not $WorkingDirectory) { return $false }
    try { $resolved = [System.IO.Path]::GetFullPath($WorkingDirectory) }
    catch { return $false }
    $comparison = if ($runningOnWindows) { [System.StringComparison]::OrdinalIgnoreCase } else { [System.StringComparison]::Ordinal }
    return [string]::Equals($resolved, $scriptDirectory, $comparison)
}
function Test-ExpectedLegacyConsumer([string]$LogicalName, [string]$Service, [string]$Destination) {
    return "$LogicalName|$Service|$Destination" -cin @(
        "schemii-config|schemii|/data/config",
        "schemii-config|schemer|/data/config",
        "schemii-config|example-profile-init|/data/config",
        "schemii-config|metadata-recovery|/data/config",
        "schemii-config|application-recovery-verify|/data/config",
        "schemii-schemas|schemii|/data/schemas",
        "schemii-schemas|schemer|/data/schemas",
        "schemii-schemas|metadata-recovery|/data/schemas",
        "schemii-schemas|application-recovery-verify|/data/schemas"
    )
}
function Assert-LegacyVolumeConsumers([string]$LogicalName) {
    $volume = "${project}_$LogicalName"
    $containers = @(& docker ps -aq)
    if ($LASTEXITCODE -ne 0) { throw "Docker could not enumerate containers while attesting $volume. No adoption manifest was written." }
    $witness = $false
    foreach ($container in @($containers | Where-Object { $_ })) {
        $mounts = @(& docker inspect --format '{{range .Mounts}}{{printf "%s|%s|%s\n" .Type .Name .Destination}}{{end}}' $container 2>$null)
        if ($LASTEXITCODE -ne 0) { throw "Docker could not inspect all mounts for container $container. No adoption manifest was written." }
        foreach ($mount in @($mounts | Where-Object { $_ })) {
            $parts = ([string]$mount) -split '\|', 3
            if ($parts.Count -ne 3) { throw "Docker returned an invalid mount record for container $container. No adoption manifest was written." }
            if ($parts[1] -cne $volume) { continue }
            $labels = @(& docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}|{{ index .Config.Labels "com.docker.compose.service" }}|{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' $container 2>$null)
            if ($LASTEXITCODE -ne 0 -or $labels.Count -ne 1) { throw "Docker could not inspect ownership labels for volume consumer $container. No adoption manifest was written." }
            $ownership = ([string]$labels[0]) -split '\|', 3
            if (
                $ownership.Count -ne 3 -or $parts[0] -cne "volume" -or
                $ownership[0] -cne $project -or -not (Test-RepositoryWorkingDirectory $ownership[2]) -or
                -not (Test-ExpectedLegacyConsumer $LogicalName $ownership[1] $parts[2])
            ) { throw "Volume $volume has a foreign or unexpected consumer: $container. No adoption manifest was written." }
            $witness = $true
        }
    }
    if (-not $witness) { throw "Volume $volume has no expected Compose project/service/repository witness. No adoption manifest was written." }
}
function Assert-InstanceStopped {
    $containers = @(& docker ps -aq --filter "label=com.docker.compose.project=$project")
    if ($LASTEXITCODE -ne 0) { throw "Docker could not enumerate instance $project; legacy adoption or recovery refuses to infer that it is stopped." }
    foreach ($container in @($containers | Where-Object { $_ })) {
        $running = (& docker inspect --format '{{.State.Running}}' $container 2>$null)
        if ($LASTEXITCODE -ne 0) { throw "Docker could not inspect instance container $container; legacy adoption or recovery refuses to infer that it is stopped." }
        if ($running -ceq "true") { throw "Stop every container in instance $project before legacy adoption, coordinated backup, or restore. No data was changed." }
        if ($running -cne "false") { throw "Docker returned an invalid running state for instance container $container; legacy adoption or recovery refuses to continue." }
    }
}
function Invoke-LegacyVolumeAdoption([string]$Confirmation) {
    if ($project -cne "schemii") { throw "Legacy adoption is limited to the historical schemii volume pair." }
    if ($Confirmation -cne "ADOPT:$project") { throw "Legacy adoption requires literal confirmation ADOPT:$project. No adoption manifest was written." }
    Assert-InstanceStopped
    foreach ($logicalName in @("schemii-config", "schemii-schemas")) {
        if (-not (Get-LegacyVolumeIdentity $logicalName)) { throw "Historical volume ${project}_$logicalName is missing, labeled, non-local, or lacks stable Docker identity. No adoption manifest was written." }
        Assert-LegacyVolumeConsumers $logicalName
    }
    if (Test-Path -LiteralPath $legacyAdoptionDirectory) {
        if (-not (Test-LegacyAdoptionSet)) { throw "Existing legacy adoption evidence is incomplete, changed, or bound to different volumes; refusing to replace it." }
        Write-Host "Historical volumes for $project are already attested by unchanged owner-only manifests."
        return
    }
    $staging = Join-Path $credentialDirectory (".legacy-volume-adoptions.v1." + [Guid]::NewGuid().ToString("N"))
    try {
        New-Item -ItemType Directory -Path $staging | Out-Null
        Protect-CredentialPath $staging $true
        foreach ($logicalName in @("schemii-config", "schemii-schemas")) {
            $identity = Get-LegacyVolumeIdentity $logicalName
            if (-not $identity) { throw "Historical volume identity changed during adoption. No adoption manifest was published." }
            $manifest = Join-Path $staging "$logicalName.manifest"
            $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes((Get-LegacyManifestBody $logicalName $identity))
            $stream = [System.IO.File]::Open($manifest, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
            try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) }
            finally { $stream.Dispose() }
            Protect-CredentialPath $manifest $false
        }
        Move-Item -LiteralPath $staging -Destination $legacyAdoptionDirectory
        $staging = $null
    }
    finally { if ($staging -and (Test-Path -LiteralPath $staging)) { Remove-Item -LiteralPath $staging -Recurse -Force } }
    if (-not (Test-LegacyAdoptionSet)) { throw "Published legacy adoption manifests failed verification." }
    Write-Host "Attested historical volumes ${project}_schemii-config and ${project}_schemii-schemas without changing their labels or contents."
}

function Invoke-MetadataPsql([string]$Container, [string]$AuthenticationPassword, [string]$Sql) {
    $localPgpass = Join-Path $credentialDirectory (".pgpass." + [Guid]::NewGuid().ToString("N"))
    try {
        [System.IO.File]::WriteAllText($localPgpass, "127.0.0.1:5432:schemii_metadata:schemii_metadata_migration:$AuthenticationPassword`n", [System.Text.UTF8Encoding]::new($false))
        Protect-CredentialPath $localPgpass $false
        & docker cp $localPgpass "${Container}:/tmp/schemii-credential-operation.pgpass" *> $null
        if ($LASTEXITCODE -ne 0) { throw "Could not stage metadata authentication." }
        & docker exec $Container sh -c 'chown postgres:postgres /tmp/schemii-credential-operation.pgpass && chmod 600 /tmp/schemii-credential-operation.pgpass' *> $null
        if ($LASTEXITCODE -ne 0) { throw "Could not protect staged metadata authentication." }
        $Sql | & docker exec -i -u postgres -e PGPASSFILE=/tmp/schemii-credential-operation.pgpass $Container psql --quiet --set ON_ERROR_STOP=1 --host 127.0.0.1 --username schemii_metadata_migration --dbname schemii_metadata *> $null
        if ($LASTEXITCODE -ne 0) { throw "Metadata authentication or credential update failed." }
    }
    finally {
        & docker exec -u postgres $Container rm -f /tmp/schemii-credential-operation.pgpass *> $null
        if (Test-Path -LiteralPath $localPgpass) { Remove-Item -LiteralPath $localPgpass -Force }
    }
}
function Test-MetadataAuthentication([string]$Container, [string]$Password) {
    try { Invoke-MetadataPsql $Container $Password "SELECT 1;"; return $true }
    catch { return $false }
}
function Wait-MetadataReady([string]$Container) {
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        & docker exec -u postgres $Container pg_isready --quiet --host 127.0.0.1 --port 5432 --dbname schemii_metadata *> $null
        if ($LASTEXITCODE -eq 0) { return }
        Start-Sleep -Seconds 1
    }
    throw "Metadata PostgreSQL did not become ready within 30 seconds."
}
function Invoke-MetadataPasswordUpdate([string]$Container, [string]$AuthenticationPassword, [hashtable]$Values) {
    $inputText = @(
        "\prompt '' migration_password"
        $Values["metadata_migration_password"]
        "\prompt '' schemii_password"
        $Values["metadata_schemii_password"]
        "\prompt '' schemer_password"
        $Values["metadata_schemer_password"]
        "SELECT schemii_admin.rotate_metadata_passwords(:'migration_password', :'schemii_password', :'schemer_password');"
    ) -join "`n"
    Invoke-MetadataPsql $Container $AuthenticationPassword $inputText
}
function Restart-CredentialConsumers([string]$MetadataContainer) {
    & docker restart $MetadataContainer *> $null
    if ($LASTEXITCODE -ne 0) { throw "Metadata container restart failed." }
    $containers = @(& docker ps -q --filter "label=com.docker.compose.project=$project")
    foreach ($container in $containers) {
        if ($container -and $container -cne $MetadataContainer) {
            & docker restart $container *> $null
            if ($LASTEXITCODE -ne 0) { throw "Dependent container restart failed." }
        }
    }
}
function Get-TransactionValues([string]$Side) {
    $values = @{}
    foreach ($name in $credentialFiles) { $values[$name] = Read-CredentialValue (Join-Path (Join-Path $credentialTransaction $Side) $name) "$Side $name" }
    return $values
}
function Replace-TransactionValues([string]$Side) {
    $values = Get-TransactionValues $Side
    foreach ($name in $credentialFiles) { Replace-CredentialFile (Join-Path $credentialDirectory $name) $values[$name] }
}
function New-CredentialTransaction([hashtable]$NewValues, [string]$Operation = "credential-operation") {
    if (Test-Path -LiteralPath $credentialTransaction -PathType Container) {
        if ((Read-InstanceMarker (Join-Path $credentialTransaction "operation") "Credential transaction operation") -cne $Operation) {
            throw "Existing credential transaction belongs to another operation."
        }
        $staged = Get-TransactionValues "new"
        foreach ($name in $credentialFiles) {
            if ($staged[$name] -cne $NewValues[$name]) { throw "Existing credential transaction does not match the reviewed credentials." }
        }
        return
    }
    $staging = Join-Path $credentialDirectory (".credential-transaction-stage." + [Guid]::NewGuid().ToString("N"))
    try {
        New-Item -ItemType Directory -Path $staging | Out-Null
        Protect-CredentialPath $staging $true
        $oldDirectory = Join-Path $staging "old"
        $newDirectory = Join-Path $staging "new"
        New-Item -ItemType Directory -Path $oldDirectory, $newDirectory | Out-Null
        Protect-CredentialPath $oldDirectory $true
        Protect-CredentialPath $newDirectory $true
        foreach ($name in $credentialFiles) {
            Write-CredentialFile (Join-Path $oldDirectory $name) (Read-CredentialValue (Join-Path $credentialDirectory $name) $name)
            Write-CredentialFile (Join-Path $newDirectory $name) $NewValues[$name]
        }
        Write-InstanceMarker (Join-Path $staging "instance") $project
        Write-InstanceMarker (Join-Path $staging "operation") $Operation
        Move-Item -LiteralPath $staging -Destination $credentialTransaction
    }
    finally { if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force } }
}
function Undo-CredentialTransaction([string]$MetadataContainer, [bool]$PreserveTransaction = $false) {
    $oldValues = Get-TransactionValues "old"
    $newValues = Get-TransactionValues "new"
    & docker start $MetadataContainer *> $null
    if ($LASTEXITCODE -ne 0) { throw "Metadata container could not start for credential recovery." }
    Wait-MetadataReady $MetadataContainer
    if (Test-MetadataAuthentication $MetadataContainer $newValues["metadata_migration_password"]) {
        Invoke-MetadataPasswordUpdate $MetadataContainer $newValues["metadata_migration_password"] $oldValues
    }
    elseif (-not (Test-MetadataAuthentication $MetadataContainer $oldValues["metadata_migration_password"])) {
        throw "Neither staged metadata credential authenticates; transaction recovery requires administrator review."
    }
    Replace-TransactionValues "old"
    Restart-CredentialConsumers $MetadataContainer
    Wait-MetadataReady $MetadataContainer
    if (-not (Test-MetadataAuthentication $MetadataContainer $oldValues["metadata_migration_password"])) { throw "Rolled-back metadata credential did not authenticate." }
    if (-not $PreserveTransaction) { Remove-Item -LiteralPath $credentialTransaction -Recurse -Force }
}
function Complete-CredentialTransaction([string]$MetadataContainer, [bool]$PreserveTransaction = $false) {
    $oldValues = Get-TransactionValues "old"
    $newValues = Get-TransactionValues "new"
    Wait-MetadataReady $MetadataContainer
    Invoke-MetadataPasswordUpdate $MetadataContainer $oldValues["metadata_migration_password"] $newValues
    Replace-TransactionValues "new"
    Restart-CredentialConsumers $MetadataContainer
    Wait-MetadataReady $MetadataContainer
    if (-not (Test-MetadataAuthentication $MetadataContainer $newValues["metadata_migration_password"])) { throw "Restored metadata credential did not authenticate." }
    if (-not $PreserveTransaction) { Remove-Item -LiteralPath $credentialTransaction -Recurse -Force }
}
function Assert-CommittedRestoreCredentials([hashtable]$ReviewedValues) {
    if ((Test-Path -LiteralPath $credentialTransaction) -and (Test-Path -LiteralPath $credentialCommitCleanup)) {
        throw "Both rollback and committed credential cleanup transactions exist; refusing automatic recovery."
    }
    if (Test-Path -LiteralPath $credentialTransaction) {
        if (-not (Test-Path -LiteralPath $credentialTransaction -PathType Container)) { throw "Credential rollback transaction is invalid." }
        if ((Read-InstanceMarker (Join-Path $credentialTransaction "instance") "Credential transaction marker") -cne $project) {
            throw "Credential transaction belongs to another instance."
        }
        if ((Read-InstanceMarker (Join-Path $credentialTransaction "operation") "Credential transaction operation") -cne "instance-restore") {
            throw "Credential transaction is not a coordinated restore transaction."
        }
        $stagedValues = Get-TransactionValues "new"
        foreach ($name in $credentialFiles) {
            if ($stagedValues[$name] -cne $ReviewedValues[$name]) { throw "Staged credentials do not match the committed restore source; refusing cleanup." }
        }
    }
    if ((Test-Path -LiteralPath $credentialCommitCleanup) -and -not (Test-Path -LiteralPath $credentialCommitCleanup -PathType Container)) {
        throw "Committed credential cleanup transaction is invalid."
    }
    foreach ($name in $credentialFiles) {
        if ((Read-CredentialValue (Join-Path $credentialDirectory $name) "Active $name") -cne $ReviewedValues[$name]) {
            throw "Active credentials do not match the committed restore source; refusing cleanup."
        }
    }
}
function Complete-CommittedCredentialCleanup {
    if (Test-Path -LiteralPath $credentialTransaction -PathType Container) {
        if (Test-Path -LiteralPath $credentialCommitCleanup) { throw "Committed credential cleanup destination already exists." }
        Move-Item -LiteralPath $credentialTransaction -Destination $credentialCommitCleanup
    }
    if (Test-Path -LiteralPath $credentialCommitCleanup) {
        if (-not (Test-Path -LiteralPath $credentialCommitCleanup -PathType Container)) { throw "Committed credential cleanup transaction is invalid." }
        Remove-Item -LiteralPath $credentialCommitCleanup -Recurse -Force
    }
}

Get-ChildItem -LiteralPath $credentialDirectory -Directory -Filter ".credential-transaction-stage.*" | Remove-Item -Recurse -Force
if ((Test-Path -LiteralPath $credentialCommitCleanup) -and $Mode -cne "instance-restore") {
    throw "An interrupted committed restore requires forward cleanup by rerunning instance-restore with its reviewed backup and RESTORE:$project confirmation."
}
if (Test-Path -LiteralPath $credentialTransaction -PathType Container) {
    if ((Read-InstanceMarker (Join-Path $credentialTransaction "instance") "Credential transaction marker") -cne $project) { throw "Credential transaction belongs to another instance; refusing recovery." }
    $transactionOperationPath = Join-Path $credentialTransaction "operation"
    $transactionOperation = if (Test-Path -LiteralPath $transactionOperationPath -PathType Leaf) { Read-InstanceMarker $transactionOperationPath "Credential transaction operation" } else { "credential-operation" }
    if ($transactionOperation -ceq "instance-restore" -and $Mode -cne "instance-restore") {
        throw "An interrupted coordinated restore must be resolved by rerunning instance-restore with its reviewed backup and RESTORE:$project confirmation; its durable state determines rollback or forward cleanup."
    }
    if ($transactionOperation -ceq "instance-restore") {
        Write-Warning "Retained coordinated credential evidence will follow the durable recovery state for $project."
    }
    else {
        $recoveryContainer = (& docker ps -aq --filter "label=com.docker.compose.project=$project" --filter "label=com.docker.compose.service=metadata-postgres" | Select-Object -First 1)
        if (-not $recoveryContainer) { throw "An incomplete credential transaction needs its metadata container for recovery." }
        Write-Warning "Recovering an incomplete $transactionOperation transaction for $project."
        Undo-CredentialTransaction $recoveryContainer
    }
}

if ($Mode -eq "credentials-backup") {
    if (-not $Path) { throw "credentials-backup requires -Path <directory>." }
    $backupDirectory = Join-Path ([System.IO.Path]::GetFullPath($Path)) $project
    New-Item -ItemType Directory -Force -Path $backupDirectory | Out-Null
    Protect-CredentialTree $backupDirectory
    foreach ($name in @("instance") + $credentialFiles) { Copy-Item -LiteralPath (Join-Path $credentialDirectory $name) -Destination (Join-Path $backupDirectory $name); Protect-CredentialPath (Join-Path $backupDirectory $name) $false }
    Write-Host "Credential backup created at $backupDirectory. Protect it like a password vault."
    exit 0
}
if ($Mode -eq "credentials-restore") {
    if (-not $Path) { throw "credentials-restore requires -Path <directory>." }
    $sourceDirectory = [System.IO.Path]::GetFullPath($Path)
    if (Test-Path (Join-Path $sourceDirectory $project) -PathType Container) { $sourceDirectory = Join-Path $sourceDirectory $project }
    $restoreSourceStaging = Copy-ProtectedRestoreSource $sourceDirectory
    $sourceDirectory = $restoreSourceStaging
    if ((Read-InstanceMarker (Join-Path $sourceDirectory "instance") "Backup instance marker") -cne $project) { throw "Backup instance marker does not exactly match $project." }
    $restored = @{}
    foreach ($name in $credentialFiles) {
        $sourceFile = Join-Path $sourceDirectory $name
        $restored[$name] = Read-CredentialValue $sourceFile "Backup $name"
    }
    if ($legacyMetadata) {
        $metadataContainer = (& docker ps -aq --filter "label=com.docker.compose.project=$project" --filter "label=com.docker.compose.service=metadata-postgres" | Select-Object -First 1)
        if (-not $metadataContainer) { throw "Start the instance before restoring credentials for its existing metadata volume. No files were changed." }
        & docker start $metadataContainer *> $null
        New-CredentialTransaction $restored
        try { Complete-CredentialTransaction $metadataContainer }
        catch {
            Write-Warning "Credential restore failed; rolling back PostgreSQL, files, and containers."
            Undo-CredentialTransaction $metadataContainer
            throw
        }
    }
    else { foreach ($name in $credentialFiles) { Replace-CredentialFile (Join-Path $credentialDirectory $name) $restored[$name] } }
    Write-Host "Credentials restored for $project and dependent containers restarted."
    exit 0
}
if ($Mode -eq "credentials-rotate") {
    $metadataContainer = (& docker ps -q --filter "label=com.docker.compose.project=$project" --filter "label=com.docker.compose.service=metadata-postgres" | Select-Object -First 1)
    if (-not $metadataContainer) { throw "Start the instance before rotating credentials. No files were changed." }
    $newValues = @{}
    $newValues["metadata_bootstrap_password"] = Read-CredentialValue (Join-Path $credentialDirectory "metadata_bootstrap_password") "metadata_bootstrap_password"
    foreach ($name in @("metadata_migration_password", "metadata_schemii_password", "metadata_schemer_password", "opencode_password")) { $newValues[$name] = New-CredentialValue }
    New-CredentialTransaction $newValues
    try { Complete-CredentialTransaction $metadataContainer }
    catch {
        Write-Warning "Credential rotation failed; rolling back PostgreSQL, files, and containers."
        Undo-CredentialTransaction $metadataContainer
        throw
    }
    Write-Host "Credentials rotated for $project and dependent containers restarted."
    exit 0
}
if ($Mode -eq "legacy-volume-adopt") {
    Invoke-LegacyVolumeAdoption $ConfirmInstance
    exit 0
}
}
finally {
    if ($restoreSourceStaging -and (Test-Path -LiteralPath $restoreSourceStaging)) {
        Remove-Item -LiteralPath $restoreSourceStaging -Recurse -Force
        $restoreSourceStaging = $null
    }
    if ($Mode -notin @("instance-backup", "instance-restore")) { Exit-CredentialLock }
}
if ($project -eq "schemii") {
    $defaultPort = 8080
    $defaultSchemerPort = 8081
}
else {
    $portSha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $portHash = $portSha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($project))
        $portNumber = [BitConverter]::ToUInt32($portHash, 0)
    }
    finally {
        $portSha.Dispose()
    }
    $defaultPort = 12000 + ($portNumber % 30000)
    $defaultSchemerPort = 12000 + (($portNumber + 1) % 30000)
}
function Test-LocalTcpPort([int]$Port) {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $task = $client.ConnectAsync("127.0.0.1", $Port)
        return $task.Wait(150) -and $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}
$currentInstance = (& docker ps -aq --filter "label=com.docker.compose.project=$project" --filter "label=com.docker.compose.service=schemii" | Select-Object -First 1)
$currentSchemiiIngress = (& docker ps -aq --filter "label=com.docker.compose.project=$project" --filter "label=com.docker.compose.service=schemii-ingress" | Select-Object -First 1)
$currentSchemer = (& docker ps -aq --filter "label=com.docker.compose.project=$project" --filter "label=com.docker.compose.service=schemer" | Select-Object -First 1)
$currentSchemerIngress = (& docker ps -aq --filter "label=com.docker.compose.project=$project" --filter "label=com.docker.compose.service=schemer-ingress" | Select-Object -First 1)
if (-not $env:SCHEMII_HOST_PORT) {
    $selectedPort = $defaultPort
    if ($currentSchemiiIngress -or $currentInstance) {
        $portContainer = if ($currentSchemiiIngress) { $currentSchemiiIngress } else { $currentInstance }
        $details = (& docker inspect $portContainer | ConvertFrom-Json)[0]
        $binding = $details.HostConfig.PortBindings.'8080/tcp'
        if ($binding) {
            $selectedPort = [int]$binding[0].HostPort
        }
        else {
            $appDetails = if ($currentInstance) { (& docker inspect $currentInstance | ConvertFrom-Json)[0] } else { $details }
            $portEnvironment = $appDetails.Config.Env | Where-Object { $_.StartsWith("SCHEMII_PORT=") } | Select-Object -First 1
            if ($portEnvironment) { $selectedPort = [int]$portEnvironment.Substring("SCHEMII_PORT=".Length) }
        }
    }
    else {
        while (Test-LocalTcpPort $selectedPort) {
            $selectedPort++
            if ($selectedPort -gt 41999) { $selectedPort = 12000 }
        }
    }
    $env:SCHEMII_HOST_PORT = [string]$selectedPort
}
if (-not $env:SCHEMER_HOST_PORT) {
    $selectedSchemerPort = $defaultSchemerPort
    if ($currentSchemerIngress -or $currentSchemer) {
        $schemerPortContainer = if ($currentSchemerIngress) { $currentSchemerIngress } else { $currentSchemer }
        $schemerDetails = (& docker inspect $schemerPortContainer | ConvertFrom-Json)[0]
        $schemerBinding = $schemerDetails.HostConfig.PortBindings.'8080/tcp'
        if (-not $schemerBinding -and -not $currentSchemerIngress) { $schemerBinding = $schemerDetails.HostConfig.PortBindings.'8081/tcp' }
        if ($schemerBinding) { $selectedSchemerPort = [int]$schemerBinding[0].HostPort }
    }
    else {
        while ((Test-LocalTcpPort $selectedSchemerPort) -or $selectedSchemerPort -eq [int]$env:SCHEMII_HOST_PORT) {
            $selectedSchemerPort++
            if ($selectedSchemerPort -gt 41999) { $selectedSchemerPort = 12000 }
        }
    }
    $env:SCHEMER_HOST_PORT = [string]$selectedSchemerPort
}
$defaultApplicationImage = "schemii:$project"
$defaultMetadataImage = "schemii-metadata-postgres:$project"
$defaultOpenCodeImage = "schemii-opencode:1.18.15-$project"
$releaseVersion = if (Test-Path -LiteralPath (Join-Path $scriptDirectory "VERSION")) { [System.IO.File]::ReadAllText((Join-Path $scriptDirectory "VERSION")).Trim() } else { "" }
$releaseRevisionPath = Join-Path $scriptDirectory "src/schemii/build_revision.txt"
$releaseRevision = if (Test-Path -LiteralPath $releaseRevisionPath) { [System.IO.File]::ReadAllText($releaseRevisionPath).Trim() } else { "" }
if ($releaseVersion -match '^[0-9]+\.[0-9]+\.[0-9]+$' -and $releaseRevision -match '^[0-9a-f]{40}$') {
    $releaseIdentity = "$releaseVersion-$releaseRevision"
    $defaultApplicationImage = "schemii:$releaseIdentity"
    $defaultMetadataImage = "schemii-metadata-postgres:$releaseIdentity"
    $defaultOpenCodeImage = "schemii-opencode:$releaseIdentity"
}
if (-not $env:SCHEMII_IMAGE) { $env:SCHEMII_IMAGE = $defaultApplicationImage }
if (-not $env:SCHEMII_METADATA_IMAGE) { $env:SCHEMII_METADATA_IMAGE = $defaultMetadataImage }
if (-not $env:SCHEMII_OPENCODE_IMAGE) { $env:SCHEMII_OPENCODE_IMAGE = $defaultOpenCodeImage }

$composeArgs = @("compose", "--project-name", $project, "--project-directory", $scriptDirectory, "-f", (Join-Path $scriptDirectory "compose.yaml"))
switch ($Mode) {
    "docker-db" {
        $composeArgs += @("-f", (Join-Path $scriptDirectory "compose.postgres.yaml"))
    }
    "ai" {
        $composeArgs += @("-f", (Join-Path $scriptDirectory "compose.ai.yaml"))
    }
    "ai-local-db" {
        if (-not $IsLinux) {
            throw "ai-local-db mode is Linux-only. Use ai mode with host.docker.internal on Docker Desktop."
        }
        $composeArgs += @("-f", (Join-Path $scriptDirectory "compose.local-db.yaml"), "-f", (Join-Path $scriptDirectory "compose.ai.yaml"), "-f", (Join-Path $scriptDirectory "compose.ai.local-db.yaml"))
    }
    "ai-docker-db" {
        $composeArgs += @("-f", (Join-Path $scriptDirectory "compose.postgres.yaml"), "-f", (Join-Path $scriptDirectory "compose.ai.yaml"))
    }
    "schemer" {
        $composeArgs += @("-f", (Join-Path $scriptDirectory "compose.postgres.yaml"), "-f", (Join-Path $scriptDirectory "compose.schemer.yaml"))
    }
    "schemer-ai" {
        $composeArgs += @("-f", (Join-Path $scriptDirectory "compose.postgres.yaml"), "-f", (Join-Path $scriptDirectory "compose.ai.yaml"), "-f", (Join-Path $scriptDirectory "compose.schemer.yaml"), "-f", (Join-Path $scriptDirectory "compose.schemer.ai.yaml"))
    }
}

if ($Mode -in @("instance-backup", "instance-restore")) {
    $recoveryComposeArgs = @(
        "compose", "--project-name", $project, "--project-directory", $scriptDirectory,
        "-f", (Join-Path $scriptDirectory "compose.yaml"),
        "-f", (Join-Path $scriptDirectory "compose.recovery.yaml")
    )
    $recoveryMetadataContainer = $null
    $recoveryContainer = $null
    $recoveryBackupStaging = $null
    $restoreSourceStaging = $null

    function Assert-DockerSucceeded([string]$Message) {
        if ($LASTEXITCODE -ne 0) { throw $Message }
    }
    function Assert-RecoveryVolume([string]$LogicalName) {
        $volume = "${project}_$LogicalName"
        $labels = (& docker volume inspect --format '{{ index .Labels "com.docker.compose.project" }}|{{ index .Labels "com.docker.compose.volume" }}' $volume 2>$null)
        if ($LASTEXITCODE -eq 0 -and $labels -ceq "$project|$LogicalName") { return }
        if ($LASTEXITCODE -eq 0 -and $labels -ceq "|" -and (Test-LegacyAdoptableVolume $LogicalName)) {
            if (Test-LegacyAdoptionSet) { return }
            throw "Historical unlabeled volume $volume lacks unchanged adoption evidence. With all schemii containers stopped, run: `$env:SCHEMII_INSTANCE='schemii'; .\start.ps1 -Mode legacy-volume-adopt -ConfirmInstance ADOPT:schemii"
        }
        throw "Required reviewed destination volume is missing or belongs to another project: $volume"
    }
    function Start-RecoveryMetadata {
        & docker @recoveryComposeArgs up -d metadata-postgres
        Assert-DockerSucceeded "Metadata PostgreSQL could not be started for recovery."
        $script:recoveryMetadataContainer = (& docker @recoveryComposeArgs ps -q metadata-postgres | Select-Object -First 1)
        if (-not $script:recoveryMetadataContainer) { throw "Metadata PostgreSQL container was not created for recovery." }
        Wait-MetadataReady $script:recoveryMetadataContainer
    }
    function Initialize-RecoveryVolumes {
        & docker image inspect $env:SCHEMII_IMAGE $env:SCHEMII_METADATA_IMAGE *> $null
        Assert-DockerSucceeded "Selected immutable recovery images are not loaded."
        & docker @recoveryComposeArgs run --rm --no-deps metadata-recovery prepare
        Assert-DockerSucceeded "Recovery volumes could not be prepared."
        Assert-RecoveryVolume "schemer-dashboards"
        Assert-RecoveryVolume "schemii-recovery"
    }
    function Stop-RecoveryMetadata {
        if ($script:recoveryMetadataContainer) {
            & docker stop $script:recoveryMetadataContainer *> $null
            Assert-DockerSucceeded "Metadata PostgreSQL could not be stopped after recovery; retained recovery evidence was preserved."
            $script:recoveryMetadataContainer = $null
        }
    }
    function Get-RecoveryState {
        $output = @(& docker @recoveryComposeArgs run --rm --no-deps metadata-recovery state)
        Assert-DockerSucceeded "Recovery transaction state could not be determined; refusing automatic rollback."
        $stateLines = @($output | ForEach-Object { [string]$_ } | Where-Object { $_.Trim().Length -gt 0 })
        if ($stateLines.Count -eq 0) { throw "Recovery transaction returned no state." }
        $state = $stateLines[-1].Trim()
        if ($state -notin @("none", "rollback-required", "committed-cleanup-required")) {
            throw "Recovery transaction returned an invalid state: $state"
        }
        return $state
    }
    function Complete-CommittedRestore([hashtable]$ReviewedValues) {
        Assert-CommittedRestoreCredentials $ReviewedValues
        & docker @recoveryComposeArgs run --rm --no-deps metadata-recovery commit
        Assert-DockerSucceeded "Committed recovery data cleanup could not be completed; rollback remains forbidden."
        Complete-CommittedCredentialCleanup
        & docker @recoveryComposeArgs run --rm --no-deps metadata-recovery finalize-commit
        Assert-DockerSucceeded "Committed recovery finalization could not be completed; rollback remains forbidden."
    }
    function Resolve-RecoverySource([string]$Source) {
        if (-not $Source) { throw "instance-restore requires -Path <directory>." }
        $resolved = [System.IO.Path]::GetFullPath($Source)
        if (Test-Path -LiteralPath (Join-Path $resolved $project) -PathType Container) { $resolved = Join-Path $resolved $project }
        if (-not (Test-Path -LiteralPath $resolved -PathType Container)) { throw "Backup directory does not exist: $resolved" }
        return $resolved
    }
    function Assert-RecoveryDestination {
        Assert-InstanceStopped
        foreach ($logical in @("schemii-config", "schemii-schemas", "schemii-metadata-postgres")) { Assert-RecoveryVolume $logical }
        Initialize-RecoveryVolumes
    }
    function Invoke-RecoveryRollback([string]$MetadataContainer) {
        if (Test-Path -LiteralPath $credentialTransaction -PathType Container) {
            Undo-CredentialTransaction $MetadataContainer $true
        }
        & docker @recoveryComposeArgs run --rm --no-deps metadata-recovery rollback
        Assert-DockerSucceeded "Instance data rollback failed; recovery evidence has been retained."
        if (Test-Path -LiteralPath $credentialTransaction -PathType Container) { Remove-Item -LiteralPath $credentialTransaction -Recurse -Force }
    }

    try {
        if ($Mode -eq "instance-backup") {
            if (-not $Path) { throw "instance-backup requires -Path <directory>." }
            $backupDirectory = Join-Path ([System.IO.Path]::GetFullPath($Path)) $project
            if (Test-Path -LiteralPath $backupDirectory) { throw "Backup destination already exists; refusing to overwrite it: $backupDirectory" }
            Assert-RecoveryDestination
            $backupParent = Split-Path -Parent $backupDirectory
            New-Item -ItemType Directory -Force -Path $backupParent | Out-Null
            $recoveryBackupStaging = "${backupDirectory}.incomplete.$([Guid]::NewGuid().ToString('N'))"
            New-Item -ItemType Directory -Path $recoveryBackupStaging | Out-Null
            Protect-CredentialTree $recoveryBackupStaging
            Start-RecoveryMetadata
            & docker @recoveryComposeArgs run --rm --no-deps application-recovery-verify
            Assert-DockerSucceeded "Current config or dashboards failed backup validation."
            $recoveryContainer = "${project}-recovery-backup-$PID"
            & docker @recoveryComposeArgs run --name $recoveryContainer --no-deps metadata-recovery backup
            Assert-DockerSucceeded "Coordinated backup failed."
            & docker cp "${recoveryContainer}:/transaction/output/." $recoveryBackupStaging
            Assert-DockerSucceeded "Backup output could not be copied to the destination."
            & docker rm $recoveryContainer *> $null
            $recoveryContainer = $null
            if (-not (Test-Path -LiteralPath (Join-Path $recoveryBackupStaging "complete") -PathType Leaf)) {
                throw "Backup copy did not complete; the destination must not be used for restore."
            }
            Protect-CredentialTree $recoveryBackupStaging
            Move-Item -LiteralPath $recoveryBackupStaging -Destination $backupDirectory
            $recoveryBackupStaging = $null
            Stop-RecoveryMetadata
            Write-Host "Coordinated backup for $project created at $backupDirectory. It contains plaintext credentials and sensitive metadata."
            exit 0
        }

        if ($ConfirmInstance -cne "RESTORE:$project") { throw "Destructive restore confirmation must exactly equal RESTORE:$project" }
        $sourceDirectory = Resolve-RecoverySource $Path
        $restoreSourceStaging = Copy-ProtectedRestoreSource $sourceDirectory
        $sourceDirectory = $restoreSourceStaging
        if ((Read-InstanceMarker (Join-Path $sourceDirectory "instance") "Backup instance marker") -cne $project) {
            throw "Backup instance marker does not exactly match $project."
        }
        $credentialSource = Join-Path $sourceDirectory "credentials"
        if ((Read-InstanceMarker (Join-Path $credentialSource "instance") "Credential backup instance marker") -cne $project) {
            throw "Credential backup instance marker does not exactly match $project."
        }
        $restored = @{}
        foreach ($name in $credentialFiles) { $restored[$name] = Read-CredentialValue (Join-Path $credentialSource $name) "Backup $name" }

        Assert-RecoveryDestination
        $recoveryState = Get-RecoveryState
        if ($recoveryState -ceq "committed-cleanup-required") {
            Complete-CommittedRestore $restored
            Write-Host "Completed forward cleanup for the committed restore of $project. The instance remains stopped for review."
            exit 0
        }
        Start-RecoveryMetadata
        $metadataContainer = $recoveryMetadataContainer
        if ($recoveryState -ceq "rollback-required" -or (Test-Path -LiteralPath $credentialTransaction -PathType Container)) {
            if (Test-Path -LiteralPath $credentialTransaction -PathType Container) { New-CredentialTransaction $restored "instance-restore" }
            Write-Warning "Rolling back the incomplete coordinated restore for $project before retry."
            Invoke-RecoveryRollback $metadataContainer
        }
        $backupMount = "${sourceDirectory}:/backup:ro"
        $backupVerified = $false
        & docker @recoveryComposeArgs run --rm --no-deps -v $backupMount metadata-recovery stage-verification
        if ($LASTEXITCODE -eq 0) {
            & docker @recoveryComposeArgs run --rm --no-deps application-recovery-verify backup /transaction/verification
        }
        if ($LASTEXITCODE -eq 0) { $backupVerified = $true }
        & docker @recoveryComposeArgs run --rm --no-deps metadata-recovery clear-verification
        if ($LASTEXITCODE -ne 0) { $backupVerified = $false }
        if (-not $backupVerified) { throw "Backup compatibility validation failed before destination data was changed." }

        New-CredentialTransaction $restored "instance-restore"
        & docker @recoveryComposeArgs run --rm --no-deps -e "SCHEMII_RECOVERY_CONFIRM=RESTORE:$project" -v $backupMount metadata-recovery restore
        if ($LASTEXITCODE -eq 0) {
            & docker @recoveryComposeArgs run --rm --no-deps metadata-migrate
        }
        if ($LASTEXITCODE -eq 0) {
            & docker @recoveryComposeArgs run --rm --no-deps metadata-recovery verify-metadata
        }
        if ($LASTEXITCODE -eq 0) {
            & docker @recoveryComposeArgs run --rm --no-deps application-recovery-verify
        }
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Instance data restore failed; restoring the reviewed destination snapshot."
            Invoke-RecoveryRollback $metadataContainer
            throw "Coordinated instance data restore failed."
        }

        try { Complete-CredentialTransaction $metadataContainer $true }
        catch {
            Write-Warning "Coordinated credential restore failed; rolling back credentials and instance data."
            Invoke-RecoveryRollback $metadataContainer
            throw
        }
        try { Stop-RecoveryMetadata }
        catch {
            Write-Warning "Metadata PostgreSQL could not be stopped; recovery evidence was retained and commit was not attempted."
            throw
        }
        try {
            & docker @recoveryComposeArgs run --rm --no-deps metadata-recovery commit
            Assert-DockerSucceeded "Recovery transaction could not be committed."
        }
        catch {
            try { $failedCommitState = Get-RecoveryState }
            catch {
                Write-Warning "Recovery commit outcome is uncertain; evidence was retained and automatic rollback was not attempted."
                throw
            }
            if ($failedCommitState -ceq "committed-cleanup-required") {
                Write-Warning "Recovery commit began and forward cleanup remains required; evidence was retained and rollback was not attempted."
                throw
            }
            if ($failedCommitState -ceq "rollback-required") {
                Write-Warning "Recovery commit failed before publication; rolling back credentials and instance data."
                Invoke-RecoveryRollback $metadataContainer
                throw
            }
            Write-Warning "Recovery commit failed without a recoverable state; evidence was retained and automatic rollback was not attempted."
            throw
        }
        Assert-CommittedRestoreCredentials $restored
        Complete-CommittedCredentialCleanup
        & docker @recoveryComposeArgs run --rm --no-deps metadata-recovery finalize-commit
        Assert-DockerSucceeded "Recovery commit marker could not be finalized; forward cleanup remains required."
        Write-Host "Coordinated restore completed for $project. The instance remains stopped for review; rerun the desired launch mode when ready."
        exit 0
    }
    finally {
        if ($recoveryContainer) { & docker rm -f $recoveryContainer *> $null }
        if ($recoveryBackupStaging -and (Test-Path -LiteralPath $recoveryBackupStaging)) { Remove-Item -LiteralPath $recoveryBackupStaging -Recurse -Force }
        if ($restoreSourceStaging -and (Test-Path -LiteralPath $restoreSourceStaging)) { Remove-Item -LiteralPath $restoreSourceStaging -Recurse -Force }
        try { Stop-RecoveryMetadata }
        finally { Exit-CredentialLock }
    }
}
$composeFiles = $composeArgs
$upArgs = $composeArgs + @("up", "--no-build", "-d", "--remove-orphans")

$appService = "schemii"
$appName = "Schemii"
$port = $env:SCHEMII_HOST_PORT
$healthServices = [System.Collections.Generic.List[object]]::new()
$healthServices.Add(@("metadata-postgres", "metadata PostgreSQL"))
$healthServices.Add(@("schemii", "Schemii backend"))
$healthServices.Add(@("schemii-ingress", "Schemii ingress"))
if ($Mode -in @("docker-db", "ai-docker-db", "schemer", "schemer-ai")) { $healthServices.Add(@("postgres", "tutorial PostgreSQL")) }
if ($Mode -in @("ai", "ai-local-db", "ai-docker-db", "schemer-ai")) { $healthServices.Add(@("opencode", "OpenCode")) }
if ($Mode -in @("schemer", "schemer-ai")) {
    $appService = "schemer"
    $appName = "Schemer"
    $port = $env:SCHEMER_HOST_PORT
    $healthServices.Add(@("schemer", "Schemer backend"))
    $healthServices.Add(@("schemer-ingress", "Schemer ingress"))
}
$requiredImages = @($env:SCHEMII_IMAGE, $env:SCHEMII_METADATA_IMAGE)
if ($Mode -in @("ai", "ai-local-db", "ai-docker-db", "schemer-ai")) { $requiredImages += $env:SCHEMII_OPENCODE_IMAGE }
& docker image inspect @requiredImages *> $null
if ($LASTEXITCODE -ne 0) { throw "Selected immutable application images are not loaded. Verify and load the promoted release image archives first." }
$url = "http://127.0.0.1:$port/"
$wasReady = $false
if (-not $NoOpen) {
    try {
        Invoke-WebRequest -Uri $url -TimeoutSec 1 -UseBasicParsing *> $null
        $wasReady = $true
    }
    catch {
        $wasReady = $false
    }
}

Write-Host "Starting $appName instance $project in $Mode mode."
Write-Host "Starting the selected immutable application artifacts; pinned dependency images may download on first use."
& docker @upArgs
if ($LASTEXITCODE -ne 0) {
    throw "$appName could not be started. Review the Docker output above."
}
foreach ($healthService in $healthServices) {
    $serviceName = $healthService[0]
    $displayName = $healthService[1]
    $containerId = (& docker @composeFiles ps -q $serviceName | Select-Object -First 1)
    if (-not $containerId) { throw "$displayName did not start. Review the Docker Compose output above." }
    $containerName = (& docker inspect --format "{{.Name}}" $containerId 2>$null).TrimStart("/")
    $health = ""
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        $health = (& docker inspect --format "{{.State.Health.Status}}" $containerId 2>$null)
        if ($health -eq "healthy") { break }
        if ($health -eq "unhealthy") { throw "$displayName failed its container health check. Run 'docker logs $containerName' for details." }
        Start-Sleep -Seconds 1
    }
    if ($health -ne "healthy") { throw "$displayName did not become ready within 60 seconds after startup. Run 'docker logs $containerName' for details." }
}

Write-Host ""
if ($appService -eq "schemer") {
    Write-Host "Schemer is ready at $url"
    Write-Host "Schemii companion: http://127.0.0.1:$($env:SCHEMII_HOST_PORT)/"
}
else { Write-Host "Schemii is ready at $url" }
Write-Host "Mode: $Mode"
Write-Host "Instance: $project"
Write-Host "Saved data remains in Docker named volumes."

if (-not $NoOpen -and -not $wasReady) {
    Start-Process $url
}
