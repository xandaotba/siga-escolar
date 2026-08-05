from django import forms
from django.core.exceptions import ValidationError

from apps.cadastros.models import Escola, Fornecedor
from apps.documentos.models import ContratoGerado
from apps.pregoes.models import Pregao

from .models import AquisicaoNotaFiscal


class AquisicaoNotaFiscalForm(forms.ModelForm):
    class Meta:
        model = AquisicaoNotaFiscal
        fields = [
            "pregao",
            "escola",
            "fornecedor",
            "contrato",
            "metodo_entrada",
            "chave_acesso",
            "numero_nota",
            "serie",
            "data_emissao",
            "data_recebimento",
            "valor_total",
            "arquivo_xml",
            "tabela_colada",
            "observacoes",
        ]
        widgets = {
            "data_emissao": forms.DateInput(attrs={"type": "date"}),
            "data_recebimento": forms.DateInput(attrs={"type": "date"}),
            "tabela_colada": forms.Textarea(attrs={"rows": 8}),
            "observacoes": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, request=None, escola_vinculada=None, **kwargs):
        super().__init__(*args, **kwargs)

        self.request = request
        self.escola_vinculada = escola_vinculada

        self.fields["pregao"].queryset = Pregao.objects.all().order_by("-ano", "-numero")
        self.fields["escola"].queryset = Escola.objects.all().order_by("nome_escola")
        self.fields["fornecedor"].queryset = Fornecedor.objects.all().order_by("razao_social")
        self.fields["contrato"].queryset = ContratoGerado.objects.exclude(
            status=ContratoGerado.STATUS_CANCELADO
        ).select_related("pregao", "escola", "fornecedor").order_by("-criado_em")

        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")

        self.fields["tabela_colada"].required = False
        self.fields["arquivo_xml"].required = False
        self.fields["contrato"].required = False
        self.fields["chave_acesso"].required = False
        self.fields["numero_nota"].required = False
        self.fields["serie"].required = False
        self.fields["data_emissao"].required = False
        self.fields["valor_total"].required = False

        self.fields["metodo_entrada"].help_text = "Nesta etapa, o sistema apenas registra a forma escolhida. A leitura XML/tabela será implementada nas próximas etapas."

        if escola_vinculada:
            self.fields["escola"].queryset = Escola.objects.filter(id=escola_vinculada.id)
            self.fields["escola"].initial = escola_vinculada
            self.fields["escola"].disabled = True

            self.fields["pregao"].queryset = self.fields["pregao"].queryset.filter(
                contratos_gerados__escola=escola_vinculada
            ).distinct()

            self.fields["fornecedor"].queryset = self.fields["fornecedor"].queryset.filter(
                contratos_gerados__escola=escola_vinculada
            ).distinct()

            self.fields["contrato"].queryset = self.fields["contrato"].queryset.filter(
                escola=escola_vinculada
            )

    def clean_chave_acesso(self):
        chave = self.cleaned_data.get("chave_acesso", "") or ""
        chave = "".join(ch for ch in chave if ch.isdigit())

        if chave and len(chave) != 44:
            raise ValidationError("A chave de acesso deve conter exatamente 44 dígitos.")

        return chave

    def clean(self):
        cleaned = super().clean()

        pregao = cleaned.get("pregao")
        escola = self.escola_vinculada or cleaned.get("escola")
        fornecedor = cleaned.get("fornecedor")
        contrato = cleaned.get("contrato")

        if self.escola_vinculada:
            cleaned["escola"] = self.escola_vinculada

        if contrato:
            if pregao and contrato.pregao_id != pregao.id:
                self.add_error("contrato", "O contrato selecionado não pertence ao certame informado.")

            if escola and contrato.escola_id != escola.id:
                self.add_error("contrato", "O contrato selecionado não pertence à escola informada.")

            if fornecedor and contrato.fornecedor_id != fornecedor.id:
                self.add_error("contrato", "O contrato selecionado não pertence ao fornecedor informado.")

        return cleaned
