[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
if ($npmCommand) {
    $npmExecutable = $npmCommand.Source
} else {
    $portableNode = Join-Path $projectRoot '.tools\node\node.exe'
    if (-not (Test-Path -LiteralPath $portableNode)) {
        throw 'Node.js 22.12+ is required. Add npm.cmd to PATH or install portable Node under .tools\node.'
    }
    $env:PATH = (Split-Path -Parent $portableNode) + ';' + $env:PATH
    $npmExecutable = Join-Path $projectRoot '.tools\node\npm.cmd'
}
Set-Location -LiteralPath $projectRoot
& $npmExecutable --prefix frontend run dev
