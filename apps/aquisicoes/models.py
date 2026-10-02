from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone

from apps.cadastros.models import Escola, Fornecedor, Item
from apps.documentos.models import ContratoGerado, ContratoItemGerado
from apps.pregoes.models import Pregao


validador_chave_nfe = RegexValidator(
    regex=r"^\d{44}$",
    message="A chave de acesso da NF-e deve conter exatamente 44 dígitos numéricos.",
)


class AquisicaoNotaFiscal(models.Model):
    """
    Registro de aquisição por Nota Fiscal.

    Nesta etapa 11.1 o objetivo é criar a base do módulo:
    - cadastro da nota;
    - vínculo com certame, escola, fornecedor e contrato;
    - definição do método de entrada dos itens.

    As próximas etapas farão a importação XML, leitura de tabela colada,
    digitação dos itens e baixa automática do saldo contratual.
    """

    METODO_XML = "xml"
    METODO_TABELA_COLADA = "tabela_colada"
    METODO_MANUAL = "manual"
    # Mantido fora de METODO_CHOICES para não exigir alteração de banco/migration.
    # A aplicação grava este valor diretamente e usa metodo_entrada_exibicao
    # para apresentar o rótulo amigável.
    METODO_CHAVE_ACESSO = "chave_acesso"

    METODO_CHOICES = [
        (METODO_XML, "Upload do XML da NF-e"),
        (METODO_TABELA_COLADA, "Colar tabela da aba Produtos e Serviços"),
        (METODO_MANUAL, "Digitação manual dos itens"),
    ]

    STATUS_RASCUNHO = "rascunho"
    STATUS_EM_CONFERENCIA = "em_conferencia"
    STATUS_CONFIRMADA = "confirmada"
    STATUS_EDITADA = "editada"
    STATUS_CANCELADA = "cancelada"

    STATUS_CHOICES = [
        (STATUS_RASCUNHO, "Rascunho"),
        (STATUS_EM_CONFERENCIA, "Em conferência"),
        (STATUS_CONFIRMADA, "Confirmada"),
        (STATUS_CANCELADA, "Cancelada"),
    ]

    pregao = models.ForeignKey(
        Pregao,
        on_delete=models.PROTECT,
        related_name="aquisicoes_notas_fiscais",
        verbose_name="Certame",
    )

    escola = models.ForeignKey(
        Escola,
        on_delete=models.PROTECT,
        related_name="aquisicoes_notas_fiscais",
        verbose_name="Escola",
    )

    fornecedor = models.ForeignKey(
        Fornecedor,
        on_delete=models.PROTECT,
        related_name="aquisicoes_notas_fiscais",
        verbose_name="Fornecedor",
    )

    contrato = models.ForeignKey(
        ContratoGerado,
        on_delete=models.PROTECT,
        related_name="aquisicoes_notas_fiscais",
        verbose_name="Contrato",
        null=True,
        blank=True,
        help_text="Contrato relacionado à aquisição. Pode ser definido automaticamente nas próximas etapas.",
    )

    metodo_entrada = models.CharField(
        "Forma de Entrada dos Itens",
        max_length=30,
        choices=METODO_CHOICES,
        default=METODO_XML,
    )

    chave_acesso = models.CharField(
        "Chave de Acesso da NF-e",
        max_length=44,
        validators=[validador_chave_nfe],
        blank=True,
        help_text="Informe os 44 dígitos da chave de acesso da NF-e.",
    )

    numero_nota = models.CharField(
        "Número da Nota Fiscal",
        max_length=30,
        blank=True,
    )

    serie = models.CharField(
        "Série",
        max_length=10,
        blank=True,
    )

    data_emissao = models.DateField(
        "Data de Emissão",
        null=True,
        blank=True,
    )

    data_recebimento = models.DateField(
        "Data de Recebimento",
        default=timezone.localdate,
    )

    valor_total = models.DecimalField(
        "Valor Total da Nota",
        max_digits=14,
        decimal_places=2,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
    )

    arquivo_xml = models.FileField(
        "Arquivo XML da NF-e",
        upload_to="aquisicoes/xml/",
        null=True,
        blank=True,
    )

    tabela_colada = models.TextField(
        "Tabela copiada da aba Produtos e Serviços",
        blank=True,
        help_text="Campo reservado para a etapa de processamento da tabela colada.",
    )

    observacoes = models.TextField(
        "Observações",
        blank=True,
    )

    status = models.CharField(
        "Status",
        max_length=30,
        choices=STATUS_CHOICES,
        default=STATUS_RASCUNHO,
    )

    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="aquisicoes_notas_fiscais_criadas",
        verbose_name="Criado por",
        null=True,
        blank=True,
    )

    confirmado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="aquisicoes_notas_fiscais_confirmadas",
        verbose_name="Confirmado por",
        null=True,
        blank=True,
    )

    confirmado_em = models.DateTimeField(
        "Confirmado em",
        null=True,
        blank=True,
    )

    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    class Meta:
        verbose_name = "Aquisição por Nota Fiscal"
        verbose_name_plural = "Aquisições por Notas Fiscais"
        ordering = ["-data_recebimento", "-criado_em"]
        constraints = [
            models.UniqueConstraint(
                fields=["chave_acesso"],
                condition=~models.Q(chave_acesso=""),
                name="aquisicao_nfe_chave_acesso_unica_quando_informada",
            ),
        ]

    def __str__(self):
        identificador = self.chave_acesso or self.numero_nota or f"#{self.pk}"
        return f"NF-e {identificador} - {self.escola}"

    @property
    def valor_itens(self):
        total = self.itens.aggregate(total=models.Sum("valor_total")).get("total")
        return total or Decimal("0")

    @property
    def quantidade_itens(self):
        return self.itens.count()

    @property
    def status_exibicao(self):
        if self.status == self.STATUS_EDITADA:
            return "Editada"
        return self.get_status_display()

    @property
    def metodo_entrada_exibicao(self):
        if self.metodo_entrada == self.METODO_CHAVE_ACESSO:
            return "Consulta pela Chave de Acesso"
        return self.get_metodo_entrada_display()

    @property
    def pode_editar(self):
        return self.status != self.STATUS_CANCELADA

    @property
    def pode_confirmar(self):
        return self.status in [self.STATUS_RASCUNHO, self.STATUS_EM_CONFERENCIA]

    def clean(self):
        super().clean()

        if self.contrato:
            if self.contrato.pregao_id != self.pregao_id:
                raise ValidationError({"contrato": "O contrato selecionado não pertence ao certame informado."})

            if self.contrato.escola_id != self.escola_id:
                raise ValidationError({"contrato": "O contrato selecionado não pertence à escola informada."})

            if self.contrato.fornecedor_id != self.fornecedor_id:
                raise ValidationError({"contrato": "O contrato selecionado não pertence ao fornecedor informado."})

            if self.contrato.status == ContratoGerado.STATUS_CANCELADO:
                raise ValidationError({"contrato": "Não é possível vincular aquisição a contrato cancelado."})


class AquisicaoNotaFiscalItem(models.Model):
    STATUS_PENDENTE = "pendente"
    STATUS_VINCULADO = "vinculado"
    STATUS_DIVERGENTE = "divergente"

    STATUS_CHOICES = [
        (STATUS_PENDENTE, "Pendente de vínculo"),
        (STATUS_VINCULADO, "Vinculado ao contrato"),
        (STATUS_DIVERGENTE, "Com divergência"),
    ]

    nota = models.ForeignKey(
        AquisicaoNotaFiscal,
        on_delete=models.CASCADE,
        related_name="itens",
        verbose_name="Nota Fiscal",
    )

    contrato_item = models.ForeignKey(
        ContratoItemGerado,
        on_delete=models.PROTECT,
        related_name="aquisicoes_itens",
        verbose_name="Item do Contrato",
        null=True,
        blank=True,
    )

    item = models.ForeignKey(
        Item,
        on_delete=models.PROTECT,
        related_name="aquisicoes_notas_fiscais",
        verbose_name="Item Cadastrado",
        null=True,
        blank=True,
    )

    codigo_produto = models.CharField("Código do Produto na NF-e", max_length=80, blank=True)

    descricao_produto = models.CharField(
        "Descrição do Produto na NF-e",
        max_length=500,
    )

    unidade = models.CharField(
        "Unidade",
        max_length=40,
        blank=True,
    )

    quantidade = models.DecimalField(
        "Quantidade",
        max_digits=14,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0"))],
    )

    valor_unitario = models.DecimalField(
        "Valor Unitário",
        max_digits=14,
        decimal_places=4,
        validators=[MinValueValidator(Decimal("0"))],
    )

    valor_total = models.DecimalField(
        "Valor Total",
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )

    status = models.CharField(
        "Status",
        max_length=30,
        choices=STATUS_CHOICES,
        default=STATUS_PENDENTE,
    )

    conferido = models.BooleanField(
        "Conferido",
        default=False,
    )

    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    class Meta:
        verbose_name = "Item da Aquisição por NF-e"
        verbose_name_plural = "Itens da Aquisição por NF-e"
        ordering = ["descricao_produto"]

    def __str__(self):
        return f"{self.descricao_produto} - {self.nota}"

    def clean(self):
        super().clean()

        if self.contrato_item:
            if self.contrato_item.contrato_id != self.nota.contrato_id:
                raise ValidationError({"contrato_item": "O item selecionado não pertence ao contrato da nota fiscal."})

            if self.item and self.contrato_item.item_id != self.item_id:
                raise ValidationError({"item": "O item cadastrado não corresponde ao item do contrato selecionado."})


class ProdutoNotaFiscalMapeamento(models.Model):
    """
    Tabela de equivalência para as próximas etapas.

    Exemplo:
    "BATATA DOCE - ACOND.: C" do fornecedor X = item cadastrado "BATATA DOCE".
    """

    fornecedor = models.ForeignKey(
        Fornecedor,
        on_delete=models.CASCADE,
        related_name="mapeamentos_produtos_nfe",
        verbose_name="Fornecedor",
    )

    descricao_nota = models.CharField(
        "Descrição do Produto na NF-e",
        max_length=500,
    )

    unidade_nota = models.CharField(
        "Unidade na NF-e",
        max_length=40,
        blank=True,
    )

    item = models.ForeignKey(
        Item,
        on_delete=models.PROTECT,
        related_name="mapeamentos_produtos_nfe",
        verbose_name="Item Cadastrado",
    )

    ativo = models.BooleanField("Ativo", default=True)

    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    class Meta:
        verbose_name = "Mapeamento de Produto da NF-e"
        verbose_name_plural = "Mapeamentos de Produtos da NF-e"
        ordering = ["fornecedor__razao_social", "descricao_nota"]
        constraints = [
            models.UniqueConstraint(
                fields=["fornecedor", "descricao_nota", "unidade_nota"],
                name="produto_nfe_mapeamento_unico_por_fornecedor",
            ),
        ]

    def __str__(self):
        return f"{self.descricao_nota} → {self.item}"
