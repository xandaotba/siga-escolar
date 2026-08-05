from django.contrib import admin
from .models import Escola, Fornecedor, Item, Municipio


@admin.register(Municipio)
class MunicipioAdmin(admin.ModelAdmin):
    list_display = ("nome", "uf", "ativo")
    search_fields = ("nome", "uf")
    list_filter = ("uf", "ativo")


@admin.register(Fornecedor)
class FornecedorAdmin(admin.ModelAdmin):
    list_display = ("razao_social", "tipo_fornecedor_chamada", "cnpj", "cpf_fornecedor_individual", "quantidade_caf_dap", "telefone", "email", "ativo")
    search_fields = ("razao_social", "cnpj", "cpf_fornecedor_individual", "representante_legal")
    list_filter = ("tipo_fornecedor_chamada", "ativo",)


@admin.register(Escola)
class EscolaAdmin(admin.ModelAdmin):
    list_display = ("nome_escola", "cnpj", "municipio", "presidente_cdce", "ativo")
    search_fields = ("nome_escola", "cnpj", "presidente_cdce")
    list_filter = ("municipio", "ativo")


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ("nome_item", "unidade_medida", "ativo")
    search_fields = ("nome_item", "descricao")
    list_filter = ("unidade_medida", "ativo")