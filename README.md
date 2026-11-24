# Documentos Técnicos com Redis

[![Abrir no Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/yzvictorr/redis-docs-tecnicos/blob/main/demo_colab.ipynb)

Implementação individual de um banco **NoSQL chave-valor (Redis)** aplicada ao mesmo domínio do API: gestão e validação de documentos técnicos de engenharia.

O backend é **Django** (mesmo stack do projeto do grupo), mas **sem banco relacional**: todo o armazenamento — documentos, índices, fila de validação, histórico, ranking e cache — vive no Redis, cada um usando a estrutura de dados mais adequada.

---

## Por que Redis?

| Necessidade do domínio | Como o Redis resolve |
|---|---|
| Fila de documentos aguardando validação | `LIST` com `LPUSH` + `RPOP` = fila FIFO nativa, atômica |
| Respostas lentas do assistente de IA (resumos) | Cache com `SET ... EX` — a chave **expira sozinha** (TTL) |
| "Quais documentos são mais consultados?" | `ZSET` com `ZINCRBY` — ranking ordenado em tempo real |
| Histórico de alterações de cada documento | `LIST` limitada com `LTRIM` (guarda só os últimos 50) |
| Filtrar documentos por status | `SET` por status, movido atomicamente com `SMOVE` |
| Gerar IDs sem colisão | `INCR` é atômico, mesmo com vários clientes |

Em vez de modelar tudo como tabelas, cada problema usa a estrutura que já resolve ele. Tudo roda em memória, com latência na casa de **sub-milissegundos**.

---

## Como rodar

### Opção 1 — Google Colab (sem instalar nada)

Abra o [`demo_colab.ipynb`](demo_colab.ipynb) pelo botão **Abrir no Colab** acima e use `Ambiente de execução › Executar tudo`.
O notebook instala e inicia o Redis dentro do Colab, carrega este projeto, popula os dados e executa a demonstração completa e os testes.
As chamadas à API usam o *test client* do Django, então não é preciso abrir servidor web.

### Opção 2 — Docker

Pré-requisito: **Docker** e **Docker Compose**.

```bash
git clone <url-do-seu-repositorio>
cd redis-docs-tecnicos
docker compose up -d --build

# popula com 8 documentos de exemplo
docker compose exec web python manage.py seed --limpar
```

- API: http://localhost:8000/api/
- RedisInsight (interface gráfica): http://localhost:5540 → *Add Redis database* → host `redis`, porta `6379`

### Testes

```bash
docker compose exec web python manage.py test
```

Os testes rodam contra um Redis real, no *database* 15, sem tocar nos dados da demonstração (database 0).

### Sem Docker

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
# com um Redis rodando em localhost:6379
python manage.py seed --limpar
python manage.py runserver
```

---

## Endpoints

| Método | Rota | O que faz | Estrutura Redis |
|---|---|---|---|
| GET | `/api/health/` | Testa conexão (`PING`) | — |
| GET | `/api/documentos/?status=&limite=&offset=` | Lista, do mais novo ao mais antigo | `ZSET` / `SET` + `HASH` |
| POST | `/api/documentos/` | Cria documento (status `rascunho`) | `INCR`, `HASH`, `MULTI/EXEC` |
| GET | `/api/documentos/{id}/` | Detalhe (conta 1 acesso no ranking) | `HASH`, `ZINCRBY` |
| PATCH | `/api/documentos/{id}/` | Edita, sobe a versão, invalida cache | `HSET`, `HINCRBY`, `DEL` |
| DELETE | `/api/documentos/{id}/` | Remove documento e todos os índices | `DEL`, `ZREM`, `SREM`, `LREM` |
| POST | `/api/documentos/{id}/submeter/` | Envia para a fila de validação | `WATCH`, `SMOVE`, `LPUSH` |
| POST | `/api/fila/proximo/` | Pega o próximo da fila (FIFO) | `RPOP` |
| GET | `/api/fila/` | Tamanho da fila | `LLEN` |
| POST | `/api/documentos/{id}/validar/` | Aprova ou reprova `{"aprovado": true, "comentario": ""}` | `WATCH`, `SMOVE` |
| GET | `/api/documentos/{id}/historico/` | Últimos 50 eventos | `LRANGE` |
| GET | `/api/documentos/{id}/resumo/` | Resumo com cache (TTL 60 s) | `GET` / `SET EX`, `TTL` |
| GET | `/api/ranking/?n=10` | Documentos mais acessados | `ZREVRANGE WITHSCORES` |
| GET | `/api/estatisticas/` | Contagem por status, fila, memória | `SCARD`, `LLEN`, `INFO` |

Exemplos prontos para o VS Code (extensão REST Client) em [`requests.http`](requests.http).

### Exemplo rápido com curl

```bash
curl -X POST localhost:8000/api/documentos/ -H "Content-Type: application/json" \
  -d '{"titulo":"Especificação de rebites","autor":"Engenharia","conteudo":"Requisitos de instalação..."}'

curl -X POST localhost:8000/api/documentos/1/submeter/
curl -X POST localhost:8000/api/fila/proximo/
curl -X POST localhost:8000/api/documentos/1/validar/ -H "Content-Type: application/json" \
  -d '{"aprovado": true, "comentario": "ok"}'

curl localhost:8000/api/documentos/1/resumo/   # 1ª vez: "fonte": "calculado" (~1,5 s)
curl localhost:8000/api/documentos/1/resumo/   # 2ª vez: "fonte": "cache" (poucos ms)
```

---

## Modelagem de chaves

| Chave | Tipo | Conteúdo |
|---|---|---|
| `doc:seq` | STRING | Contador de IDs |
| `doc:{id}` | HASH | `id, titulo, tipo, autor, conteudo, status, versao, criado_em, atualizado_em` |
| `docs:por_data` | ZSET | Todos os IDs, *score* = timestamp de criação |
| `docs:status:{status}` | SET | IDs em cada status |
| `fila:validacao` | LIST | IDs aguardando validação |
| `doc:{id}:historico` | LIST | Eventos em JSON (máx. 50) |
| `ranking:acessos` | ZSET | IDs, *score* = número de acessos |
| `cache:resumo:{id}` | STRING | Resumo em JSON, expira em 60 s |

Detalhes, fluxo de status e decisões de projeto em [`docs/modelagem.md`](docs/modelagem.md).

---

## Vantagens e desvantagens

### Vantagens
- **Velocidade**: dados em memória, operações em microssegundos/sub-milissegundos.
- **Estruturas prontas**: fila, ranking, conjuntos e expiração já vêm no banco; nada disso precisou ser programado na aplicação.
- **Operações atômicas**: `INCR`, `SMOVE`, `RPOP` e transações `MULTI/EXEC` evitam condições de corrida sem *locks* manuais.
- **TTL nativo**: o cache se limpa sozinho.
- **Simples de operar**: um único binário, imagem Docker de poucos MB, sem esquema para migrar.

### Desvantagens
- **Memória é o limite**: todo o *dataset* precisa caber na RAM, que é mais cara que disco.
- **Sem consultas ad hoc**: não existe `WHERE` nem `JOIN`. Todo filtro precisa de um índice mantido à mão (por isso `docs:status:*` e `docs:por_data`).
- **Durabilidade configurável, não garantida por padrão**: com AOF a cada segundo, pode-se perder até ~1 s de escrita numa queda.
- **Integridade é responsabilidade da aplicação**: não há chave estrangeira nem validação de esquema.
- **Busca textual** exige módulo extra (RediSearch / Redis Stack).

### Quando usar (e quando não)
Redis brilha como **complemento** de um banco principal: cache, filas, sessões, rankings, contadores e dados temporários. Para o **registro oficial** dos documentos, com relatórios e consultas livres, um banco relacional ou de documentos continua sendo a melhor fonte de verdade.

---

## Estrutura do projeto

```
redis-docs-tecnicos/
├── config/                  # settings, urls, wsgi do Django
├── documentos/
│   ├── redis_client.py      # conexão (pool) com o Redis
│   ├── repository.py        # TODA a lógica de chaves e comandos Redis
│   ├── views.py             # endpoints JSON
│   ├── urls.py
│   ├── tests.py             # testes de integração com Redis real
│   └── management/commands/seed.py
├── docs/
│   ├── modelagem.md
│   └── roteiro-video.md
├── demo_colab.ipynb         # demonstração completa no Google Colab
├── docker-compose.yml       # redis + web + redisinsight
├── Dockerfile
├── requests.http
└── requirements.txt
```

## Autor
<Seu nome> — <curso / semestre>
