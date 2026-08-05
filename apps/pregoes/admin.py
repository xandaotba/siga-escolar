from django.contrib import admin

from .models import (
    Pregao,
    PregaoMunicipio,
    PregaoFornecedor,
    QuantitativoPregao,
    QuantitativoEscola,
    PregaoItem,
    Lance,
    DesistenciaItem,
    ResultadoItem,
    PropostaInicialItem,
)


class PregaoMunicipioInline(admin.TabularInline):
    model = PregaoMunicipio
    extra = 1


class PregaoFornecedorInline(admin.TabularInline):
    model = PregaoFornecedor
    extra = 1


@admin.register(Pregao)
class PregaoAdmin(admin.ModelAdmin):
    list_display = (
        "tipo_certame",
        "numero",
        "ano",
        "nome_pregoeiro",
        "data_pregao",
        "status",
    )
    search_fields = ("numero", "ano", "nome_pregoeiro")
    list_filter = ("tipo_certame", "ano", "status", "data_pregao")
    inlines = [PregaoMunicipioInline, PregaoFornecedorInline]

    def save_model(self, request, obj, form, change):
        if not obj.criado_por:
            obj.criado_por = request.user
        super().save_model(request, obj, form, change)


@admin.register(QuantitativoPregao)
class QuantitativoPregaoAdmin(admin.ModelAdmin):
    list_display = ("pregao", "item", "quantidade")
    search_fields = ("pregao__numero", "item__nome_item")
    list_filter = ("pregao", "item")


@admin.register(QuantitativoEscola)
class QuantitativoEscolaAdmin(admin.ModelAdmin):
    list_display = ("pregao", "escola", "item", "quantidade")
    search_fields = (
        "pregao__numero",
        "escola__nome_escola",
        "item__nome_item",
    )
    list_filter = ("pregao", "escola", "item")


@admin.register(PregaoItem)
class PregaoItemAdmin(admin.ModelAdmin):
    list_display = (
        "pregao",
        "ordem",
        "item",
        "quantidade_total",
        "status",
        "fornecedor_atual",
        "rodada_atual",
        "fornecedor_vencedor",
        "menor_lance",
    )
    search_fields = ("pregao__numero", "item__nome_item")
    list_filter = ("pregao", "status")


@admin.register(Lance)
class LanceAdmin(admin.ModelAdmin):
    list_display = (
        "pregao",
        "pregao_item",
        "fornecedor",
        "valor_lance",
        "ordem_lance",
        "criado_em",
    )
    search_fields = (
        "pregao__numero",
        "pregao_item__item__nome_item",
        "fornecedor__razao_social",
    )
    list_filter = ("pregao", "pregao_item", "fornecedor")

    def save_model(self, request, obj, form, change):
        if not obj.registrado_por:
            obj.registrado_por = request.user
        super().save_model(request, obj, form, change)


@admin.register(DesistenciaItem)
class DesistenciaItemAdmin(admin.ModelAdmin):
    list_display = (
        "pregao",
        "pregao_item",
        "fornecedor",
        "criado_em",
    )
    search_fields = (
        "pregao__numero",
        "pregao_item__item__nome_item",
        "fornecedor__razao_social",
    )
    list_filter = ("pregao", "pregao_item", "fornecedor")

    def save_model(self, request, obj, form, change):
        if not obj.registrado_por:
            obj.registrado_por = request.user
        super().save_model(request, obj, form, change)


@admin.register(ResultadoItem)
class ResultadoItemAdmin(admin.ModelAdmin):
    list_display = (
        "pregao",
        "pregao_item",
        "status_resultado",
        "primeiro_fornecedor",
        "primeiro_valor",
        "segundo_fornecedor",
        "segundo_valor",
        "terceiro_fornecedor",
        "terceiro_valor",
    )
    search_fields = (
        "pregao__numero",
        "pregao_item__item__nome_item",
        "primeiro_fornecedor__razao_social",
    )
    list_filter = ("pregao", "status_resultado")

@admin.register(PropostaInicialItem)
class PropostaInicialItemAdmin(admin.ModelAdmin):
    list_display = (
        "pregao",
        "pregao_item",
        "fornecedor",
        "marca",
        "preco_inicial",
    )
    search_fields = (
        "pregao__numero",
        "pregao_item__item__nome_item",
        "fornecedor__razao_social",
        "marca",
    )
    list_filter = ("pregao", "pregao_item", "fornecedor")