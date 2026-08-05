from django import forms
from .models import Escola, Fornecedor, Item, Municipio

class FornecedorForm(forms.ModelForm):
    class Meta:
        model = Fornecedor
        fields = [
            "tipo_fornecedor_chamada",
            "razao_social",
            "cnpj",
            "cpf_fornecedor_individual",
            "caf_dap",
            "quantidade_caf_dap",
            "endereco",
            "telefone",
            "representante_legal",
            "rg_representante",
            "orgao_expedidor_representante",
            "cpf_representante",
            "email",
            "nome_banco",
            "agencia",
            "conta_corrente",
            "pix",
            "ativo",
        ]

        widgets = {
            "tipo_fornecedor_chamada": forms.Select(attrs={"id": "id_tipo_fornecedor_chamada"}),
            "razao_social": forms.TextInput(attrs={"placeholder": "Razão social ou nome do fornecedor"}),
            "cnpj": forms.TextInput(attrs={"placeholder": "00.000.000/0000-00", "id": "id_cnpj"}),
            "cpf_fornecedor_individual": forms.TextInput(attrs={"placeholder": "000.000.000-00"}),
            "caf_dap": forms.TextInput(attrs={"placeholder": "Número do CAF/DAP"}),
            "quantidade_caf_dap": forms.NumberInput(attrs={"min": "1", "placeholder": "1"}),
            "endereco": forms.TextInput(attrs={"placeholder": "Endereço completo"}),
            "telefone": forms.TextInput(attrs={"placeholder": "(00) 00000-0000"}),
            "representante_legal": forms.TextInput(attrs={"placeholder": "Nome do representante legal"}),
            "rg_representante": forms.TextInput(attrs={"placeholder": "RG do representante"}),
            "orgao_expedidor_representante": forms.TextInput(attrs={"placeholder": "Órgão expedidor"}),
            "cpf_representante": forms.TextInput(attrs={"placeholder": "000.000.000-00"}),
            "email": forms.EmailInput(attrs={"placeholder": "email@exemplo.com"}),
            "nome_banco": forms.TextInput(attrs={"placeholder": "Nome do banco"}),
            "agencia": forms.TextInput(attrs={"placeholder": "Agência"}),
            "conta_corrente": forms.TextInput(attrs={"placeholder": "Conta corrente"}),
            "pix": forms.TextInput(attrs={"placeholder": "Chave PIX"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Estes campos são obrigatórios apenas conforme o tipo de fornecedor.
        # Por isso ficam como não obrigatórios no HTML e a validação correta é feita no clean().
        campos_condicionais = [
            "cnpj",
            "cpf_fornecedor_individual",
            "caf_dap",
            "quantidade_caf_dap",
        ]

        for campo in campos_condicionais:
            if campo in self.fields:
                self.fields[campo].required = False

        if "quantidade_caf_dap" in self.fields:
            self.fields["quantidade_caf_dap"].initial = self.fields["quantidade_caf_dap"].initial or 1

    def clean(self):
        cleaned_data = super().clean()
        tipo = cleaned_data.get("tipo_fornecedor_chamada")
        cnpj = cleaned_data.get("cnpj")
        cpf_individual = cleaned_data.get("cpf_fornecedor_individual")
        caf_dap = cleaned_data.get("caf_dap")
        quantidade_caf_dap = cleaned_data.get("quantidade_caf_dap") or 1

        if tipo == Fornecedor.TIPO_INDIVIDUAL:
            cleaned_data["cnpj"] = None
            cleaned_data["quantidade_caf_dap"] = 1

            if not cpf_individual:
                self.add_error("cpf_fornecedor_individual", "Informe o CPF do fornecedor individual.")

            if not caf_dap:
                self.add_error("caf_dap", "Informe o CAF/DAP do fornecedor individual.")

        elif tipo == Fornecedor.TIPO_GRUPO_FORMAL:
            cleaned_data["cpf_fornecedor_individual"] = ""
            cleaned_data["caf_dap"] = ""

            if not cnpj:
                self.add_error("cnpj", "Informe o CNPJ do grupo formal/cooperativa.")

            if quantidade_caf_dap < 1:
                self.add_error("quantidade_caf_dap", "Informe uma quantidade válida de CAF/DAP vinculadas.")

        else:
            cleaned_data["cpf_fornecedor_individual"] = ""
            cleaned_data["caf_dap"] = ""

            if not cnpj:
                self.add_error("cnpj", "Informe o CNPJ da empresa comum.")

            if quantidade_caf_dap < 1:
                cleaned_data["quantidade_caf_dap"] = 1

        return cleaned_data


class EscolaForm(forms.ModelForm):
    class Meta:
        model = Escola
        fields = [
            "nome_escola",
            "cnpj",
            "endereco",
            "numero",
            "bairro",
            "municipio",
            "presidente_cdce",
            "rg_presidente",
            "cpf_presidente",
            "ativo",
        ]

        widgets = {
            "nome_escola": forms.TextInput(attrs={"placeholder": "Nome da escola"}),
            "cnpj": forms.TextInput(attrs={"placeholder": "00.000.000/0000-00", "id": "id_cnpj"}),
            "endereco": forms.TextInput(attrs={"placeholder": "Endereço"}),
            "numero": forms.TextInput(attrs={"placeholder": "Número"}),
            "bairro": forms.TextInput(attrs={"placeholder": "Bairro"}),
            "presidente_cdce": forms.TextInput(attrs={"placeholder": "Nome do presidente do CDCE"}),
            "rg_presidente": forms.TextInput(attrs={"placeholder": "RG do presidente"}),
            "cpf_presidente": forms.TextInput(attrs={"placeholder": "000.000.000-00"}),
        }


class ItemForm(forms.ModelForm):
    class Meta:
        model = Item
        fields = [
            "nome_item",
            "unidade_medida",
            "descricao",
            "ativo",
        ]

        widgets = {
            "nome_item": forms.TextInput(attrs={"placeholder": "Nome do item"}),
            "descricao": forms.Textarea(
                attrs={
                    "placeholder": "Descrição detalhada do item",
                    "rows": 4,
                }
            ),
        }

class MunicipioForm(forms.ModelForm):
    class Meta:
        model = Municipio
        fields = [
            "nome",
            "uf",
            "ativo",
        ]

        widgets = {
            "nome": forms.TextInput(attrs={"placeholder": "Nome do município"}),
            "uf": forms.TextInput(attrs={"placeholder": "UF, exemplo: MT"}),
        }