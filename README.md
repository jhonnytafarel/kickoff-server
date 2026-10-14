# KickOff — servidor comunitário experimental

Servidor em **Python + asyncio + SQLite** reconstruído para estudar e recuperar
o funcionamento do cliente **KickOff Global 1.0.7 (00321)**. O projeto já permite
testar criação de personagens, lojas, habilidades, treino, salas e partidas
locais. Esta publicação abre o código para a comunidade continuar o trabalho.

**É um ambiente local de desenvolvimento.** O login ainda usa uma conta fictícia
única. PeerTeste e os bots não substituem autenticação/multiplayer com pessoas
reais. O host antigo ainda informa placar e ações. Não abra as portas deste MVP
na internet como se ele fosse um serviço público pronto.

O repositório funciona sem instalar o cliente para executar o servidor e os
testes. Para jogar com gráficos, forneça sua instalação compatível do jogo.
Executáveis, texturas, modelos, músicas, mapas e arquivos XPK não são incluídos.
Veja [NOTICE.md](NOTICE.md) para o escopo da publicação.

## 1. O que já está implementado

| Área | Implementado | Limite atual |
|---|---|---|
| Login | Front, versão, sessão local e entrada | Conta `localtest` única; sem senhas reais |
| Personagens | Até 3 por conta, criação, seleção, exclusão, aparência inicial | Nome: 4–14 letras ASCII; sem números/acentos |
| Introdução | Primeiro avatar recebe introdução; conclusão por personagem | Ao reconectar, a cena pendente recomeça |
| Item Shop | Compra, seleção, atributos permitidos, débito e inventário | Validade sandbox em 2037; sem renovação real |
| Skill Shop | Compra, propriedade, níveis e salvamento | Até 5 skills ativas; efeitos dependem do exercício/ação |
| Aparência | Equipar, remover e restaurar peças iniciais | Compatibilidade de corpo/roupas é preservada |
| Economia | Carteira, histórico de compras e recompensas em SQLite | Ajuste local; ainda não é economia online autoritativa |
| Treino | Exercícios e progresso por personagem | Practice não paga XP/PG por padrão |
| Missões | Estado, aceite, abandono e recompensa PG | Conclusão ainda usa gancho GM; item-recompensa não reconstruído |
| Jogo livre | Lobby, sala, mapa, bola, times, pronto, vagas e saída | Common exige 2–3 jogadores prontos por time |
| Partida | Loading, goleiros, relay, resultado, retorno e replay protegido | Placar e ações ainda informados pelo cliente host |
| Bots | BotTreino e três peers separados com IA experimental | Sem contas persistentes; comportamento/colisões incompletos |
| Amigos/Buddy | Bootstrap e lista vazia compatível | Chat, clube e amizades reais ainda não implementados |

As traduções, telas redesenhadas e Campo do Morro do ambiente de pesquisa são
mudanças do **cliente**; não vêm junto com esta publicação do **servidor**.

## 2. Requisitos

- Python **3.12 ou 3.13**; versão local validada: 3.12.
- Git para clonar o projeto.
- `pycryptodome`, instalado pelo arquivo `requirements.txt`, para o codec AES
  usado pelos bots e pela suíte completa.
- Windows para abrir o cliente original. O servidor/testes são Python e podem
  ser desenvolvidos também em Linux; a configuração de rede publicada é local.
- Opcional, para jogar: instalação do cliente compatível, com `Bin/` e `Data/`.

SQLite já acompanha o Python. Não precisa instalar MySQL, PostgreSQL, Node,
Unity, Rust, Docker ou um servidor de banco separado para este pacote.

## 3. Instalação no Windows

Use PowerShell. Estes comandos não alteram a política de execução da máquina.

```powershell
git clone --branch community/server-release https://github.com/jhonnytafarel/kickoff-server.git
cd kickoff-server
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Se estiver usando Python3.13, troque `py -3.12` por `py -3.13`. Nos exemplos,
usamos diretamente o Python do ambiente virtual; ativar a venv é opcional.

Em Linux:

```bash
git clone --branch community/server-release https://github.com/jhonnytafarel/kickoff-server.git
cd kickoff-server
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s reverse/tests -v
```

Nos outros comandos deste README, substitua `.venv\Scripts\python.exe` por
`.venv/bin/python` quando usar Linux.

## 4. Subir o servidor e parar

Abra um terminal na raiz do repositório e execute:

```powershell
.venv\Scripts\python.exe reverse/server/capture_server.py --emulate-front
```

Deixe o terminal aberto. O banco da conta de demonstração e as pastas de captura
são criados automaticamente. O log deve mostrar `Listening` e
`Experimental front/version/local-account login enabled.`

Para parar: **Ctrl+C nesse terminal**. Se o cliente estiver conectado, a sessão
será interrompida; feche-o e reabra depois de reiniciar o servidor.

Para usar outro banco e separar os registros de um teste:

```powershell
.venv\Scripts\python.exe reverse/server/capture_server.py --emulate-front --database reverse/server/data/meu-teste.sqlite3 --output reverse/captures/meu-teste
```

Sem `--emulate-front`, a ferramenta captura mensagens, mas não faz login nem
emula conta/personagem. Use esse modo apenas para estudar o transporte.

## 5. Preparar e abrir sua cópia do cliente

Este passo é **opcional**. O servidor e os testes não precisam dos arquivos do
jogo. Para testes visuais, indique a pasta da sua instalação existente:

```powershell
.venv\Scripts\python.exe reverse/tools/prepare_client.py --source "D:\MinhaInstalacaoKickOff"
```

A pasta fornecida precisa conter `Bin/Game/Game.exe`, `Bin/Game/StreetSoccer.cfg`
e `Data/`. O comando cria `reverse/local-client/`, copia Bin/Data, aponta a
conexão para o servidor local e usa o switch de pesquisa nativo `DisableXTrap`.
**A instalação original não é editada.** Se a cópia privada já existir, o
comando recusa sobrescrevê-la. Essa cópia fica ignorada pelo Git.

No executável original compatível, a checagem antiga de Windows pode bloquear
Windows10/11. Para essa versão específica:

```powershell
.venv\Scripts\python.exe reverse/tools/prepare_client.py --source "D:\MinhaInstalacaoKickOff" --patch-os
```

`--patch-os` verifica o SHA256 e a instrução original antes de mudar **um byte
somente na cópia**. Versões desconhecidas são recusadas antes da cópia. O
manifesto privado informa o hash e as configurações usadas. Não há patch de
roupas: a restauração de aparência é feita pelo protocolo DefaultFeatures.

Com o servidor já aberto, em outro PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File reverse/start-local-game.ps1
```

Esse launcher usa uma identidade fictícia e hash de zeros para `localtest`.
Não usa nem exige sua senha antiga. Para fechar somente essa cópia privada:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File reverse/stop-local-game.ps1
```

O cliente usa os recursos originais fornecidos por você. Diferenças de hardware,
drivers, dados do cliente ou outra versão podem exigir ajustes específicos.
O tamanho lógico1024×768 é preservado para evitar desalinhamento dos cliques.
O argumento `--windowed` do preparo mantém esse tamanho em janela; ele não
redimensiona arbitrariamente a interface antiga.

## 6. Personagem, loja e habilidades

No primeiro login, crie um personagem com **4 a 14 letras**, por exemplo
`JogadorRua`. Números e acentos não são aceitos pelo cliente legado. Há três
espaços independentes de personagem e um selecionado por conta.

Novos personagens recebem **nível1 e XP0** definidos pelo servidor. A conta nova
começa com **10.000PG e 0Cash**. PG é Game Points, a moeda de jogo. O catálogo
atual contém382 mercadorias de um item e99 skills, com481 definições de venda;
algumas não estão à venda ou exigem nível/corpo/posição específicos no cliente.

Compras debitam dentro da mesma transação que entrega o item ou skill. Falta de
saldo, seleções inválidas e atributos fora do orçamento do catálogo são
recusados. Repetir um pedido já entregue não cobra nem duplica a entrega.

Em My Skill/Minhas habilidades, coloque as habilidades e confirme no botão
Mount/Salvar. O servidor valida propriedade, níveis e máximo de cinco ativas.
**Group/Sort podem se repetir:** várias habilidades de passe compartilham1/1.
Esses campos não são posições exclusivas; só o ID da skill deve ser único.

Voltar ao padrão da roupa repõe as seis peças originais daquele personagem.
Roupas de corpo incompatível não são transformadas automaticamente em unissex.

## 7. Treino com BotTreino

Reinicie o servidor com o flag do oponente virtual:

```powershell
.venv\Scripts\python.exe reverse/server/capture_server.py --emulate-front --practice-bots
```

No jogo, entre em Jogo livre/Free Game, crie uma sala **Practice/Treino**, espere
o BotTreino aparecer no outro time e inicie. Esse bot valida a participação e
sincronização de um peer virtual; não equivale aos três peers com IA abaixo.

Os exercícios de Practice Zone têm teclas e ações próprias. Uma skill de chute
direto depende de receber um passe; possuir/equipar a skill não habilita qualquer
ação em qualquer exercício. Sair/Quit deve passar por resultado e voltar à sala.

## 8. Testar Common2×2 com três clientes separados

No terminal do servidor:

```powershell
.venv\Scripts\python.exe reverse/server/capture_server.py --emulate-front --test-client
```

Abra três terminais adicionais, todos na raiz do projeto:

```powershell
.venv\Scripts\python.exe reverse/tools/headless_client.py --peer-index 0 --ai
```

```powershell
.venv\Scripts\python.exe reverse/tools/headless_client.py --peer-index 1 --ai
```

```powershell
.venv\Scripts\python.exe reverse/tools/headless_client.py --peer-index 2 --ai
```

Abra o cliente gráfico. Em Jogo livre, crie uma sala **Common/Normal2×2 sem
senha**. Os peers procuram uma sala disponível e entram automaticamente,
equilibrando os times. Espere os três, marque Ready/Pronto e depois Start/Iniciar.
Sem jogadores suficientes dos dois lados, o servidor recusa o início.

`--peer-index` escolhe PeerTeste, PeerTeste2 ou PeerTeste3. Sem `--ai`, o peer
faz o fluxo de rede, mas não tenta movimentação/disputa. Com `--ai`, emite ações
experimentais observadas no protocolo. Pare os peers com Ctrl+C. Após destruir
a sala ou desconectar o host, reinicie os peers para outro ciclo.

São **conexões de teste separadas**, sem renderização e sem carteira própria;
esse teste não prova compatibilidade com dois clientes completos nem qualidade
online em outra rede. Os três peers não persistem personagens ou dinheiro.

## 9. XP, PG, níveis e multiplicadores

Configuração: [reverse/server/config/economy.json](reverse/server/config/economy.json).

| Parâmetro | Padrão | Uso |
|---|---:|---|
| `starting_game_points` | 10000 | Saldo de conta nova |
| `starting_cash` | 0 | Cash de conta nova |
| `legacy_game_points` / `legacy_cash` | 999999999 | Migração de antigas contas de pesquisa sem carteira |
| `enforce_level_requirements` | true | Requisitos para comprar/equipar/iniciar com skills |
| `match_base_xp` | 150 | XP base por partida elegível |
| `match_base_game_points` | 100 | PG base por partida elegível |
| `xp_multiplier` | 2 | Multiplicador de XP |
| `game_points_multiplier` | 2 | Multiplicador de PG |
| `minimum_match_seconds` | 120 | Tempo mínimo desde o início autorizado |
| `reward_practice` | false | Practice sem pagamento por padrão |

Padrão: **300XP + 200PG em Common após120s**. O tempo mínimo inclui loading;
a recompensa é de participação, sem multiplicar por gols/vitória informados
pelo host. A curva de níveis é a do catálogo do cliente. A configuração impede
recompensas que excedam os campos nativos do pacote.

Para consultar e alterar taxas com validação e backup do JSON:

```powershell
.venv\Scripts\python.exe reverse/tools/economy_admin.py
.venv\Scripts\python.exe reverse/tools/economy_admin.py --xp 1 --gold 1
.venv\Scripts\python.exe reverse/tools/economy_admin.py --xp 2 --gold 2
```

As taxas são relidas ao liquidar a partida. Saldo inicial só se aplica a contas
novas; editar o arquivo não reseta carteiras existentes. Cada partida iniciada
recebe um ID no servidor: a recompensa é paga uma vez, e o ACK de repetição
contém XP/PG zero para não reaplicar os valores no cliente.

Auditar o banco sem modificá-lo:

```powershell
.venv\Scripts\python.exe reverse/tools/audit_economy.py --database reverse/server/data/kickoff.sqlite3
```

## 10. Banco de dados e histórico das compras

Banco padrão: `reverse/server/data/kickoff.sqlite3`, criado no primeiro início
com `--emulate-front`. Não há nenhuma conta/banco de um jogador incluído no Git.

| Tabela | Conteúdo |
|---|---|
| `accounts` | Conta local e personagem selecionado |
| `characters` | Até três avatares, aparência inicial, payload e XP/nível |
| `items` | Inventário compartilhado pela conta |
| `skills` | Skills compradas e configuração por personagem |
| `wallets` | PG/Cash por conta |
| `economy_ledger` | Compras, recompensas, valores e dataUTC |
| `match_receipts` | Recibo de liquidação de cada partida |
| `character_match_history` | Partidas/vitórias/empates reportados pelo host |
| `tutorial_progress` | Exercícios concluídos por personagem |
| `character_onboarding` | Introdução e flags por personagem |
| `mission_progress` | Missão aceita/concluída/recompensa coletada |
| `environment_updates` | Configurações nativas recebidas |

Para ver as últimas compras, usando o SQLite do Python:

```powershell
.venv\Scripts\python.exe reverse/tools/purchase_history.py --limit 20
.venv\Scripts\python.exe reverse/tools/purchase_history.py --database reverse/server/data/meu-teste.sqlite3 --character 1001 --limit 20
```

Os movimentos de compra são negativos no ledger; recompensas são positivas.
A referência identifica item/seleção ou skill/personagem. O histórico começa
quando a carteira/ledger foi implementada, sem reconstruir compras antigas.
Ainda não há uma tela de histórico dentro do cliente.

Para backup com o servidor parado:

```powershell
New-Item -ItemType Directory -Force reverse/backups | Out-Null
Copy-Item -LiteralPath reverse/server/data/kickoff.sqlite3 -Destination reverse/backups/kickoff-manual.sqlite3
```

As migrações também criam backups antes de alterações estruturais. Não delete
nem substitua o banco para corrigir um problema de protocolo.

## 11. Missões e gancho local de conclusão

O servidor publica27 missões compatíveis. O contador de objetivos ainda não é
calculado de forma completa/autoritativa. Para testar o ciclo de uma missão
aceita, por exemplo a de ID7:

```powershell
Invoke-WebRequest "http://127.0.0.1:18080/gm/mission_success?id=7"
```

O hook marca a missão e avisa o cliente conectado. Ao coletar, PG e estado são
gravados numa transação; uma missão coletada não reabre nem paga outra vez.
Recompensa de item não reconstruída é recusada sem consumir a recompensa.
O hook HTTP é de desenvolvimento, sem autenticação, e fica em loopback.

## 12. Testes

```powershell
.venv\Scripts\python.exe -m unittest discover -s reverse/tests -v
```

Os testes usam bancos temporários e portas efêmeras. Não precisam instalar
o jogo nem abrir uma janela. Cobrem CRC/frames, TCP/UDP, personagens, roupas,
lojas, carteira, concorrência, requisitos de nível, grupos compartilhados de
skills, introdução, missão, sala, resultado, replay, relay e peers/bots.

Os três testes históricos de patch x86 dependentes do executável original
ficaram fora deste pacote; os testes de aparência/DefaultFeatures do servidor
continuam incluídos. Nenhum executável é baixado nos testes ou no CI.

Na publicação inicial, **121 testes passaram** em uma venv isolada com Python3.12,
incluindo três testes novos de independência dos metadados, preparo seguro da
configuração e consulta somente leitura do histórico. O sucesso em outros
ambientes deve ser acompanhado pelo CI.

CI em [.github/workflows/tests.yml](.github/workflows/tests.yml), com Python3.12/3.13.
O sucesso automatizado não equivale à validação visual de todos os efeitos.

## 13. Portas e componentes

| Porta | Transporte/endereço | Responsabilidade |
|---|---|---|
| 18080 | HTTP,127.0.0.1 | Notice, autenticação experimental, gancho GM |
| 19999 | TCP,loopback e IPv4 do host | Front e redirecionamento |
| 19998 | TCP,loopback e IPv4 do host | Buddy/bootstrap e sessão auxiliar |
| 20000 | TCP,loopback e IPv4 do host | Conta, personagem, lojas e salas |
| 19998–20000 | UDP,apenas loopback | Captura de investigação |
| 20001 | TCP/UDP,loopback e IPv4 do host | Relay de partida e fallback |
| 20002 | TCP,apenas loopback e com `--test-client` | Ingresso de PeerTeste |
| 20002–20004 | UDP,IPv4 do host, nos peers | Sockets próprios de PeerTeste0–2 |

O cliente antigo vincula o UDP dele ao IPv4 retornado pelo nome do computador
na porta20000. O servidor não ocupa essa portaUDP nesse endereço; usa20001
para relay. Por isso o caminho de teste usa também o IPv4 local/virtual do host
para TCP/relay. Não substitua todos os endereços por127.0.0.1 arbitrariamente.

## 14. Arquivos principais

| Arquivo em `reverse/server/` | Responsabilidade |
|---|---|
| `capture_server.py` | Entrypoint, listeners, roteamento e captura |
| `protocol.py` | Frames, CRC e ACKs nativos |
| `characters.py` | Store SQLite e validações de personagem/inventário |
| `character_profiles.py` | Corpos, peças iniciais e compatibilidade |
| `character_slots.py` / `onboarding.py` | Migrações e introdução |
| `catalog.py` / `resource_catalog.py` | Metadados independentes do cliente |
| `resources/game_catalog.json` | IDs, preços, perfis, níveis e missões de referência |
| `economy.py` / `match_economy.py` | Compras e liquidação de recompensas |
| `rooms.py` | Estado das salas temporárias |
| `netstate.py` | Endereços entregues ao cliente |
| `relay.py` | Transporte UDP/TCP de partida |
| `bots.py` / `bot_ai.py` | Peer virtual e decisões experimentais |
| `test_peer.py` | Entrada separada dos peers de teste |
| `messenger.py` | Bootstrap da lista de amigos |
| `missions.py` | Categorias, missões e recompensas |

## 15. Diagnóstico

- **Servidor não inicia:** confirme Python/venv e dependências. Outra instância
  pode estar usando as portas. Pare só a instância do projeto, sem matar todos
  os processos Python da máquina.
- **Jogo fica conectando:** confirme servidor com `--emulate-front`, cliente
  preparado, versão compatível e endpoints. Consulte `reverse/captures/events.jsonl`.
- **Bot no loading:** instale `requirements.txt`, use o modo/flag correto e
  confira `udp_error`, `tcp_error` e os ACKs no arquivo de eventos.
- **Common não começa:** exige dois ou três por time, prontos. Ready não é Start.
- **Habilidade não salva:** confira propriedade, nível, contagem e campos
  ativos. Grupos/sorts repetidos são válidos; não torne essas colunas únicas.
- **Sem XP/PG:** Practice é zero por padrão; Common exige120s e elenco completo.
  Repetir resultado não paga outra vez.
- **Aviso de validade/data:** as peças atuais usam validade sandbox2037.
  Não mova o campo de expiração para o uint64: o leitor nativo usa offset26.
- **`Cannot listen`:** verifique também o IPv4 do host e a existência de um
  adaptador virtual. O processo pode estar parcialmente iniciado; corrija a
  porta/endereço antes de considerar o serviço saudável.

Capture o último pedido/ACK e o horário da falha. Um `unhandled_request` não
prova que um ACK é necessário: alguns pacotes nativos são unidirecionais.
Não envie ACKs extras para "destravar" sem verificar o leitor do cliente.

**Não publique seus dumps, bancos ou logs completos em issues.** Logs binários
podem conter nomes e dados de autenticação. A pasta runtime e a cópia do cliente
estão ignoradas pelo Git; os fixtures do repositório são exemplos selecionados.

## 16. Próximas contribuições

Prioridades: autenticação e sessão por usuário; dois clientes gráficos reais;
verificação autoritativa do placar/tempo/atributos; objetivos de missão reais;
amizades/chat; duração/renovação de itens; latência/perda/reconexão e persistência
dos demais jogadores. O cliente mobile seria um projeto novo, não um APK deste
servidor Python ou do executável Windows.

Veja [CONTRIBUTING.md](CONTRIBUTING.md) para enviar correções e o protocolo de teste.
Licença do código/documentação: [MIT](LICENSE).
