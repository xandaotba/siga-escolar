from django.contrib import admin

from .models import ContratoGerado, ContratoItemGerado


class ContratoItemGeradoInline(admin.TabularInline):
    model = ContratoItemGerado
    extra = 0
    readonly_fields = (
        "quantitativo_escola",
        "item",
        "marca",
        "unidade",
        "quantidade_contratada",
        "valor_unitario",
        "valor_total",
    )


@admin.register(ContratoGerado)
class ContratoGeradoAdmin(admin.ModelAdmin):
    list_display = (
        "pregao",
        "escola",
        "fornecedor",
        "numero_contrato",
        "valor_total",
        "status",
        "criado_em",
    )
    search_fields = (
        "pregao__numero",
        "escola__nome_escola",
        "fornecedor__razao_social",
        "numero_contrato",
    )
    list_filter = ("pregao", "status", "criado_em")
    inlines = [ContratoItemGeradoInline]


@admin.register(ContratoItemGerado)
class ContratoItemGeradoAdmin(admin.ModelAdmin):
    list_display = (
        "contrato",
        "item",
        "marca",
        "quantidade_contratada",
        "valor_unitario",
        "valor_total",
    )
    search_fields = (
        "contrato__escola__nome_escola",
        "contrato__fornecedor__razao_social",
        "item__nome_item",
        "marca",
    )
    list_filter = ("contrato__pregao", "contrato__fornecedor")