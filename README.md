# Monitor de preços — PS5 Digital

Lê 2× por dia (9h e 21h, horário de Brasília) o preço à vista e o parcelado de dois modelos
nas lojas Kabum, Amazon, Magazine Luiza e Carrefour. Grava o histórico em `data/prices.csv`
e avisa no Telegram quando:

- o preço **cai** em relação à última leitura;
- bate o **menor preço já registrado** naquela loja;
- fica **abaixo do valor-alvo** definido em `config.toml`;
- uma loja **falha 3 vezes seguidas** (bloqueio ou mudança no site). Nesse caso o aviso já sugere uma alternativa.

Página com gráfico: `docs/index.html` (publicada pelo GitHub Pages).

## Quais anúncios contam

Só valem ofertas **novas**, vendidas pela conta oficial de uma das 4 lojas (KaBuM!, Amazon,
Magalu, Carrefour), mesmo que dentro de outro marketplace. Qualquer outro vendedor é ignorado,
e também títulos com leitor de disco, usado, reembalado, Pro, 1 TB, acessórios etc.

| Loja | Como lê | Observação |
|---|---|---|
| Kabum | requisição simples | – |
| Carrefour | requisição simples | Console sem jogos: busca no site a cada execução (hoje só há marketplace) |
| Amazon | navegador sem janela (Playwright) | Lê "Todas as ofertas" e escolhe a menor de Amazon, KaBuM! ou Magalu |
| Magazine Luiza | Playwright → **plano B: Buscapé** | O Magalu bloqueia leitura automática (403). Pelo Buscapé o vendedor final **não** é confirmado, e isso aparece no aviso e na página |

O monitor não tenta passar por CAPTCHA nem por bloqueio: registra a falha e te avisa.

## Configuração (uma vez só)

### 1. Criar o bot do Telegram (~3 min)
1. No Telegram, abra o **@BotFather** → `/newbot` → escolha um nome. Ele te dá um **token** (`123456:ABC...`).
2. Abra o seu bot novo e mande qualquer mensagem (ex.: "oi").
3. No navegador, abra `https://api.telegram.org/bot<SEU_TOKEN>/getUpdates` e copie o número em `"chat":{"id": ...}`. Esse é o seu **chat id**.

### 2. Subir para o GitHub
1. Crie um repositório **público** vazio, por exemplo `ps5-monitor`.
2. Nesta pasta:
   ```bash
   git init -b main && git add . && git commit -m "Monitor de preços PS5"
   git remote add origin https://github.com/<seu-usuario>/ps5-monitor.git
   git push -u origin main
   ```

### 3. Guardar os segredos
No repositório: **Settings → Secrets and variables → Actions → New repository secret**
- `TELEGRAM_BOT_TOKEN` = o token do BotFather
- `TELEGRAM_CHAT_ID` = o seu chat id

### 4. Ativar o agendamento
1. Aba **Actions** → se aparecer, clique em *"I understand my workflows, go ahead and enable them"*.
2. Abra **Monitor de preços PS5** → **Run workflow** para testar na hora.
3. Confira o resumo da execução (tabela por loja) e se chegou mensagem no Telegram. Na primeira execução só chega aviso se algum preço já estiver abaixo do alvo.

Depois disso ele roda sozinho às 9h e às 21h. O GitHub pode atrasar alguns minutos.

### 5. Página com gráfico (GitHub Pages)
**Settings → Pages → Source: Deploy from a branch → Branch `main`, pasta `/docs` → Save.**
Em ~1 min a página fica em `https://<seu-usuario>.github.io/ps5-monitor/`.

## Ajustes do dia a dia
- **Valor-alvo:** edite `valor_alvo` em `config.toml` (direto no GitHub, no ícone de lápis).
- **Trocar ou adicionar anúncio:** edite a lista `urls` da loja em `config.toml`.
- **Rodar no seu computador:**
  ```bash
  python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/python -m playwright install chromium
  .venv/bin/python -m monitor --dry-run          # só mostra, não grava nem avisa
  .venv/bin/python -m monitor --loja=kabum --dry-run
  TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... .venv/bin/python -m monitor.notify   # testa o Telegram
  ```

## Se uma loja começar a falhar
Você recebe "⚠️ <loja> falhando" depois de 3 falhas seguidas (~1,5 dia). Caminhos possíveis:
- **Amazon bloqueando o GitHub:** trocar a leitura para o Buscapé/Zoom (como já é feito com o Magalu)
  ou rodar o monitor no seu PC, onde o IP residencial costuma passar.
- **Site mudou de formato:** o resumo da execução em *Actions* mostra o erro exato.
