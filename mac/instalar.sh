#!/bin/zsh
# Instala o agendamento do Carrefour neste Mac (9h15 e 21h15).
# Uso:  zsh mac/instalar.sh            (instalar / atualizar)
#       zsh mac/instalar.sh remover    (desinstalar)
set -e
REPO="${0:A:h:h}"
LABEL="com.ps5monitor.carrefour"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ "${1:-}" = "remover" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Agendamento removido."
  exit 0
fi

chmod +x "$REPO/mac/rodar-carrefour.sh"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/zsh</string><string>$REPO/mac/rodar-carrefour.sh</string></array>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Hour</key><integer>9</integer><key>Minute</key><integer>15</integer></dict>
    <dict><key>Hour</key><integer>21</integer><key>Minute</key><integer>15</integer></dict>
  </array>
  <key>StandardOutPath</key><string>$REPO/mac/carrefour.log</string>
  <key>StandardErrorPath</key><string>$REPO/mac/carrefour.log</string>
</dict>
</plist>
PL
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Agendado: Carrefour às 9h15 e 21h15. Log em $REPO/mac/carrefour.log"
echo "Rodar agora:  launchctl kickstart gui/$(id -u)/$LABEL"
