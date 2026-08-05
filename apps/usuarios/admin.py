from django.contrib import admin

from .models import PerfilUsuario


@admin.register(PerfilUsuario)
class PerfilUsuarioAdmin(admin.ModelAdmin):
    list_display = ["usuario", "perfil", "escola", "criado_em"]
    list_filter = ["perfil", "escola"]
    search_fields = ["usuario__username", "usuario__first_name", "usuario__last_name", "usuario__email"]
