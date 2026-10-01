$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$scope = [System.Windows.Automation.TreeScope]::Descendants
$idProperty = [System.Windows.Automation.AutomationElement]::AutomationIdProperty
$settingsWindow = $null
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    $settingsProcess = Get-Process -Name WsaSettings -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($settingsProcess) {
        $processCondition = [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ProcessIdProperty, [int]$settingsProcess.Id)
        $settingsWindow = [System.Windows.Automation.AutomationElement]::RootElement.FindFirst($scope, $processCondition)
        if ($settingsWindow) { break }
    }
    Start-Sleep -Milliseconds 200
}
if (-not $settingsWindow) { throw 'WSA settings window not available' }
$systemTab = $settingsWindow.FindFirst($scope, [System.Windows.Automation.PropertyCondition]::new($idProperty, 'LeftPaneSystem'))
if (-not $systemTab) { throw 'WSA system tab not available' }
$systemTab.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Select()
$shutdownButton = $null
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    $shutdownButton = $settingsWindow.FindFirst($scope, [System.Windows.Automation.PropertyCondition]::new($idProperty, 'ShutdownAndroidButton'))
    if ($shutdownButton) { break }
    Start-Sleep -Milliseconds 150
}
if (-not $shutdownButton) { throw 'WSA shutdown button not available' }
$shutdownButton.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
