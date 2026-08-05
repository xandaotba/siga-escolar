from django.urls import path
from . import views

app_name = "execucao"

urlpatterns = [
    path("", views.execucao_pregao, name="execucao_pregao"),
    path("abrir/<int:pregao_id>/", views.abrir_execucao, name="abrir_execucao"),
    path(
        "pregao/<int:pregao_id>/item/<int:item_id>/",
        views.tela_lances,
        name="tela_lances",
    ),
    path(
        "pregao/<int:pregao_id>/proximo-item/",
        views.proximo_item,
        name="proximo_item",
    ),
    path(
        "pregao/<int:pregao_id>/fim/",
        views.fim_pregao,
        name="fim_pregao",
    ),
    path(
        "pregao/<int:pregao_id>/conferencia/",
        views.conferencia_pregao,
        name="conferencia_pregao",
    ),
    path(
        "pregao/<int:pregao_id>/item/<int:item_id>/alterar/",
        views.alterar_item_conferencia,
        name="alterar_item_conferencia",
    ),
    path(
        "pregao/<int:pregao_id>/item/<int:item_id>/lance/<int:lance_id>/alterar/",
        views.alterar_lance_conferencia,
        name="alterar_lance_conferencia",
    ),
    path(
        "pregao/<int:pregao_id>/item/<int:item_id>/lance/<int:lance_id>/excluir/",
        views.excluir_lance_conferencia,
        name="excluir_lance_conferencia",
    ),
    path(
        "pregao/<int:pregao_id>/finalizar/",
        views.finalizar_pregao,
        name="finalizar_pregao",
    ),
    path(
        "pregao/<int:pregao_id>/finalizado/",
        views.pregao_finalizado,
        name="pregao_finalizado",
    ),
]