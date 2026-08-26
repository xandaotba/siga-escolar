from django.urls import path
from . import views

app_name = "pregoes"

urlpatterns = [
    path("", views.pregoes, name="pregoes"),
    path("<int:pregao_id>/editar/", views.editar_pregao, name="editar_pregao"),

    path(
        "<int:pregao_id>/relacao-fornecedores/",
        views.relacao_fornecedores_word,
        name="relacao_fornecedores_word",
    ),

    path("quantitativo-pregao/", views.quantitativo_pregao, name="quantitativo_pregao"),
    path("quantitativo-escola/", views.quantitativo_escola, name="quantitativo_escola"),

    path(
        "relatorio-distribuicao/",
        views.relatorio_distribuicao,
        name="relatorio_distribuicao",
    ),
    path(
    "ajax/escolas-por-pregao/<int:pregao_id>/",
    views.escolas_por_pregao,
    name="escolas_por_pregao",
    ),
    path(
        "media-precos/",
        views.media_precos,
        name="media_precos",
    ),
    path(
    "propostas-iniciais/",
    views.propostas_iniciais,
    name="propostas_iniciais",
    ),
    path(
    "ajax/itens-por-pregao/<int:pregao_id>/",
    views.itens_por_pregao,
    name="itens_por_pregao",
    ),
]