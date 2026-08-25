from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import models


def only_digits(value):
    if value is None:
        return ""
    return "".join(ch for ch in str(value) if ch.isdigit())


def validar_cpf(value):
    cpf = only_digits(value)

    if len(cpf) != 11 or cpf == cpf[0] * 11:
        raise ValidationError("CPF inválido.")

    soma = sum(int(cpf[i]) * (10 - i) for i in range(9))
    digito1 = (soma * 10) % 11
    digito1 = 0 if digito1 == 10 else digito1

    soma = sum(int(cpf[i]) * (11 - i) for i in range(10))
    digito2 = (soma * 10) % 11
    digito2 = 0 if digito2 == 10 else digito2

    if digito1 != int(cpf[9]) or digito2 != int(cpf[10]):
        raise ValidationError("CPF inválido.")


def validar_cnpj(value):
    cnpj = only_digits(value)

    if len(cnpj) != 14 or cnpj == cnpj[0] * 14:
        raise ValidationError("CNPJ inválido.")

    pesos1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    pesos2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]

    soma = sum(int(cnpj[i]) * pesos1[i] for i in range(12))
    resto = soma % 11
    digito1 = 0 if resto < 2 else 11 - resto

    soma = sum(int(cnpj[i]) * pesos2[i] for i in range(13))
    resto = soma % 11
    digito2 = 0 if resto < 2 else 11 - resto

    if digito1 != int(cnpj[12]) or digito2 != int(cnpj[13]):
        raise ValidationError("CNPJ inválido.")


class TimeStampedModel(models.Model):
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AtivoModel(TimeStampedModel):
    ativo = models.BooleanField(default=True)

    class Meta:
        abstract = True


class Municipio(AtivoModel):
    nome = models.CharField(max_length=120)
    uf = models.CharField(max_length=2, default="MT")

    class Meta:
        verbose_name = "Município"
        verbose_name_plural = "Municípios"
        ordering = ["nome"]
        constraints = [
            models.UniqueConstraint(fields=["nome", "uf"], name="unique_municipio_uf")
        ]

    def __str__(self):
        return f"{self.nome}/{self.uf}"


class Fornecedor(AtivoModel):
    TIPO_EMPRESA_COMUM = "empresa_comum"
    TIPO_INDIVIDUAL = "individual"
    TIPO_GRUPO_FORMAL = "grupo_formal"

    TIPO_FORNECEDOR_CHAMADA_CHOICES = [
        (TIPO_EMPRESA_COMUM, "Empresa comum"),
        (TIPO_INDIVIDUAL, "Fornecedor Individual"),
        (TIPO_GRUPO_FORMAL, "Grupo Formal / Cooperativa"),
    ]

    LIMITE_ANUAL_POR_CAF_DAP = Decimal("40000.00")

    tipo_fornecedor_chamada = models.CharField(
        "Tipo do Fornecedor",
        max_length=30,
        choices=TIPO_FORNECEDOR_CHAMADA_CHOICES,
        default=TIPO_EMPRESA_COMUM,
        help_text="Classificação usada especialmente na Chamada Pública da Agricultura Familiar.",
    )

    fornecedor_me_epp = models.BooleanField(
        "Fornecedor ME/EPP",
        default=False,
        help_text="Marque quando o fornecedor for Microempresa (ME) ou Empresa de Pequeno Porte (EPP).",
    )

    razao_social = models.CharField("Razão Social / Nome do Fornecedor", max_length=255)
    cnpj = models.CharField(
        "CNPJ",
        max_length=18,
        unique=True,
        validators=[validar_cnpj],
        null=True,
        blank=True,
        help_text="Obrigatório para empresa comum e grupo formal/cooperativa. Não usado para fornecedor individual.",
    )
    cpf_fornecedor_individual = models.CharField(
        "CPF do Fornecedor Individual",
        max_length=14,
        blank=True,
        help_text="Use este campo quando o fornecedor for pessoa física da agricultura familiar.",
    )
    caf_dap = models.CharField(
        "CAF/DAP",
        max_length=50,
        blank=True,
        help_text="Número do CAF ou DAP do fornecedor individual ou do grupo formal/cooperativa.",
    )
    quantidade_caf_dap = models.PositiveIntegerField(
        "Quantidade de CAF/DAP",
        default=1,
        help_text="Para grupo formal/cooperativa, informe a quantidade de CAF/DAP vinculadas.",
    )
    caf_dap_juridica = models.CharField(
        "CAF/DAP Jurídica",
        max_length=50,
        blank=True,
        help_text="Obrigatória para grupo formal/cooperativa.",
    )

    endereco = models.CharField("Endereço", max_length=255)
    telefone = models.CharField("Telefone", max_length=30, blank=True)

    representante_legal = models.CharField("Representante Legal", max_length=255)
    rg_representante = models.CharField("RG do Representante", max_length=50)
    orgao_expedidor_representante = models.CharField("Órgão Expedidor", max_length=50)
    cpf_representante = models.CharField(
        "CPF do Representante",
        max_length=14,
        validators=[validar_cpf],
    )
    procurador = models.CharField("Procurador", max_length=255, blank=True)
    rg_procurador = models.CharField("RG do Procurador", max_length=50, blank=True)
    orgao_expedidor_procurador = models.CharField(
        "Órgão Expedidor do Procurador",
        max_length=50,
        blank=True,
    )
    cpf_procurador = models.CharField(
        "CPF do Procurador",
        max_length=14,
        blank=True,
        validators=[validar_cpf],
    )

    email = models.EmailField("E-mail", max_length=150, blank=True)
    nome_banco = models.CharField("Nome do Banco", max_length=100, blank=True)
    agencia = models.CharField("Agência", max_length=30, blank=True)
    conta_corrente = models.CharField("Conta Corrente", max_length=30, blank=True)
    pix = models.CharField("PIX", max_length=150, blank=True)

    class Meta:
        verbose_name = "Fornecedor"
        verbose_name_plural = "Fornecedores"
        ordering = ["razao_social"]

    def clean(self):
        super().clean()

        if self.tipo_fornecedor_chamada == self.TIPO_INDIVIDUAL:
            self.cnpj = None
            self.quantidade_caf_dap = 1
            self.caf_dap_juridica = ""
            self.fornecedor_me_epp = False

            if not self.cpf_fornecedor_individual:
                raise ValidationError("Informe o CPF do fornecedor individual.")

            validar_cpf(self.cpf_fornecedor_individual)

            if not self.caf_dap:
                raise ValidationError("Informe o CAF/DAP do fornecedor individual.")

        elif self.tipo_fornecedor_chamada == self.TIPO_GRUPO_FORMAL:
            self.cpf_fornecedor_individual = ""
            self.caf_dap = ""

            if not self.cnpj:
                raise ValidationError("Informe o CNPJ do grupo formal/cooperativa.")

            validar_cnpj(self.cnpj)

            if not self.quantidade_caf_dap or self.quantidade_caf_dap < 1:
                raise ValidationError("Informe uma quantidade válida de CAF/DAP vinculadas ao grupo formal/cooperativa.")

            if not self.caf_dap_juridica:
                raise ValidationError("Informe o número da CAF/DAP Jurídica do grupo formal/cooperativa.")

        else:
            self.cpf_fornecedor_individual = ""
            self.caf_dap = ""
            self.caf_dap_juridica = ""

            if not self.cnpj:
                raise ValidationError("Informe o CNPJ da empresa comum.")

            validar_cnpj(self.cnpj)

            if not self.quantidade_caf_dap or self.quantidade_caf_dap < 1:
                self.quantidade_caf_dap = 1

    @property
    def eh_fornecedor_chamada_publica(self):
        return self.tipo_fornecedor_chamada in [
            self.TIPO_INDIVIDUAL,
            self.TIPO_GRUPO_FORMAL,
        ]

    @property
    def limite_anual_chamada_publica(self):
        if self.tipo_fornecedor_chamada == self.TIPO_INDIVIDUAL:
            return self.LIMITE_ANUAL_POR_CAF_DAP

        if self.tipo_fornecedor_chamada == self.TIPO_GRUPO_FORMAL:
            quantidade = self.quantidade_caf_dap or 0
            return self.LIMITE_ANUAL_POR_CAF_DAP * Decimal(quantidade)

        return Decimal("0.00")

    def get_limite_anual_chamada_publica_display(self):
        valor = self.limite_anual_chamada_publica

        if not valor:
            return "-"

        texto = f"{valor:,.2f}"
        texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")
        return f"R$ {texto}"

    def __str__(self):
        return self.razao_social


class Escola(AtivoModel):
    nome_escola = models.CharField("Nome da Escola", max_length=255)
    cnpj = models.CharField(
        "CNPJ",
        max_length=18,
        unique=True,
        validators=[validar_cnpj],
        null=True,
        blank=True,
        help_text="Obrigatório para empresa comum e grupo formal/cooperativa. Não usado para fornecedor individual.",
    )
    endereco = models.CharField("Endereço", max_length=255)
    numero = models.CharField("Número", max_length=20, blank=True)
    bairro = models.CharField("Bairro", max_length=100, blank=True)

    municipio = models.ForeignKey(
        Municipio,
        on_delete=models.PROTECT,
        related_name="escolas",
        verbose_name="Município",
    )

    presidente_cdce = models.CharField("Presidente do CDCE", max_length=255)
    rg_presidente = models.CharField("RG do Presidente", max_length=50)
    cpf_presidente = models.CharField(
        "CPF do Presidente",
        max_length=14,
        validators=[validar_cpf],
    )

    class Meta:
        verbose_name = "Escola"
        verbose_name_plural = "Escolas"
        ordering = ["nome_escola"]

    def __str__(self):
        return self.nome_escola



class UnidadeMedida(AtivoModel):
    nome = models.CharField("Nome", max_length=100)
    sigla = models.CharField("Sigla", max_length=20, blank=True)
    codigo = models.CharField(
        "Código interno",
        max_length=50,
        unique=True,
        help_text="Código usado internamente nos itens. Ex.: kg, litro, unidade.",
    )

    class Meta:
        verbose_name = "Unidade de Medida"
        verbose_name_plural = "Unidades de Medida"
        ordering = ["nome"]

    def __str__(self):
        if self.sigla:
            return f"{self.nome} ({self.sigla})"
        return self.nome


class Item(AtivoModel):
    nome_item = models.CharField("Nome do Item", max_length=255)

    unidade_medida = models.CharField(
        "Unidade de Medida",
        max_length=50,
    )

    descricao = models.TextField("Descrição do Item")

    class Meta:
        verbose_name = "Item"
        verbose_name_plural = "Itens"
        ordering = ["nome_item"]

    def get_unidade_medida_display(self):
        unidade = UnidadeMedida.objects.filter(codigo=self.unidade_medida).first()

        if unidade:
            return unidade.nome

        unidades_legadas = {
            "kg": "Quilograma",
            "litro": "Litro",
            "unidade": "Unidade",
            "pacote": "Pacote",
            "caixa": "Caixa",
            "duzia": "Dúzia",
        }

        return unidades_legadas.get(self.unidade_medida, self.unidade_medida)

    def __str__(self):
        return f"{self.nome_item} - {self.get_unidade_medida_display()}"
