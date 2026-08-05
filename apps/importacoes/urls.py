from django.urls import path

from . import views

app_name = "importacoes"

urlpatterns = [
    path("fornecedores/", views.importar_fornecedores, name="fornecedores"),
    path("fornecedores/modelo/", views.baixar_modelo_fornecedores, name="modelo_fornecedores"),
    path("escolas/", views.importar_escolas, name="escolas"),
    path("escolas/modelo/", views.baixar_modelo_escolas, name="modelo_escolas"),
    path("itens/", views.importar_itens, name="itens"),
    path("itens/modelo/", views.baixar_modelo_itens, name="modelo_itens"),
    path("quantitativo-pregao/", views.importar_quantitativo_pregao, name="quantitativo_pregao"),
    path("quantitativo-pregao/modelo/", views.baixar_modelo_quantitativo_pregao, name="modelo_quantitativo_pregao"),
    path("quantitativo-escola/", views.importar_quantitativo_escola, name="quantitativo_escola"),
    path("quantitativo-escola/modelo/", views.baixar_modelo_quantitativo_escola, name="modelo_quantitativo_escola"),
    path("exportacoes/", views.exportacoes, name="exportacoes"),
    path("exportacoes/backup-completo/", views.baixar_backup_completo, name="backup_completo"),
]
