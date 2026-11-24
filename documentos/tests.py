"""
Testes de integração: rodam contra um Redis de verdade, no database 15,
para não misturar com os dados da demonstração (database 0).

    docker compose run --rm web python manage.py test
"""

import json
import os

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from documentos import repository as repo
from documentos.redis_client import get_redis

REDIS_TESTE = os.getenv("REDIS_TEST_URL", settings.REDIS_URL.rsplit("/", 1)[0] + "/15")


@override_settings(REDIS_URL=REDIS_TESTE, RESUMO_ATRASO_SEGUNDOS=0, RESUMO_TTL_SEGUNDOS=30)
class DocumentosTest(SimpleTestCase):
    def setUp(self):
        get_redis().flushdb()

    def tearDown(self):
        get_redis().flushdb()

    def _post(self, url, dados=None):
        return self.client.post(url, json.dumps(dados or {}), content_type="application/json")

    def _criar(self, titulo="Doc de teste", conteudo="um dois tres"):
        return self._post("/api/documentos/", {"titulo": titulo, "autor": "Teste", "conteudo": conteudo}).json()

    def test_criar_e_obter(self):
        doc = self._criar()
        self.assertEqual(doc["status"], "rascunho")
        r = self.client.get(f"/api/documentos/{doc['id']}/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["titulo"], "Doc de teste")

    def test_titulo_obrigatorio(self):
        r = self._post("/api/documentos/", {"autor": "x"})
        self.assertEqual(r.status_code, 400)

    def test_documento_inexistente(self):
        self.assertEqual(self.client.get("/api/documentos/999/").status_code, 404)

    def test_listagem_mais_novo_primeiro_e_filtro_status(self):
        a = self._criar("A")
        b = self._criar("B")
        self._post(f"/api/documentos/{a['id']}/submeter/")
        todos = self.client.get("/api/documentos/").json()["documentos"]
        self.assertEqual([d["titulo"] for d in todos], ["B", "A"])
        em_validacao = self.client.get("/api/documentos/?status=em_validacao").json()["documentos"]
        self.assertEqual([d["id"] for d in em_validacao], [a["id"]])
        self.assertNotIn(b["id"], [d["id"] for d in em_validacao])

    def test_fila_e_fifo(self):
        a, b = self._criar("A"), self._criar("B")
        self._post(f"/api/documentos/{a['id']}/submeter/")
        self._post(f"/api/documentos/{b['id']}/submeter/")
        self.assertEqual(self.client.get("/api/fila/").json()["tamanho"], 2)
        self.assertEqual(self._post("/api/fila/proximo/").json()["id"], a["id"])
        self.assertEqual(self._post("/api/fila/proximo/").json()["id"], b["id"])
        self.assertEqual(self._post("/api/fila/proximo/").json()["mensagem"], "Fila vazia.")

    def test_fluxo_de_validacao_e_historico(self):
        doc = self._criar()
        url = f"/api/documentos/{doc['id']}"
        # Não dá para validar um rascunho
        self.assertEqual(self._post(f"{url}/validar/", {"aprovado": True}).status_code, 409)
        self._post(f"{url}/submeter/")
        # Em validação não pode ser editado
        r = self.client.patch(f"{url}/", json.dumps({"titulo": "x"}), content_type="application/json")
        self.assertEqual(r.status_code, 409)
        r = self._post(f"{url}/validar/", {"aprovado": False, "comentario": "falta anexo"})
        self.assertEqual(r.json()["status"], "reprovado")
        self.assertEqual(self.client.get("/api/fila/").json()["tamanho"], 0)
        acoes = [e["acao"] for e in self.client.get(f"{url}/historico/").json()["eventos"]]
        self.assertEqual(acoes, ["reprovado", "submetido", "criado"])

    def test_edicao_incrementa_versao_e_invalida_cache(self):
        doc = self._criar(conteudo="texto antigo")
        url = f"/api/documentos/{doc['id']}"
        self.assertEqual(self.client.get(f"{url}/resumo/").json()["fonte"], "calculado")
        segundo = self.client.get(f"{url}/resumo/").json()
        self.assertEqual(segundo["fonte"], "cache")
        self.assertGreater(segundo["ttl_restante_s"], 0)
        r = self.client.patch(f"{url}/", json.dumps({"conteudo": "texto novo"}), content_type="application/json")
        self.assertEqual(r.json()["versao"], "2")
        terceiro = self.client.get(f"{url}/resumo/").json()
        self.assertEqual(terceiro["fonte"], "calculado")
        self.assertEqual(terceiro["resumo"], "texto novo")

    def test_ranking_de_acessos(self):
        a, b = self._criar("A"), self._criar("B")
        for _ in range(3):
            self.client.get(f"/api/documentos/{b['id']}/")
        self.client.get(f"/api/documentos/{a['id']}/")
        ranking = self.client.get("/api/ranking/").json()["ranking"]
        self.assertEqual(ranking[0]["titulo"], "B")
        self.assertEqual(ranking[0]["acessos"], 3)

    def test_excluir_remove_indices(self):
        doc = self._criar()
        self._post(f"/api/documentos/{doc['id']}/submeter/")
        self.assertEqual(self.client.delete(f"/api/documentos/{doc['id']}/").status_code, 204)
        stats = self.client.get("/api/estatisticas/").json()
        self.assertEqual(stats["total_documentos"], 0)
        self.assertEqual(stats["na_fila"], 0)
        self.assertEqual(sum(stats["por_status"].values()), 0)
        r = get_redis()
        self.assertEqual(r.keys(f"*{doc['id']}*"), [])

    def test_ttl_expira(self):
        doc = self._criar()
        repo.resumo(doc["id"])
        chave = repo.K_RESUMO.format(id=doc["id"])
        get_redis().pexpire(chave, 1)  # força a expiração para não esperar 30 s
        import time
        time.sleep(0.05)
        self.assertIsNone(get_redis().get(chave))
