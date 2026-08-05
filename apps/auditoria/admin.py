from django.contrib import admin

from .models import RegistroAuditoria


@admin.register(RegistroAuditoria)
class RegistroAuditoriaAdmin(admin.ModelAdmin):
    list_display = (
        "criado_em",
        "usuario_username",
        "acao",
        "modulo",
        "descricao",
        "ip",
    )
    list_filter = ("acao", "modulo", "criado_em")
    search_fields = (
        "usuario_username",
        "usuario_nome",
        "descricao",
        "objeto_repr",
        "caminho",
        "view_name",
        "ip",
    )
    readonly_fields = [field.name for field in RegistroAuditoria._meta.fields]
    ordering = ("-criado_em",)
