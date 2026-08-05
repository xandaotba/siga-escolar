from django.contrib import admin

from .models import (
    AquisicaoNotaFiscal,
    AquisicaoNotaFiscalItem,
    ProdutoNotaFiscalMapeamento,
)


class AquisicaoNotaFiscalItemInline(admin.TabularInline):
    model = AquisicaoNotaFiscalItem
    extra = 0
    autocomplete_fields = ["item", "contrato_item"]


@admin.register(AquisicaoNotaFiscal)
class AquisicaoNotaFiscalAdmin(admin.ModelAdmin):
    list_display = [
        "chave_acesso",
        "numero_nota",
        "pregao",
        "escola",
        "fornecedor",
        "metodo_entrada",
        "status",
        "valor_total",
        "data_recebimento",
    ]
    list_filter = ["status", "metodo_entrada", "pregao", "data_recebimento"]
    search_fields = [
        "chave_acesso",
        "numero_nota",
        "escola__nome_escola",
        "fornecedor__razao_social",
        "fornecedor__cnpj",
    ]
    autocomplete_fields = ["pregao", "escola", "fornecedor", "contrato", "criado_por", "confirmado_por"]
    inlines = [AquisicaoNotaFiscalItemInline]


@admin.register(AquisicaoNotaFiscalItem)
class AquisicaoNotaFiscalItemAdmin(admin.ModelAdmin):
    list_display = [
        "nota",
        "descricao_produto",
        "item",
        "quantidade",
        "unidade",
        "valor_total",
        "status",
        "conferido",
    ]
    list_filter = ["status", "conferido"]
    search_fields = ["descricao_produto", "item__nome_item", "nota__chave_acesso", "nota__numero_nota"]
    autocomplete_fields = ["nota", "item", "contrato_item"]


@admin.register(ProdutoNotaFiscalMapeamento)
class ProdutoNotaFiscalMapeamentoAdmin(admin.ModelAdmin):
    list_display = ["fornecedor", "descricao_nota", "unidade_nota", "item", "ativo"]
    list_filter = ["ativo", "fornecedor"]
    search_fields = ["descricao_nota", "item__nome_item", "fornecedor__razao_social"]
    autocomplete_fields = ["fornecedor", "item"]
