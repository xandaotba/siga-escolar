from django import forms


class ImportacaoPlanilhaForm(forms.Form):
    arquivo = forms.FileField(
        label="Arquivo da planilha",
        help_text="Envie um arquivo .xlsx usando o modelo disponibilizado pelo sistema.",
    )

    atualizar_existentes = forms.BooleanField(
        label="Atualizar registros já existentes quando encontrados",
        required=False,
        initial=True,
    )

    def clean_arquivo(self):
        arquivo = self.cleaned_data["arquivo"]

        if not arquivo.name.lower().endswith(".xlsx"):
            raise forms.ValidationError("Envie um arquivo no formato .xlsx.")

        return arquivo
