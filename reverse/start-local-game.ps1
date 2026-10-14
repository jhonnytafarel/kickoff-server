param()
$ErrorActionPreference='Stop'
$taskGameDir=Join-Path $PSScriptRoot 'local-client/Bin/Game'
$taskGamePath=Join-Path $taskGameDir 'Game.exe'
if (-not (Test-Path -LiteralPath $taskGamePath)) { throw 'Prepare a copia: python reverse/tools/prepare_client.py --source CAMINHO_DO_JOGO' }
if (-not (Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort 19999 -State Listen -ErrorAction SilentlyContinue)) { throw 'Inicie o servidor primeiro com --emulate-front.' }
# Fictitious localtest identity with an all-zero hash; no real credentials.
Start-Process -FilePath $taskGamePath -WorkingDirectory $taskGameDir -ArgumentList "-AuthenticationCode='bG9jYWx0ZXN0IDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAw'"
