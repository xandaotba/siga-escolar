from django.urls import path

from . import views

app_name = "aquisicoes"

urlpatterns = [
    path("adicionar-manualmente/", views.aquisicao_manual_nova, name="aquisicao_manual_nova"),
    path("upload-xml/", views.upload_xml_nfe, name="upload_xml"),
    path("chave-acesso/", views.chave_acesso_nfe, name="chave_acesso"),
    path("colar-tabela/", views.colar_tabela_nfe, name="colar_tabela"),
    path("relatorios/saldo-aquisicoes/", views.relatorio_saldo_aquisicoes, name="relatorio_saldo_aquisicoes"),
    path("ajax/contratos/<int:contrato_id>/itens/", views.ajax_itens_contrato, name="ajax_itens_contrato"),
    path("ajax/certames/<int:pregao_id>/escolas/", views.ajax_escolas_por_certame, name="ajax_escolas_por_certame"),
    path("ajax/certames/<int:pregao_id>/escolas/<int:escola_id>/fornecedores/", views.ajax_fornecedores_por_certame_escola, name="ajax_fornecedores_por_certame_escola"),
    path("ajax/certames/<int:pregao_id>/escolas/<int:escola_id>/fornecedores/<int:fornecedor_id>/contratos/", views.ajax_contratos_por_certame_escola_fornecedor, name="ajax_contratos_por_certame_escola_fornecedor"),
    path("", views.notas_fiscais, name="notas_fiscais"),
    path("notas-fiscais/", views.notas_fiscais, name="notas_fiscais"),
    path("notas-fiscais/nova/", views.nota_fiscal_nova, name="nota_fiscal_nova"),
    path("notas-fiscais/<int:nota_id>/", views.nota_fiscal_detalhe, name="nota_fiscal_detalhe"),
    path("notas-fiscais/<int:nota_id>/editar/", views.nota_fiscal_editar, name="nota_fiscal_editar"),
    path("notas-fiscais/<int:nota_id>/confirmar/", views.nota_fiscal_confirmar, name="nota_fiscal_confirmar"),
    path("notas-fiscais/<int:nota_id>/cancelar/", views.nota_fiscal_cancelar, name="nota_fiscal_cancelar"),
]
