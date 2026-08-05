from django.conf import settings
from django.db import models

from apps.cadastros.models import Escola


class PerfilUsuario(models.Model):
    PERFIL_ADMINISTRADOR = "administrador"
    PERFIL_PREGOEIRO = "pregoeiro"
    PERFIL_CONSULTA_ESCOLA = "consulta_escola"

    PERFIL_CHOICES = [
        (PERFIL_ADMINISTRADOR, "Administrador"),
        (PERFIL_PREGOEIRO, "Pregoeiro"),
        (PERFIL_CONSULTA_ESCOLA, "Consulta/Escola"),
    ]

    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="perfil_acesso",
        verbose_name="Usuário",
    )

    perfil = models.CharField(
        "Perfil",
        max_length=30,
        choices=PERFIL_CHOICES,
        default=PERFIL_CONSULTA_ESCOLA,
    )

    escola = models.ForeignKey(
        Escola,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="usuarios_consulta",
        verbose_name="Escola vinculada",
        help_text="Use principalmente para usuários do perfil Consulta/Escola.",
    )

    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    class Meta:
        verbose_name = "Perfil de Usuário"
        verbose_name_plural = "Perfis de Usuários"
        ordering = ["usuario__first_name", "usuario__username"]

    def __str__(self):
        return f"{self.usuario.username} - {self.get_perfil_display()}"
