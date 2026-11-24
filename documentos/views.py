import json
from functools import wraps

from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from . import repository as repo
from .redis_client import get_redis


def _corpo(request):
    if not request.body:
        return {}
    try:
        dados = json.loads(request.body)
    except json.JSONDecodeError:
        raise repo.DadosInvalidos("Corpo da requisição não é um JSON válido.")
    if not isinstance(dados, dict):
        raise repo.DadosInvalidos("O corpo deve ser um objeto JSON.")
    return dados


def _int(valor, padrao, minimo=0, maximo=100):
    try:
        return max(minimo, min(maximo, int(valor)))
    except (TypeError, ValueError):
        return padrao


def tratar_erros(view):
    """Converte as exceções do repositório em respostas HTTP."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except repo.DocumentoNaoEncontrado as e:
            return JsonResponse({"erro": f"Documento {e} não encontrado."}, status=404)
        except repo.DadosInvalidos as e:
            return JsonResponse({"erro": str(e)}, status=400)
        except repo.TransicaoInvalida as e:
            return JsonResponse({"erro": str(e)}, status=409)
    return wrapper


@require_http_methods(["GET"])
def health(request):
    get_redis().ping()
    return JsonResponse({"redis": "ok"})


@csrf_exempt
@require_http_methods(["GET", "POST"])
@tratar_erros
def documentos(request):
    if request.method == "POST":
        return JsonResponse(repo.criar(_corpo(request)), status=201)
    docs = repo.listar(
        status=request.GET.get("status"),
        limite=_int(request.GET.get("limite"), 20, 1, 100),
        offset=_int(request.GET.get("offset"), 0, 0, 10**6),
    )
    return JsonResponse({"quantidade": len(docs), "documentos": docs})


@csrf_exempt
@require_http_methods(["GET", "PATCH", "DELETE"])
@tratar_erros
def documento(request, doc_id):
    if request.method == "PATCH":
        return JsonResponse(repo.atualizar(doc_id, _corpo(request)))
    if request.method == "DELETE":
        repo.excluir(doc_id)
        return HttpResponse(status=204)
    return JsonResponse(repo.obter(doc_id))


@csrf_exempt
@require_http_methods(["POST"])
@tratar_erros
def submeter(request, doc_id):
    return JsonResponse(repo.submeter(doc_id))


@csrf_exempt
@require_http_methods(["POST"])
@tratar_erros
def validar(request, doc_id):
    dados = _corpo(request)
    if not isinstance(dados.get("aprovado"), bool):
        raise repo.DadosInvalidos("Informe 'aprovado': true ou false.")
    return JsonResponse(repo.validar(doc_id, dados["aprovado"], dados.get("comentario", "")))


@require_http_methods(["GET"])
@tratar_erros
def historico(request, doc_id):
    return JsonResponse({"id": str(doc_id), "eventos": repo.historico(doc_id)})


@require_http_methods(["GET"])
@tratar_erros
def resumo(request, doc_id):
    return JsonResponse(repo.resumo(doc_id))


@require_http_methods(["GET"])
def fila(request):
    return JsonResponse({"tamanho": repo.tamanho_fila()})


@csrf_exempt
@require_http_methods(["POST"])
def fila_proximo(request):
    doc = repo.proximo_da_fila()
    if doc is None:
        return JsonResponse({"mensagem": "Fila vazia."}, status=200)
    return JsonResponse(doc)


@require_http_methods(["GET"])
def ranking(request):
    n = _int(request.GET.get("n"), 10, 1, 50)
    return JsonResponse({"ranking": repo.ranking(n)})


@require_http_methods(["GET"])
def estatisticas(request):
    return JsonResponse(repo.estatisticas())
