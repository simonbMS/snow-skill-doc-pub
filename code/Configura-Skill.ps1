# Genera una copia di SKILL-template.md con i placeholder sostituiti dai valori inseriti dall'utente.

$templatePath = Join-Path $PSScriptRoot 'SKILL-template.md'
$outputPath = Join-Path $PSScriptRoot 'SKILL.md'
$valuesPath = Join-Path $PSScriptRoot 'Configura-Skill.values.json'

$placeholders = [ordered]@{
    'AGENT_MANAGED_IDENTITY_CLIENT_ID' = "Client ID della User-Assigned Managed Identity usata dall'Azure SRE Agent per autenticarsi ad Azure Key Vault. Formato: GUID."
    'SNOW_INSTANCE_NAME'               = "Nome dell'istanza ServiceNow, senza protocollo e senza il suffisso '.service-now.com'. Esempio: postecertif (certificazione), postecomprod (produzione)."
    'KEYVAULT_NAME'                    = "Nome della risorsa Azure Key Vault che contiene le credenziali ServiceNow (solo il nome, non l'URL completo)."
    'SOURCE_SYSTEM'                    = "Identificativo del sistema sorgente inviato nel campo custom ServiceNow 'u_source_system'. Esempio: AINOISRE<SOM>."
    'SOURCE_QUEUE'                     = "Gruppo ServiceNow dal quale e' consentita la riassegnazione (deve corrispondere esattamente al nome in ServiceNow). Esempio: AINOI_<SOM>."
    'DESTINATION_QUEUE'                = "Gruppo ServiceNow destinatario della riassegnazione (deve corrispondere esattamente al nome in ServiceNow)."
}

if (-not (Test-Path -LiteralPath $templatePath -PathType Leaf)) {
    throw "File non trovato: $templatePath"
}

# Carica i valori salvati in precedenza, se presenti.
$savedValues = @{}
if (Test-Path -LiteralPath $valuesPath -PathType Leaf) {
    $json = Get-Content -LiteralPath $valuesPath -Raw | ConvertFrom-Json
    foreach ($property in $json.PSObject.Properties) {
        $savedValues[$property.Name] = $property.Value
    }
}

$values = [ordered]@{}
foreach ($key in $placeholders.Keys) {
    if ($savedValues.ContainsKey($key) -and -not [string]::IsNullOrWhiteSpace($savedValues[$key])) {
        $values[$key] = $savedValues[$key]
        continue
    }
    Write-Host ""
    Write-Host "***********************"
    Write-Host $placeholders[$key] -ForegroundColor Green
    Write-Host "***********************"
    $value = ''
    while ([string]::IsNullOrWhiteSpace($value)) {
        Write-Host "Inserisci il valore per {{$key}}: " -ForegroundColor Cyan -noNewline
        $value = Read-Host
    }
    $values[$key] = $value
}

$values | ConvertTo-Json | Set-Content -LiteralPath $valuesPath

Copy-Item -LiteralPath $templatePath -Destination $outputPath -Force

$content = Get-Content -LiteralPath $outputPath -Raw
foreach ($key in $values.Keys) {
    $content = $content.Replace("{{$key}}", $values[$key])
}
Set-Content -LiteralPath $outputPath -Value $content -NoNewline

Write-Host ""
Write-Host "Riepilogo valori inseriti:" -ForegroundColor Cyan
$values.GetEnumerator() | ForEach-Object { [pscustomobject]@{ Placeholder = "{{$($_.Key)}}"; Valore = $_.Value } } | Format-Table -AutoSize

Write-Host ""
Write-Host "File generato: $outputPath" -ForegroundColor Green
