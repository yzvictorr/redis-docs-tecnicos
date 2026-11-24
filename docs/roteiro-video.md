# Roteiro do vídeo (cerca de 5min47)

Ritmo de referência: ~140 palavras por minuto. Cada bloco tem o tempo, o que aparece na tela e a fala.
Antes de gravar: abra o `demo_colab.ipynb` no Google Colab e rode as seções 1 a 4 (ambiente, projeto, funções de apoio e *seed*). Na gravação, rode as células da seção 5 uma por uma.

---

## 0:00 – 0:35 · Abertura (slides 1 e 2)

**Tela:** slide de título → slide de contexto.

> Olá, eu sou [nome], e neste vídeo apresento a minha implementação individual de um banco NoSQL. Escolhi o **Redis**, um banco chave-valor em memória, e apliquei no mesmo domínio do nosso API: documentos técnicos de engenharia que passam por um fluxo de validação. O backend é em Django, como no projeto do grupo, mas aqui não existe banco relacional: tudo fica no Redis.

## 0:35 – 1:25 · Por que Redis (slide 3)

**Tela:** slide "Por que Redis".

> Olhando o sistema, os pontos que mais pedem desempenho não são tabelas: são uma **fila** de documentos esperando validação, um **cache** para as respostas do assistente de IA, que são lentas, e **contadores e rankings** de acesso. O Redis já traz essas estruturas prontas: listas que funcionam como fila, chaves que expiram sozinhas e conjuntos ordenados para ranking. Comparei com MongoDB, que seria bom para guardar o documento, mas não tem fila nem expiração como estrutura nativa; e com Cassandra e Neo4j, que resolvem problemas de escala e de relacionamento que esse escopo não tem.

## 1:25 – 2:15 · Modelagem (slide 4)

**Tela:** slide com a tabela de chaves.

> A modelagem é por chaves. Cada documento é um **hash** `doc:{id}`. Como o Redis não tem WHERE, eu mantenho índices: um **sorted set** com a data de criação para listar do mais novo ao mais antigo, e um **set** por status. A fila de validação é uma **lista**; o histórico é outra lista limitada aos últimos 50 eventos; o ranking é um sorted set; e o resumo da IA é uma string com **TTL de 60 segundos**. As trocas de status usam transação com `WATCH`, então dois validadores não conseguem mexer no mesmo documento ao mesmo tempo.

## 2:15 – 4:05 · Demonstração (slide 5, depois o notebook no Colab)

**Tela:** `demo_colab.ipynb` no Google Colab, seção 5. Rode uma célula por vez e mostre a saída.

> Para a demonstração, uso um notebook no Google Colab: ele sobe o Redis e chama os endpoints do Django direto nas células.

1. **5.1 Criar** → mostrar `"status": "rascunho"`, `"versao": "1"` e o `HASH` impresso logo abaixo.
   > Crio um documento novo. O ID vem de um `INCR`, que é atômico, e o documento fica guardado como um hash.
2. **5.2 Submeter** → a fila aumenta.
   > Ao submeter, ele entra no fim da fila de validação.
3. **5.3 Próximo da fila** → sai um documento do *seed*, não o recém-enviado.
   > O validador pega o próximo. Repare que saiu um documento que já estava esperando: o primeiro que entrou é o primeiro que sai.
4. **5.4 Validar** com `aprovado: false` → histórico; depois a tentativa de aprovar dá **409**.
   > Reprovei com comentário, e o histórico registrou cada passo. Se eu tento aprovar um documento já reprovado, o sistema recusa.
5. **5.5 Resumo** duas vezes → a linha final compara `calculado` (~1500 ms) com `cache` (poucos ms) e mostra o TTL.
   > Primeira chamada: calculado, um segundo e meio. Segunda: veio do cache, em poucos milissegundos, e a chave vai sumir sozinha em 60 segundos. Se eu edito o documento, o cache é apagado.
6. **5.6 Ranking** → o documento novo sobe para o topo depois dos acessos.
7. **5.7 Visão geral** → a tabela com cada chave, o tipo e o TTL.
   > E aqui dá para ver cada estrutura que eu usei: hash, lista, set, sorted set e a string com expiração.

## 4:05 – 4:55 · Vantagens (slide 6)

**Tela:** slide de vantagens.

> As vantagens que senti na prática: **velocidade**, porque tudo está em memória; **estruturas prontas**, então eu não programei fila, ranking nem expiração, só usei; **atomicidade**, com comandos como INCR, SMOVE e transações; e **simplicidade de operação**: uma imagem Docker pequena e nenhum esquema para migrar.

## 4:55 – 5:35 · Desvantagens (slide 7)

**Tela:** slide de desvantagens.

> As desvantagens: todos os dados precisam **caber na RAM**, que é cara. **Não há consultas livres**: cada filtro novo exige um índice mantido à mão, e eu precisei garantir na aplicação que os índices não ficassem inconsistentes. A **durabilidade** depende de configuração: com AOF, ainda dá para perder cerca de um segundo de escrita. E não há chave estrangeira nem validação de esquema.

## 5:35 – 5:47 · Conclusão (slide 8)

**Tela:** slide final com o link do repositório.

> Minha conclusão: o Redis é excelente como **complemento** do banco principal, para fila, cache e ranking, mas não como fonte oficial dos documentos. O código e a documentação estão no repositório da tela. Obrigado!
