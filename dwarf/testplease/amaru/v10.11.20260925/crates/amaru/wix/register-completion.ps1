[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $InstallRoot
)

$completionPath = Join-Path $InstallRoot 'share\powershell\completions\amaru.ps1'
$startMarker = '# >>> amaru completion >>>'
$endMarker = '# <<< amaru completion <<<'
$escapedCompletionPath = $completionPath.Replace("'", "''")
$snippet = @(
    $startMarker,
    "if (Test-Path -LiteralPath '$escapedCompletionPath') {",
    "  . '$escapedCompletionPath'",
    "}",
    $endMarker,
    ''
) -join [Environment]::NewLine
$pattern = "(?ms)^$([regex]::Escape($startMarker))\r?\n.*?^$([regex]::Escape($endMarker))\r?\n?"
$profilePaths = New-Object System.Collections.Generic.List[string]
$documentsPath = [Environment]::GetFolderPath('MyDocuments')
$profilePaths.Add((Join-Path $documentsPath 'WindowsPowerShell\profile.ps1'))
$profilePaths.Add((Join-Path $documentsPath 'PowerShell\profile.ps1'))

$profilePaths |
    Select-Object -Unique |
    ForEach-Object {
        $profilePath = $_
        $parentPath = Split-Path -Parent $profilePath
        if (-not (Test-Path -LiteralPath $parentPath)) {
            New-Item -Path $parentPath -ItemType Directory -Force | Out-Null
        }

        $currentContent = if (Test-Path -LiteralPath $profilePath) {
            Get-Content -LiteralPath $profilePath -Raw
        } else {
            ''
        }

        $cleanedContent = [regex]::Replace($currentContent, $pattern, '').TrimEnd()
        $newContent = if ([string]::IsNullOrWhiteSpace($cleanedContent)) {
            $snippet
        } else {
            $cleanedContent + [Environment]::NewLine + [Environment]::NewLine + $snippet
        }

        Set-Content -LiteralPath $profilePath -Value $newContent -Encoding UTF8
    }
