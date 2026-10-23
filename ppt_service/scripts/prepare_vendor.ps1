param(
    [Parameter(Mandatory = $true)][string]$PptMasterRepository
)
$ErrorActionPreference = 'Stop'
$commit = 'c350bc31e6d43b7bcf5234183d650ba8f9f4c9d7'
$serviceRoot = Split-Path -Parent $PSScriptRoot
$vendorDirectory = Join-Path $serviceRoot '.vendors'
$archive = Join-Path $vendorDirectory 'ppt-master.tar'
$repository = (Resolve-Path -LiteralPath $PptMasterRepository).Path
& git -C $repository cat-file -e "$commit`^{commit}"
if ($LASTEXITCODE -ne 0) { throw 'Fixed PPT Master commit is unavailable locally; no network clone is attempted.' }
if (Test-Path -LiteralPath $archive) { throw "Archive already exists; refusing to overwrite: $archive" }
New-Item -ItemType Directory -Path $vendorDirectory -Force | Out-Null
# Git writes tar bytes directly, avoiding PowerShell native-output transcoding.
& git -C $repository archive --format=tar "--output=$archive" $commit
if ($LASTEXITCODE -ne 0) { throw 'git archive failed.' }
$hash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
[pscustomobject]@{ commit = $commit; archive = $archive; sha256 = $hash; bytes = (Get-Item -LiteralPath $archive).Length } | ConvertTo-Json
