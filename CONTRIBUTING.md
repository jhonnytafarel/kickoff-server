# Contribuir

1. Faça fork, crie uma branch descritiva e instale a venv/requirements do README.
2. Reproduza o problema com um banco temporário. Registre horário, pedido, ACK
   e etapa; remova informações privadas antes de compartilhar um exemplo.
3. Confirme ordem/tamanho dos campos pelo leitor do cliente ou por evidência
   existente. Separe o que foi confirmado do que ainda é hipótese.
4. Adicione regressão útil ao bug e execute `python -m unittest discover -s reverse/tests -v`.
5. Abra PR explicando comportamento anterior/novo, testes e o que precisa de
   reteste visual. Não inclua executáveis, imagens, XPKs, banco ou captura completa.

## Regras importantes

- Preserve os originais do cliente; adapte só uma cópia privada ignorada pelo Git.
- `Group/Sort` de skills podem repetir. Contar skills ativas, no máximo5;
  validar IDs únicos, propriedade e níveis.
- Equipar roupa responde somente ao ACK nativo. ACKs extras de inventário/info
  podem quebrar a interface ou a aparência.
- CRC e layout são os do cliente legado. Não use crc32 padrão como substituto.
- Recompensas vêm da política do servidor; nunca aceite XP/PG enviados pelo host.
- Backups antes de migração; nenhuma correção deve resetar dados como atalho.
- Não afirmar online público pronto, física autoritativa ou efeito visual de
  skill sem validar esses caminhos.
- O JSON de compatibilidade substitui a dependência de scripts extraídos da
  máquina de pesquisa. Mudanças nele precisam manter coerência com o cliente.

Informe versão do cliente, Python, SO e passos da reprodução. Em falhas de
rede, informe se havia bot, PeerTeste ou cliente gráfico e qual modo da sala.
