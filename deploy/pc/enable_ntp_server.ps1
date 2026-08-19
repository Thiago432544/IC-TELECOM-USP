# Ativa o servidor NTP nativo do Windows (w32time) para servir as Rasps.
# Rodar como Administrador no PC do SPA.
$ErrorActionPreference = "Stop"

# O PC sincroniza da internet (interface 192.168.124.29) e serve na 192.168.11.101
Set-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\TimeProviders\NtpServer" -Name Enabled -Value 1
Set-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\Config" -Name AnnounceFlags -Value 5
w32tm /config /manualpeerlist:"pool.ntp.org,0x9" /syncfromflags:manual /update

Set-Service w32time -StartupType Automatic
Restart-Service w32time

New-NetFirewallRule -DisplayName "NTP server (Rasps Porto)" -Direction Inbound `
  -Protocol UDP -LocalPort 123 -Action Allow -ErrorAction SilentlyContinue

w32tm /query /status
Write-Host "OK. Teste de uma Rasp:  ntpdate -q 192.168.10.101  (ou aguarde o timesyncd)"
