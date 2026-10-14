# Instruções para agentes neste repositório

Leia README.md e CONTRIBUTING.md. Este é um pacote independente do servidor
local; não pressupor uma instalação do cliente ou dados da máquina do autor.

- Python3.12+, asyncio e SQLite. Não reescrever em outra linguagem sem pedido.
- Metadados em reverse/server/resources/game_catalog.json; nunca exigir
  reverse/extracted ou reverse/local-client para importar o servidor.
- Dados, capturas, backups e cliente privado devem permanecer ignorados.
- Não apagar/resubstituir o banco para resolver regressões. Backups antes de migração.
- Testes: python -m unittest discover -s reverse/tests -v. Usar bancos temporários.
- Skills: Group/Sort repetidos são válidos; só ID é único, máximo5 ativas,
  pares ativos/inativos coerentes, propriedade e níveis.
- Não emitir ACKs adicionais em equip. Não inventar campos pelo nome da tela.
- Alterações no servidor/Store/relay requerem reinício; protocol.py tem reload local.
- Não confundir testes dos peers com autenticação/multiplayer público completo.
- Preserve o cliente original, não publicar seu conteúdo, nem logs/credenciais.
- Não delegar para subagentes automaticamente.
