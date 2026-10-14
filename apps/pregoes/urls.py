from django.urls import path
from . import views

app_name = "pregoes"

urlpatterns = [
    path("", views.pregoes, name="pregoes"),
    path("<int:pregao_id>/editar/", views.editar_pregao, name="editar_pregao"),
    path(
        "<int:pregao_id>/ordenador-despesas/",
        views.editar_ordenador_despesas,
        name="editar_ordenador_despesas",
    ),

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
        "propostas-iniciais/gerar-link-digital/",
        views.gerar_link_proposta_digital,
        name="gerar_link_proposta_digital",
    ),
    path(
        "propostas-iniciais/localizar-proposta-digital/",
        views.localizar_proposta_digital,
        name="localizar_proposta_digital",
    ),
    path(
        "propostas-iniciais/controle-propostas-digitais/",
        views.controle_propostas_digitais,
        name="controle_propostas_digitais",
    ),
    path(
        "propostas-iniciais/controle-propostas-digitais/<int:proposta_id>/invalidar/",
        views.invalidar_proposta_digital,
        name="invalidar_proposta_digital",
    ),
    path(
        "proposta-digital/<uuid:token>/",
        views.proposta_digital_publica,
        name="proposta_digital_publica",
    ),
    path(
        "proposta-digital/<uuid:token>/consultar-cnpj/",
        views.consultar_cnpj_proposta_digital,
        name="consultar_cnpj_proposta_digital",
    ),
    path(
        "proposta-digital/<uuid:token>/finalizar/",
        views.finalizar_proposta_digital,
        name="finalizar_proposta_digital",
    ),
    path(
    "ajax/itens-por-pregao/<int:pregao_id>/",
    views.itens_por_pregao,
    name="itens_por_pregao",
    ),
]