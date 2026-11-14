# Run a DAX query file against the model open in Power BI Desktop and print the rows as JSON.
# Read-only: it only executes EVALUATE queries. Used to test measures (see documentation/07).
#
#   powershell -File powerbi\tools\run_dax.ps1 -Port 62355 -QueryFile query.dax
#
# The port is the local Analysis Services port of the open Desktop instance
# (the powerbi-modeling MCP "ListLocalInstances" operation shows it).
param(
    [Parameter(Mandatory = $true)][int]$Port,
    [Parameter(Mandatory = $true)][string]$QueryFile
)
$ErrorActionPreference = 'Stop'
$desktop = Get-Process PBIDesktop -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $desktop) { throw 'Power BI Desktop is not running.' }
$dll = Join-Path (Split-Path $desktop.Path) 'Microsoft.PowerBI.AdomdClient.dll'
Add-Type -Path $dll
$conn = New-Object Microsoft.AnalysisServices.AdomdClient.AdomdConnection("Data Source=localhost:$Port")
$conn.Open()
try {
    $cmd = $conn.CreateCommand()
    $cmd.CommandText = [System.IO.File]::ReadAllText((Resolve-Path $QueryFile), [System.Text.Encoding]::UTF8)
    $reader = $cmd.ExecuteReader()
    $rows = @()
    while ($reader.Read()) {
        $row = [ordered]@{}
        for ($i = 0; $i -lt $reader.FieldCount; $i++) { $row[$reader.GetName($i)] = $reader.GetValue($i) }
        $rows += [pscustomobject]$row
    }
    $reader.Close()
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    ConvertTo-Json -InputObject @($rows) -Depth 4
}
finally { $conn.Close() }
