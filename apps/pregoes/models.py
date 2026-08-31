from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from apps.cadastros.models import Fornecedor, Escola, Item, Municipio, validar_cpf


class Pregao(models.Model):
    TIPO_PREGAO_PRESENCIAL = "pregao_presencial"
    TIPO_CHAMADA_PUBLICA = "chamada_publica"

    TIPO_CERTAME_CHOICES = [
        (TIPO_PREGAO_PRESENCIAL, "Pregão Presencial"),
        (TIPO_CHAMADA_PUBLICA, "Chamada Pública"),
    ]

    STATUS_NAO_INICIADO = "nao_iniciado"
    STATUS_EM_ANDAMENTO = "em_andamento"
    STATUS_FINALIZADO = "finalizado"

    STATUS_CHOICES = [
        (STATUS_NAO_INICIADO, "Não iniciado"),
        (STATUS_EM_ANDAMENTO, "Em andamento"),
        (STATUS_FINALIZADO, "Finalizado"),
    ]

    tipo_certame = models.CharField(
        "Tipo de Certame",
        max_length=30,
        choices=TIPO_CERTAME_CHOICES,
        default=TIPO_PREGAO_PRESENCIAL,
        help_text="Define se o processo será executado como Pregão Presencial ou Chamada Pública.",
    )

    numero = models.CharField("Número do Certame", max_length=30)
    numero_processo = models.CharField(
        "Número do Processo",
        max_length=100,
        blank=True,
        help_text="Número do processo administrativo vinculado ao certame.",
    )
    ano = models.PositiveIntegerField("Ano")

    municipios = models.ManyToManyField(
        Municipio,
        through="PregaoMunicipio",
        related_name="pregoes",
        verbose_name="Municípios",
    )

    fornecedores = models.ManyToManyField(
        Fornecedor,
        through="PregaoFornecedor",
        related_name="pregoes",
        verbose_name="Fornecedores",
    )

    nome_pregoeiro = models.CharField("Nome do Pregoeiro", max_length=255)
    cpf_pregoeiro = models.CharField(
        "CPF do Pregoeiro",
        max_length=14,
        validators=[validar_cpf],
    )

    nome_ordenador_despesas = models.CharField(
        "Nome do Ordenador de Despesas",
        max_length=255,
        blank=True,
        help_text="Nome completo do ordenador de despesas vinculado ao certame.",
    )
    rg_ordenador_despesas = models.CharField(
        "RG do Ordenador de Despesas",
        max_length=50,
        blank=True,
    )
    cpf_ordenador_despesas = models.CharField(
        "CPF do Ordenador de Despesas",
        max_length=14,
        blank=True,
        validators=[validar_cpf],
    )

    local_pregao = models.CharField("Local do Pregão", max_length=255)
    data_pregao = models.DateField("Data do Pregão")

    status = models.CharField(
        "Status",
        max_length=30,
        choices=STATUS_CHOICES,
        default=STATUS_NAO_INICIADO,
    )

    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="pregoes_criados",
        verbose_name="Criado por",
        null=True,
        blank=True,
    )

    finalizado_em = models.DateTimeField("Finalizado em", null=True, blank=True)

    percentual_alerta_media = models.DecimalField(
        "Percentual de Alerta da Média",
        max_digits=5,
        decimal_places=2,
        default=50,
        help_text="Percentual de alerta aplicado a todos os itens deste certame.",
    )

    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    class Meta:
        verbose_name = "Certame"
        verbose_name_plural = "Certames"
        ordering = ["-ano", "-numero"]
        constraints = [
            models.UniqueConstraint(
                fields=["tipo_certame", "numero", "ano"],
                name="unique_tipo_numero_ano_certame",
            )
        ]

    def __str__(self):
        return f"{self.get_tipo_certame_display()} {self.numero}/{self.ano}"

    @property
    def eh_pregao_presencial(self):
        return self.tipo_certame == self.TIPO_PREGAO_PRESENCIAL

    @property
    def eh_chamada_publica(self):
        return self.tipo_certame == self.TIPO_CHAMADA_PUBLICA


class PregaoMunicipio(models.Model):
    pregao = models.ForeignKey(
        Pregao,
        on_delete=models.CASCADE,
        verbose_name="Pregão",
    )
    municipio = models.ForeignKey(
        Municipio,
        on_delete=models.PROTECT,
        verbose_name="Município",
    )

    class Meta:
        verbose_name = "Município do Pregão"
        verbose_name_plural = "Municípios do Pregão"
        constraints = [
            models.UniqueConstraint(
                fields=["pregao", "municipio"],
                name="unique_municipio_por_pregao",
            )
        ]

    def __str__(self):
        return f"{self.pregao} - {self.municipio}"


class PregaoFornecedor(models.Model):
    pregao = models.ForeignKey(
        Pregao,
        on_delete=models.CASCADE,
        verbose_name="Pregão",
    )
    fornecedor = models.ForeignKey(
        Fornecedor,
        on_delete=models.PROTECT,
        verbose_name="Fornecedor",
    )
    ordem_inicial = models.PositiveIntegerField(
        "Ordem inicial",
        null=True,
        blank=True,
    )
    ativo_no_pregao = models.BooleanField("Ativo no pregão", default=True)

    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    class Meta:
        verbose_name = "Fornecedor do Pregão"
        verbose_name_plural = "Fornecedores do Pregão"
        ordering = ["ordem_inicial", "fornecedor__razao_social"]
        constraints = [
            models.UniqueConstraint(
                fields=["pregao", "fornecedor"],
                name="unique_fornecedor_por_pregao",
            )
        ]

    def __str__(self):
        return f"{self.pregao} - {self.fornecedor}"


class QuantitativoPregao(models.Model):
    pregao = models.ForeignKey(
        Pregao,
        on_delete=models.CASCADE,
        related_name="quantitativos_pregao",
        verbose_name="Pregão",
    )
    item = models.ForeignKey(
        Item,
        on_delete=models.PROTECT,
        related_name="quantitativos_pregao",
        verbose_name="Item",
    )
    quantidade = models.DecimalField(
        "Quantidade",
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(0.001)],
    )

    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    class Meta:
        verbose_name = "Quantitativo por Pregão"
        verbose_name_plural = "Quantitativos por Pregão"
        ordering = ["item__nome_item"]
        constraints = [
            models.UniqueConstraint(
                fields=["pregao", "item"],
                name="unique_item_por_pregao_quantitativo",
            )
        ]

    def __str__(self):
        return f"{self.pregao} - {self.item} - {self.quantidade}"

    def clean(self):
        if self.pregao and self.pregao.status != Pregao.STATUS_NAO_INICIADO:
            raise ValidationError(
                "Não é permitido alterar quantitativos de pregão já iniciado ou finalizado."
            )


class QuantitativoEscola(models.Model):
    pregao = models.ForeignKey(
        Pregao,
        on_delete=models.CASCADE,
        related_name="quantitativos_escola",
        verbose_name="Pregão",
    )
    escola = models.ForeignKey(
        Escola,
        on_delete=models.PROTECT,
        related_name="quantitativos",
        verbose_name="Escola",
    )
    item = models.ForeignKey(
        Item,
        on_delete=models.PROTECT,
        related_name="quantitativos_escola",
        verbose_name="Item",
    )
    quantidade = models.DecimalField(
        "Quantidade",
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(0.001)],
    )

    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    class Meta:
        verbose_name = "Quantitativo por Escola"
        verbose_name_plural = "Quantitativos por Escola"
        ordering = ["escola__nome_escola", "item__nome_item"]
        constraints = [
            models.UniqueConstraint(
                fields=["pregao", "escola", "item"],
                name="unique_item_por_escola_pregao",
            )
        ]

    def __str__(self):
        return f"{self.pregao} - {self.escola} - {self.item} - {self.quantidade}"

    def clean(self):
        if self.pregao and self.pregao.status != Pregao.STATUS_NAO_INICIADO:
            raise ValidationError(
                "Não é permitido alterar quantitativos de escola em pregão já iniciado ou finalizado."
            )

from django.utils import timezone


class PregaoItem(models.Model):
    STATUS_PENDENTE = "pendente"
    STATUS_EM_DISPUTA = "em_disputa"
    STATUS_ENCERRADO = "encerrado"
    STATUS_DESERTO = "deserto"
    STATUS_FRACASSADO = "fracassado"

    STATUS_CHOICES = [
        (STATUS_PENDENTE, "Pendente"),
        (STATUS_EM_DISPUTA, "Em disputa"),
        (STATUS_ENCERRADO, "Encerrado"),
        (STATUS_DESERTO, "Deserto"),
        (STATUS_FRACASSADO, "Fracassado"),
    ]

    pregao = models.ForeignKey(
        Pregao,
        on_delete=models.CASCADE,
        related_name="itens_execucao",
        verbose_name="Pregão",
    )
    item = models.ForeignKey(
        Item,
        on_delete=models.PROTECT,
        related_name="execucoes",
        verbose_name="Item",
    )
    ordem = models.PositiveIntegerField("Ordem")
    status = models.CharField(
        "Status",
        max_length=30,
        choices=STATUS_CHOICES,
        default=STATUS_PENDENTE,
    )
    quantidade_total = models.DecimalField(
        "Quantidade Total",
        max_digits=12,
        decimal_places=3,
    )

    fornecedor_vencedor = models.ForeignKey(
        Fornecedor,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="itens_vencidos",
        verbose_name="Fornecedor Vencedor",
    )
    fornecedor_atual = models.ForeignKey(
    Fornecedor,
    null=True,
    blank=True,
    on_delete=models.PROTECT,
    related_name="itens_em_rodada",
    verbose_name="Fornecedor Atual",
    )

    rodada_atual = models.PositiveIntegerField(
        "Rodada Atual",
        default=1,
    )
    menor_lance = models.DecimalField(
        "Menor Lance",
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
    )

    media_preco = models.DecimalField(
        "Média de Preço",
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Preço médio/de referência do item neste pregão.",
    )

    percentual_alerta_media = models.DecimalField(
        "Percentual de Alerta da Média (legado)",
        max_digits=5,
        decimal_places=2,
        default=50,
        help_text="Campo legado. O percentual vigente é definido no certame.",
    )

    iniciado_em = models.DateTimeField("Iniciado em", null=True, blank=True)
    encerrado_em = models.DateTimeField("Encerrado em", null=True, blank=True)

    class Meta:
        verbose_name = "Item do Pregão"
        verbose_name_plural = "Itens do Pregão"
        ordering = ["pregao", "ordem"]
        constraints = [
            models.UniqueConstraint(
                fields=["pregao", "item"],
                name="unique_item_execucao_por_pregao",
            ),
            models.UniqueConstraint(
                fields=["pregao", "ordem"],
                name="unique_ordem_item_por_pregao",
            ),
        ]

    def __str__(self):
        return f"{self.pregao} - Item {self.ordem}: {self.item.nome_item}"


class Lance(models.Model):
    pregao = models.ForeignKey(
        Pregao,
        on_delete=models.CASCADE,
        related_name="lances",
        verbose_name="Pregão",
    )
    pregao_item = models.ForeignKey(
        PregaoItem,
        on_delete=models.CASCADE,
        related_name="lances",
        verbose_name="Item do Pregão",
    )
    fornecedor = models.ForeignKey(
        Fornecedor,
        on_delete=models.PROTECT,
        related_name="lances",
        verbose_name="Fornecedor",
    )
    valor_lance = models.DecimalField(
        "Valor do Lance",
        max_digits=12,
        decimal_places=4,
        validators=[MinValueValidator(0.0001)],
    )
    ordem_lance = models.PositiveIntegerField("Ordem do Lance")
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)

    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="lances_registrados",
        verbose_name="Registrado por",
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = "Lance"
        verbose_name_plural = "Lances"
        ordering = ["pregao_item", "ordem_lance"]
        constraints = [
            models.UniqueConstraint(
                fields=["pregao_item", "ordem_lance"],
                name="unique_ordem_lance_por_item",
            )
        ]

    def __str__(self):
        return f"{self.pregao_item} - {self.fornecedor} - R$ {self.valor_lance}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

        menor_lance_item = (
            Lance.objects.filter(pregao_item=self.pregao_item)
            .order_by("valor_lance", "criado_em")
            .first()
        )

        if menor_lance_item:
            self.pregao_item.menor_lance = menor_lance_item.valor_lance
            self.pregao_item.fornecedor_vencedor = menor_lance_item.fornecedor
            self.pregao_item.save(update_fields=["menor_lance", "fornecedor_vencedor"])


class DesistenciaItem(models.Model):
    pregao = models.ForeignKey(
        Pregao,
        on_delete=models.CASCADE,
        related_name="desistencias",
        verbose_name="Pregão",
    )
    pregao_item = models.ForeignKey(
        PregaoItem,
        on_delete=models.CASCADE,
        related_name="desistencias",
        verbose_name="Item do Pregão",
    )
    fornecedor = models.ForeignKey(
        Fornecedor,
        on_delete=models.PROTECT,
        related_name="desistencias",
        verbose_name="Fornecedor",
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)

    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="desistencias_registradas",
        verbose_name="Registrado por",
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = "Desistência do Item"
        verbose_name_plural = "Desistências dos Itens"
        constraints = [
            models.UniqueConstraint(
                fields=["pregao_item", "fornecedor"],
                name="unique_desistencia_fornecedor_item",
            )
        ]

    def __str__(self):
        return f"{self.pregao_item} - {self.fornecedor} desistiu"


class ResultadoItem(models.Model):
    STATUS_ADJUDICADO = "adjudicado"
    STATUS_DESERTO = "deserto"
    STATUS_FRACASSADO = "fracassado"

    STATUS_CHOICES = [
        (STATUS_ADJUDICADO, "Adjudicado"),
        (STATUS_DESERTO, "Deserto"),
        (STATUS_FRACASSADO, "Fracassado"),
    ]

    pregao = models.ForeignKey(
        Pregao,
        on_delete=models.CASCADE,
        related_name="resultados",
        verbose_name="Pregão",
    )
    pregao_item = models.OneToOneField(
        PregaoItem,
        on_delete=models.CASCADE,
        related_name="resultado",
        verbose_name="Item do Pregão",
    )
    status_resultado = models.CharField(
        "Status do Resultado",
        max_length=30,
        choices=STATUS_CHOICES,
    )

    primeiro_fornecedor = models.ForeignKey(
        Fornecedor,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="resultados_primeiro_lugar",
        verbose_name="1º Colocado",
    )
    primeiro_valor = models.DecimalField(
        "Valor do 1º Colocado",
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
    )

    segundo_fornecedor = models.ForeignKey(
        Fornecedor,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="resultados_segundo_lugar",
        verbose_name="2º Colocado",
    )
    segundo_valor = models.DecimalField(
        "Valor do 2º Colocado",
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
    )

    terceiro_fornecedor = models.ForeignKey(
        Fornecedor,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="resultados_terceiro_lugar",
        verbose_name="3º Colocado",
    )
    terceiro_valor = models.DecimalField(
        "Valor do 3º Colocado",
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
    )

    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    class Meta:
        verbose_name = "Resultado do Item"
        verbose_name_plural = "Resultados dos Itens"
        ordering = ["pregao", "pregao_item__ordem"]

    def __str__(self):
        return f"Resultado - {self.pregao_item}"
    
class PropostaInicialItem(models.Model):
    pregao = models.ForeignKey(
        Pregao,
        on_delete=models.CASCADE,
        related_name="propostas_iniciais",
        verbose_name="Pregão",
    )

    pregao_item = models.ForeignKey(
        PregaoItem,
        on_delete=models.CASCADE,
        related_name="propostas_iniciais",
        verbose_name="Item do Pregão",
    )

    fornecedor = models.ForeignKey(
        Fornecedor,
        on_delete=models.PROTECT,
        related_name="propostas_iniciais",
        verbose_name="Fornecedor",
    )

    participa = models.BooleanField(
        "Participa do item",
        default=True,
        help_text="Desmarque quando o fornecedor não participar deste item.",
    )

    marca = models.CharField(
        "Marca",
        max_length=150,
        blank=True,
    )

    preco_inicial = models.DecimalField(
        "Preço Inicial",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
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
        verbose_name = "Proposta Inicial do Item"
        verbose_name_plural = "Propostas Iniciais dos Itens"
        unique_together = ("pregao", "pregao_item", "fornecedor")
        ordering = ["pregao_item__ordem", "fornecedor__razao_social"]

    def __str__(self):
        return f"{self.pregao} - {self.pregao_item.item.nome_item} - {self.fornecedor.razao_social}"