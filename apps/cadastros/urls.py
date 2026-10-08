from django.urls import path
from . import views

app_name = "cadastros"

urlpatterns = [

    path("unidades-medida/", views.unidades_medida, name="unidades_medida"),
    path("unidades-medida/<int:unidade_id>/editar/", views.editar_unidade_medida, name="editar_unidade_medida"),
    path("unidades-medida/<int:unidade_id>/alternar-status/", views.alternar_status_unidade_medida, name="alternar_status_unidade_medida"),
    path("municipios/", views.municipios, name="municipios"),
    path("municipios/<int:municipio_id>/editar/", views.editar_municipio, name="editar_municipio"),
    path("municipios/<int:municipio_id>/alternar-status/", views.alternar_status_municipio, name="alternar_status_municipio"),

    path("fornecedores/", views.fornecedores, name="fornecedores"),
    path("fornecedores/consultar-cnpj/", views.consultar_cnpj_fornecedor, name="consultar_cnpj_fornecedor"),
    path("fornecedores/<int:fornecedor_id>/editar/", views.editar_fornecedor, name="editar_fornecedor"),
    path("fornecedores/<int:fornecedor_id>/alternar-status/", views.alternar_status_fornecedor, name="alternar_status_fornecedor"),

    path("escolas/", views.escolas, name="escolas"),
    path("escolas/<int:escola_id>/editar/", views.editar_escola, name="editar_escola"),
    path("escolas/<int:escola_id>/alternar-status/", views.alternar_status_escola, name="alternar_status_escola"),

    path("itens/", views.itens, name="itens"),
    path("itens/<int:item_id>/editar/", views.editar_item, name="editar_item"),
    path("itens/<int:item_id>/alternar-status/", views.alternar_status_item, name="alternar_status_item"),
]