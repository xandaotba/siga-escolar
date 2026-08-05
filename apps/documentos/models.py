from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError

from django.core.validators import MinValueValidator
from django.db import models

from apps.cadastros.models import Escola, Fornecedor, Item
from apps.pregoes.models import Pregao, PregaoItem, QuantitativoEscola


class ContratoGerado(models.Model):
    STATUS_PREVIO = "previo"
    STATUS_GERADO = "gerado"
    STATUS_CANCELADO = "cancelado"
    STATUS_DISTRATADO = "distratado"
    STATUS_PARCIALMENTE_DISTRATADO = "parcialmente_distratado"

    STATUS_CHOICES = [
        (STATUS_PREVIO, "Prévio"),
        (STATUS_GERADO, "Gerado"),
        (STATUS_CANCELADO, "Cancelado"),
        (STATUS_DISTRATADO, "Distratado"),
        (STATUS_PARCIALMENTE_DISTRATADO, "Parcialmente Distratado"),
    ]

    pregao = models.ForeignKey(
        Pregao,
        on_delete=models.PROTECT,
        related_name="contratos_gerados",
        verbose_name="Pregão",
    )

    escola = models.ForeignKey(
        Escola,
        on_delete=models.PROTECT,
        related_name="contratos_gerados",
        verbose_name="Escola",
    )

    fornecedor = models.ForeignKey(
        Fornecedor,
        on_delete=models.PROTECT,
        related_name="contratos_gerados",
        verbose_name="Fornecedor",
    )

    numero_contrato = models.CharField(
        "Número do Contrato",
        max_length=50,
        blank=True,
    )

    numero_sequencial = models.PositiveIntegerField(
        "Número Sequencial",
        null=True,
        blank=True,
        help_text="Sequencial anual do contrato dentro da escola.",
    )

    ano_contrato = models.PositiveIntegerField(
        "Ano do Contrato",
        null=True,
        blank=True,
        help_text="Ano utilizado para reiniciar a numeração do contrato.",
    )

    valor_total = models.DecimalField(
        "Valor Total",
        max_digits=14,
        decimal_places=2,
        default=Decimal("0"),
    )

    data_distrato = models.DateField(
        "Data do Distrato",
        null=True,
        blank=True,
    )

    motivo_distrato = models.TextField(
        "Motivo do Distrato",
        blank=True,
    )

    status = models.CharField(
        "Status",
        max_length=30,
        choices=STATUS_CHOICES,
        default=STATUS_GERADO,
    )

    criado_em = models.DateTimeField(
        "Criado em",
        auto_now_add=True,
    )

    atualizado_em = models.DateTimeField(
        "Atualizado em",
        auto_now=True,
    )

    class Meta:
        verbose_name = "Contrato Gerado"
        verbose_name_plural = "Contratos Gerados"
        ordering = ["-criado_em"]
        constraints = [
            models.UniqueConstraint(
                fields=["escola", "ano_contrato", "numero_sequencial"],
                name="unico_numero_contrato_por_escola_ano",
            ),
        ]

    def __str__(self):
        return f"{self.pregao} - {self.escola} - {self.fornecedor}"


class ContratoItemGerado(models.Model):
    contrato = models.ForeignKey(
        ContratoGerado,
        on_delete=models.CASCADE,
        related_name="itens",
        verbose_name="Contrato",
    )

    quantitativo_escola = models.ForeignKey(
        QuantitativoEscola,
        on_delete=models.PROTECT,
        related_name="itens_contratados",
        verbose_name="Quantitativo da Escola",
    )

    item = models.ForeignKey(
        Item,
        on_delete=models.PROTECT,
        related_name="itens_contratados",
        verbose_name="Item",
    )

    marca = models.CharField(
        "Marca",
        max_length=150,
        blank=True,
    )

    unidade = models.CharField(
        "Unidade",
        max_length=100,
        blank=True,
    )

    quantidade_contratada = models.DecimalField(
        "Quantidade Contratada",
        max_digits=12,
        decimal_places=3,
    )

    valor_unitario = models.DecimalField(
        "Valor Unitário",
        max_digits=12,
        decimal_places=2,
    )

    valor_total = models.DecimalField(
        "Valor Total",
        max_digits=14,
        decimal_places=2,
    )

    criado_em = models.DateTimeField(
        "Criado em",
        auto_now_add=True,
    )

    class Meta:
        verbose_name = "Item de Contrato Gerado"
        verbose_name_plural = "Itens de Contratos Gerados"
        ordering = ["item__nome_item"]

    def __str__(self):
        return f"{self.contrato} - {self.item}"



class DistratoContrato(models.Model):
    TIPO_TOTAL = "total"
    TIPO_PARCIAL = "parcial"

    TIPO_CHOICES = [
        (TIPO_TOTAL, "Total"),
        (TIPO_PARCIAL, "Parcial"),
    ]

    contrato = models.ForeignKey(
        ContratoGerado,
        on_delete=models.CASCADE,
        related_name="distratos",
        verbose_name="Contrato",
    )

    data_distrato = models.DateField("Data do Distrato")
    motivo_distrato = models.TextField("Motivo do Distrato")
    tipo = models.CharField(
        "Tipo de Distrato",
        max_length=20,
        choices=TIPO_CHOICES,
        default=TIPO_TOTAL,
    )

    valor_total = models.DecimalField(
        "Valor Total Distratado",
        max_digits=14,
        decimal_places=2,
        default=Decimal("0"),
    )

    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    class Meta:
        verbose_name = "Distrato de Contrato"
        verbose_name_plural = "Distratos de Contratos"
        ordering = ["-criado_em"]

    def __str__(self):
        return f"Distrato {self.get_tipo_display()} - {self.contrato}"


class DistratoContratoItem(models.Model):
    distrato = models.ForeignKey(
        DistratoContrato,
        on_delete=models.CASCADE,
        related_name="itens",
        verbose_name="Distrato",
    )

    contrato_item = models.ForeignKey(
        ContratoItemGerado,
        on_delete=models.PROTECT,
        related_name="distratos",
        verbose_name="Item do Contrato",
    )

    quantidade_distratada = models.DecimalField(
        "Quantidade Distratada",
        max_digits=12,
        decimal_places=3,
    )

    valor_unitario = models.DecimalField(
        "Valor Unitário",
        max_digits=12,
        decimal_places=2,
    )

    valor_total = models.DecimalField(
        "Valor Total",
        max_digits=14,
        decimal_places=2,
    )

    criado_em = models.DateTimeField("Criado em", auto_now_add=True)

    class Meta:
        verbose_name = "Item de Distrato"
        verbose_name_plural = "Itens de Distrato"
        ordering = ["contrato_item__item__nome_item"]

    def __str__(self):
        return f"{self.distrato} - {self.contrato_item.item}"



class RealinhamentoPreco(models.Model):
    STATUS_REGISTRADO = "registrado"
    STATUS_CANCELADO = "cancelado"

    STATUS_CHOICES = [
        (STATUS_REGISTRADO, "Registrado"),
        (STATUS_CANCELADO, "Cancelado"),
    ]

    contrato = models.ForeignKey(
        ContratoGerado,
        on_delete=models.PROTECT,
        related_name="realinhamentos",
        verbose_name="Contrato",
    )

    data_realinhamento = models.DateField("Data de Vigência do Realinhamento")
    justificativa = models.TextField("Justificativa do Realinhamento")

    numero_termo_aditivo = models.CharField(
        "Número do Termo Aditivo",
        max_length=50,
        blank=True,
    )

    data_termo_aditivo = models.DateField(
        "Data do Termo Aditivo",
        null=True,
        blank=True,
    )

    observacoes_termo_aditivo = models.TextField(
        "Observações do Termo Aditivo",
        blank=True,
    )

    valor_total_anterior = models.DecimalField(
        "Valor Total Anterior",
        max_digits=14,
        decimal_places=2,
        default=Decimal("0"),
    )

    valor_total_novo = models.DecimalField(
        "Valor Total Novo",
        max_digits=14,
        decimal_places=2,
        default=Decimal("0"),
    )

    diferenca_total = models.DecimalField(
        "Diferença Total",
        max_digits=14,
        decimal_places=2,
        default=Decimal("0"),
    )

    status = models.CharField(
        "Status",
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_REGISTRADO,
    )

    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    class Meta:
        verbose_name = "Realinhamento de Preço"
        verbose_name_plural = "Realinhamentos de Preços"
        ordering = ["-data_realinhamento", "-criado_em"]

    def __str__(self):
        return f"Realinhamento {self.contrato} - {self.data_realinhamento}"


class RealinhamentoPrecoItem(models.Model):
    realinhamento = models.ForeignKey(
        RealinhamentoPreco,
        on_delete=models.CASCADE,
        related_name="itens",
        verbose_name="Realinhamento",
    )

    contrato_item = models.ForeignKey(
        ContratoItemGerado,
        on_delete=models.PROTECT,
        related_name="realinhamentos",
        verbose_name="Item do Contrato",
    )

    quantidade_ativa = models.DecimalField(
        "Quantidade Ativa",
        max_digits=12,
        decimal_places=3,
    )

    valor_unitario_anterior = models.DecimalField(
        "Valor Unitário Anterior",
        max_digits=12,
        decimal_places=2,
    )

    valor_unitario_novo = models.DecimalField(
        "Valor Unitário Novo",
        max_digits=12,
        decimal_places=2,
    )

    valor_total_anterior = models.DecimalField(
        "Valor Total Anterior",
        max_digits=14,
        decimal_places=2,
    )

    valor_total_novo = models.DecimalField(
        "Valor Total Novo",
        max_digits=14,
        decimal_places=2,
    )

    diferenca_valor = models.DecimalField(
        "Diferença de Valor",
        max_digits=14,
        decimal_places=2,
    )

    criado_em = models.DateTimeField("Criado em", auto_now_add=True)

    class Meta:
        verbose_name = "Item de Realinhamento de Preço"
        verbose_name_plural = "Itens de Realinhamento de Preço"
        ordering = ["contrato_item__item__nome_item"]

    def __str__(self):
        return f"{self.realinhamento} - {self.contrato_item.item}"

class TrocaMarca(models.Model):
    STATUS_REGISTRADO = "registrado"
    STATUS_CANCELADO = "cancelado"

    STATUS_CHOICES = [
        (STATUS_REGISTRADO, "Registrado"),
        (STATUS_CANCELADO, "Cancelado"),
    ]

    contrato = models.ForeignKey(
        ContratoGerado,
        on_delete=models.PROTECT,
        related_name="trocas_marca",
        verbose_name="Contrato",
    )

    data_troca_marca = models.DateField("Data de Vigência da Troca de Marca")
    justificativa = models.TextField("Justificativa da Troca de Marca")

    numero_termo_aditivo = models.CharField(
        "Número do Termo Aditivo",
        max_length=50,
        blank=True,
    )

    data_termo_aditivo = models.DateField(
        "Data do Termo Aditivo",
        null=True,
        blank=True,
    )

    observacoes_termo_aditivo = models.TextField(
        "Observações do Termo Aditivo",
        blank=True,
    )

    status = models.CharField(
        "Status",
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_REGISTRADO,
    )

    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    class Meta:
        verbose_name = "Troca de Marca"
        verbose_name_plural = "Trocas de Marca"
        ordering = ["-data_troca_marca", "-criado_em"]

    def __str__(self):
        return f"Troca de Marca {self.contrato} - {self.data_troca_marca}"


class TrocaMarcaItem(models.Model):
    troca_marca = models.ForeignKey(
        TrocaMarca,
        on_delete=models.CASCADE,
        related_name="itens",
        verbose_name="Troca de Marca",
    )

    contrato_item = models.ForeignKey(
        ContratoItemGerado,
        on_delete=models.PROTECT,
        related_name="trocas_marca",
        verbose_name="Item do Contrato",
    )

    quantidade_ativa = models.DecimalField(
        "Quantidade Ativa",
        max_digits=12,
        decimal_places=3,
    )

    marca_anterior = models.CharField(
        "Marca Anterior",
        max_length=150,
        blank=True,
    )

    marca_nova = models.CharField(
        "Nova Marca",
        max_length=150,
    )

    criado_em = models.DateTimeField("Criado em", auto_now_add=True)

    class Meta:
        verbose_name = "Item de Troca de Marca"
        verbose_name_plural = "Itens de Troca de Marca"
        ordering = ["contrato_item__item__nome_item"]

    def __str__(self):
        return f"{self.troca_marca} - {self.contrato_item.item}"


class ProjetoVenda(models.Model):
    STATUS_REGISTRADO = "registrado"
    STATUS_CANCELADO = "cancelado"

    STATUS_CHOICES = [
        (STATUS_REGISTRADO, "Registrado"),
        (STATUS_CANCELADO, "Cancelado"),
    ]

    pregao = models.ForeignKey(
        Pregao,
        on_delete=models.PROTECT,
        related_name="projetos_venda",
        verbose_name="Chamada Pública",
    )

    fornecedor = models.ForeignKey(
        Fornecedor,
        on_delete=models.PROTECT,
        related_name="projetos_venda",
        verbose_name="Fornecedor",
    )

    data_entrega = models.DateField("Data de Entrega do Projeto")
    cronograma_entrega = models.CharField(
        "Cronograma de Entrega",
        max_length=255,
        blank=True,
        help_text="Ex.: semanal, quinzenal, mensal ou conforme solicitação da escola.",
    )
    observacoes = models.TextField("Observações", blank=True)

    valor_total = models.DecimalField(
        "Valor Total do Projeto",
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    status = models.CharField(
        "Status",
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_REGISTRADO,
    )

    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="projetos_venda_registrados",
        verbose_name="Registrado por",
        null=True,
        blank=True,
    )

    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    class Meta:
        verbose_name = "Projeto de Venda"
        verbose_name_plural = "Projetos de Venda"
        ordering = ["-data_entrega", "-criado_em"]

    def clean(self):
        if self.pregao and self.pregao.tipo_certame != Pregao.TIPO_CHAMADA_PUBLICA:
            raise ValidationError("Projeto de venda só pode ser registrado para Chamada Pública.")

        if self.fornecedor and not self.fornecedor.eh_fornecedor_chamada_publica:
            raise ValidationError("O fornecedor precisa ser Fornecedor Individual ou Grupo Formal/Cooperativa.")

    @property
    def limite_anual_fornecedor(self):
        if not self.fornecedor:
            return Decimal("0.00")
        return self.fornecedor.limite_anual_chamada_publica

    @property
    def saldo_limite_fornecedor(self):
        limite = self.limite_anual_fornecedor or Decimal("0.00")
        return limite - (self.valor_total or Decimal("0.00"))

    def atualizar_valor_total(self):
        total = self.itens.aggregate(total=models.Sum("valor_total")).get("total") or Decimal("0.00")
        self.valor_total = total
        self.save(update_fields=["valor_total", "atualizado_em"])
        return total

    def __str__(self):
        return f"{self.pregao} - {self.fornecedor} - R$ {self.valor_total}"


class ProjetoVendaItem(models.Model):
    projeto = models.ForeignKey(
        ProjetoVenda,
        on_delete=models.CASCADE,
        related_name="itens",
        verbose_name="Projeto de Venda",
    )

    pregao_item = models.ForeignKey(
        PregaoItem,
        on_delete=models.PROTECT,
        related_name="projetos_venda_itens",
        verbose_name="Item da Chamada Pública",
    )

    item = models.ForeignKey(
        Item,
        on_delete=models.PROTECT,
        related_name="projetos_venda_itens",
        verbose_name="Produto",
    )

    marca = models.CharField(
        "Marca / Descrição",
        max_length=150,
        blank=True,
        help_text="Marca ou descrição apresentada pelo fornecedor para o produto.",
    )

    quantidade_ofertada = models.DecimalField(
        "Quantidade Ofertada",
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )

    valor_unitario = models.DecimalField(
        "Valor Unitário",
        max_digits=12,
        decimal_places=2,
    )

    valor_total = models.DecimalField(
        "Valor Total",
        max_digits=14,
        decimal_places=2,
    )

    cronograma_entrega = models.CharField(
        "Cronograma de Entrega",
        max_length=255,
        blank=True,
    )

    criado_em = models.DateTimeField("Criado em", auto_now_add=True)

    class Meta:
        verbose_name = "Item do Projeto de Venda"
        verbose_name_plural = "Itens do Projeto de Venda"
        ordering = ["pregao_item__ordem", "item__nome_item"]
        constraints = [
            models.UniqueConstraint(
                fields=["projeto", "pregao_item"],
                name="unique_item_por_projeto_venda",
            )
        ]

    def clean(self):
        if self.projeto and self.pregao_item:
            if self.pregao_item.pregao_id != self.projeto.pregao_id:
                raise ValidationError("O item informado não pertence à chamada pública do projeto.")

        if self.item and self.pregao_item and self.item_id != self.pregao_item.item_id:
            raise ValidationError("O produto informado não corresponde ao item da chamada pública.")

        if self.quantidade_ofertada and self.quantidade_ofertada <= 0:
            raise ValidationError("A quantidade ofertada deve ser maior que zero.")

        if self.valor_unitario and self.valor_unitario <= 0:
            raise ValidationError("O valor unitário deve ser maior que zero.")

    def save(self, *args, **kwargs):
        self.item = self.pregao_item.item

        if self.valor_unitario and self.quantidade_ofertada:
            self.valor_total = self.quantidade_ofertada * self.valor_unitario

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.projeto} - {self.item} - {self.quantidade_ofertada}"


class ResultadoChamadaPublicaItem(models.Model):
    STATUS_REGISTRADO = "registrado"
    STATUS_CANCELADO = "cancelado"

    STATUS_CHOICES = [
        (STATUS_REGISTRADO, "Registrado"),
        (STATUS_CANCELADO, "Cancelado"),
    ]

    pregao = models.ForeignKey(
        Pregao,
        on_delete=models.PROTECT,
        related_name="resultados_chamada_publica",
        verbose_name="Chamada Pública",
    )

    projeto = models.ForeignKey(
        ProjetoVenda,
        on_delete=models.PROTECT,
        related_name="resultados",
        verbose_name="Projeto de Venda",
    )

    projeto_item = models.ForeignKey(
        ProjetoVendaItem,
        on_delete=models.PROTECT,
        related_name="resultados",
        verbose_name="Item do Projeto de Venda",
    )

    fornecedor = models.ForeignKey(
        Fornecedor,
        on_delete=models.PROTECT,
        related_name="resultados_chamada_publica",
        verbose_name="Fornecedor",
    )

    pregao_item = models.ForeignKey(
        PregaoItem,
        on_delete=models.PROTECT,
        related_name="resultados_chamada_publica",
        verbose_name="Item da Chamada Pública",
    )

    item = models.ForeignKey(
        Item,
        on_delete=models.PROTECT,
        related_name="resultados_chamada_publica",
        verbose_name="Produto",
    )

    quantidade_adjudicada = models.DecimalField(
        "Quantidade Adjudicada",
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )

    valor_unitario = models.DecimalField(
        "Valor Unitário",
        max_digits=12,
        decimal_places=2,
    )

    valor_total = models.DecimalField(
        "Valor Total",
        max_digits=14,
        decimal_places=2,
    )

    status = models.CharField(
        "Status",
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_REGISTRADO,
    )

    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="resultados_chamada_publica_registrados",
        verbose_name="Registrado por",
        null=True,
        blank=True,
    )

    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    class Meta:
        verbose_name = "Resultado da Chamada Pública"
        verbose_name_plural = "Resultados da Chamada Pública"
        ordering = ["pregao_item__ordem", "fornecedor__razao_social"]
        constraints = [
            models.UniqueConstraint(
                fields=["projeto_item"],
                name="unique_resultado_por_item_projeto_venda",
            )
        ]

    def clean(self):
        if self.pregao and self.pregao.tipo_certame != Pregao.TIPO_CHAMADA_PUBLICA:
            raise ValidationError("Resultado de Chamada Pública só pode ser registrado para certame do tipo Chamada Pública.")

        if self.projeto and self.projeto.pregao_id != self.pregao_id:
            raise ValidationError("O projeto de venda não pertence à Chamada Pública informada.")

        if self.projeto_item and self.projeto_item.projeto_id != self.projeto_id:
            raise ValidationError("O item informado não pertence ao projeto de venda.")

        if self.projeto_item and self.quantidade_adjudicada:
            if self.quantidade_adjudicada > self.projeto_item.quantidade_ofertada:
                raise ValidationError("A quantidade adjudicada não pode ser maior que a quantidade ofertada no projeto.")

    def save(self, *args, **kwargs):
        self.pregao = self.projeto.pregao
        self.fornecedor = self.projeto.fornecedor
        self.pregao_item = self.projeto_item.pregao_item
        self.item = self.projeto_item.item
        self.valor_unitario = self.projeto_item.valor_unitario

        if self.quantidade_adjudicada and self.valor_unitario:
            self.valor_total = self.quantidade_adjudicada * self.valor_unitario

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.pregao} - {self.item} - {self.fornecedor} - R$ {self.valor_total}"

