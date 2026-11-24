from django.urls import path

from . import views

urlpatterns = [
    path("health/", views.health),
    path("documentos/", views.documentos),
    path("documentos/<int:doc_id>/", views.documento),
    path("documentos/<int:doc_id>/submeter/", views.submeter),
    path("documentos/<int:doc_id>/validar/", views.validar),
    path("documentos/<int:doc_id>/historico/", views.historico),
    path("documentos/<int:doc_id>/resumo/", views.resumo),
    path("fila/", views.fila),
    path("fila/proximo/", views.fila_proximo),
    path("ranking/", views.ranking),
    path("estatisticas/", views.estatisticas),
]
