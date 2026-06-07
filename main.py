from fastapi import FastAPI
import requests
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

app = FastAPI(title="Monitor São Ludgero API")

LAT = -28.325
LON = -49.176
TIMEZONE = "America/Sao_Paulo"

CODIGO_ANA_BRACO_NORTE = "84559800"

TERMOS_REGIAO_SC = [
    "Sul Catarinense",
    "Grande Florianópolis",
    "Vale do Itajaí",
    "Serrana",
    "Norte Catarinense",
    "Oeste Catarinense"
]


def buscar_previsao(dias: int = 7):
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": LAT,
        "longitude": LON,
        "daily": "precipitation_sum,temperature_2m_max,temperature_2m_min,wind_gusts_10m_max",
        "hourly": "precipitation,precipitation_probability",
        "timezone": TIMEZONE,
        "forecast_days": dias
    }
    resposta = requests.get(url, params=params, timeout=20)
    resposta.raise_for_status()
    return resposta.json()


def classificar_risco(acumulado_72h: float, acumulado_7d: float):
    if acumulado_72h >= 120 or acumulado_7d >= 180:
        return "muito alto", "Risco elevado para cheias, enxurradas e ocorrências generalizadas."
    elif acumulado_72h >= 80 or acumulado_7d >= 120:
        return "alto", "Atenção máxima para elevação de rios, enxurradas e alagamentos."
    elif acumulado_72h >= 50 or acumulado_7d >= 80:
        return "moderado", "Atenção para alagamentos pontuais e resposta rápida de córregos."
    elif acumulado_72h >= 25 or acumulado_7d >= 50:
        return "atenção", "Risco baixo a moderado, com atenção a pancadas localmente fortes."
    else:
        return "baixo", "Sem indicativo de risco hidrológico relevante pela previsão atual."


def classificar_nivel_rio(cota_cm: float):
    if cota_cm >= 700:
        return "emergência enchente"
    elif cota_cm >= 600:
        return "alerta enchente"
    elif cota_cm >= 500:
        return "atenção enchente"
    elif cota_cm >= 24:
        return "normal"
    elif cota_cm >= 16:
        return "atenção estiagem"
    elif cota_cm >= 10:
        return "alerta estiagem"
    else:
        return "emergência estiagem"


def texto_xml(elemento, nome):
    for item in elemento.iter():
        if item.tag.split("}")[-1] == nome:
            return item.text
    return None


def buscar_alertas_inmet():
    url_base = "https://apiprevmet3.inmet.gov.br/avisos/rss"
    resposta = requests.get(url_base, timeout=20)
    resposta.raise_for_status()

    texto = resposta.text
    ids = sorted(set(re.findall(r"avisos/(?:rss/)?(\d+)", texto)))

    alertas = []
    agora = datetime.now().astimezone()

    for aviso_id in ids[:100]:
        try:
            url_aviso = f"https://apiprevmet3.inmet.gov.br/avisos/rss/{aviso_id}"
            r = requests.get(url_aviso, timeout=15)

            if r.status_code != 200:
                continue

            raiz = ET.fromstring(r.content)

            headline = texto_xml(raiz, "headline")
            description = texto_xml(raiz, "description")
            severity = texto_xml(raiz, "severity")
            urgency = texto_xml(raiz, "urgency")
            certainty = texto_xml(raiz, "certainty")
            onset = texto_xml(raiz, "onset")
            expires = texto_xml(raiz, "expires")
            instruction = texto_xml(raiz, "instruction")
            area_desc = texto_xml(raiz, "areaDesc") or ""

            pertence_regiao = any(
                termo.lower() in area_desc.lower()
                for termo in TERMOS_REGIAO_SC
            )

            if not pertence_regiao:
                continue

            if expires:
                data_fim = datetime.fromisoformat(expires)
                if data_fim < agora:
                    continue

            alertas.append({
                "id": aviso_id,
                "titulo": headline,
                "descricao": description,
                "severidade": severity,
                "urgencia": urgency,
                "certeza": certainty,
                "inicio": onset,
                "fim": expires,
                "areas_afetadas": area_desc,
                "instrucoes": instruction,
                "link": f"https://avisos.inmet.gov.br/{aviso_id}"
            })

        except Exception:
            continue

    alertas.sort(key=lambda x: x.get("inicio") or "")
    return alertas


def procurar_valor_numerico(item):
    chaves_possiveis = [
        "nivel",
        "Nivel",
        "cota",
        "Cota",
        "valor",
        "Valor",
        "valorNivel",
        "ValorNivel",
        "nivelConsistido",
        "NivelConsistido"
    ]

    for chave in chaves_possiveis:
        if chave in item and item[chave] is not None:
            try:
                return float(str(item[chave]).replace(",", "."))
            except Exception:
                pass

    return None


def procurar_data(item):
    chaves_possiveis = [
        "dataHora",
        "DataHora",
        "data",
        "Data",
        "horario",
        "Horario",
        "dataMedicao",
        "DataMedicao"
    ]

    for chave in chaves_possiveis:
        if chave in item and item[chave]:
            return item[chave]

    return None


def buscar_nivel_ana():
    url_estacao = "https://www.snirh.gov.br/hidroweb/rest/api/estacaotelemetrica"
    params_estacao = {"id": CODIGO_ANA_BRACO_NORTE}

    r1 = requests.get(url_estacao, params=params_estacao, timeout=25)
    r1.raise_for_status()
    estacao = r1.json()

    codigo_interno = estacao.get("id") or estacao.get("codigo") or estacao.get("codEstacao")

    if not codigo_interno:
        return {
            "status": "erro",
            "mensagem": "Não foi possível obter o código interno da estação ANA.",
            "retorno_estacao": estacao
        }

    agora_utc = datetime.now(timezone.utc)
    inicio_utc = agora_utc - timedelta(days=3)

    periodo_inicial = inicio_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    periodo_final = agora_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    url_dados = "https://www.snirh.gov.br/hidroweb/rest/api/documento/gerarTelemetricas"
    params_dados = {
        "codigosEstacoes": codigo_interno,
        "tipoArquivo": 2,
        "periodoInicial": periodo_inicial,
        "periodoFinal": periodo_final
    }

    r2 = requests.get(url_dados, params=params_dados, timeout=40)
    r2.raise_for_status()

    dados = r2.json()

    if isinstance(dados, dict):
        lista = dados.get("content") or dados.get("dados") or dados.get("items") or []
    elif isinstance(dados, list):
        lista = dados
    else:
        lista = []

    medicoes_validas = []

    for item in lista:
        if not isinstance(item, dict):
            continue

        valor = procurar_valor_numerico(item)
        data = procurar_data(item)

        if valor is not None:
            medicoes_validas.append({
                "data": data,
                "cota_cm": valor,
                "item_original": item
            })

    if not medicoes_validas:
        return {
            "status": "sem_dados",
            "mensagem": "A ANA respondeu, mas não foram encontradas medições válidas de nível/cota.",
            "codigo_ana": CODIGO_ANA_BRACO_NORTE,
            "codigo_interno": codigo_interno,
            "amostra_retorno": lista[:3]
        }

    ultima = medicoes_validas[-1]
    cota_cm = ultima["cota_cm"]
    cota_m = round(cota_cm / 100, 2)
    situacao = classificar_nivel_rio(cota_cm)

    return {
        "status": "ok",
        "fonte": "ANA / HidroWeb Telemetria",
        "estacao": "Braço do Norte - Montante",
        "codigo_ana": CODIGO_ANA_BRACO_NORTE,
        "codigo_interno": codigo_interno,
        "data_ultima_medicao": ultima["data"],
        "nivel_cm": cota_cm,
        "nivel_m": cota_m,
        "situacao": situacao,
        "criterios_cm": {
            "normal": "24,01 a 500 cm",
            "atencao_enchente": "500,01 a 600 cm",
            "alerta_enchente": "600,01 a 700 cm",
            "emergencia_enchente": "acima de 700 cm"
        }
    }


@app.get("/")
def inicio():
    return {
        "status": "online",
        "sistema": "Monitor São Ludgero API",
        "versao": "1.6"
    }


@app.get("/previsao-chuva")
def previsao_chuva(dias: int = 7):
    dados = buscar_previsao(dias)
    return {
        "local": "São Ludgero/SC",
        "fonte": "Open-Meteo",
        "previsao_diaria": dados.get("daily", {}),
        "previsao_horaria": dados.get("hourly", {})
    }


@app.get("/risco-hidrologico")
def risco_hidrologico():
    dados = buscar_previsao(7)
    diaria = dados.get("daily", {})
    chuva = diaria.get("precipitation_sum", [])

    chuva_24h = round(sum(chuva[:1]), 1)
    chuva_48h = round(sum(chuva[:2]), 1)
    chuva_72h = round(sum(chuva[:3]), 1)
    chuva_7d = round(sum(chuva), 1)

    risco, observacao = classificar_risco(chuva_72h, chuva_7d)

    return {
        "local": "São Ludgero/SC",
        "fonte": "Open-Meteo",
        "chuva_24h_mm": chuva_24h,
        "chuva_48h_mm": chuva_48h,
        "chuva_72h_mm": chuva_72h,
        "chuva_7dias_mm": chuva_7d,
        "risco": risco,
        "observacao": observacao
    }


@app.get("/alertas-ativos")
def alertas_ativos():
    try:
        alertas = buscar_alertas_inmet()
        return {
            "local_referencia": "São Ludgero/SC",
            "fonte": "INMET - Alert-AS / CAP RSS",
            "quantidade_alertas_encontrados": len(alertas),
            "alertas": alertas,
            "observacao": "Filtro por regiões de SC e apenas alertas ativos ou futuros."
        }
    except Exception as erro:
        return {
            "fonte": "INMET",
            "status": "erro_na_consulta",
            "mensagem": "Não foi possível consultar automaticamente os alertas do INMET neste momento.",
            "erro": str(erro),
            "consulta_manual": "https://alertas2.inmet.gov.br/"
        }


@app.get("/nivel-rio-braco-norte")
def nivel_rio_braco_norte():
    try:
        return buscar_nivel_ana()
    except Exception as erro:
        return {
            "status": "erro_na_consulta",
            "fonte": "ANA / HidroWeb Telemetria",
            "estacao": "Braço do Norte - Montante",
            "codigo_ana": CODIGO_ANA_BRACO_NORTE,
            "mensagem": "Não foi possível consultar automaticamente o nível do rio na ANA neste momento.",
            "erro": str(erro)
        }
