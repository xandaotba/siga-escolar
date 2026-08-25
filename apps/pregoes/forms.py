from django import forms

from apps.cadastros.models import Fornecedor, Municipio
from .models import Pregao, PregaoFornecedor, PregaoMunicipio


class PregaoForm(forms.ModelForm):
    municipios = forms.ModelMultipleChoiceField(
        queryset=Municipio.objects.filter(ativo=True).order_by("nome"),
        widget=forms.CheckboxSelectMultiple,
        required=True,
        label="Municípios",
    )

    fornecedores = forms.ModelMultipleChoiceField(
        queryset=Fornecedor.objects.filter(ativo=True).order_by("razao_social"),
        widget=forms.CheckboxSelectMultiple,
        required=True,
        label="Fornecedores",
    )

    class Meta:
        model = Pregao
        fields = [
            "tipo_certame",
            "numero",
            "numero_processo",
            "ano",
            "nome_pregoeiro",
            "cpf_pregoeiro",
            "local_pregao",
            "data_pregao",
            "municipios",
            "fornecedores",
        ]

        widgets = {
            "tipo_certame": forms.Select(),
            "numero": forms.TextInput(attrs={"placeholder": "Exemplo: 001"}),
            "numero_processo": forms.TextInput(
                attrs={"placeholder": "Exemplo: SEDUC-PRO-2026/000000"}
            ),
            "ano": forms.NumberInput(attrs={"placeholder": "Exemplo: 2026"}),
            "nome_pregoeiro": forms.TextInput(attrs={"placeholder": "Nome do pregoeiro"}),
            "cpf_pregoeiro": forms.TextInput(attrs={"placeholder": "000.000.000-00"}),
            "local_pregao": forms.TextInput(attrs={"placeholder": "Local do pregão"}),
            "data_pregao": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.instance and self.instance.pk:
            self.fields["municipios"].initial = self.instance.municipios.all()
            self.fields["fornecedores"].initial = self.instance.fornecedores.all()

    def save(self, commit=True, user=None):
        municipios = self.cleaned_data.pop("municipios")
        fornecedores = self.cleaned_data.pop("fornecedores")

        pregao = super().save(commit=False)

        if user and user.is_authenticated and not pregao.criado_por:
            pregao.criado_por = user

        if commit:
            pregao.save()

            PregaoMunicipio.objects.filter(pregao=pregao).delete()
            PregaoFornecedor.objects.filter(pregao=pregao).delete()

            for municipio in municipios:
                PregaoMunicipio.objects.create(
                    pregao=pregao,
                    municipio=municipio,
                )

            ordem = 1
            for fornecedor in fornecedores:
                PregaoFornecedor.objects.create(
                    pregao=pregao,
                    fornecedor=fornecedor,
                    ordem_inicial=ordem,
                    ativo_no_pregao=True,
                )
                ordem += 1

        return pregao