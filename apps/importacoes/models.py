from datetime import time

from django.conf import settings
from django.db import models


class ConfiguracaoBackup(models.Model):
    ativo = models.BooleanField(
        "Backup automático ativo",
        default=True,
    )
    horario_diario = models.TimeField(
        "Horário diário",
        default=time(2, 0),
        help_text="Horário local de Mato Grosso para execução diária.",
    )
    timezone = models.CharField(
        "Fuso horário",
        max_length=64,
        default="America/Cuiaba",
    )
    atualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="configuracoes_backup_atualizadas",
    )
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Configuração de Backup"
        verbose_name_plural = "Configurações de Backup"

    def __str__(self):
        status = "Ativo" if self.ativo else "Inativo"
        return f"Backup diário {status} às {self.horario_diario:%H:%M}"

    @classmethod
    def obter(cls):
        objeto, _ = cls.objects.get_or_create(
            pk=1,
            defaults={
                "ativo": True,
                "horario_diario": time(2, 0),
                "timezone": "America/Cuiaba",
            },
        )
        return objeto


class HistoricoBackup(models.Model):
    TIPO_MANUAL = "manual"
    TIPO_AUTOMATICO = "automatico"
    TIPO_CHOICES = [
        (TIPO_MANUAL, "Manual"),
        (TIPO_AUTOMATICO, "Automático"),
    ]

    STATUS_CONCLUIDO = "concluido"
    STATUS_ERRO = "erro"
    STATUS_CHOICES = [
        (STATUS_CONCLUIDO, "Concluído"),
        (STATUS_ERRO, "Erro"),
    ]

    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)

    iniciado_em = models.DateTimeField()
    concluido_em = models.DateTimeField(null=True, blank=True)

    nome_arquivo = models.CharField(max_length=255, blank=True)
    tamanho_bytes = models.BigIntegerField(default=0)

    drive_file_id = models.CharField(max_length=255, blank=True)
    drive_web_view_link = models.URLField(max_length=1000, blank=True)

    mensagem_erro = models.TextField(blank=True)

    solicitado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="backups_solicitados",
    )

    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Histórico de Backup"
        verbose_name_plural = "Históricos de Backup"
        ordering = ["-iniciado_em", "-id"]
        indexes = [
            models.Index(fields=["-iniciado_em"]),
            models.Index(fields=["tipo", "status"]),
        ]

    def __str__(self):
        return f"{self.get_tipo_display()} - {self.get_status_display()} - {self.iniciado_em:%d/%m/%Y %H:%M}"

    @property
    def tamanho_formatado(self):
        tamanho = int(self.tamanho_bytes or 0)
        unidades = ["B", "KB", "MB", "GB", "TB"]
        valor = float(tamanho)
        indice = 0

        while valor >= 1024 and indice < len(unidades) - 1:
            valor /= 1024
            indice += 1

        if indice == 0:
            return f"{int(valor)} {unidades[indice]}"
        return f"{valor:.2f} {unidades[indice]}".replace(".", ",")
