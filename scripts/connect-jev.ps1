$ErrorActionPreference = 'Stop'
$secret = Read-Host 'Paste your TypeSafe API key (hidden)' -AsSecureString
$credential = [System.Net.NetworkCredential]::new('', $secret)
$value = $credential.Password.Trim()
if ([string]::IsNullOrWhiteSpace($value)) { throw 'No key entered; nothing changed.' }
[Environment]::SetEnvironmentVariable('TYPESAFE_API_KEY', $value, 'User')
$env:TYPESAFE_API_KEY = $value
$value = $null
$credential = $null
$secret.Dispose()
Write-Host 'TypeSafe key saved in your Windows user environment. Restart the local server when no scan or analysis is running.'
Write-Host 'The next scan will classify descriptions with Jev. OpenAI billing is separate.'
