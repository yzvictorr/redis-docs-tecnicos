# Modelagem e decisões de projeto

## 1. Contexto

No API do grupo, o sistema gerencia documentos técnicos de engenharia que passam por um fluxo de validação, com apoio de um assistente de IA. Esta implementação individual reproduz o núcleo desse domínio usando **Redis** como único banco, para avaliar onde um banco chave-valor em memória ajuda e onde ele atrapalha.

## 2. Por que Redis e não outro NoSQL

| Família | Exemplo | Por que não foi a escolha aqui |
|---|---|---|
| Documento | MongoDB | Bom para o registro dos documentos, mas não traz fila, ranking e TTL como estruturas de primeira classe |
| Colunar | Cassandra | Feito para volumes enormes e escrita distribuída; excessivo para o escopo e difícil de demonstrar em 5 min |
| Grafo | Neo4j | Ótimo para relações (documento → revisões → normas), mas o fluxo principal do domínio é fila + status |
| **Chave-valor** | **Redis** | Resolve diretamente os pontos de desempenho do sistema: fila de validação, cache das respostas da IA, contadores e ranking |

## 3. Fluxo de status

```
     criar
       │
       ▼
  ┌──────────┐  submeter  ┌──────────────┐  validar(true)  ┌──────────┐
  │ rascunho │ ─────────► │ em_validacao │ ──────────────► │ aprovado │
  └──────────┘            └──────────────┘                 └──────────┘
                             ▲        │
                    submeter │        │ validar(false)
                             │        ▼
                          ┌───────────────┐
                          │   reprovado   │  (pode ser editado e reenviado)
                          └───────────────┘
```

- Só `rascunho` e `reprovado` aceitam edição.
- Toda troca de status passa por `_mudar_status()` em `repository.py`.

## 4. Chaves e estruturas

### `doc:{id}` — HASH
Um documento é um *hash* com campos planos. `HGETALL` lê tudo de uma vez; `HSET` altera só os campos enviados; `HINCRBY versao 1` sobe a versão de forma atômica.

### `docs:por_data` — ZSET (índice principal)
*Score* = timestamp de criação. `ZREVRANGE 0 19` devolve os 20 mais novos já ordenados e paginados, sem varrer o banco com `KEYS` (que bloqueia o servidor em produção).

### `docs:status:{status}` — SET (índice secundário)
Substitui um `WHERE status = ?`. Na troca de status, `SMOVE` tira o ID de um conjunto e põe no outro numa única operação atômica.

### `fila:validacao` — LIST
`LPUSH` coloca no início e `RPOP` tira do fim: o primeiro a entrar é o primeiro a sair. Como o `RPOP` é atômico, dois validadores nunca recebem o mesmo documento. Em produção dá para usar `BLMOVE` para mover para uma lista "em processamento" e não perder itens se o validador cair.

### `doc:{id}:historico` — LIST limitada
Cada evento é um JSON (`acao`, `detalhe`, `em`). Depois de cada `LPUSH`, um `LTRIM 0 49` mantém só os 50 mais recentes, então a memória gasta por documento é limitada.

### `ranking:acessos` — ZSET
Cada `GET /documentos/{id}/` executa `ZINCRBY ranking:acessos 1 {id}`. O top N sai com `ZREVRANGE 0 N-1 WITHSCORES`, sempre ordenado, sem `ORDER BY`.

### `cache:resumo:{id}` — STRING com TTL
Padrão **cache-aside**:

1. `GET cache:resumo:{id}` → se existe, devolve (`"fonte": "cache"`).
2. Se não existe, calcula o resumo (simula a chamada à IA, ~1,5 s), grava com `SET ... EX 60` e devolve (`"fonte": "calculado"`).
3. Ao editar o documento, `DEL cache:resumo:{id}` invalida o cache para não servir resumo velho.

## 5. Consistência

- **Criação e edição** usam `MULTI/EXEC` (pipeline transacional): documento e índices são gravados juntos.
- **Troca de status** usa `WATCH` (controle de concorrência otimista): lê o status atual, valida a transição e só executa se ninguém alterou o documento nesse meio-tempo. Se alguém alterou, o Redis aborta e a operação é repetida.
- **Exclusão** remove o hash e o ID de todos os índices, fila, ranking, histórico e cache na mesma transação.

## 6. Persistência

O `docker-compose.yml` sobe o Redis com `--appendonly yes` (AOF): cada escrita é registrada em log e regravada no disco a cada segundo (`appendfsync everysec`, padrão). Numa queda, perde-se no máximo cerca de 1 segundo de escritas.

## 7. O que faria diferente em produção

- Redis como **complemento** do banco principal (PostgreSQL ou MongoDB guardando o registro oficial), ficando só com fila, cache, ranking e sessões.
- `BLMOVE` em vez de `RPOP` para a fila ser à prova de falha do consumidor.
- Redis Stack (RediSearch) se fosse preciso busca por texto.
- `maxmemory` + política `allkeys-lru` se ele for usado só como cache.
