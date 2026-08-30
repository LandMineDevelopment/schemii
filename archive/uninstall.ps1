param(
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
$runningOnWindows = $env:OS -eq "Windows_NT"
$repoDirectory = (Resolve-Path $PSScriptRoot).Path
$homeDirectory = [System.IO.Path]::GetFullPath($HOME)
if (
    $repoDirectory -eq [System.IO.Path]::GetPathRoot($repoDirectory) -or
    $repoDirectory -eq $homeDirectory -or
    -not (Test-Path (Join-Path $repoDirectory "compose.yaml") -PathType Leaf) -or
    -not (Test-Path (Join-Path $repoDirectory "start.ps1") -PathType Leaf) -or
    -not (Test-Path (Join-Path $repoDirectory "src/schemii") -PathType Container)
) {
    throw "Refusing to remove $repoDirectory because it is not a recognized Schemii repository."
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker was not found. Install or restore Docker first so Schemii containers and volumes can be removed safely."
}
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker is unavailable or access was denied. Start Docker and run 'docker info' before uninstalling Schemii."
}

$credentialRoot = if ($env:SCHEMII_CREDENTIAL_ROOT) { $env:SCHEMII_CREDENTIAL_ROOT } elseif ($runningOnWindows) { Join-Path $env:LOCALAPPDATA "Schemii\credentials" } else { Join-Path $HOME ".local/share/schemii/credentials" }
if (-not [System.IO.Path]::IsPathRooted($credentialRoot) -or ($env:SCHEMII_CREDENTIAL_DIR -and -not [System.IO.Path]::IsPathRooted($env:SCHEMII_CREDENTIAL_DIR))) {
    throw "SCHEMII_CREDENTIAL_ROOT and SCHEMII_CREDENTIAL_DIR must be absolute paths."
}
$volumeSuffixes = @(
    "schemii-config", "schemii-schemas", "schemii-postgres", "schemii-metadata-postgres",
    "schemii-opencode-data", "schemii-opencode-config", "schemii-opencode-state", "schemii-opencode-cache",
    "schemer-dashboards", "schemii-recovery", "host-postgres-socket"
)
$approvedProjects = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
$orphanVolumeCounts = @{}
$orphanVolumesSeen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
$ownedImages = @{}
$legacyValidatedManifestBodies = @{}
$legacyValidatedIdentityBodies = @{}
$allContainerIds = @(docker ps -aq | Where-Object { $_ })
$allVolumes = @(docker volume ls -q | Where-Object { $_ })

function Test-ProjectName([string]$Project) { return $Project -cmatch '^[a-z0-9][a-z0-9_-]*$' }
function Test-RepositoryWorkingDirectory([string]$WorkingDirectory) {
    if (-not $WorkingDirectory) { return $false }
    try { $resolved = [System.IO.Path]::GetFullPath($WorkingDirectory) }
    catch { return $false }
    $comparison = if ($runningOnWindows) { [System.StringComparison]::OrdinalIgnoreCase } else { [System.StringComparison]::Ordinal }
    return [string]::Equals($resolved, $repoDirectory, $comparison)
}
function Get-CredentialDirectory([string]$Project) {
    if ($env:SCHEMII_CREDENTIAL_DIR) { return $env:SCHEMII_CREDENTIAL_DIR }
    return Join-Path $credentialRoot $Project
}
function Test-CredentialMarker([string]$Project) {
    $instanceFile = Join-Path (Get-CredentialDirectory $Project) "instance"
    if (-not (Test-Path $instanceFile -PathType Leaf)) { return $false }
    $raw = [System.IO.File]::ReadAllText($instanceFile, [System.Text.Encoding]::UTF8)
    return $raw -cmatch ("\A" + [regex]::Escape($Project) + "(?:\r?\n)?\z")
}
function Get-LegacyVolumeIdentity([string]$Project, [string]$LogicalName) {
    $volume = "${Project}_$LogicalName"
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
function Get-LegacyManifestBody([string]$Project, [string]$LogicalName, $Identity) {
    return (@(
        "format=schemii-legacy-volume-adoption-v1",
        "project=$Project",
        "repository=$repoDirectory",
        "logical=$LogicalName",
        "volume=$($Identity.Name)",
        "created-at=$($Identity.CreatedAt)",
        "driver=$($Identity.Driver)",
        "mountpoint=$($Identity.Mountpoint)",
        "scope=$($Identity.Scope)"
    ) -join "`n") + "`n"
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
function Test-LegacyAdoptionSet([string]$Project) {
    if ($Project -cne "schemii" -or -not (Test-CredentialMarker $Project)) { return $false }
    $adoptionDirectory = Join-Path (Get-CredentialDirectory $Project) "legacy-volume-adoptions.v1"
    if (-not (Test-OwnerOnlyPath $adoptionDirectory $true)) { return $false }
    $items = @(Get-ChildItem -LiteralPath $adoptionDirectory -Force)
    if ($items.Count -ne 2) { return $false }
    $expectedNames = @("schemii-config.manifest", "schemii-schemas.manifest")
    foreach ($item in $items) {
        if ($item.Name -cnotin $expectedNames -or -not (Test-OwnerOnlyPath $item.FullName $false)) { return $false }
    }
    foreach ($logicalName in @("schemii-config", "schemii-schemas")) {
        $identity = Get-LegacyVolumeIdentity $Project $logicalName
        if (-not $identity) { return $false }
        $manifest = Join-Path $adoptionDirectory "$logicalName.manifest"
        if (-not (Test-OwnerOnlyPath $manifest $false)) { return $false }
        try { $actual = [System.IO.File]::ReadAllText($manifest, [System.Text.Encoding]::UTF8) }
        catch { return $false }
        $expected = Get-LegacyManifestBody $Project $logicalName $identity
        if ($actual -cne $expected) { return $false }
        $script:legacyValidatedManifestBodies[$logicalName] = $actual
        $script:legacyValidatedIdentityBodies[$logicalName] = $expected
    }
    return $true
}
function Test-LegacyResourceStillAttested([string]$Project, [string]$LogicalName) {
    if ($Project -cne "schemii" -or $LogicalName -cnotin @("schemii-config", "schemii-schemas") -or -not (Test-CredentialMarker $Project)) { return $false }
    $adoptionDirectory = Join-Path (Get-CredentialDirectory $Project) "legacy-volume-adoptions.v1"
    if (-not (Test-OwnerOnlyPath $adoptionDirectory $true)) { return $false }
    $items = @(Get-ChildItem -LiteralPath $adoptionDirectory -Force)
    if ($items.Count -ne 2) { return $false }
    foreach ($logical in @("schemii-config", "schemii-schemas")) {
        $manifest = Join-Path $adoptionDirectory "$logical.manifest"
        if (-not (Test-OwnerOnlyPath $manifest $false) -or -not $script:legacyValidatedManifestBodies.ContainsKey($logical)) { return $false }
        try { $actual = [System.IO.File]::ReadAllText($manifest, [System.Text.Encoding]::UTF8) }
        catch { return $false }
        if ($actual -cne $script:legacyValidatedManifestBodies[$logical]) { return $false }
    }
    $identity = Get-LegacyVolumeIdentity $Project $LogicalName
    if (-not $identity -or -not $script:legacyValidatedIdentityBodies.ContainsKey($LogicalName)) { return $false }
    return (Get-LegacyManifestBody $Project $LogicalName $identity) -ceq $script:legacyValidatedIdentityBodies[$LogicalName]
}
function Invoke-DockerRemoval([string[]]$DockerArguments) {
    & docker @DockerArguments
    if ($LASTEXITCODE -ne 0) { throw "Docker removal failed: docker $($DockerArguments -join ' ')" }
}
foreach ($containerId in $allContainerIds) {
    $labels = (& docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}|{{ index .Config.Labels "com.docker.compose.service" }}|{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' $containerId 2>$null) -split '\|', 3
    if ($labels.Count -eq 3 -and (Test-ProjectName $labels[0]) -and $labels[1] -cin @("schemii", "schemer") -and (Test-RepositoryWorkingDirectory $labels[2])) {
        [void]$approvedProjects.Add($labels[0])
    }
}
foreach ($volume in $allVolumes) {
    $labels = (& docker volume inspect --format '{{ index .Labels "com.docker.compose.project" }}|{{ index .Labels "com.docker.compose.volume" }}' $volume 2>$null) -split '\|', 2
    if ($labels.Count -ne 2) { continue }
    $project, $logicalName = $labels
    $key = "${project}:$logicalName"
    if ((Test-ProjectName $project) -and $volumeSuffixes -ccontains $logicalName -and $volume -ceq "${project}_$logicalName" -and $orphanVolumesSeen.Add($key)) {
        $orphanVolumeCounts[$project] = 1 + [int]$orphanVolumeCounts[$project]
    }
}
foreach ($project in @($orphanVolumeCounts.Keys)) {
    if ($orphanVolumeCounts[$project] -ge 2 -or (Test-CredentialMarker $project)) { [void]$approvedProjects.Add($project) }
}
if (Test-LegacyAdoptionSet "schemii") { [void]$approvedProjects.Add("schemii") }

Write-Host "This permanently removes:"
Write-Host "  - every verified Schemii Docker container and network"
Write-Host "  - all verified Schemii designs, profiles, passwords, migration history, PostgreSQL data, AI credentials, and chats"
Write-Host "  - safely attributable project-scoped Schemii images"
Write-Host "  - each verified instance credential directory"
Write-Host "  - repository: $repoDirectory"
if ($approvedProjects.Count) {
    Write-Host "Detected Schemii instances:"
    $approvedProjects | Sort-Object | ForEach-Object { Write-Host "  - $_" }
}
else { Write-Host "Detected Schemii instances: none" }
Write-Host "Unrelated or ambiguously owned Docker projects, images, and volumes are not removed."

if (-not $Yes) {
    $confirmation = Read-Host "Type UNINSTALL to continue"
    if ($confirmation -cne "UNINSTALL") {
        Write-Host "Uninstall cancelled. Nothing was removed."
        exit 1
    }
}

foreach ($project in @($approvedProjects | Sort-Object)) {
    $legacyAttested = Test-LegacyAdoptionSet $project
    $ownedContainerIds = @()
    foreach ($containerId in $allContainerIds) {
        $details = (& docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}|{{ index .Config.Labels "com.docker.compose.service" }}|{{ index .Config.Labels "com.docker.compose.project.working_dir" }}|{{.Image}}|{{.Config.Image}}' $containerId 2>$null) -split '\|', 5
        if ($details.Count -ne 5 -or $details[0] -cne $project -or -not (Test-RepositoryWorkingDirectory $details[2])) { continue }
        $ownedContainerIds += $containerId
        if ($details[4] -cin @("schemii:$project", "schemii-metadata-postgres:$project", "schemii-opencode:1.18.15-$project")) {
            $ownedImages[$details[4]] = $details[3]
        }
    }
    if ($ownedContainerIds.Count) { Invoke-DockerRemoval (@("rm", "-f") + $ownedContainerIds) }

    foreach ($networkId in @(docker network ls -q --filter "label=com.docker.compose.project=$project" | Where-Object { $_ })) {
        $labels = (& docker network inspect --format '{{ index .Labels "com.docker.compose.project" }}|{{ index .Labels "com.docker.compose.network" }}|{{.Name}}' $networkId 2>$null) -split '\|', 3
        if ($labels.Count -eq 3 -and $labels[0] -ceq $project -and $labels[1] -cin @("default", "schemii-ingress", "schemer-ingress", "schemii-loopback", "schemer-loopback") -and $labels[2] -ceq "${project}_$($labels[1])") {
            Invoke-DockerRemoval @("network", "rm", $networkId)
        }
    }
    foreach ($volume in $allVolumes) {
        $labels = (& docker volume inspect --format '{{ index .Labels "com.docker.compose.project" }}|{{ index .Labels "com.docker.compose.volume" }}|{{.Name}}' $volume 2>$null) -split '\|', 3
        if ($labels.Count -eq 3 -and $labels[0] -ceq $project -and $volumeSuffixes -ccontains $labels[1] -and $labels[2] -ceq "${project}_$($labels[1])" -and $volume -ceq $labels[2]) {
            Invoke-DockerRemoval @("volume", "rm", $volume)
        }
        elseif ($legacyAttested -and $project -ceq "schemii" -and $volume -cin @("schemii_schemii-config", "schemii_schemii-schemas")) {
            $logicalName = $volume.Substring("schemii_".Length)
            if (Test-LegacyResourceStillAttested $project $logicalName) {
                Invoke-DockerRemoval @("volume", "rm", $volume)
            }
            else { throw "Legacy adoption evidence or volume identity changed during uninstall; remaining data and credentials were preserved." }
        }
    }
}

foreach ($imageReference in @($ownedImages.Keys)) {
    $imageId = $ownedImages[$imageReference]
    $currentId = & docker image inspect --format '{{.Id}}' $imageReference 2>$null
    $inspectSucceeded = $LASTEXITCODE -eq 0
    $imageUsers = @(docker ps -aq --filter "ancestor=$imageId" | Where-Object { $_ })
    $referenceCheckSucceeded = $LASTEXITCODE -eq 0
    if ($imageId -and $inspectSucceeded -and $referenceCheckSucceeded -and $currentId -ceq $imageId -and -not $imageUsers.Count) {
        Invoke-DockerRemoval @("image", "rm", $imageReference)
    }
}

foreach ($project in @($approvedProjects | Sort-Object)) {
    if (Test-CredentialMarker $project) { Remove-Item -LiteralPath (Get-CredentialDirectory $project) -Recurse -Force }
}
$repoParent = Split-Path -Parent $repoDirectory
$repoName = Split-Path -Leaf $repoDirectory
Write-Host "Verified Docker resources removed. Removing repository $repoDirectory"
Set-Location $repoParent
Remove-Item -LiteralPath (Join-Path $repoParent $repoName) -Recurse -Force
Write-Host "Schemii has been uninstalled."
