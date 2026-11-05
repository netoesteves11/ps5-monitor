#!/bin/zsh
# Lê só o Carrefour (que bloqueia o GitHub) a partir deste Mac e envia o resultado ao repositório.
# Chamado pelo launchd às 9h15 e 21h15 (ver mac/instalar.sh). Se o Mac estiver dormindo,
# roda assim que ele acordar.
set -u
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
REPO="${0:A:h:h}"
cd "$REPO" || exit 1
echo "=== $(date '+%d/%m/%Y %H:%M') ==="

# Segredos do Telegram ficam fora do repositório.
[ -f "$HOME/.config/ps5-monitor/env" ] && source "$HOME/.config/ps5-monitor/env"
export TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID

git pull --rebase --quiet || { echo "git pull falhou"; exit 1; }
.venv/bin/python -m monitor --loja=carrefour
git add data/ docs/data.json
if git diff --cached --quiet; then
  echo "Nada novo para gravar."
  exit 0
fi
git commit --quiet -m "Leitura Carrefour pelo Mac $(date '+%d/%m/%Y %H:%M')"
for i in 1 2 3; do
  git pull --rebase --quiet && git push --quiet && { echo "Enviado."; exit 0; }
  sleep 30
done
echo "Não consegui enviar ao GitHub; tento de novo na próxima execução."
exit 1
