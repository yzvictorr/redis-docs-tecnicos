"""
Camada de acesso ao Redis.

Toda a modelagem de chaves fica concentrada aqui, para que as views
não precisem saber nada sobre a estrutura interna do banco.

Chaves usadas (veja docs/modelagem.md):

    doc:seq                  STRING   contador para gerar IDs (INCR)
    doc:{id}                 HASH     campos do documento técnico
    docs:por_data            ZSET     índice de todos os docs, score = timestamp de criação
    docs:status:{status}     SET      índice secundário por status
    fila:validacao           LIST     fila FIFO de documentos aguardando validação
    doc:{id}:historico       LIST     últimos eventos do documento (JSON), limitado a 50
    ranking:acessos          ZSET     documentos mais acessados (ZINCRBY a cada leitura)
    cache:resumo:{id}        STRING   resumo pré-calculado, expira sozinho (TTL)
"""

import json
import time
from datetime import datetime, timezone

from django.conf import settings
from redis.exceptions import WatchError

from .redis_client import get_redis

STATUS_VALIDOS = ("rascunho", "em_validacao", "aprovado", "reprovado")
TIPOS_SUGERIDOS = ("especificacao", "relatorio_ensaio", "desenho", "procedimento", "manual")
CAMPOS_EDITAVEIS = ("titulo", "tipo", "autor", "conteudo")
HISTORICO_MAX = 50

K_SEQ = "doc:seq"
K_DOC = "doc:{id}"
K_INDICE = "docs:por_data"
K_STATUS = "docs:status:{status}"
K_FILA = "fila:validacao"
K_HISTORICO = "doc:{id}:historico"
K_RANKING = "ranking:acessos"
K_RESUMO = "cache:resumo:{id}"


class DocumentoNaoEncontrado(Exception):
    pass


class TransicaoInvalida(Exception):
    pass


class DadosInvalidos(Exception):
    pass


def _agora():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _registrar_evento(pipe, doc_id, acao, detalhe=""):
    """Empilha um evento no histórico e corta a lista em HISTORICO_MAX itens."""
    chave = K_HISTORICO.format(id=doc_id)
    evento = json.dumps({"acao": acao, "detalhe": detalhe, "em": _agora()}, ensure_ascii=False)
    pipe.lpush(chave, evento)
    pipe.ltrim(chave, 0, HISTORICO_MAX - 1)


# ---------------------------------------------------------------- CRUD

def criar(dados):
    titulo = (dados.get("titulo") or "").strip()
    if not titulo:
        raise DadosInvalidos("O campo 'titulo' é obrigatório.")

    r = get_redis()
    doc_id = r.incr(K_SEQ)  # atômico: dois clientes nunca recebem o mesmo ID
    agora = _agora()
    doc = {
        "id": str(doc_id),
        "titulo": titulo,
        "tipo": dados.get("tipo") or "especificacao",
        "autor": dados.get("autor") or "anonimo",
        "conteudo": dados.get("conteudo") or "",
        "status": "rascunho",
        "versao": "1",
        "criado_em": agora,
        "atualizado_em": agora,
    }

    # MULTI/EXEC: o documento e todos os índices são gravados juntos ou nada é gravado.
    with r.pipeline(transaction=True) as p:
        p.hset(K_DOC.format(id=doc_id), mapping=doc)
        p.zadd(K_INDICE, {doc_id: time.time()})
        p.sadd(K_STATUS.format(status="rascunho"), doc_id)
        _registrar_evento(p, doc_id, "criado", f"por {doc['autor']}")
        p.execute()
    return doc


def obter(doc_id, contar_acesso=True):
    r = get_redis()
    doc = r.hgetall(K_DOC.format(id=doc_id))
    if not doc:
        raise DocumentoNaoEncontrado(doc_id)
    if contar_acesso:
        r.zincrby(K_RANKING, 1, doc_id)
    return doc


def listar(status=None, limite=20, offset=0):
    r = get_redis()
    if status:
        if status not in STATUS_VALIDOS:
            raise DadosInvalidos(f"Status inválido. Use um destes: {', '.join(STATUS_VALIDOS)}")
        ids = sorted(r.smembers(K_STATUS.format(status=status)), key=int, reverse=True)
        ids = ids[offset:offset + limite]
    else:
        # ZREVRANGE já devolve do mais novo para o mais antigo, paginado.
        ids = r.zrevrange(K_INDICE, offset, offset + limite - 1)

    with r.pipeline(transaction=False) as p:  # 1 ida e volta para N documentos
        for doc_id in ids:
            p.hgetall(K_DOC.format(id=doc_id))
        docs = p.execute()
    return [d for d in docs if d]


def atualizar(doc_id, dados):
    campos = {k: v for k, v in dados.items() if k in CAMPOS_EDITAVEIS and v is not None}
    if not campos:
        raise DadosInvalidos(f"Nada para atualizar. Campos aceitos: {', '.join(CAMPOS_EDITAVEIS)}")

    r = get_redis()
    chave = K_DOC.format(id=doc_id)
    status = r.hget(chave, "status")
    if status is None:
        raise DocumentoNaoEncontrado(doc_id)
    if status not in ("rascunho", "reprovado"):
        raise TransicaoInvalida(f"Documento em '{status}' não pode ser editado.")

    campos["atualizado_em"] = _agora()
    with r.pipeline(transaction=True) as p:
        p.hset(chave, mapping=campos)
        p.hincrby(chave, "versao", 1)
        p.delete(K_RESUMO.format(id=doc_id))  # invalida o cache: o conteúdo mudou
        _registrar_evento(p, doc_id, "editado", ", ".join(k for k in campos if k != "atualizado_em"))
        p.execute()
    return obter(doc_id, contar_acesso=False)


def excluir(doc_id):
    r = get_redis()
    chave = K_DOC.format(id=doc_id)
    status = r.hget(chave, "status")
    if status is None:
        raise DocumentoNaoEncontrado(doc_id)
    with r.pipeline(transaction=True) as p:
        p.delete(chave, K_HISTORICO.format(id=doc_id), K_RESUMO.format(id=doc_id))
        p.zrem(K_INDICE, doc_id)
        p.zrem(K_RANKING, doc_id)
        p.srem(K_STATUS.format(status=status), doc_id)
        p.lrem(K_FILA, 0, doc_id)
        p.execute()


# ------------------------------------------------------ fluxo de validação

def _mudar_status(doc_id, permitidos, novo, acao, detalhe="", extra=None):
    """
    Troca o status com controle de concorrência otimista (WATCH).
    Se outro cliente alterar o documento entre a leitura e o EXEC,
    o Redis aborta a transação e tentamos de novo.
    """
    r = get_redis()
    chave = K_DOC.format(id=doc_id)
    with r.pipeline() as p:
        while True:
            try:
                p.watch(chave)
                atual = p.hget(chave, "status")
                if atual is None:
                    raise DocumentoNaoEncontrado(doc_id)
                if atual not in permitidos:
                    raise TransicaoInvalida(
                        f"Não é possível ir de '{atual}' para '{novo}'. "
                        f"Status permitidos para esta ação: {', '.join(permitidos)}."
                    )
                p.multi()
                p.hset(chave, mapping={"status": novo, "atualizado_em": _agora()})
                p.smove(K_STATUS.format(status=atual), K_STATUS.format(status=novo), doc_id)
                if extra:
                    extra(p)
                _registrar_evento(p, doc_id, acao, detalhe)
                p.execute()
                return
            except WatchError:
                continue


def submeter(doc_id):
    _mudar_status(
        doc_id, ("rascunho", "reprovado"), "em_validacao", "submetido",
        extra=lambda p: p.lpush(K_FILA, doc_id),  # entra no fim da fila
    )
    return obter(doc_id, contar_acesso=False)


def proximo_da_fila():
    """Retira o documento mais antigo da fila (LPUSH + RPOP = FIFO)."""
    r = get_redis()
    doc_id = r.rpop(K_FILA)
    if doc_id is None:
        return None
    return obter(doc_id, contar_acesso=False)


def tamanho_fila():
    return get_redis().llen(K_FILA)


def validar(doc_id, aprovado, comentario=""):
    novo = "aprovado" if aprovado else "reprovado"
    _mudar_status(
        doc_id, ("em_validacao",), novo, novo, comentario,
        extra=lambda p: p.lrem(K_FILA, 0, doc_id),  # caso tenha sido validado sem sair da fila
    )
    return obter(doc_id, contar_acesso=False)


def historico(doc_id):
    r = get_redis()
    if not r.exists(K_DOC.format(id=doc_id)):
        raise DocumentoNaoEncontrado(doc_id)
    return [json.loads(e) for e in r.lrange(K_HISTORICO.format(id=doc_id), 0, -1)]


# ------------------------------------------------------ cache com TTL

def _gerar_resumo(doc):
    """Simula uma operação cara (no API real seria a chamada ao assistente de IA)."""
    time.sleep(getattr(settings, "RESUMO_ATRASO_SEGUNDOS", 1.5))
    palavras = doc.get("conteudo", "").split()
    trecho = " ".join(palavras[:30]) + ("..." if len(palavras) > 30 else "")
    return {
        "id": doc["id"],
        "titulo": doc["titulo"],
        "total_palavras": len(palavras),
        "resumo": trecho or "(documento sem conteúdo)",
        "gerado_em": _agora(),
    }


def resumo(doc_id):
    """Padrão cache-aside: tenta o cache; se não houver, calcula e grava com expiração."""
    r = get_redis()
    chave = K_RESUMO.format(id=doc_id)
    inicio = time.perf_counter()

    em_cache = r.get(chave)
    if em_cache:
        dados, fonte = json.loads(em_cache), "cache"
    else:
        doc = obter(doc_id, contar_acesso=False)
        dados, fonte = _gerar_resumo(doc), "calculado"
        r.set(chave, json.dumps(dados, ensure_ascii=False), ex=settings.RESUMO_TTL_SEGUNDOS)

    dados["fonte"] = fonte
    dados["ttl_restante_s"] = r.ttl(chave)
    dados["tempo_resposta_ms"] = round((time.perf_counter() - inicio) * 1000, 1)
    return dados


# ------------------------------------------------------ ranking e estatísticas

def ranking(n=10):
    r = get_redis()
    top = r.zrevrange(K_RANKING, 0, n - 1, withscores=True)
    with r.pipeline(transaction=False) as p:
        for doc_id, _ in top:
            p.hget(K_DOC.format(id=doc_id), "titulo")
        titulos = p.execute()
    return [
        {"posicao": i + 1, "id": doc_id, "titulo": titulo, "acessos": int(score)}
        for i, ((doc_id, score), titulo) in enumerate(zip(top, titulos))
    ]


def estatisticas():
    r = get_redis()
    with r.pipeline(transaction=False) as p:
        p.zcard(K_INDICE)
        for s in STATUS_VALIDOS:
            p.scard(K_STATUS.format(status=s))
        p.llen(K_FILA)
        resultados = p.execute()
    memoria = r.info("memory").get("used_memory_human")
    return {
        "total_documentos": resultados[0],
        "por_status": dict(zip(STATUS_VALIDOS, resultados[1:1 + len(STATUS_VALIDOS)])),
        "na_fila": resultados[-1],
        "memoria_redis": memoria,
    }
