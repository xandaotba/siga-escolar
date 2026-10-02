import json
import logging
import os
import shutil
import time
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import (
    JavascriptException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


logger = logging.getLogger(__name__)


CONSULTA_DANFE_URL = "https://consultadanfe.com/"
CONSULTA_DANFE_ENDPOINT_CHAVE = "https://consultadanfe.com/chave"


class ConsultaDanfeErro(Exception):
    """Erro controlado durante a consulta pública no Consulta DANFE."""


class ConsultaDanfeTimeout(ConsultaDanfeErro):
    """O site não retornou a NF-e dentro do tempo configurado."""


class ConsultaDanfeRespostaInvalida(ConsultaDanfeErro):
    """A resposta recebida não contém um XML de NF-e utilizável."""


def _env_bool(nome, padrao=True):
    valor = os.getenv(nome)
    if valor is None:
        return padrao

    return str(valor).strip().lower() not in {
        "0",
        "false",
        "nao",
        "não",
        "off",
        "no",
    }


def _localizar_chrome():
    """
    No Windows, normalmente o Selenium Manager encontra o Chrome instalado.
    Em Linux/Railway tentamos localizar explicitamente os binários mais comuns.
    """
    candidatos = [
        os.getenv("CHROME_BIN"),
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ]

    for candidato in candidatos:
        if candidato and Path(candidato).exists():
            return str(candidato)

    return None


def _criar_driver():
    options = Options()

    if _env_bool("CONSULTA_DANFE_HEADLESS", True):
        options.add_argument("--headless=new")

    options.add_argument("--window-size=1920,1080")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--lang=pt-BR")
    options.add_argument("--log-level=3")

    chrome_bin = _localizar_chrome()
    if chrome_bin:
        options.binary_location = chrome_bin

    # Precisamos do log de rede para capturar a resposta JSON do POST /chave.
    options.set_capability(
        "goog:loggingPrefs",
        {
            "performance": "ALL",
            "browser": "ALL",
        },
    )

    try:
        driver = webdriver.Chrome(options=options)
    except WebDriverException as erro:
        raise ConsultaDanfeErro(
            "Não foi possível iniciar o Google Chrome/Chromium pelo Selenium. "
            "Verifique se o Chrome está instalado e se a versão do Selenium "
            "está atualizada."
        ) from erro

    try:
        driver.execute_cdp_cmd("Network.enable", {})
    except Exception:
        # O performance log continua sendo habilitado pelas capabilities.
        pass

    return driver


def _encontrar_campo_chave(driver, timeout):
    wait = WebDriverWait(driver, timeout)

    seletores = [
        (
            By.CSS_SELECTOR,
            'input[placeholder*="chave de acesso" i]',
        ),
        (
            By.CSS_SELECTOR,
            'input[maxlength="44"]',
        ),
        (
            By.XPATH,
            '//input[contains(translate(@placeholder, '
            '"CHAVEDEACESSO", "chavedeacesso"), "chave")]',
        ),
    ]

    ultimo_erro = None

    for by, seletor in seletores:
        try:
            return wait.until(
                EC.visibility_of_element_located((by, seletor))
            )
        except TimeoutException as erro:
            ultimo_erro = erro

    raise ConsultaDanfeErro(
        "O campo de Chave de Acesso não foi localizado na página do Consulta DANFE. "
        "O site pode ter alterado o layout."
    ) from ultimo_erro


def _encontrar_botao_imprimir(driver, timeout):
    wait = WebDriverWait(driver, timeout)

    seletores = [
        (
            By.XPATH,
            '//button[contains(normalize-space(.), "Imprimir DANFE")]',
        ),
        (
            By.XPATH,
            '//button[contains(normalize-space(.), "DANFE")]',
        ),
    ]

    ultimo_erro = None

    for by, seletor in seletores:
        try:
            return wait.until(
                EC.element_to_be_clickable((by, seletor))
            )
        except TimeoutException as erro:
            ultimo_erro = erro

    raise ConsultaDanfeErro(
        'O botão "Imprimir DANFE" não foi localizado. '
        "O site pode ter alterado o layout."
    ) from ultimo_erro


def _ler_corpo_resposta(driver, request_id):
    """
    O body pode ainda não estar disponível exatamente no instante de
    Network.responseReceived. Fazemos pequenas tentativas sem interromper
    toda a consulta.
    """
    for _ in range(12):
        try:
            retorno = driver.execute_cdp_cmd(
                "Network.getResponseBody",
                {"requestId": request_id},
            )
            corpo = retorno.get("body") or ""
            if corpo:
                return corpo
        except (JavascriptException, WebDriverException):
            pass

        time.sleep(0.15)

    return ""


def _extrair_resposta_chave(driver, timeout):
    inicio = time.monotonic()
    requests_chave = {}

    while time.monotonic() - inicio < timeout:
        try:
            registros = driver.get_log("performance")
        except WebDriverException as erro:
            raise ConsultaDanfeErro(
                "O Chrome não disponibilizou o log de rede necessário para "
                "obter o XML da NF-e."
            ) from erro

        for registro in registros:
            try:
                envelope = json.loads(registro.get("message") or "{}")
                mensagem = envelope.get("message") or {}
                metodo = mensagem.get("method")
                params = mensagem.get("params") or {}
            except (TypeError, ValueError, json.JSONDecodeError):
                continue

            if metodo == "Network.responseReceived":
                resposta = params.get("response") or {}
                url = str(resposta.get("url") or "").rstrip("/")

                if url != CONSULTA_DANFE_ENDPOINT_CHAVE.rstrip("/"):
                    continue

                request_id = params.get("requestId")
                status_http = int(resposta.get("status") or 0)

                if request_id:
                    requests_chave[request_id] = status_http

                    corpo = _ler_corpo_resposta(driver, request_id)
                    if corpo:
                        return corpo, status_http

            elif metodo == "Network.loadingFinished":
                request_id = params.get("requestId")

                if request_id in requests_chave:
                    corpo = _ler_corpo_resposta(driver, request_id)
                    if corpo:
                        return corpo, requests_chave[request_id]

        time.sleep(0.20)

    raise ConsultaDanfeTimeout(
        "O Consulta DANFE não retornou a nota dentro do tempo esperado. "
        "Tente novamente ou utilize a opção Upload XML."
    )


def _validar_json_consulta(corpo, chave_solicitada):
    try:
        resposta = json.loads(corpo)
    except json.JSONDecodeError as erro:
        raise ConsultaDanfeRespostaInvalida(
            "O Consulta DANFE respondeu em um formato inesperado."
        ) from erro

    status = str(resposta.get("status") or "").strip().lower()

    if status not in {"sucesso", "success"}:
        mensagem = (
            resposta.get("mensagem")
            or resposta.get("message")
            or resposta.get("erro")
            or "A consulta não retornou uma NF-e."
        )
        raise ConsultaDanfeErro(str(mensagem))

    chave_retornada = "".join(
        caractere
        for caractere in str(resposta.get("chave_acesso") or "")
        if caractere.isdigit()
    )

    if chave_retornada and chave_retornada != chave_solicitada:
        raise ConsultaDanfeRespostaInvalida(
            "A chave devolvida pelo Consulta DANFE é diferente da chave solicitada."
        )

    codigo_xml = resposta.get("codigo_xml") or resposta.get("xml") or ""

    if not isinstance(codigo_xml, str):
        raise ConsultaDanfeRespostaInvalida(
            "O XML retornado pelo Consulta DANFE não é válido."
        )

    codigo_xml = codigo_xml.strip()

    if not codigo_xml.startswith("<"):
        raise ConsultaDanfeRespostaInvalida(
            "A consulta foi concluída, mas não retornou o conteúdo XML da NF-e."
        )

    xml_minusculo = codigo_xml.lower()

    if "<nfe" not in xml_minusculo and "<nfeproc" not in xml_minusculo:
        raise ConsultaDanfeRespostaInvalida(
            "O conteúdo retornado não parece ser um XML de NF-e."
        )

    return {
        "codigo_xml": codigo_xml,
        "chave_acesso": chave_retornada or chave_solicitada,
        "resposta": resposta,
    }


def consultar_xml_consultadanfe(chave, timeout=None):
    """
    Executa o fluxo público normal do Consulta DANFE em um Chrome controlado
    pelo Selenium e captura a resposta JSON do POST /chave.

    Não resolve nem contorna desafios de CAPTCHA. Se o próprio site não
    concluir normalmente a consulta no navegador automatizado, a função
    retorna um erro controlado.
    """
    chave = "".join(caractere for caractere in str(chave or "") if caractere.isdigit())

    if len(chave) != 44:
        raise ConsultaDanfeErro(
            "A Chave de Acesso precisa conter exatamente 44 dígitos."
        )

    if timeout is None:
        try:
            timeout = int(os.getenv("CONSULTA_DANFE_TIMEOUT", "45"))
        except (TypeError, ValueError):
            timeout = 45

    timeout = max(15, min(timeout, 120))

    driver = None

    try:
        logger.info("[Consulta DANFE] Iniciando navegador.")
        driver = _criar_driver()

        logger.info("[Consulta DANFE] Abrindo %s", CONSULTA_DANFE_URL)
        driver.get(CONSULTA_DANFE_URL)

        campo = _encontrar_campo_chave(driver, min(timeout, 25))
        campo.clear()
        campo.send_keys(chave)

        botao = _encontrar_botao_imprimir(driver, min(timeout, 25))

        logger.info("[Consulta DANFE] Consultando chave %s...", chave[-8:])
        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center'});",
            botao,
        )
        botao.click()

        corpo, status_http = _extrair_resposta_chave(driver, timeout)

        if status_http < 200 or status_http >= 300:
            raise ConsultaDanfeErro(
                f"O Consulta DANFE respondeu com HTTP {status_http}."
            )

        resultado = _validar_json_consulta(corpo, chave)

        logger.info(
            "[Consulta DANFE] XML recebido com sucesso para a chave ...%s.",
            chave[-8:],
        )

        return resultado

    except TimeoutException as erro:
        raise ConsultaDanfeTimeout(
            "A página do Consulta DANFE demorou demais para responder. "
            "Tente novamente ou utilize Upload XML."
        ) from erro

    except ConsultaDanfeErro:
        raise

    except WebDriverException as erro:
        raise ConsultaDanfeErro(
            "O navegador automatizado não conseguiu concluir a consulta no "
            "Consulta DANFE. Tente novamente ou utilize Upload XML."
        ) from erro

    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
