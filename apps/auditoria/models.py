from django.conf import settings
from django.db import models


class RegistroAuditoria(models.Model):
    ACAO_LOGIN = "login"
    ACAO_LOGOUT = "logout"
    ACAO_ACESSO_BLOQUEADO = "acesso_bloqueado"
    ACAO_CRIACAO = "criacao"
    ACAO_EDICAO = "edicao"
    ACAO_EXCLUSAO = "exclusao"
    ACAO_IMPORTACAO = "importacao"
    ACAO_EXPORTACAO = "exportacao"
    ACAO_GERACAO_DOCUMENTO = "geracao_documento"
    ACAO_CANCELAMENTO = "cancelamento"
    ACAO_DISTRATO = "distrato"
    ACAO_REALINHAMENTO = "realinhamento"
    ACAO_TROCA_MARCA = "troca_marca"
    ACAO_FINALIZACAO = "finalizacao"
    ACAO_POST = "post"

    ACAO_CHOICES = [
        (ACAO_LOGIN, "Login"),
        (ACAO_LOGOUT, "Logout"),
        (ACAO_ACESSO_BLOQUEADO, "Acesso bloqueado"),
        (ACAO_CRIACAO, "Criação"),
        (ACAO_EDICAO, "Edição"),
        (ACAO_EXCLUSAO, "Exclusão"),
        (ACAO_IMPORTACAO, "Importação"),
        (ACAO_EXPORTACAO, "Exportação"),
        (ACAO_GERACAO_DOCUMENTO, "Geração de documento"),
        (ACAO_CANCELAMENTO, "Cancelamento"),
        (ACAO_DISTRATO, "Distrato"),
        (ACAO_REALINHAMENTO, "Realinhamento de preço"),
        (ACAO_TROCA_MARCA, "Troca de marca"),
        (ACAO_FINALIZACAO, "Finalização"),
        (ACAO_POST, "Alteração"),
    ]

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="registros_auditoria",
        verbose_name="Usuário",
    )
    usuario_nome = models.CharField("Nome do usuário", max_length=255, blank=True)
    usuario_username = models.CharField("Login do usuário", max_length=150, blank=True)

    acao = models.CharField("Ação", max_length=40, choices=ACAO_CHOICES)
    modulo = models.CharField("Módulo", max_length=100, blank=True)
    descricao = models.TextField("Descrição")
    objeto_tipo = models.CharField("Tipo do objeto", max_length=120, blank=True)
    objeto_id = models.CharField("ID do objeto", max_length=80, blank=True)
    objeto_repr = models.CharField("Objeto", max_length=255, blank=True)

    metodo = models.CharField("Método HTTP", max_length=10, blank=True)
    caminho = models.CharField("Caminho", max_length=500, blank=True)
    view_name = models.CharField("View", max_length=180, blank=True)

    ip = models.GenericIPAddressField("IP", null=True, blank=True)
    user_agent = models.TextField("Navegador/Dispositivo", blank=True)

    dados = models.JSONField("Dados extras", default=dict, blank=True)

    criado_em = models.DateTimeField("Data/Hora", auto_now_add=True)

    class Meta:
        verbose_name = "Registro de Auditoria"
        verbose_name_plural = "Registros de Auditoria"
        ordering = ["-criado_em"]
        indexes = [
            models.Index(fields=["-criado_em"]),
            models.Index(fields=["acao"]),
            models.Index(fields=["modulo"]),
            models.Index(fields=["usuario"]),
        ]

    def __str__(self):
        usuario = self.usuario_username or self.usuario_nome or "Sistema"
        return f"{self.get_acao_display()} - {usuario} - {self.criado_em:%d/%m/%Y %H:%M}"
