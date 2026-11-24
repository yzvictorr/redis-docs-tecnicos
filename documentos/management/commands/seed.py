import random

from django.core.management.base import BaseCommand

from documentos import repository as repo
from documentos.redis_client import get_redis

DOCUMENTOS = [
    ("Especificação de torque - fixadores da asa", "especificacao", "Ana Souza"),
    ("Relatório de ensaio de fadiga - longarina", "relatorio_ensaio", "Bruno Lima"),
    ("Desenho de montagem - painel de acesso", "desenho", "Carla Mendes"),
    ("Procedimento de inspeção não destrutiva", "procedimento", "Diego Alves"),
    ("Manual de manutenção - trem de pouso", "manual", "Elisa Rocha"),
    ("Especificação de selantes - tanque integral", "especificacao", "Ana Souza"),
    ("Relatório de ensaio de tração - liga 7075", "relatorio_ensaio", "Bruno Lima"),
    ("Procedimento de controle de configuração", "procedimento", "Carla Mendes"),
]

TEXTO = (
    "Este documento define requisitos, critérios de aceitação e responsabilidades "
    "para a execução da atividade descrita no título. Inclui referências normativas, "
    "tolerâncias dimensionais, registros obrigatórios e o fluxo de aprovação pela "
    "engenharia de qualidade antes da liberação para produção."
)


class Command(BaseCommand):
    help = "Popula o Redis com documentos técnicos de exemplo para a demonstração."

    def add_arguments(self, parser):
        parser.add_argument("--limpar", action="store_true", help="Apaga o banco atual antes (FLUSHDB).")

    def handle(self, *args, **opts):
        if opts["limpar"]:
            get_redis().flushdb()
            self.stdout.write(self.style.WARNING("Banco Redis limpo."))

        random.seed(42)
        ids = []
        for titulo, tipo, autor in DOCUMENTOS:
            doc = repo.criar({"titulo": titulo, "tipo": tipo, "autor": autor, "conteudo": TEXTO})
            ids.append(doc["id"])

        # Parte vai para a fila, parte é validada, o resto fica em rascunho.
        for doc_id in ids[:6]:
            repo.submeter(doc_id)
        repo.validar(ids[0], True, "Conforme norma interna")
        repo.validar(ids[1], False, "Faltam os registros de calibração")
        repo.validar(ids[2], True, "")

        # Simula acessos para o ranking.
        for doc_id in ids:
            for _ in range(random.randint(1, 15)):
                repo.obter(doc_id)

        self.stdout.write(self.style.SUCCESS(f"{len(ids)} documentos criados."))
        self.stdout.write(str(repo.estatisticas()))
