from django.conf import settings
from django.db import models

from apps.cadastros.models import Fornecedor
from apps.pregoes.models import Pregao, PregaoItem


class BeneficioMEEPP(models.Model):
    STATUS_PENDENTE = "pendente"
    STATUS_EXERCIDO = "exercido"
    STATUS_RECUSADO = "recusado"
    STATUS_ENCERRADO = "encerrado"

    STATUS_CHOICES = [
        (STATUS_PENDENTE, "Pendente"),
        (STATUS_EXERCIDO, "Exercido"),
        (STATUS_RECUSADO, "Não exercido"),
        (STATUS_ENCERRADO, "Encerrado"),
    ]

    pregao = models.ForeignKey(
        Pregao,
        on_delete=models.CASCADE,
        related_name="beneficios_me_epp",
        verbose_name="Pregão",
    )
    pregao_item = models.ForeignKey(
        PregaoItem,
        on_delete=models.CASCADE,
        related_name="beneficios_me_epp",
        verbose_name="Item do Pregão",
    )
    fornecedor = models.ForeignKey(
        Fornecedor,
        on_delete=models.PROTECT,
        related_name="beneficios_me_epp",
        verbose_name="Fornecedor ME/EPP",
    )
    ordem_convocacao = models.PositiveIntegerField("Ordem de Convocação", default=1)
    valor_referencia = models.DecimalField("Melhor Valor de Referência", max_digits=12, decimal_places=4)
    valor_original_me_epp = models.DecimalField("Valor Original da ME/EPP", max_digits=12, decimal_places=4)
    percentual_margem = models.DecimalField("Percentual da Margem", max_digits=5, decimal_places=2, default=5)
    nova_oferta = models.DecimalField("Nova Oferta da ME/EPP", max_digits=12, decimal_places=4, null=True, blank=True)
    status = models.CharField("Status", max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDENTE)
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="beneficios_me_epp_registrados",
        null=True,
        blank=True,
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Benefício ME/EPP"
        verbose_name_plural = "Benefícios ME/EPP"
        ordering = ["pregao_item", "ordem_convocacao", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["pregao_item", "fornecedor"],
                name="unique_beneficio_me_epp_item_fornecedor",
            )
        ]

    def __str__(self):
        return f"{self.pregao_item} - {self.fornecedor.razao_social} ({self.get_status_display()})"
