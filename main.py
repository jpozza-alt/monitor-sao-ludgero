from fastapi import FastAPI
import requests
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

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
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


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

    ids = sorted(set(re.findall(r"avisos/(?:rss/)?(\d+)", resposta.text)))
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

            if not any(t.lower() in area_desc.lower() for t in TERMOS_REGIAO_SC):
                continue

            if expires and datetime.fromisoformat(expires) < agora:
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


def buscar_nivel_ana():
    hoje = datetime.now()
    inicio = hoje - timedelta(days=10)

    url = "https://telemetriaws1.ana.gov.br/ServiceANA.asmx/DadosHidrometeorologicos"
    params = {
        "CodEstacao": CODIGO_ANA_BRACO_NORTE,
        "DataInicio": inicio.strftime("%d/%m/%Y"),
        "DataFim": hoje.strftime("%d/%m/%Y")
    }

    resposta = requests.get(url, params=params, timeout=30)
    resposta.raise_for_status()

    raiz = ET.fromstring(resposta.content)
    medicoes = []

    for item in raiz.iter():
        tags = {filho.tag.split("}")[-1]: filho.text for filho in list(item)}

        if "Nivel" in tags:
            try:
                nivel_cm = float(str(tags.get("Nivel")).replace(",", "."))
                medicoes.append({
                    "data_hora": tags.get("DataHora"),
                    "nivel_cm": nivel_cm,
                    "vazao": tags.get("Vazao")
                })
            except Exception:
                continue

    if not medicoes:
        return {
            "status": "sem_dados",
            "fonte": "ANA / TelemetriaWS1",
            "estacao": "Braço do Norte - Montante",
            "codigo_ana": CODIGO_ANA_BRACO_NORTE,
            "mensagem": "A ANA respondeu, mas não retornou medições de nível no período consultado."
        }

    ultima = medicoes[-1]
    nivel_cm = ultima["nivel_cm"]

    return {
        "status": "ok",
        "fonte": "ANA / TelemetriaWS1",
        "estacao": "Braço do Norte - Montante",
        "codigo_ana": CODIGO_ANA_BRACO_NORTE,
        "data_ultima_medicao": ultima["data_hora"],
        "nivel_cm": nivel_cm,
        "nivel_m": round(nivel_cm / 100, 2),
        "vazao": ultima["vazao"],
        "situacao": classificar_nivel_rio(nivel_cm)
    }


@app.get("/")
def inicio():
    return {
        "status": "online",
        "sistema": "Monitor São Ludgero API",
        "versao": "1.8"
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
    chuva = dados.get("daily", {}).get("precipitation_sum", [])

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
            "erro": str(erro)
        }


@app.get("/nivel-rio-braco-norte")
def nivel_rio_braco_norte():
    try:
        return buscar_nivel_ana()
    except Exception as erro:
        return {
            "status": "erro_na_consulta",
            "fonte": "ANA / TelemetriaWS1",
            "estacao": "Braço do Norte - Montante",
            "codigo_ana": CODIGO_ANA_BRACO_NORTE,
            "erro": str(erro)
        }


@app.get("/debug-ana")
def debug_ana():
    hoje = datetime.now()
    inicio = hoje - timedelta(days=10)

    url = "https://telemetriaws1.ana.gov.br/ServiceANA.asmx/DadosHidrometeorologicos"
    params = {
        "CodEstacao": CODIGO_ANA_BRACO_NORTE,
        "DataInicio": inicio.strftime("%d/%m/%Y"),
        "DataFim": hoje.strftime("%d/%m/%Y")
    }

    try:
        resposta = requests.get(url, params=params, timeout=30)

        tags = []
        texto_resposta = resposta.text[:2000]

        try:
            raiz = ET.fromstring(resposta.content)
            tags = sorted(set([item.tag.split("}")[-1] for item in raiz.iter()]))
        except Exception:
            pass

        return {
            "status_http": resposta.status_code,
            "url_consultada": resposta.url,
            "primeiros_2000_caracteres": texto_resposta,
            "tags_encontradas": tags
        }

    except Exception as erro:
        return {
            "status": "erro_debug",
            "erro": str(erro)
        }
